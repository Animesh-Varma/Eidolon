import time
from .base_controller import BaseController

class WindowsController(BaseController):
    def __init__(self):
        self.connected = False

    def start_hfp_server(self) -> bool:
        # TODO: Implement Winsock RFCOMM and SCO socket connections here.
        time.sleep(1)
        self.connected = True
        return True

    def read_audio(self, chunk_size: int) -> bytes:
        time.sleep(0.1)
        return b'\x00' * chunk_size

    def write_audio(self, audio_bytes: bytes):
        pass

    def stop_server(self):
        self.connected = False