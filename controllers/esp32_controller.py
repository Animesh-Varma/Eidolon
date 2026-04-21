import socket
import threading
import time
import numpy as np
from .base_controller import BaseController


class ESP32Controller(BaseController):
    def __init__(self, host='0.0.0.0', audio_port=8000, ctrl_port=8001):
        super().__init__()
        self.host = host
        self.audio_port = audio_port
        self.ctrl_port = ctrl_port
        self.running = False

        self.audio_sock = None
        self.ctrl_sock = None
        self.audio_conn = None
        self.ctrl_conn = None

        self.audio_buffer = bytearray()

    def start_hfp_server(self) -> bool:
        self.running = True
        self.ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ctrl_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ctrl_sock.bind((self.host, self.ctrl_port))
        self.ctrl_sock.listen(5)

        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.audio_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.audio_sock.bind((self.host, self.audio_port))
        self.audio_sock.listen(5)

        threading.Thread(target=self._connection_loop, daemon=True).start()
        return True

    def _connection_loop(self):
        print("\n\033[93m[*] ESP32 Bridge server running. Waiting for ESP32 to connect via Wi-Fi...\033[0m")
        while self.running:
            try:
                self.ctrl_conn, addr = self.ctrl_sock.accept()
                self.audio_conn, addr2 = self.audio_sock.accept()

                self.audio_conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.ctrl_conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                print(f"\n\033[92m[✔] ESP32 Connected! Routing Audio -> {addr2[0]}\033[0m")
                self.audio_buffer.clear()

                self._ctrl_reader()
            except Exception:
                time.sleep(1)

    def _ctrl_reader(self):
        while self.running and self.ctrl_conn:
            try:
                data = self.ctrl_conn.recv(1024)
                if not data: break
                for line in data.decode('utf-8').split('\n'):
                    event = line.strip()
                    if event in["RING", "CALL_ACTIVE", "CALL_ENDED"]:
                        self.event_queue.put(event)
            except Exception:
                break

        print("\n\033[91m[!] ESP32 Disconnected. Waiting for reconnect...\033[0m")
        if self.ctrl_conn: self.ctrl_conn.close(); self.ctrl_conn = None
        if self.audio_conn: self.audio_conn.close(); self.audio_conn = None

    def read_audio(self, chunk_size: int) -> bytes:
        if not self.audio_conn:
            time.sleep(0.05)
            return b''

        try:
            self.audio_conn.settimeout(0.01)
            while True:
                data = self.audio_conn.recv(8192)
                if not data: break
                self.audio_buffer.extend(data)
        except (socket.timeout, socket.error):
            pass

        if len(self.audio_buffer) >= chunk_size:
            even_len = len(self.audio_buffer) - (len(self.audio_buffer) % 2)
            raw_data = bytes(self.audio_buffer[:even_len])
            self.audio_buffer = self.audio_buffer[even_len:]
            return raw_data

        return b''

    def write_audio(self, audio_bytes: bytes, sample_rate: int = 16000):
        if not self.audio_conn: return

        # 2048 is the sweet spot for 16kHz audio over Wi-Fi
        chunk_size = 2048
        bytes_per_sec = sample_rate * 2
        sleep_time = chunk_size / bytes_per_sec

        for i in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[i:i + chunk_size]
            try:
                self.audio_conn.sendall(chunk)
                time.sleep(sleep_time * 0.95)
            except socket.error:
                break

    def send_at_command(self, cmd_bytes: bytes):
        if self.ctrl_conn:
            try:
                self.ctrl_conn.sendall(cmd_bytes)
            except:
                pass

    def open_sco_channel(self):
        if self.ctrl_conn:
            try:
                self.ctrl_conn.sendall(b"OPEN_AUDIO\n")
            except Exception:
                pass

    def stop_server(self):
        self.running = False
        if self.ctrl_conn: self.ctrl_conn.close()
        if self.audio_conn: self.audio_conn.close()
        if self.ctrl_sock: self.ctrl_sock.close()
        if self.audio_sock: self.audio_sock.close()