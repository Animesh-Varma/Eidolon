import time
from .base_controller import BaseController

class LinuxController(BaseController):
    def __init__(self):
        self.connected = False
        # Here you would typically import dbus and setup BlueZ sockets

    def start_hfp_server(self) -> bool:
        # TODO: Implement DBus / AF_BLUETOOTH AF_SCO sockets here.
        # Ensure we spoof Class of Device (CoD) without renaming adapter.
        time.sleep(1) # Simulating handshake
        self.connected = True
        return True

    def read_audio(self, chunk_size: int) -> bytes:
        # Return empty buffer to simulate silence and keep STT alive
        time.sleep(0.1)
        return b'\x00' * chunk_size

    def write_audio(self, audio_bytes: bytes):
        # Write bytes directly to SCO socket
        pass

    def stop_server(self):
        self.connected = False