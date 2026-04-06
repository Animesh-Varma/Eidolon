from abc import ABC, abstractmethod


class BaseController(ABC):
    """
    Abstract Base Class for OS-Specific Bluetooth HFP implementations.
    Ensures that all OS controllers provide the exact same interface to main.py.
    """

    @abstractmethod
    def start_hfp_server(self) -> bool:
        """Starts the HFP server and SDP spoofing without changing adapter name."""
        pass

    @abstractmethod
    def read_audio(self, chunk_size: int) -> bytes:
        """Reads raw audio bytes from the SCO/eSCO socket."""
        pass

    @abstractmethod
    def write_audio(self, audio_bytes: bytes):
        """Writes raw audio bytes to the SCO/eSCO socket."""
        pass

    @abstractmethod
    def stop_server(self):
        """Safely closes sockets and cleans up the Bluetooth connection."""
        pass