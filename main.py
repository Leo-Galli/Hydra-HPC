import os
import sys
import time
import json
import uuid
import socket
import threading
import hashlib
import hmac
import psutil
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
import zmq
from datetime import datetime
from collections import deque

# --- CONFIGURAZIONE ELITE ---
SECRET_KEY = b"HYDRA_PROMETHEUS_V17_2026"
TCP_PORT = 5555
UDP_PORT = 5556 
MAX_LOGS = 100

# --- HELPER DI RETE ---
def get_local_info():
    """Ritorna l'hostname e tutti gli IP disponibili."""
    hostname = socket.gethostname()
    ips = []
    for interface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                ips.append({"int": interface, "ip": addr.address})
    return hostname, ips

# --- STILI CSS OBSIDIAN V17 ---
def apply_v17_styles():
    st.set_page_config(page_title="HYDRA V17", layout="wide")
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@300;400;800&display=swap');
        
        :root { --accent: #00ff88; --bg: #050505; }
        
        html, body, [class*="css"] { 
            background-color: var(--bg); 
            color: #eee; 
            font-family: 'Inter', sans-serif;
        }
        
        .stMetric { background: #111; border: 1px solid #222; border-radius: 15px; padding: 15px !important; }
        
        .master-card {
            background: linear-gradient(180deg, #0f0f0f 0%, #050505 100%);
            border: 1px solid #333;
            padding: 20px;
            border-radius: 20px;
            border-left: 5px solid var(--accent);
            margin-bottom: 20px;
        }
        
        .target-box {
            border: 1px solid #444;
            padding: 10px;
            border-radius: 10px;
            margin-top: 5px;
            cursor: pointer;
            transition: 0.2s;
        }
        .target-box:hover { border-color: var(--accent); background: rgba(0, 255, 136, 0.05); }
        
        .status-led {
            height: 10px; width: 10px; background-color: var(--accent);
            border-radius: 50%; display: inline-block;
            box-shadow: 0 0 10px var(--accent);
            margin-right: 10px;
        }
        
        h1 { font-weight: 800; letter-spacing: -3px; font-size: 4rem; margin-bottom: 0; }
        code { font-family: 'JetBrains Mono', monospace !important; color: var(--accent) !important; }
        </style>
    """, unsafe_allow_html=True)

# --- ENGINE MASTER ---
class PrometheusMaster:
    def __init__(self):
        self.hostname, self.interfaces = get_local_info()
        self.ctx = zmq.Context()
        self.socket = self.ctx.socket(zmq.ROUTER)
        self.socket.bind(f"tcp://0.0.0.0:{TCP_PORT}")
        self.nodes = {} 
        self.events = deque(maxlen=MAX_LOGS)
        self.lock = threading.Lock()
        self.running = True

    def start(self):
        threading.Thread(target=self._udp_beacon, daemon=True).start()
        threading.Thread(target=self._zmq_listen, daemon=True).start()
        threading.Thread(target=self._monitor, daemon=True).start()

    def _udp_beacon(self):
        """Annuncia: ID|HOSTNAME|IP su tutte le interfacce."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.running:
            for iface in self.interfaces:
                try:
                    payload = f"HYDRA_V17|{self.hostname}|{iface['ip']}".encode()
                    sock.sendto(payload, ('<broadcast>', UDP_PORT))
                except: pass
            time.sleep(2)

    def _zmq_listen(self):
        while self.running:
            if self.socket.poll(100):
                try:
                    parts = self.socket.recv_multipart()
                    # ROUTER format: [ID_WORKER, EMPTY, PAYLOAD, SIG]
                    identity, payload, sig = parts[0], parts[2], parts[3].decode()
                    
                    if hmac.new(SECRET_KEY, payload, hashlib.sha256).hexdigest() == sig:
                        data = json.loads(payload.decode())
                        self._handle(identity, data)
                except Exception as e:
                    self.events.appendleft({"t": "ERR", "m": str(e)})

    def _handle(self, identity, data):
        nid = data['id']
        with self.lock:
            self.nodes[nid] = {
                'id_zmq': identity,
                'hostname': data.get('host', 'Unknown'),
                'stats': data['s'],
                'history': data['h'],
                'last_seen': time.time(),
                'ip': data.get('ip', '?.?.?.?')
            }
            if data['type'] == 'DATA':
                self.events.appendleft({
                    "Time": datetime.now().strftime("%H:%M:%S"),
                    "Node": nid,
                    "Action": "HEARTBEAT_SYNC",
                    "CPU": f"{data['s']['cpu']}%"
                })

    def _monitor(self):
        while self.running:
            now = time.time()
            with self.lock:
                to_delete = [n for n, d in self.nodes.items() if now - d['last_seen'] > 10]
                for n in to_delete: del self.nodes[n]
            time.sleep(3)

# --- ENGINE WORKER ---
class PrometheusWorker:
    def __init__(self, master_ip):
        self.hostname = socket.gethostname()
        self.id = f"H-{uuid.uuid4().hex[:4].upper()}"
        self.master_ip = master_ip
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.DEALER)
        self.sock.setsockopt_string(zmq.IDENTITY, self.id)
        self.history = deque([0]*40, maxlen=40)
        self.stats = {"cpu": 0, "ram": 0, "tasks": 0}
        self.connected = False

    def start(self):
        self.sock.connect(f"tcp://{self.master_ip}:{TCP_PORT}")
        self.connected = True
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self):
        while True:
            self.stats["cpu"] = psutil.cpu_percent()
            self.stats["ram"] = psutil.virtual_memory().percent
            self.history.append(self.stats["cpu"])
            
            # Prepazione Payload
            data = {
                'type': 'DATA',
                'id': self.id,
                'host': self.hostname,
                's': self.stats,
                'h': list(self.history),
                'ip': socket.gethostbyname(self.hostname)
            }
            msg = json.dumps(data).encode()
            sig = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest().encode()
            
            try:
                self.sock.send_multipart([b"", msg, sig])
            except:
                self.connected = False
            
            time.sleep(1.5)

# --- UI LOGIC ---
def main():
    apply_v17_styles()
    
    if len(sys.argv) < 2:
        st.error("Usa: 'master' o 'worker' come argomento.")
        return

    mode = sys.argv[1].lower()

    if mode == "master":
        if 'master' not in st.session_state:
            st.session_state.master = PrometheusMaster()
            st.session_state.master.start()
        
        m = st.session_state.master
        
        st.markdown("<h1>HYDRA <span style='color:#00ff88'>MASTER</span></h1>", unsafe_allow_html=True)
        st.write(f"📡 Server Nome: `{m.hostname}` | Porta: `{TCP_PORT}`")
        
        # Dashboard
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("NODI AGGANCIATI", len(m.nodes))
        col_m2.metric("EVENTI TOTALI", len(m.events))
        col_m3.metric("STATUS", "READY")

        st.write("---")
        
        # Mappa Nodi
        st.subheader("🛰️ ACTIVE MESH NODES")
        if not m.nodes:
            st.info("In attesa di Worker... Assicurati che i Client facciano lo scan.")
        else:
            n_list = list(m.nodes.items())
            for i in range(0, len(n_list), 3):
                cols = st.columns(3)
                for j in range(3):
                    if i+j < len(n_list):
                        nid, d = n_list[i+j]
                        with cols[j]:
                            st.markdown(f"""
                            <div class='master-card'>
                                <div class='status-led'></div> <b>{d['hostname']}</b><br>
                                <small>ID: {nid} | IP: {d['ip']}</small><br>
                                <hr style='margin:10px 0; border:0; border-top:1px solid #333;'>
                                <span style='color:#00ff88'>CPU: {d['stats']['cpu']}%</span> | RAM: {d['stats']['ram']}%
                            </div>
                            """, unsafe_allow_html=True)
                            fig = go.Figure(go.Scatter(y=d['history'], fill='tozeroy', line=dict(color='#00ff88', width=2)))
                            fig.update_layout(height=80, margin=dict(l=0,r=0,t=0,b=0), xaxis_visible=False, yaxis_visible=False, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                            st.plotly_chart(fig, use_container_width=True, key=nid)

        st.subheader("📜 REAL-TIME HUB LOGS")
        st.dataframe(pd.DataFrame(list(m.events)), use_container_width=True)
        time.sleep(1); st.rerun()

    elif mode == "worker":
        st.markdown("<h1>HYDRA <span style='color:#00ff88'>WORKER</span></h1>", unsafe_allow_html=True)
        
        if 'worker' not in st.session_state:
            st.subheader("🔍 Ricerca Master in corso...")
            
            # Logica di Scan
            if 'found_masters' not in st.session_state:
                st.session_state.found_masters = {}

            if st.button("📡 AVVIA SCAN RETE"):
                with st.spinner("Scansione interfacce (WiFi, USB, LAN)..."):
                    udp_in = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    udp_in.bind(('', UDP_PORT))
                    udp_in.settimeout(4.0)
                    start_scan = time.time()
                    while time.time() - start_scan < 4:
                        try:
                            data, addr = udp_in.recvfrom(1024)
                            raw = data.decode().split("|")
                            if raw[0] == "HYDRA_V17":
                                # salviamo hostname e IP
                                st.session_state.found_masters[raw[2]] = raw[1]
                        except: pass
                    udp_in.close()
            
            if st.session_state.found_masters:
                st.write("### Master Rilevati:")
                for ip, name in st.session_state.found_masters.items():
                    col_a, col_b = st.columns([3, 1])
                    col_a.markdown(f"<div class='target-box'>🖥️ <b>{name}</b><br><small>Indirizzo: {ip}</small></div>", unsafe_allow_html=True)
                    if col_b.button("CONNETTI", key=ip):
                        st.session_state.worker = PrometheusWorker(ip)
                        st.session_state.worker.start()
                        st.rerun()
            else:
                st.warning("Nessun Master trovato. Controlla che il Master sia attivo e sulla stessa rete/cavo USB.")
                manual_ip = st.text_input("Inserisci IP Master manualmente:")
                if st.button("FORZA CONNESSIONE"):
                    st.session_state.worker = PrometheusWorker(manual_ip)
                    st.session_state.worker.start()
                    st.rerun()
        else:
            w = st.session_state.worker
            st.markdown(f"**CONNESSO A:** `{w.master_ip}` | **MIO ID:** `{w.id}`")
            
            c1, c2, c3 = st.columns(3)
            c1.metric("CPU NODE", f"{w.stats['cpu']}%")
            c2.metric("RAM NODE", f"{w.stats['ram']}%")
            c3.metric("LINK", "ACTIVE" if w.connected else "LOST")
            
            fig = go.Figure(go.Scatter(y=list(w.history), fill='tozeroy', line=dict(color='#00ff88', width=3)))
            fig.update_layout(height=300, margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', yaxis=dict(gridcolor='#111'), xaxis=dict(gridcolor='#111'))
            st.plotly_chart(fig, use_container_width=True)
            
            if st.button("❌ DISCONNETTI"):
                del st.session_state.worker
                st.rerun()
                
        time.sleep(1); st.rerun()

if __name__ == "__main__":
    main()