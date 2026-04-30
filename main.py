import os, sys, time, json, uuid, socket, threading, hashlib, hmac
import psutil
import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
import zmq
from datetime import datetime
from collections import deque

# --- CORE SYSTEM SETTINGS V2.1 ---
VERSION = "V2.1.0 FIXED"
CODENAME = "HYDRA"
SECRET_KEY = b"HYDRA_SINGULARITY_ENCRYPT_2026"
TCP_PORT = 5555
UDP_PORT = 5556
BT_SSID = "HYDRA_COMMAND_CENTER"
NODE_TIMEOUT_SEC = 10

# --- UI STYLES ---
def apply_styles():
    st.set_page_config(
        page_title=f"{CODENAME} {VERSION}",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;500;800&family=Inter:wght@400;900&display=swap');
        :root { --neon: #00ff88; --bg: #050505; --panel: #0d0d0d; --border: #1a1a1a; }
        html, body, [class*="css"] { background-color: var(--bg); color: #dcdcdc; font-family: 'Inter', sans-serif; }
        .main-title { font-size: 3.5rem; font-weight: 900; letter-spacing: -3px; color: white; margin-bottom: 0px; }
        .sub-title { color: var(--neon); font-family: 'JetBrains Mono'; font-size: 0.8rem; letter-spacing: 5px; text-transform: uppercase; margin-bottom: 40px; }
        .stMetric { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 20px !important; }
        .node-card {
            background: linear-gradient(160deg, #0f0f0f, #050505);
            border: 1px solid var(--border);
            border-left: 5px solid var(--neon);
            padding: 25px; border-radius: 10px; margin-bottom: 20px;
        }
        .log-terminal {
            background: #000; border: 1px solid var(--border); padding: 15px;
            font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; color: #555;
            height: 350px; overflow-y: auto; border-radius: 8px; white-space: pre-wrap;
        }
        .stButton>button {
            background: transparent; border: 1px solid var(--neon); color: var(--neon);
            font-weight: 800; border-radius: 8px; height: 3.5rem; transition: 0.4s; width: 100%;
            text-transform: uppercase; letter-spacing: 2px;
        }
        .stButton>button:hover { background: var(--neon); color: black; box-shadow: 0 0 30px var(--neon); }
        .status-pill { background: #1a1a1a; padding: 5px 12px; border-radius: 50px; font-size: 0.65rem; color: var(--neon); font-weight: bold; border: 1px solid var(--neon); }
        </style>
    """, unsafe_allow_html=True)


# --- UTILITY: Secure HMAC signature ---
def make_signature(payload: bytes) -> bytes:
    return hmac.new(SECRET_KEY, payload, hashlib.sha256).digest()

def verify_signature(payload: bytes, sig: bytes) -> bool:
    expected = make_signature(payload)
    return hmac.compare_digest(expected, sig)  # FIX: usa compare_digest (timing-safe)


# --- MASTER CORE ---
class HydraMaster:
    def __init__(self):
        self.nodes = {}
        self.events = deque(maxlen=200)
        self.lock = threading.Lock()
        self.active = True
        self._socket_ok = False

        self.ctx = zmq.Context()
        self.socket = self.ctx.socket(zmq.ROUTER)
        self.socket.setsockopt(zmq.LINGER, 0)
        try:
            self.socket.bind(f"tcp://0.0.0.0:{TCP_PORT}")
            self._socket_ok = True
            self.events.appendleft(f"[{self._ts()}] Master bound to tcp://0.0.0.0:{TCP_PORT}")
        except Exception as e:
            self.events.appendleft(f"[{self._ts()}] ERRORE BIND porta {TCP_PORT}: {e}")

    def _ts(self):
        return datetime.now().strftime('%H:%M:%S')

    def launch(self):
        if not self._socket_ok:
            return
        threading.Thread(target=self._discovery_beacon, daemon=True).start()
        threading.Thread(target=self._data_collector, daemon=True).start()
        self.events.appendleft(f"[{self._ts()}] Thread avviati: beacon UDP + collector ZMQ")

    def _discovery_beacon(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while self.active:
                try:
                    local_ip = socket.gethostbyname(socket.gethostname())
                except Exception:
                    local_ip = "127.0.0.1"
                msg = f"HYDRA_BEACON|{BT_SSID}|{local_ip}".encode()
                for dest in [('<broadcast>', UDP_PORT), ('127.0.0.1', UDP_PORT)]:
                    try:
                        s.sendto(msg, dest)
                    except Exception:
                        pass
                time.sleep(2)

    def _data_collector(self):
        while self.active:
            try:
                # poll con timeout 1s per non bloccare il thread
                if self.socket.poll(1000, zmq.POLLIN):
                    parts = self.socket.recv_multipart(flags=zmq.NOBLOCK)
                    # ROUTER frame: [identity, empty_delimiter, payload, signature]
                    if len(parts) < 4:
                        continue
                    identity, _, payload, sig = parts[0], parts[1], parts[2], parts[3]
                    # FIX: verifica firma con compare_digest (non confronto stringa hex)
                    if verify_signature(payload, sig):
                        data = json.loads(payload.decode('utf-8'))
                        self._sync_node(identity, data)
                    else:
                        self.events.appendleft(f"[{self._ts()}] FIRMA INVALIDA da nodo sconosciuto")
            except zmq.Again:
                pass
            except Exception as e:
                self.events.appendleft(f"[{self._ts()}] Errore collector: {e}")

    def _sync_node(self, identity, data):
        nid = data.get('id', 'UNKNOWN')
        with self.lock:
            is_new = nid not in self.nodes
            self.nodes[nid] = {
                'host': data.get('host', '?'),
                'ip': data.get('ip', '?'),
                'stats': data.get('s', {}),
                'history': data.get('h', []),
                'last': time.time()
            }
            if is_new:
                self.events.appendleft(
                    f"[{self._ts()}] HANDSHAKE OK: {nid} @ {data.get('ip', '?')}"
                )

    def get_active_nodes(self):
        now = time.time()
        with self.lock:
            return {
                k: v for k, v in self.nodes.items()
                if now - v['last'] < NODE_TIMEOUT_SEC
            }

    def shutdown(self):
        self.active = False
        self.socket.close()
        self.ctx.term()


# --- WORKER CORE ---
class HydraWorker:
    def __init__(self, target_ip):
        self.id = f"HYDRA-NODE-{uuid.uuid4().hex[:6].upper()}"
        self.target = target_ip
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.DEALER)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.setsockopt_string(zmq.IDENTITY, self.id)
        self.cpu_history = deque([0] * 60, maxlen=60)
        self.connected = False
        self.error_msg = ""

    def engage_link(self) -> bool:
        try:
            self.sock.connect(f"tcp://{self.target}:{TCP_PORT}")
            self.connected = True
            threading.Thread(target=self._telemetry_stream, daemon=True).start()
            return True
        except Exception as e:
            self.error_msg = str(e)
            return False

    def disconnect(self):
        self.connected = False
        try:
            self.sock.close()
            self.ctx.term()
        except Exception:
            pass

    def _telemetry_stream(self):
        while self.connected:
            try:
                stats = {
                    'cpu': psutil.cpu_percent(interval=None),
                    'ram': psutil.virtual_memory().percent,
                    'disk': psutil.disk_usage('/').percent,
                    'threads': threading.active_count()
                }
                self.cpu_history.append(stats['cpu'])
                payload_obj = {
                    'id': self.id,
                    'host': socket.gethostname(),
                    'ip': self.target,
                    's': stats,
                    'h': list(self.cpu_history)
                }
                raw_data = json.dumps(payload_obj).encode('utf-8')
                # FIX: firma con .digest() (bytes), non .hexdigest() (stringa)
                signature = make_signature(raw_data)
                self.sock.send_multipart([b"", raw_data, signature])
            except zmq.ZMQError as e:
                self.connected = False
                break
            except Exception:
                pass
            time.sleep(1)


# --- MASTER UI ---
def render_master(m: HydraMaster):
    st.markdown("<div class='main-title'>HYDRA OVERLORD</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='sub-title'>{VERSION} // PORT:{TCP_PORT}</div>", unsafe_allow_html=True)

    if not m._socket_ok:
        st.error(f"CRITICO: impossibile aprire porta {TCP_PORT}. Termina altri processi Python e riavvia.")
        return

    active_nodes = m.get_active_nodes()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NODI CONNESSI", len(active_nodes))
    c2.metric("PORTA TCP", TCP_PORT)
    c3.metric("CIFRATURA", "HMAC-256")
    c4.metric("BROADCAST", "ON")

    st.write("---")
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.subheader("TOPOLOGIA NODI")
        if not active_nodes:
            st.info("In ascolto su loopback e LAN. Avvia il worker su un altro PC o terminale.")
        for nid, d in active_nodes.items():
            st.markdown(f"""
                <div class='node-card'>
                    <div style='display:flex; justify-content:space-between; align-items:center;'>
                        <span><span class='status-pill'>ONLINE</span> <b>{d['host']}</b></span>
                        <code style='color:#555;'>{nid}</code>
                    </div>
                    <div style='display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap:20px; margin-top:20px;'>
                        <div><small>CPU</small><br><b style='color:var(--neon); font-size:1.5rem;'>{d['stats'].get('cpu', 0)}%</b></div>
                        <div><small>RAM</small><br><b style='color:var(--neon); font-size:1.5rem;'>{d['stats'].get('ram', 0)}%</b></div>
                        <div><small>THREADS</small><br><b style='color:var(--neon); font-size:1.5rem;'>{d['stats'].get('threads', 0)}</b></div>
                        <div><small>IP</small><br><b>{d['ip']}</b></div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
            if d['history']:
                fig = go.Figure(go.Scatter(
                    y=d['history'], fill='tozeroy',
                    line=dict(color='#00ff88', width=2)
                ))
                fig.update_layout(
                    height=120, margin=dict(l=0, r=0, t=0, b=0),
                    xaxis_visible=False, yaxis_visible=False,
                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
                )
                st.plotly_chart(fig, use_container_width=True, key=f"chart_{nid}")

    with col_right:
        st.subheader("LOG MASTER")
        log_content = "\n".join(list(m.events)) if m.events else "In attesa di segnali..."
        st.markdown(f"<div class='log-terminal'>{log_content}</div>", unsafe_allow_html=True)
        if st.button("AGGIORNA LOG"):
            st.rerun()

    # FIX: auto-refresh ogni 2s senza time.sleep + st.rerun() aggressivo
    st.markdown("""
        <script>setTimeout(function(){ window.location.reload(); }, 2000);</script>
    """, unsafe_allow_html=True)


# --- WORKER UI ---
def render_worker():
    st.markdown("<div class='main-title'>HYDRA WORKER</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='sub-title'>MODULO TELEMETRIA // {VERSION}</div>", unsafe_allow_html=True)

    if 'step' not in st.session_state:
        st.session_state.step = 1

    s1, s2, s3 = st.columns(3)
    s1.markdown(f"<div style='text-align:center; color:{'#00ff88' if st.session_state.step >= 1 else '#444'};'>[ 1. SIGNAL ]</div>", unsafe_allow_html=True)
    s2.markdown(f"<div style='text-align:center; color:{'#00ff88' if st.session_state.step >= 2 else '#444'};'>[ 2. AUTH ]</div>", unsafe_allow_html=True)
    s3.markdown(f"<div style='text-align:center; color:{'#00ff88' if st.session_state.step >= 3 else '#444'};'>[ 3. STREAM ]</div>", unsafe_allow_html=True)
    st.write("---")

    # STEP 1: Scoperta del Master
    if st.session_state.step == 1:
        st.subheader("Trova il Master Controller")

        # FIX: text_input FUORI dal blocco if-button (era dentro, non veniva mai renderizzato)
        method = st.radio(
            "Metodo di scoperta:",
            ["Automatico (Beacon + Localhost)", "Scansione subnet", "IP manuale"]
        )

        # Mostra input IP sempre se selezionato manuale (non dentro if-button!)
        static_ip = ""
        if method == "IP manuale":
            static_ip = st.text_input("Inserisci IP del Master:", placeholder="es. 192.168.1.100")

        if st.button("AVVIA SCOPERTA"):
            if method == "Automatico (Beacon + Localhost)":
                found = False
                # Prima prova UDP beacon
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        s.bind(('', UDP_PORT))
                        s.settimeout(3.0)
                        data, addr = s.recvfrom(1024)
                        parts = data.decode().split("|")
                        if parts[0] == "HYDRA_BEACON":
                            st.session_state.target_ip = parts[2]
                            found = True
                except Exception:
                    pass

                # Se beacon fallisce, controlla localhost
                if not found:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(0.5)
                            if s.connect_ex(('127.0.0.1', TCP_PORT)) == 0:
                                st.session_state.target_ip = "127.0.0.1"
                                found = True
                    except Exception:
                        pass

                if found:
                    st.session_state.step = 2
                    st.rerun()
                else:
                    st.error("Nessun Master trovato su localhost o LAN. Avvia prima il Master.")

            elif method == "Scansione subnet":
                found = False
                with st.spinner("Scansione subnet in corso..."):
                    try:
                        local_ip = socket.gethostbyname(socket.gethostname())
                        base_ip = ".".join(local_ip.split('.')[:-1])
                        for i in range(1, 255):
                            target = f"{base_ip}.{i}"
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                                s.settimeout(0.02)  # FIX: timeout leggermente più alto di 0.01
                                if s.connect_ex((target, TCP_PORT)) == 0:
                                    st.session_state.target_ip = target
                                    found = True
                                    break
                    except Exception as e:
                        st.error(f"Errore scansione: {e}")

                if found:
                    st.session_state.step = 2
                    st.rerun()
                else:
                    st.error("Scansione completata: nessun Master trovato.")

            elif method == "IP manuale":
                # FIX: usa la variabile static_ip definita FUORI dall'if-button
                if static_ip.strip():
                    st.session_state.target_ip = static_ip.strip()
                    st.session_state.step = 2
                    st.rerun()
                else:
                    st.error("Inserisci un indirizzo IP valido.")

    # STEP 2: Autenticazione
    elif st.session_state.step == 2:
        st.success(f"Master trovato: {st.session_state.target_ip}")
        key_in = st.text_input("Chiave di accesso:", type="password")

        col_auth, col_back = st.columns([3, 1])
        with col_auth:
            if st.button("AUTORIZZA TUNNEL"):
                if key_in.encode() == SECRET_KEY:
                    worker = HydraWorker(st.session_state.target_ip)
                    if worker.engage_link():
                        st.session_state.worker = worker
                        st.session_state.step = 3
                        st.rerun()
                    else:
                        st.error(f"Connessione ZMQ fallita: {worker.error_msg}")
                else:
                    st.error("Accesso negato: chiave di sicurezza non valida.")
        with col_back:
            if st.button("INDIETRO"):
                st.session_state.step = 1
                st.rerun()

    # STEP 3: Streaming attivo
    elif st.session_state.step == 3:
        w: HydraWorker = st.session_state.worker

        if not w.connected:
            st.error("Connessione persa. Riconnetti.")
            if st.button("RICONNETTI"):
                st.session_state.step = 1
                if 'worker' in st.session_state:
                    del st.session_state.worker
                st.rerun()
            return

        st.markdown(
            f"<div class='node-card'>LINK SICURO ATTIVO → {st.session_state.target_ip}</div>",
            unsafe_allow_html=True
        )
        st.metric("ID NODO", w.id)

        fig = go.Figure(go.Scatter(
            y=list(w.cpu_history), fill='tozeroy',
            line=dict(color='#00ff88', width=3)
        ))
        fig.update_layout(
            height=450, paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)', margin=dict(l=0, r=0, t=0, b=0)
        )
        st.plotly_chart(fig, use_container_width=True)

        if st.button("TERMINA CONNESSIONE"):
            w.disconnect()
            st.session_state.step = 1
            del st.session_state.worker
            st.rerun()

        # FIX: refresh ogni 2s tramite JS invece di time.sleep + st.rerun()
        st.markdown(
            "<script>setTimeout(function(){ window.location.reload(); }, 2000);</script>",
            unsafe_allow_html=True
        )


# --- ENTRY POINT ---
def main():
    apply_styles()
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "master"

    if mode == "master":
        if 'master' not in st.session_state:
            st.session_state.master = HydraMaster()
            st.session_state.master.launch()
        render_master(st.session_state.master)

    elif mode == "worker":
        render_worker()

    else:
        st.error(f"Modalità non valida: '{mode}'. Usa 'master' o 'worker'.")


if __name__ == "__main__":
    main()
