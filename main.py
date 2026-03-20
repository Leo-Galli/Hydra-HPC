"""
                                                
     ██╗  ██╗██╗   ██╗██████╗ ██████╗  █████╗ 
     ██║  ██║╚██╗ ██╔╝██╔══██╗██╔══██╗██╔══██╗
     ███████║ ╚████╔╝ ██║  ██║██████╔╝███████║
     ██╔══██║  ╚██╔╝  ██║  ██║██╔══██╗██╔══██║
     ██║  ██║   ██║   ██████╔╝██║  ██║██║  ██║
     ╚═╝  ╚═╝   ╚═╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝

HYDRA APEX - DYNAMIC LOAD BALANCING & SYSTEM ABSTRACTION
[MODULE]: KERNEL_V120_STABLE
[SECURITY]: CLUSTER_KEY_ENFORCED
"""

import os
import sys
import time
import json
import uuid
import socket
import logging
import asyncio
import threading
import hashlib
from datetime import datetime
from typing import Dict, List, Any

import zmq
import zmq.asyncio
import psutil
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
from dotenv import load_dotenv

# --- INITIALIZATION ---
load_dotenv()
PORT = int(os.getenv("HYDRA_BROKER_PORT", 5555))
CLUSTER_KEY = os.getenv("HYDRA_CLUSTER_SECRET", "HYDRA_VOID_ALPHA_2026")
CPU_THRESHOLD = 75.0  # Threshold to stop sending tasks (Gaming Mode)

# =============================================================================
# NETWORK & TELEMETRY KERNEL
# =============================================================================

class SystemKernel:
    @staticmethod
    def get_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 1))
            return s.getsockname()[0]
        except: return '127.0.0.1'
        finally: s.close()

    @staticmethod
    def fetch_telemetry():
        vm = psutil.virtual_memory()
        cpu_load = psutil.cpu_percent(interval=None)
        # Check if the system is "Busy" (Gaming/Rendering)
        status = "IDLE" if cpu_load < 15 else "ACTIVE"
        if cpu_load > CPU_THRESHOLD: status = "CRITICAL_LOAD"
        
        return {
            "cpu": cpu_load,
            "ram_p": vm.percent,
            "ram_a": vm.available // (1024 * 1024),
            "status": status,
            "uptime": int(time.time() - psutil.boot_time())
        }

# =============================================================================
# MASTER ORCHESTRATOR
# =============================================================================

class HydraMaster:
    def __init__(self):
        self.ctx = zmq.asyncio.Context()
        self.router = self.ctx.socket(zmq.ROUTER)
        self.router.setsockopt(zmq.LINGER, 0)
        try:
            self.router.bind(f"tcp://*:{PORT}")
        except zmq.error.ZMQError:
            pass
        
        self.nodes = {}
        self.history = []
        self.start_ts = datetime.now()
        self.active = False

    async def run_kernel(self):
        if self.active: return
        self.active = True
        while True:
            try:
                frames = await self.router.recv_multipart()
                nid = frames[0].decode()
                data = json.loads(frames[2].decode())
                
                if data.get("secret") != CLUSTER_KEY: continue
                
                if data["type"] == "PULSE":
                    self.nodes[nid] = {
                        "alias": data["alias"],
                        "ip": data["ip"],
                        "telemetry": data["telemetry"],
                        "ts": datetime.now()
                    }
                elif data["type"] == "DATA_RETURN":
                    self.history.append({
                        "job": data["job_id"], "node": data["alias"],
                        "ms": f"{data['elapsed']*1000:.1f}ms", "ts": datetime.now().strftime("%H:%M:%S")
                    })
            except: await asyncio.sleep(0.01)

# =============================================================================
# WORKER PROCESS
# =============================================================================

class HydraWorker:
    def __init__(self, master_ip: str):
        self.master_ip = master_ip
        self.ctx = zmq.Context()
        self.dealer = self.ctx.socket(zmq.DEALER)
        self.id = f"NODE-{socket.gethostname().upper()}-{uuid.uuid4().hex[:4].upper()}"
        self.dealer.setsockopt_string(zmq.IDENTITY, self.id)
        self.dealer.connect(f"tcp://{self.master_ip}:{PORT}")

    def boot(self):
        threading.Thread(target=self.pulse, daemon=True).start()
        while True:
            try:
                _, raw = self.dealer.recv_multipart()
                job = json.loads(raw.decode())
                if job["op"] == "TASK":
                    self.execute(job)
            except: break

    def pulse(self):
        while True:
            try:
                payload = {
                    "type": "PULSE", "alias": self.id,
                    "ip": SystemKernel.get_ip(),
                    "telemetry": SystemKernel.fetch_telemetry(),
                    "secret": CLUSTER_KEY
                }
                self.dealer.send_string("", zmq.SNDMORE)
                self.dealer.send_json(payload)
                time.sleep(2.5)
            except: break

    def execute(self, job):
        t0 = time.time()
        # Simulated workload (e.g., Cryptographic verification)
        _ = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(1200000)]
        
        result = {
            "type": "DATA_RETURN", "job_id": job["id"], "alias": self.id,
            "elapsed": time.time() - t0, "secret": CLUSTER_KEY
        }
        self.dealer.send_string("", zmq.SNDMORE)
        self.dealer.send_json(result)

# =============================================================================
# APEX DASHBOARD (NO EMOJI - INDUSTRIAL DESIGN)
# =============================================================================

def mission_control(master):
    st.set_page_config(page_title="HYDRA APEX", layout="wide")
    
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;700&display=swap');
        .stApp { background-color: #050505; color: #00ffcc; font-family: 'JetBrains Mono', monospace; }
        [data-testid="stHeader"], footer { visibility: hidden; }
        .stMetric { border: 1px solid #00ffcc33; padding: 15px; border-radius: 0px; background: #0a0a0a; }
        .stButton>button { 
            border: 1px solid #00ffcc; background: transparent; color: #00ffcc; 
            border-radius: 0px; width: 100%; letter-spacing: 2px;
        }
        .stButton>button:hover { background: #00ffcc; color: #000; }
        </style>
    """, unsafe_allow_html=True)

    st.title("HYDRA APEX | CENTRAL UNIT")
    st.caption(f"NET_ADDR: {SystemKernel.get_ip()} | PORT_ACTIVE: {PORT}")

    # Node Filtering (Gaming Aware)
    online_nodes = {k: v for k, v in master.nodes.items() if (datetime.now() - v["ts"]).seconds < 10}
    ready_nodes = {k: v for k, v in online_nodes.items() if v["telemetry"]["cpu"] < CPU_THRESHOLD}
    
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("NODES_ATTACHED", len(online_nodes))
    m2.metric("NODES_READY", len(ready_nodes))
    m3.metric("JOBS_PROCESSED", len(master.history))
    m4.metric("SYSTEM_INTEGRITY", "STABLE")

    st.markdown("---")

    if online_nodes:
        c_left, c_right = st.columns([2, 1])
        
        with c_left:
            st.subheader("CLUSTER_REGISTRY")
            df_data = []
            for k, v in online_nodes.items():
                df_data.append({
                    "NODE_ID": v["alias"], 
                    "CPU_LOAD": f"{v['telemetry']['cpu']}%", 
                    "RAM_VAL": f"{v['telemetry']['ram_p']}%",
                    "STATUS": v["telemetry"]["status"]
                })
            st.dataframe(pd.DataFrame(df_data), use_container_width=True, hide_index=True)
            
            # Load Graph
            fig = go.Figure()
            fig.add_trace(go.Scatter(y=[float(d["CPU_LOAD"].strip('%')) for d in df_data], 
                                     x=[d["NODE_ID"] for d in df_data], mode='lines+markers',
                                     line=dict(color='#00ffcc')))
            fig.update_layout(template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', 
                              plot_bgcolor='rgba(0,0,0,0)', height=300, margin=dict(l=0,r=0,t=20,b=0))
            st.plotly_chart(fig, use_container_width=True)

        with c_right:
            st.subheader("COMMAND_CENTER")
            if st.button("RUN_DISTRIBUTED_COMPUTE"):
                if not ready_nodes:
                    st.error("NO NODES BELOW LOAD THRESHOLD")
                else:
                    for nid in ready_nodes:
                        master.router.send_multipart([nid.encode(), b"", json.dumps({"op": "TASK", "id": uuid.uuid4().hex[:6]}).encode()])
                    st.success(f"TASK_SENT_TO_{len(ready_nodes)}_NODES")

            st.subheader("LOG_STREAM")
            st.table(pd.DataFrame(master.history).tail(10))
    else:
        st.warning("AWAITING_NODE_HANDSHAKE...")

    time.sleep(2)
    st.rerun()

# =============================================================================
# BOOT
# =============================================================================

if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(1)
    mode = sys.argv[1].lower()

    if mode == "master":
        if 'kernel' not in st.session_state:
            st.session_state.kernel = HydraMaster()
            def start_async(m):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(m.run_kernel())
            threading.Thread(target=start_async, args=(st.session_state.kernel,), daemon=True).start()
        mission_control(st.session_state.kernel)

    elif mode == "worker":
        ip = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
        HydraWorker(ip).boot()