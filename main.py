# ==============================================================================
# [IDE EXECUTION WARNING]:
# If you run this script using PyCharm's standard "Run" button, the Curses TUI
# will crash with `_curses.error: cbreak() returned ERR`. Curses requires a real TTY.
#
# HOW TO FIX:
# 1. Use the PyCharm Terminal tab (bottom of screen) and run: `python main.py`
# ==============================================================================

import sys
import time
import threading
import queue
import logging
import subprocess
import IOBluetooth
import speech_recognition as sr
import numpy as np
import re
import curses
import os

if 'TERM' not in os.environ:
    os.environ['TERM'] = 'xterm-256color'

from controllers import get_os_controller

logging.getLogger().setLevel(logging.ERROR)


class StdoutRedirector:
    """Thread-safe redirector to pipe controller print() logs cleanly into the Curses UI window."""

    def __init__(self, ui_queue):
        self.ui_queue = ui_queue
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        if '\n' in self.buffer:
            lines = self.buffer.split('\n')
            for line in lines[:-1]:
                clean = self.ansi_escape.sub('', line).strip()
                if clean and "Type response" not in clean:
                    self.ui_queue.put(("log", clean))
            self.buffer = lines[-1]

    def flush(self):
        pass


class LocalAIEngine:
    def __init__(self, bt_controller, ui_queue: queue.Queue):
        self.bt_controller = bt_controller
        self.ui_queue = ui_queue
        self.text_input_queue = queue.Queue()
        self.running = True
        self.in_call = False

        # Audio Pipeline
        self.recognizer = sr.Recognizer()
        self.speech_buffer = bytearray()
        self.stt_active = False
        self.tts_active = False
        self.silence_chunks = 0
        self.RMS_THRESHOLD = 300

        # Tier 1: Quick & Dirty Live STT
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
            import os
            SetLogLevel(-1)
            if os.path.exists("model"):
                self.vosk_model = Model("model")
                self.vosk_rec = KaldiRecognizer(self.vosk_model, 16000)
            else:
                self.ui_queue.put(("log", "[Warning] Vosk 'model' folder missing. Live STT disabled."))
                self.vosk_rec = None
        except ImportError:
            self.ui_queue.put(("log", "[Warning] 'vosk' module missing. Live STT disabled."))
            self.vosk_rec = None

    def process_bluetooth_events(self):
        while self.running:
            try:
                event = self.bt_controller.event_queue.get(timeout=0.5)
                if event == "RING":
                    self.ui_queue.put(("log", "--- INCOMING CALL: Type 'answer' to pick up ---"))
                elif event == "CALL_ACTIVE":
                    if not self.in_call:
                        self.in_call = True
                        self.ui_queue.put(("log", "--- CALL ANSWERED ---"))
                        if sys.platform == 'darwin':
                            self.ui_queue.put(("log", "[!] ACTION REQUIRED: Switch phone to SPEAKERPHONE [!]"))
                        self.bt_controller.open_sco_channel()
                elif event == "CALL_ENDED":
                    self.in_call = False
                    self.ui_queue.put(("log", "--- CALL ENDED ---"))
            except queue.Empty:
                continue

    def process_incoming_audio(self):
        import json
        while self.running:
            if self.in_call:
                if self.tts_active:
                    self.speech_buffer.clear()
                    self.stt_active = False
                    time.sleep(0.1)
                    continue

                audio_chunk = self.bt_controller.read_audio(2048)
                if audio_chunk:
                    audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
                    rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))

                    if rms > self.RMS_THRESHOLD:
                        self.stt_active = True
                        self.silence_chunks = 0
                        self.speech_buffer.extend(audio_chunk)

                        # --- TIER 1: LIVE STT (Instant Feedback) ---
                        if self.vosk_rec:
                            if self.vosk_rec.AcceptWaveform(audio_chunk):
                                self.ui_queue.put(("live", ""))
                            else:
                                partial = json.loads(self.vosk_rec.PartialResult()).get("partial", "")
                                if partial:
                                    self.ui_queue.put(("live", partial))

                    elif self.stt_active:
                        self.silence_chunks += 1
                        self.speech_buffer.extend(audio_chunk)

                        if self.silence_chunks > 8 or len(self.speech_buffer) > 256000:
                            chunk_to_process = bytes(self.speech_buffer)

                            self.speech_buffer.clear()
                            self.stt_active = False
                            self.silence_chunks = 0
                            self.ui_queue.put(("live", ""))  # Clear live UI

                            if self.vosk_rec:
                                self.vosk_rec.Reset()

                            # --- TIER 2: ACCURATE STT (Background Correction) ---
                            if len(chunk_to_process) > 16000:
                                threading.Thread(target=self._async_stt, args=(chunk_to_process,), daemon=True).start()
            else:
                time.sleep(0.1)

    def _async_stt(self, raw_bytes):
        audio_data = sr.AudioData(raw_bytes, 16000, 2)
        try:
            # noinspection PyUnresolvedReferences
            text = self.recognizer.recognize_google(audio_data)
            self.ui_queue.put(("chat", f"[Caller]: {text}"))
        except sr.UnknownValueError:
            pass
        except Exception as e:
            self.ui_queue.put(("chat", f"[STT Error]: {e}"))

    def process_outgoing_text(self):
        while self.running:
            try:
                text = self.text_input_queue.get(timeout=1).strip()
                if text.lower() == 'answer':
                    self.bt_controller.send_at_command(b"ATA\r")
                    continue
                elif text.lower() in ['decline', 'reject', 'hangup']:
                    self.bt_controller.send_at_command(b"AT+CHUP\r")
                    self.in_call = False
                    continue
                if not self.in_call:
                    self.ui_queue.put(("log", "Error: Not in an active call."))
                    continue

                self.ui_queue.put(("chat", f"[Eidolon TTS]: {text}"))
                self.tts_active = True

                if sys.platform == 'darwin':
                    subprocess.run(['say', text])
                else:
                    self.ui_queue.put(("log", "[System]: Digital TTS injection executed."))

                self.tts_active = False
            except queue.Empty:
                continue


class CursesTUI:
    def __init__(self, ai_engine, ui_queue):
        self.ai = ai_engine
        self.ui_queue = ui_queue
        self.logs = []
        self.chats = []
        self.live_stt = ""
        self.current_input = ""

    def display_loop(self, stdscr):
        curses.curs_set(1)
        stdscr.nodelay(True)
        stdscr.timeout(100)

        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)  # Logs
        curses.init_pair(2, curses.COLOR_GREEN, -1)  # Caller Chat
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # Eidolon Chat
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)  # Live STT

        while self.ai.running:
            # 1. Process Queue
            try:
                while True:
                    msg_type, msg = self.ui_queue.get_nowait()
                    if msg_type == "log":
                        self.logs.append(msg)
                        if len(self.logs) > 100: self.logs.pop(0)
                    elif msg_type == "chat":
                        self.chats.append(msg)
                        if len(self.chats) > 100: self.chats.pop(0)
                    elif msg_type == "live":
                        self.live_stt = msg
            except queue.Empty:
                pass

            # 2. Process Input
            try:
                c = stdscr.getch()
                if c != -1:
                    if c in [10, 13]:  # Enter
                        if self.current_input.lower() in ['exit', 'quit']:
                            self.ai.running = False
                            break
                        if self.current_input.strip():
                            self.ai.text_input_queue.put(self.current_input)
                        self.current_input = ""
                    elif c in [127, 8, 263]:  # Backspace
                        self.current_input = self.current_input[:-1]
                    elif 32 <= c <= 126:
                        self.current_input += chr(c)
            except Exception:
                pass

            # 3. Draw UI
            h, w = stdscr.getmaxyx()
            stdscr.erase()

            # --- TOP WINDOW (Logs) ---
            log_h = h // 2

            curses.init_pair(5, curses.COLOR_RED, -1)  # Errors / Disconnections
            curses.init_pair(6, curses.COLOR_YELLOW, -1)  # Phone Replies

            for i, log in enumerate(self.logs[-(log_h - 2):]):
                color = curses.color_pair(1)

                if "[Phone Replies]" in log:
                    color = curses.color_pair(6)
                elif "[DEBUG]" in log or "gains OPEN" in log or "[State]" in log:
                    color = curses.color_pair(2)  # Green
                elif "[DELEGATE]" in log or "Error" in log or "rejected" in log or "silent" in log:
                    color = curses.color_pair(5)  # Red
                elif "Hunting" in log or "SDP" in log:
                    color = curses.color_pair(4)  # Magenta

                try:
                    stdscr.addnstr(1 + i, 2, log, w - 4, color)
                except curses.error:
                    pass

            stdscr.addstr(0, 2, " SYSTEM LOGS ", curses.A_BOLD)
            stdscr.hline(log_h, 0, curses.ACS_HLINE, w)

            # --- MIDDLE WINDOW (Chat/TTS) ---
            chat_h = h - log_h - 3
            for i, chat in enumerate(self.chats[-(chat_h - 2):]):
                color = curses.color_pair(2) if "[Caller]" in chat else curses.color_pair(3)
                try:
                    stdscr.addnstr(log_h + 1 + i, 2, chat, w - 4, color)
                except curses.error:
                    pass

            # Draw Live Sub-100ms Transcription
            if self.live_stt:
                try:
                    stdscr.addnstr(log_h + chat_h - 1, 2, f"Live: {self.live_stt}...", w - 4,
                                   curses.color_pair(4) | curses.A_DIM)
                except curses.error:
                    pass

            stdscr.addstr(log_h, 2, " TRANSCRIPTION & TTS ", curses.A_BOLD)
            stdscr.hline(h - 2, 0, curses.ACS_HLINE, w)

            # --- BOTTOM WINDOW (Dedicated Input Bar) ---
            stdscr.addstr(h - 1, 0, "Type response > ")
            stdscr.addstr(h - 1, 16, self.current_input)

            stdscr.refresh()


class EidolonOrchestrator:
    def __init__(self):
        self.ui_queue = queue.Queue()
        sys.stdout = StdoutRedirector(self.ui_queue)  # Intercept controller prints!

        self.bt_controller = get_os_controller()
        self.ai = LocalAIEngine(self.bt_controller, self.ui_queue)
        self.tui = CursesTUI(self.ai, self.ui_queue)

    def run(self):
        self.ui_queue.put(("log", f"Detected OS -> {sys.platform}"))
        success = self.bt_controller.start_hfp_server()
        if not success:
            return

        threading.Thread(target=self.ai.process_incoming_audio, daemon=True).start()
        threading.Thread(target=self.ai.process_outgoing_text, daemon=True).start()
        threading.Thread(target=self.ai.process_bluetooth_events, daemon=True).start()

        # Run Curses UI in a background thread to prevent blocking macOS Event Loop
        ui_thread = threading.Thread(target=lambda: curses.wrapper(self.tui.display_loop), daemon=True)
        ui_thread.start()

        while self.ai.running:
            if sys.platform == 'darwin':
                import Foundation
                Foundation.NSRunLoop.mainRunLoop().runMode_beforeDate_(
                    Foundation.NSDefaultRunLoopMode,
                    Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1)
                )
            else:
                time.sleep(0.1)

        self.bt_controller.stop_server()


if __name__ == "__main__":
    app = EidolonOrchestrator()
    app.run()