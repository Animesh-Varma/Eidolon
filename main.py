import sys
import time
import threading
import queue
import logging

# Import our cross-platform controller factory
from controllers import get_os_controller

# Suppress underlying audio warnings for a clean TUI
logging.getLogger().setLevel(logging.ERROR)


class LocalAIEngine:
    def __init__(self, bt_controller, ui_queue: queue.Queue):
        self.bt_controller = bt_controller
        self.ui_queue = ui_queue

        self.text_input_queue = queue.Queue()  # For outgoing typed messages
        self.running = True

        # TODO: Initialize Vosk/Whisper for STT, Pyttsx3 for TTS here

    def process_incoming_audio(self):
        """Continuously reads from Bluetooth and processes STT."""
        while self.running:
            try:
                # Ask OS controller for chunk of raw Bluetooth audio
                audio_chunk = self.bt_controller.read_audio(1024)

                # Mock STT detection logic
                if b'MOCK_SPEECH' in audio_chunk:
                    self.ui_queue.put("[Caller]: Hello, I need to order food.")
            except Exception as e:
                self.ui_queue.put(f"[Error] STT Pipeline: {e}")
                time.sleep(1)

    def process_outgoing_text(self):
        """Receives text from TUI, generates TTS, sends to Bluetooth."""
        while self.running:
            try:
                text = self.text_input_queue.get(timeout=1)
                self.ui_queue.put(f"[Eidolon TTS]: Processing '{text}'...")

                # Simulate TTS generation delay
                time.sleep(1)

                # Write "audio bytes" back to the OS controller
                self.bt_controller.write_audio(b'ENCODED_TTS_AUDIO_BYTES')
                self.ui_queue.put("[Eidolon TTS]: Audio sent over Bluetooth.")
            except queue.Empty:
                continue


class TUI:
    def __init__(self, ai_engine: LocalAIEngine, ui_queue: queue.Queue):
        self.ai_engine = ai_engine
        self.ui_queue = ui_queue
        self.running = True

    def display_loop(self):
        while self.running:
            try:
                msg = self.ui_queue.get(timeout=0.1)
                sys.stdout.write('\r\x1b[K')  # Clear current line
                print(msg)
                sys.stdout.write('Type response > ')
                sys.stdout.flush()
            except queue.Empty:
                continue

    def input_loop(self):
        while self.running:
            try:
                sys.stdout.write('Type response > ')
                sys.stdout.flush()
                user_text = input()
                if user_text.lower() in ['exit', 'quit']:
                    self.running = False
                    break
                if user_text.strip():
                    self.ai_engine.text_input_queue.put(user_text)
            except EOFError:
                break


class EidolonOrchestrator:
    def __init__(self):
        self.ui_queue = queue.Queue()

        # 1. Initialize OS Specific Bluetooth Controller dynamically
        self.ui_queue.put(f"[System]: Detected OS -> {sys.platform}")
        self.bt_controller = get_os_controller()

        # 2. Initialize AI Pipeline and TUI
        self.ai = LocalAIEngine(self.bt_controller, self.ui_queue)
        self.tui = TUI(self.ai, self.ui_queue)

    def run(self):
        self.ui_queue.put("\n=== EIDOLON HFP POC INITIALIZING ===")

        # Start OS Bluetooth Server
        success = self.bt_controller.start_hfp_server()
        if success:
            self.ui_queue.put(
                f"[Bluetooth]: {self.bt_controller.__class__.__name__} HFP Server active. Awaiting connection...")
        else:
            self.ui_queue.put("[Bluetooth]: Failed to start HFP server.")
            return

        # Start background threads
        threading.Thread(target=self.ai.process_incoming_audio, daemon=True).start()
        threading.Thread(target=self.ai.process_outgoing_text, daemon=True).start()
        threading.Thread(target=self.tui.display_loop, daemon=True).start()

        # Run UI on main thread (Blocks)
        self.tui.input_loop()

        # Cleanup process
        self.ui_queue.put("Shutting down Eidolon...")
        self.ai.running = False
        self.tui.running = False
        self.bt_controller.stop_server()


if __name__ == "__main__":
    app = EidolonOrchestrator()
    app.run()