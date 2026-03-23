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

# --- CONFIGURAZIONE INTEGRATA ---
SECRET_KEY = b"HYDRA_OBSIDIAN_V5_2026"
TCP_PORT = 5555
UDP_PORT = 5556
MAX_LOGS = 50
SSID_NAME = "HYDRA_NEXUS"
SSID_PASS = "obsidian2026"

# --- LOGICA ACCESS POINT (WINDOWS) ---
def manage_hotspot(action="start"):
    """Crea una rete WiFi dedicata se il PC lo supporta."""
    try:
        if action == "start":
            subprocess.run(f'netsh wlan set hostednetwork mode=allow ssid={SSID_NAME} key={SSID_PASS}', shell=True, capture_output=True)
            subprocess.run('netsh wlan start hostednetwork', shell=True, capture_output=True)
    except: pass

# --- UTILS DI RETE ---
def get_local_ips():
    ips = []
    for interface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                ips.append(addr.address)
    return ips if ips else ["127.0.0.1"]

# --- STILI CSS (ESTESI DALL'ORIGINALE) ---
def apply_custom_styles():
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;700&family=Fira+Code:wght@400;500&display=swap');
        html, body, [class*="css"] { 
            background-color: #0d0d0d; 
            color: #e0e0e0; 
            font-family: 'Inter', sans-serif;
        }
        .metric-card {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.1);
            padding: 20px;
            border-radius: 15px;
            text-align: center;
        }
        .node-container {
            background: linear-gradient(145deg, #161616, #0f0f0f);
            border: 1px solid #222;
            padding: 20px;
            border-radius: 20px;
            margin-bottom: 15px;
            transition: all 0.3s ease;
        }
        .node-container:hover { border-color: #00ff88; box-shadow: 0 0 15px rgba(0, 255, 136, 0.1); }
        .status-online { color: #00ff88; font-weight: bold; font-size: 12px; }
        code { font-family: 'Fira Code', monospace !important; color: #00ff88 !important; }
        .stDataFrame { background: rgba(255, 255, 255, 0.01); border-radius: 10px; }
        .stButton>button { background: #111; border: 1px solid #333; color: #eee; border-radius: 10px; width: 100%; }
        .stButton>button:hover { border-color: #00ff88; color: #00ff88; }
        </style>
    """, unsafe_allow_html=True)

# --- CLASSE MASTER ---
class ObsidianMaster:
    def __init__(self):
        self.ctx = zmq.Context()
        self.socket = self.ctx.socket(zmq.ROUTER)
        # Binding su 0.0.0.0 per accettare LAN, USB e WiFi simultaneamente
        self.socket.bind(f"tcp://0.0.0.0:{TCP_PORT}")
        self.nodes = {} 
        self.event_stream = deque(maxlen=MAX_LOGS)
        self.metrics = {"tasks_ok": 0}
        self.lock = threading.Lock()

    def start(self):
        manage_hotspot("start")
        threading.Thread(target=self._beacon_sender, daemon=True).start()
        threading.Thread(target=self._network_engine, daemon=True).start()
        threading.Thread(target=self._cleanup, daemon=True).start()

    def _beacon_sender(self):
        """Invia l'IP del master in broadcast per l'auto-rilevamento."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while True:
            for ip in get_local_ips():
                msg = f"HYDRA_BEACON|{ip}".encode()
                try: sock.sendto(msg, ('<broadcast>', UDP_PORT))
                except: pass
            time.sleep(2)

    def _network_engine(self):
        while True:
            if self.socket.poll(100):
                try:
                    parts = self.socket.recv_multipart()
                    addr, payload, sig = parts[0], parts[2], parts[3].decode()
                    if hmac.new(SECRET_KEY, payload, hashlib.sha256).hexdigest() == sig:
                        data = json.loads(payload.decode())
                        nid = data['id']
                        with self.lock:
                            if data['t'] == 'HEARTBEAT':
                                self.nodes[nid] = {'addr': addr, 'stats': data['s'], 'history': data['h'], 'last': time.time()}
                            elif data['t'] == 'ACK':
                                self.metrics["tasks_ok"] += 1
                                self.event_stream.appendleft({'time': datetime.now().strftime("%H:%M:%S"), 'node': nid, 'job': data['jid'], 'status': 'DONE', 'lat': f"{data['el']:.2f}s"})
                except: pass

    def _cleanup(self):
        while True:
            now = time.time()
            with self.lock:
                dead = [n for n, d in self.nodes.items() if now - d['last'] > 12]
                for n in dead: del self.nodes[n]
            time.sleep(5)

# --- CLASSE WORKER ---
class ObsidianWorker:
    def __init__(self, master_ip):
        self.id = f"HYDRA-{socket.gethostname()}-{uuid.uuid4().hex[:3]}".upper()
        self.master_ip = master_ip
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.DEALER)
        self.sock.setsockopt_string(zmq.IDENTITY, self.id)
        self.history = deque([0]*25, maxlen=25)
        self.local_events = deque(maxlen=MAX_LOGS)
        self.metrics = {"tasks_done": 0, "cpu": 0, "ram": 0}
        self.active = False

    def start(self):
        self.sock.connect(f"tcp://{self.master_ip}:{TCP_PORT}")
        self.active = True
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._task_loop, daemon=True).start()

    def _task_loop(self):
        while self.active:
            if self.sock.poll(1000):
                try:
                    parts = self.sock.recv_multipart()
                    payload, sig = parts[1], parts[2].decode()
                    if hmac.new(SECRET_KEY, payload, hashlib.sha256).hexdigest() == sig:
                        req = json.loads(payload.decode())
                        t0 = time.time()
                        for _ in range(250000): hashlib.sha256(b"work").hexdigest()
                        el = time.time() - t0
                        self.metrics["tasks_done"] += 1
                        self.local_events.appendleft({'time': datetime.now().strftime("%H:%M:%S"), 'job': req['jid'], 'status': 'COMPLETED', 'lat': f"{el:.3f}s"})
                        ans = {'t': 'ACK', 'id': self.id, 'jid': req['jid'], 'el': el}
                        msg = json.dumps(ans).encode()
                        sig_ans = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()
                        self.sock.send_multipart([b"", msg, sig_ans.encode()])
                except: pass

    def _heartbeat_loop(self):
        while self.active:
            self.metrics["cpu"], self.metrics["ram"] = psutil.cpu_percent(), psutil.virtual_memory().percent
            self.history.append(self.metrics["cpu"])
            data = {'t': 'HEARTBEAT', 'id': self.id, 's': {'cpu': self.metrics["cpu"], 'ram': self.metrics["ram"]}, 'h': list(self.history)}
            msg = json.dumps(data).encode()
            sig = hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()
            try: self.sock.send_multipart([b"", msg, sig.encode()])
            except: pass
            time.sleep(2)

# --- UI DRAWING ---
def draw_header(title, subtitle):
    c1, c2 = st.columns([4, 1])
    with c1:
        st.markdown(f"<h1 style='margin:0; letter-spacing:-2px;'>{title} <span style='color:#00ff88'>OBSIDIAN</span></h1>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:#666;'>{subtitle}</p>", unsafe_allow_html=True)
    with c2:
        st.markdown(f"<div class='metric-card'><small>SYSTEM CLOCK</small><br><b style='color:#00ff88'>{datetime.now().strftime('%H:%M:%S')}</b></div>", unsafe_allow_html=True)
    st.write("---")

def main():
    apply_custom_styles()
    if len(sys.argv) < 2:
        st.error("Specificare modalità: master o worker")
        return
    
    mode = sys.argv[1].lower()

    if mode == "master":
        if 'engine' not in st.session_state:
            st.session_state.engine = ObsidianMaster()
            st.session_state.engine.start()
        
        master = st.session_state.engine
        draw_header("HYDRA", "Industrial Distributed Computing Core // Master Mode")

        # Top Metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("NODES ACTIVE", len(master.nodes))
        m2.metric("JOBS COMPLETED", master.metrics["tasks_ok"])
        avg_load = np.mean([n['stats']['cpu'] for n in master.nodes.values()]) if master.nodes else 0
        m3.metric("CLUSTER LOAD", f"{avg_load:.1f}%")
        m4.metric("IP ADDR", get_local_ips()[0])

        st.markdown("### 🛰️ NETWORK NODES")
        if not master.nodes: st.info("Waiting for nodes on SSID: HYDRA_NEXUS...")
        else:
            node_items = list(master.nodes.items())
            for i in range(0, len(node_items), 3):
                cols = st.columns(3)
                for j in range(3):
                    if i + j < len(node_items):
                        nid, data = node_items[i+j]
                        with cols[j]:
                            st.markdown(f"<div class='node-container'><span class='status-online'>● ONLINE</span><h4 style='margin:5px 0;'>{nid}</h4><p style='font-size:13px; color:#888;'>CPU: {data['stats']['cpu']}% | RAM: {data['stats']['ram']}%</p></div>", unsafe_allow_html=True)
                            fig = go.Figure(go.Scatter(y=data['history'], fill='tozeroy', line=dict(color='#00ff88', width=2)))
                            fig.update_layout(height=80, margin=dict(l=0,r=0,t=0,b=0), xaxis_visible=False, yaxis_visible=False, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                            st.plotly_chart(fig, use_container_width=True, key=f"g_{nid}")

        st.markdown("### 📜 GLOBAL EVENT STREAM")
        st.dataframe(pd.DataFrame(list(master.event_stream)), use_container_width=True)
        time.sleep(2); st.rerun()

    elif mode == "worker":
        draw_header("NODE", "Worker Interface")
        
        if 'node' not in st.session_state:
            st.markdown("<div class='node-container'>", unsafe_allow_html=True)
            st.subheader("📡 Connection Hub")
            
            if st.button("AUTO-SCAN FOR MASTER"):
                with st.spinner("Listening for Beacon..."):
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.bind(('', UDP_PORT)); sock.settimeout(5.0)
                    try:
                        data, addr = sock.recvfrom(1024)
                        if data.startswith(b"HYDRA_BEACON"):
                            st.session_state.target_ip = data.decode().split("|")[1]
                            st.success(f"Master Found: {st.session_state.target_ip}")
                    except: st.error("No Master found. Check WiFi/USB cable.")
                    finally: sock.close()

            manual_ip = st.text_input("Master IP:", st.session_state.get('target_ip', '127.0.0.1'))
            if st.button("🚀 CONNECT TO MESH"):
                st.session_state.node = ObsidianWorker(manual_ip)
                st.session_state.node.start()
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            worker = st.session_state.node
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("STATUS", "LINKED")
            m2.metric("TASKS DONE", worker.metrics["tasks_done"])
            m3.metric("CPU", f"{worker.metrics['cpu']}%")
            m4.metric("RAM", f"{worker.metrics['ram']}%")

            st.markdown("### 📈 PERFORMANCE")
            fig = go.Figure(go.Scatter(y=list(worker.history), fill='tozeroy', line=dict(color='#00ff88', width=3)))
            fig.update_layout(height=250, margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("### 📜 MY TASK HISTORY")
            st.dataframe(pd.DataFrame(list(worker.local_events)), use_container_width=True)
            time.sleep(2); st.rerun()

if __name__ == "__main__":
    main()