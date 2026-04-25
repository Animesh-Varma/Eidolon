import contextlib
import re
import socket
import subprocess
import threading
import time
import select
from typing import List, Tuple, Optional

import numpy as np

from .base_controller import BaseController

DEBUG = True


class LinuxController(BaseController):
    """
    Direct-to-Kernel Linux Bluetooth Hands-Free Controller.
    Bypasses DBus and standard audio servers (PulseAudio/PipeWire) to interact
    directly with hardware basebands via AF_BLUETOOTH sockets.
    """

    def __init__(self):
        super().__init__()
        self.connected = False
        self.channel_is_open = False
        self.in_call = False

        self.rfcomm_sock: Optional[socket.socket] = None
        self.sco_sock: Optional[socket.socket] = None
        self.sco_conn: Optional[socket.socket] = None

        self.local_mac = self._get_local_mac()
        self.at_state = 0
        self.target_mac: Optional[str] = None

    def _debug_print(self, msg: str) -> None:
        if DEBUG:
            print(f"[DEBUG - LinuxController]: {msg}")

    def _extract_mac(self, text: str) -> Optional[str]:
        """Safely extracts a 17-char MAC, stripping away any ANSI color codes."""
        match = re.search(r"([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})", text)
        return match.group(0).upper() if match else None

    def _get_local_mac(self) -> Optional[str]:
        try:
            out = subprocess.check_output(['bluetoothctl', 'show']).decode()
            for line in out.splitlines():
                if "Controller" in line:
                    return self._extract_mac(line)
        except Exception:
            pass
        return None

    def _get_paired_devices(self) -> List[Tuple[str, str]]:
        devices = []
        try:
            out = subprocess.check_output(['bluetoothctl', 'devices']).decode()
            for line in out.splitlines():
                if line.startswith("Device "):
                    mac = self._extract_mac(line)
                    parts = line.split(" ", 2)
                    name = parts[2] if len(parts) >= 3 else "Unknown"
                    if mac:
                        devices.append((mac, name))
        except Exception as e:
            self._debug_print(f"Failed to fetch devices: {e}")
        return devices

    def start_hfp_server(self) -> bool:
        if not self.local_mac:
            return False
        self.connected = True
        threading.Thread(target=self._bluetooth_worker, daemon=True).start()
        return True

    def _bluetooth_worker(self) -> None:
        self._debug_print("Hunting for paired phones via Native RFCOMM Sockets...")

        while self.connected and not self.channel_is_open:
            devices = self._get_paired_devices()

            for mac, name in devices:
                if self.channel_is_open:
                    break

                for channel in [1, 2, 3, 4, 10, 11, 12, 13, 14]:
                    if self.channel_is_open:
                        break

                    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
                    sock.settimeout(2.0)

                    try:
                        sock.connect((mac, channel))
                        sock.sendall(b"AT+BRSF=115\r")
                        response = sock.recv(1024)

                        if b"+BRSF" in response or (b"OK" in response and b"ERROR" not in response):
                            self._debug_print(
                                f"\033[92mPort {channel} is the true HFP Audio Gateway for {name}!\033[0m")
                            sock.settimeout(None)
                            self.rfcomm_sock = sock
                            self.channel_is_open = True
                            self.target_mac = mac
                            self.at_state = 1
                            threading.Thread(target=self._rfcomm_reader, daemon=True).start()
                            self._process_raw_buffer(response)
                            break
                        else:
                            sock.close()
                    except socket.timeout:
                        sock.close()
                    except socket.error:
                        sock.close()

            if not self.channel_is_open:
                time.sleep(3.0)

    def _rfcomm_reader(self) -> None:
        while self.connected and self.channel_is_open and self.rfcomm_sock:
            try:
                data = self.rfcomm_sock.recv(1024)
                if not data:
                    break
                self._process_raw_buffer(data)
            except Exception:
                break
        self.channel_is_open = False

    def _process_raw_buffer(self, data_bytes: bytes) -> None:
        for line in data_bytes.split(b'\r'):
            clean_line = line.strip(b'\n').decode('utf-8', errors='ignore').strip()
            if clean_line:
                self._handle_at_response(clean_line)

    def _handle_at_response(self, data_str: str) -> None:
        if data_str not in ["OK", "ERROR"]:
            self._debug_print(f"[Phone Replies]: {data_str}")

        if "OK" in data_str:
            if self.at_state == 1:
                self.send_at_command(b"AT+BAC=1\r")
                self.at_state = 2
            elif self.at_state == 2:
                self.send_at_command(b"AT+CIND=?\r")
                self.at_state = 3
            elif self.at_state == 3:
                self.send_at_command(b"AT+CIND?\r")
                self.at_state = 4
            elif self.at_state == 4:
                self.send_at_command(b"AT+CMER=3,0,0,1\r")
                self.at_state = 5
            elif self.at_state == 5:
                self.send_at_command(b"AT+CHLD=?\r")
                self.at_state = 6
            elif self.at_state == 6:
                self._debug_print("\033[92m[State] SLC Established! Linux is now an active HFP Unit.\033[0m")
                self.at_state = 7

        if "RING" in data_str or "+CIEV: 2,1" in data_str:
            self.event_queue.put("RING")
        elif "+CIEV: 1,1" in data_str:
            self.in_call = True
            self.event_queue.put("CALL_ACTIVE")
        elif "+CIEV: 1,0" in data_str:
            self.in_call = False
            self.event_queue.put("CALL_ENDED")
            self._close_sco()

        if "+BCS: 1" in data_str:
            self.send_at_command(b"AT+BCS=1\r")
        elif "+BCS: 2" in data_str:
            self.send_at_command(b"AT+BCS=2\r")

    def send_at_command(self, cmd_bytes: bytes) -> None:
        if self.rfcomm_sock and self.channel_is_open:
            with contextlib.suppress(Exception):
                self.rfcomm_sock.sendall(cmd_bytes)

    def open_sco_channel(self) -> None:
        if self.sco_conn:
            return
        self._debug_print("\033[95m[Kernel Audio] Engaging SCO Link...\033[0m")

        def _sco_listener():
            try:
                self.sco_sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_SCO)
                self.sco_sock.connect(self.target_mac)
                self.sco_conn = self.sco_sock
                self.sco_conn.settimeout(1.0)
                self._debug_print("\033[92m[Kernel Audio] SCO Stream CONNECTED to Baseband!\033[0m")
            except Exception as e:
                self._debug_print(f"Active connect failed ({e}). Falling back to listen mode...")
                try:
                    self.sco_sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_SCO)
                    self.sco_sock.bind(self.local_mac)
                    self.sco_sock.listen(1)
                    self.sco_sock.settimeout(5.0)
                    self.sco_conn, info = self.sco_sock.accept()
                    self.sco_conn.settimeout(1.0)
                    self._debug_print(f"\033[92m[Kernel Audio] SCO Stream ACCEPTED from {info}!\033[0m")
                except Exception as e2:
                    self._debug_print(f"Failed to open SCO channel entirely: {e2}")
                    self._close_sco()

        threading.Thread(target=_sco_listener, daemon=True).start()

    def read_audio(self, chunk_size: int) -> bytes:
        if not self.in_call or not self.sco_conn:
            time.sleep(0.05)
            return b''

        buf = bytearray()
        try:
            ready, _, _ = select.select([self.sco_conn], [], [], 0.05)
            if ready:
                while True:
                    packet = self.sco_conn.recv(4096)
                    if packet:
                        buf.extend(packet)

                    r, _, _ = select.select([self.sco_conn], [], [], 0.0)
                    if not r:
                        break
        except Exception:
            pass

        even_len = len(buf) - (len(buf) % 2)
        return bytes(buf[:even_len])

    def write_audio(self, audio_bytes: bytes, sample_rate: int = 8000) -> None:
        if not self.sco_conn or not self.in_call:
            return

        chunk_size = 48  # Kernel SCO MTU Standard

        try:
            for i in range(0, len(audio_bytes), chunk_size):
                if not self.in_call or not self.sco_conn:
                    break

                chunk = audio_bytes[i:i + chunk_size]

                if len(chunk) < chunk_size:
                    chunk += b'\x00' * (chunk_size - len(chunk))

                self.sco_conn.send(chunk)

        except Exception as e:
            self._debug_print(f"Failed to write audio: {e}")

    def _close_sco(self) -> None:
        if self.sco_conn:
            with contextlib.suppress(Exception):
                self.sco_conn.shutdown(socket.SHUT_RDWR)
                self.sco_conn.close()
            self.sco_conn = None

        if self.sco_sock:
            with contextlib.suppress(Exception):
                self.sco_sock.shutdown(socket.SHUT_RDWR)
                self.sco_sock.close()
            self.sco_sock = None

    def stop_server(self) -> None:
        self.connected = False
        self.channel_is_open = False
        self._close_sco()
        if self.rfcomm_sock:
            with contextlib.suppress(Exception):
                self.rfcomm_sock.shutdown(socket.SHUT_RDWR)
                self.rfcomm_sock.close()
            self.rfcomm_sock = None