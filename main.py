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
import subprocess
from datetime import datetime
from collections import deque

# --- CONFIGURAZIONE PROGETTO ---
PROJECT_NAME = "HYDRA_NEXUS_V18"
SSID_NAME = "HYDRA_NEXUS_AP"
AP_PASSWORD = "obsidian_secure_2026"
SECRET_KEY = b"HYDRA_GHOST_2026_KEY"
TCP_PORT = 5555
UDP_PORT = 5556
MAX_LOGS = 100

# --- UTILS SISTEMA (WIFI & NETWORK) ---
class NexusNetwork:
    @staticmethod
    def create_ap():
        """Tenta di creare l'Access Point del Progetto."""
        try:
            # Configura l'Hosted Network di Windows
            subprocess.run(f'netsh wlan set hostednetwork mode=allow ssid={SSID_NAME} key={AP_PASSWORD}', shell=True, capture_output=True)
            res = subprocess.run('netsh wlan start hostednetwork', shell=True, capture_output=True)
            if res.returncode == 0:
                return True, f"Wireless '{SSID_NAME}' creato con successo."
            else:
                return False, "Hardware AP non supportato. Usa l'Hotspot Mobile di Windows manualmente."
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_active_ips():
        ips = []
        for interface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    ips.append({"int": interface, "ip": addr.address})
        return ips

# --- STILI CSS ---
def apply_styles():
    st.set_page_config(page_title=PROJECT_NAME, layout="wide")
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Inter:wght@300;400;700&display=swap');
        
        :root { --glow: #00ff88; --dark: #080808; }
        
        html, body, [class*="css"] { 
            background-color: var(--dark); 
            color: #d1d1d1; 
            font-family: 'Inter', sans-serif;
        }
        
        .stMetric { background: #111; border: 1px solid #222; border-radius: 12px; }
        
        .header-box {
            padding: 30px;
            background: linear-gradient(90deg, #111 0%, #050505 100%);
            border-bottom: 2px solid var(--glow);
            margin-bottom: 30px;
        }
        
        .project-title {
            font-family: 'Orbitron', sans-serif;
            font-size: 3.5rem;
            color: var(--glow);
            text-shadow: 0 0 20px rgba(0, 255, 136, 0.4);
            margin: 0;
        }

        .node-card {
            background: #111;
            border: 1px solid #222;
            padding: 20px;
            border-radius: 15px;
            border-top: 4px solid var(--glow);
            margin-bottom: 15px;
        }
        
        .status-dot {
            height: 12px; width: 12px; background-color: var(--glow);
            border-radius: 50%; display: inline-block;
            box-shadow: 0 0 10px var(--glow);
            margin-right: 10px;
        }
        
        .stButton>button {
            background: transparent; border: 1px solid var(--glow); color: var(--glow);
            font-family: 'Orbitron'; border-radius: 5px; transition: 0.3s;
        }
        .stButton>button:hover { background: var(--glow); color: black; box-shadow: 0 0 15px var(--glow); }
        </style>
    """, unsafe_allow_html=True)

# --- CLASSE SERVER (MASTER) ---
class HydraServer:
    def __init__(self):
        self.ctx = zmq.Context()
        self.socket = self.ctx.socket(zmq.ROUTER)
        self.socket.bind(f"tcp://0.0.0.0:{TCP_PORT}")
        self.nodes = {} 
        self.logs = deque(maxlen=MAX_LOGS)
        self.lock = threading.Lock()
        self.running = True

    def start(self):
        threading.Thread(target=self._broadcast_presence, daemon=True).start()
        threading.Thread(target=self._receive_loop, daemon=True).start()

    def _broadcast_presence(self):
        """Invia l'identità del progetto via UDP Broadcast."""
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.running:
            ips = NexusNetwork.get_active_ips()
            for entry in ips:
                try:
                    # Formato: IDENTIFIER | PROJECT_NAME | IP
                    payload = f"HYDRA_V18|{PROJECT_NAME}|{entry['ip']}".encode()
                    udp_sock.sendto(payload, ('<broadcast>', UDP_PORT))
                except: pass
            time.sleep(2)

    def _receive_loop(self):
        while self.running:
            if self.socket.poll(100):
                try:
                    parts = self.socket.recv_multipart()
                    identity, payload, signature = parts[0], parts[2], parts[3].decode()
                    
                    if hmac.new(SECRET_KEY, payload, hashlib.sha256).hexdigest() == signature:
                        data = json.loads(payload.decode())
                        self._process_data(identity, data)
                except: pass

    def _process_data(self, identity, data):
        nid = data['id']
        with self.lock:
            self.nodes[nid] = {
                'id_zmq': identity,
                'hostname': data.get('host', 'Generic_Node'),
                'stats': data['s'],
                'history': data['h'],
                'last_seen': time.time()
            }
            if data['t'] == 'DATA':
                self.logs.appendleft({
                    "Timestamp": datetime.now().strftime("%H:%M:%S"),
                    "Node_ID": nid,
                    "Project": PROJECT_NAME,
                    "CPU_Usage": f"{data['s']['cpu']}%"
                })

# --- CLASSE CLIENT (WORKER) ---
class HydraClient:
    def __init__(self, target_ip):
        self.id = f"HYDRA-NODE-{uuid.uuid4().hex[:4].upper()}"
        self.master_ip = target_ip
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.DEALER)
        self.sock.setsockopt_string(zmq.IDENTITY, self.id)
        self.history = deque([0]*40, maxlen=40)
        self.stats = {"cpu": 0, "ram": 0}
        self.is_connected = False

    def connect(self):
        self.sock.connect(f"tcp://{self.master_ip}:{TCP_PORT}")
        self.is_connected = True
        threading.Thread(target=self._sync_loop, daemon=True).start()

    def _sync_loop(self):
        while True:
            self.stats["cpu"] = psutil.cpu_percent()
            self.stats["ram"] = psutil.virtual_memory().percent
            self.history.append(self.stats["cpu"])
            
            data = {
                't': 'DATA',
                'id': self.id,
                'host': socket.gethostname(),
                's': self.stats,
                'h': list(self.history)
            }
            msg = json.dumps(data).encode()
            sig = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest().encode()
            
            try:
                self.sock.send_multipart([b"", msg, sig])
            except:
                self.is_connected = False
            time.sleep(1.5)

# --- INTERFACCIA STREAMLIT ---
def main():
    apply_styles()
    
    if len(sys.argv) < 2:
        st.error("Specificare 'server' o 'client' all'avvio.")
        return

    mode = sys.argv[1].lower()

    if mode == "server":
        if 'server_engine' not in st.session_state:
            st.session_state.server_engine = HydraServer()
            st.session_state.server_engine.start()
        
        srv = st.session_state.server_engine

        # Header Server
        st.markdown(f"""
            <div class='header-box'>
                <h1 class='project-title'>{PROJECT_NAME} // SERVER</h1>
                <p style='color:#888;'>Broadcast attivo su SSID: <b>{SSID_NAME}</b></p>
            </div>
        """, unsafe_allow_html=True)

        if st.sidebar.button("🛠️ FORZA CREAZIONE WIRELESS"):
            ok, msg = NexusNetwork.create_ap()
            if ok: st.sidebar.success(msg)
            else: st.sidebar.warning(msg)

        # Dashboard Metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("NODI IN RETE", len(srv.nodes))
        m2.metric("TRAFFICO DATI", f"{len(srv.logs)} PKTS")
        m3.metric("PROJECT STATUS", "ACTIVE")

        # Mappa Nodi
        st.write("---")
        st.subheader("🛰️ GHOST MESH TOPOLOGY")
        if not srv.nodes:
            st.info("In attesa di connessioni Client... Assicurati che i Client facciano lo 'Scan'.")
        else:
            n_items = list(srv.nodes.items())
            for i in range(0, len(n_items), 3):
                cols = st.columns(3)
                for j in range(3):
                    if i+j < len(n_items):
                        nid, d = n_items[i+j]
                        with cols[j]:
                            st.markdown(f"""
                                <div class='node-card'>
                                    <div class='status-dot'></div> <b>{d['hostname']}</b><br>
                                    <small style='color:#666;'>UID: {nid}</small><br>
                                    <p style='margin-top:10px; font-family:monospace;'>CPU: {d['stats']['cpu']}% | RAM: {d['stats']['ram']}%</p>
                                </div>
                            """, unsafe_allow_html=True)
                            fig = go.Figure(go.Scatter(y=d['history'], fill='tozeroy', line=dict(color='#00ff88', width=2)))
                            fig.update_layout(height=80, margin=dict(l=0,r=0,t=0,b=0), xaxis_visible=False, yaxis_visible=False, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                            st.plotly_chart(fig, use_container_width=True, key=nid)

        st.subheader("📜 PROJECT SYNC LOGS")
        st.dataframe(pd.DataFrame(list(srv.logs)), use_container_width=True)
        time.sleep(1); st.rerun()

    elif mode == "client":
        st.markdown(f"""
            <div class='header-box'>
                <h1 class='project-title'>{PROJECT_NAME} // CLIENT</h1>
                <p style='color:#888;'>Ricerca attiva di Server nel perimetro...</p>
            </div>
        """, unsafe_allow_html=True)

        if 'client_engine' not in st.session_state:
            # Fase di Ricerca (Scan)
            st.subheader("🔍 RADAR DI SCANSIONE")
            
            if 'discovered' not in st.session_state:
                st.session_state.discovered = {}

            if st.button("📡 AVVIA SCANSIONE"):
                with st.spinner("Ascolto pacchetti GHOST V18..."):
                    scanner = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    scanner.bind(('', UDP_PORT))
                    scanner.settimeout(4.0)
                    end_time = time.time() + 4
                    while time.time() < end_time:
                        try:
                            data, addr = scanner.recvfrom(1024)
                            raw = data.decode().split("|")
                            if raw[0] == "HYDRA_V18":
                                # IP: {PROJ_NAME}
                                st.session_state.discovered[raw[2]] = raw[1]
                        except: pass
                    scanner.close()

            if st.session_state.discovered:
                st.write("### 🟢 SERVER RILEVATI:")
                for ip, name in st.session_state.found_masters.items() if hasattr(st.session_state, 'found_masters') else st.session_state.discovered.items():
                    c_node, c_act = st.columns([3, 1])
                    c_node.markdown(f"""
                        <div style='background:#1a1a1a; padding:15px; border-radius:10px; border-left:4px solid #00ff88;'>
                            <b>PROGETTO: {name}</b><br>
                            <small>IP IDENTIFICATO: {ip}</small>
                        </div>
                    """, unsafe_allow_html=True)
                    if c_act.button("COLLEGATI", key=ip):
                        st.session_state.client_engine = HydraClient(ip)
                        st.session_state.client_engine.connect()
                        st.rerun()
            else:
                st.warning("Nessun server HYDRA rilevato. Connettiti al Wi-Fi 'HYDRA_NEXUS_AP' o usa un cavo USB.")
                manual = st.text_input("IP Manuale:")
                if st.button("FORZA LINK"):
                    st.session_state.client_engine = HydraClient(manual)
                    st.session_state.client_engine.connect()
                    st.rerun()
        else:
            # Fase Connessa
            cl = st.session_state.client_engine
            st.success(f"COLLEGATO AL SERVER: {cl.master_ip}")
            
            # Local Stats
            c1, c2, c3 = st.columns(3)
            c1.metric("MIO ID", cl.id[:15])
            c2.metric("CPU CARICO", f"{cl.stats['cpu']}%")
            c3.metric("RAM CARICO", f"{cl.stats['ram']}%")

            fig = go.Figure(go.Scatter(y=list(cl.history), fill='tozeroy', line=dict(color='#00ff88', width=3)))
            fig.update_layout(height=300, margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', yaxis=dict(gridcolor='#111'), xaxis=dict(gridcolor='#111'))
            st.plotly_chart(fig, use_container_width=True)

            if st.button("🔌 SCOLLEGATI"):
                del st.session_state.client_engine
                st.rerun()
        
        time.sleep(1); st.rerun()

if __name__ == "__main__":
    main()