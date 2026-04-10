import queue
from abc import ABC, abstractmethod


class BaseController(ABC):
    """
    Abstract Base Class for OS-Specific Bluetooth HFP implementations.
    Ensures that all OS controllers provide the exact same interface to main.py.
    """
    def __init__(self):
        self.event_queue = queue.Queue()

    @abstractmethod
    def start_hfp_server(self) -> bool:
        pass

    @abstractmethod
    def read_audio(self, chunk_size: int) -> bytes:
        pass

    @abstractmethod
    def write_audio(self, audio_bytes: bytes):
        pass

    @abstractmethod
    def stop_server(self):
        pass

    @abstractmethod
    def send_at_command(self, cmd_bytes: bytes):
        """Sends an AT command over the RFCOMM socket."""
        pass

    @abstractmethod
    def open_sco_channel(self):
        """Requests the baseband to open the raw SCO Audio channel."""
        pass