# ==============================================================================
# [IDE EXECUTION WARNING]:
# Curses requires a real TTY. Run via standard terminal: `python main.py`
# On Linux, you MUST run this with sudo to access AF_BLUETOOTH sockets:
#   $ sudo /path/to/.venv/bin/python main.py
# ==============================================================================

import asyncio
import curses
import logging
import os
import re
import subprocess
import sys
import threading
import time
import queue

import numpy as np
import edge_tts

if 'TERM' not in os.environ:
    os.environ['TERM'] = 'xterm-256color'

from controllers import get_os_controller

logging.getLogger().setLevel(logging.ERROR)


class StdoutRedirector:
    """Thread-safe redirector to pipe print() logs cleanly into the Curses UI."""

    def __init__(self, ui_queue: queue.Queue):
        self.ui_queue = ui_queue
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        self.buffer = ""

    def write(self, text: str):
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
    """Manages STT transcription, TTS generation, and Bluetooth call state."""

    def __init__(self, bt_controller, ui_queue: queue.Queue):
        self.bt_controller = bt_controller
        self.ui_queue = ui_queue
        self.text_input_queue = queue.Queue()
        self.running = True
        self.in_call = False

        # Audio Pipeline
        self.speech_buffer = bytearray()
        self.stt_active = False
        self.tts_active = False
        self.silence_chunks = 0
        self.RMS_THRESHOLD = 300

        # Production-Grade STT (Faster-Whisper)
        try:
            from faster_whisper import WhisperModel
            self.ui_queue.put(("log", "[*] Loading Whisper AI Model (base.en)..."))
            # 'base.en' is extremely fast and accurate on CPU.
            # (Use 'small.en' if you have a dedicated GPU)
            self.stt_model = WhisperModel("base.en", device="auto", compute_type="int8")
            self.ui_queue.put(("log", "[✔] Whisper AI Online!"))
        except ImportError:
            self.stt_model = None
            self.ui_queue.put(("log", "[Error] 'faster-whisper' missing! Run: pip install faster-whisper"))
        except Exception as e:
            self.stt_model = None
            self.ui_queue.put(("log", f"[STT Init Error]: {e}"))

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

                        time.sleep(0.5)
                        self.bt_controller.open_sco_channel()

                elif event == "CALL_ENDED":
                    self.in_call = False
                    self.ui_queue.put(("log", "--- CALL ENDED ---"))
            except queue.Empty:
                continue

    def process_incoming_audio(self):
        while self.running:
            if not self.in_call:
                time.sleep(0.1)
                continue

            # Must continuously drain the socket to prevent TCP Deadlocks!
            audio_chunk = self.bt_controller.read_audio(2048)

            if self.tts_active:
                self.speech_buffer.clear()
                self.stt_active = False
                continue

            if audio_chunk:
                audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
                rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))

                if rms > self.RMS_THRESHOLD:
                    if not self.stt_active:
                        self.stt_active = True
                        self.ui_queue.put(("live", "Listening..."))

                    self.silence_chunks = 0
                    self.speech_buffer.extend(audio_chunk)

                elif self.stt_active:
                    self.silence_chunks += 1
                    self.speech_buffer.extend(audio_chunk)

                    # Trigger STT after ~0.5 seconds of silence
                    if self.silence_chunks > 8 or len(self.speech_buffer) > 256000:
                        chunk_to_process = bytes(self.speech_buffer)
                        self.speech_buffer.clear()
                        self.stt_active = False
                        self.silence_chunks = 0
                        self.ui_queue.put(("live", "Transcribing..."))

                        if len(chunk_to_process) > 16000:  # Ensure we have at least 0.5s of audio
                            threading.Thread(target=self._async_stt, args=(chunk_to_process,), daemon=True).start()
                        else:
                            self.ui_queue.put(("live", ""))

    def _async_stt(self, raw_bytes: bytes):
        if not self.stt_model:
            self.ui_queue.put(("chat", "[STT Error]: Whisper AI is not loaded."))
            self.ui_queue.put(("live", ""))
            return

        try:
            # Whisper natively requires a float32 NumPy array ranging from -1.0 to 1.0
            audio_np = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            # Transcribe with AI Voice Activity Detection (VAD) to filter out Bluetooth static
            segments, info = self.stt_model.transcribe(
                audio_np,
                beam_size=5,
                language="en",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500)
            )

            text = "".join([segment.text for segment in segments]).strip()

            if text:
                self.ui_queue.put(("chat", f"[Caller]: {text}"))

        except Exception as e:
            self.ui_queue.put(("chat", f"[STT Process Error]: {e}"))
        finally:
            self.ui_queue.put(("live", ""))

    def process_outgoing_text(self):
        while self.running:
            try:
                text = self.text_input_queue.get(timeout=1).strip()
                text_lower = text.lower()

                if text_lower == 'answer':
                    self.bt_controller.send_at_command(b"ATA\r")
                    continue
                elif text_lower in ['decline', 'reject', 'hangup']:
                    self.bt_controller.send_at_command(b"AT+CHUP\r")
                    self.in_call = False
                    continue

                if not self.in_call:
                    self.ui_queue.put(("log", "Error: Not in an active call."))
                    continue

                self.ui_queue.put(("chat", f"[Eidolon TTS]: {text}"))
                self.tts_active = True

                try:
                    temp_mp3 = "/tmp/eidolon_tts.mp3"
                    temp_pcm = "/tmp/eidolon_tts.pcm"

                    async def _generate():
                        communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
                        await communicate.save(temp_mp3)

                    asyncio.run(_generate())

                    subprocess.run([
                        'ffmpeg', '-y', '-i', temp_mp3,
                        '-f', 's16le', '-ac', '1', '-ar', '16000',
                        temp_pcm
                    ], check=True, capture_output=True)

                    if os.path.exists(temp_pcm):
                        with open(temp_pcm, 'rb') as f:
                            audio_bytes = f.read()

                        self.bt_controller.write_audio(audio_bytes, sample_rate=16000)

                except Exception as e:
                    self.ui_queue.put(("log", f"[TTS Error]: {e}"))
                finally:
                    self.tts_active = False

            except queue.Empty:
                continue


class CursesTUI:
    """Manages the Terminal User Interface."""

    def __init__(self, ai_engine: LocalAIEngine, ui_queue: queue.Queue):
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
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_MAGENTA, -1)
        curses.init_pair(5, curses.COLOR_RED, -1)
        curses.init_pair(6, curses.COLOR_YELLOW, -1)

        while self.ai.running:
            # 1. Process Messages
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

            # 2. Handle User Input
            try:
                c = stdscr.getch()
                if c != -1:
                    if c in [10, 13]:
                        if self.current_input.lower() in ['exit', 'quit']:
                            self.ai.running = False
                            break
                        if self.current_input.strip():
                            self.ai.text_input_queue.put(self.current_input)
                        self.current_input = ""
                    elif c in [127, 8, 263]:
                        self.current_input = self.current_input[:-1]
                    elif 32 <= c <= 126:
                        self.current_input += chr(c)
            except Exception:
                pass

            # 3. Draw Screen
            h, w = stdscr.getmaxyx()
            stdscr.erase()

            log_h = h // 2
            for i, log in enumerate(self.logs[-(log_h - 2):]):
                color = curses.color_pair(1)
                if "[Phone Replies]" in log:
                    color = curses.color_pair(6)
                elif "[DEBUG]" in log or "gains OPEN" in log or "[State]" in log:
                    color = curses.color_pair(2)
                elif "[DELEGATE]" in log or "Error" in log or "rejected" in log or "silent" in log:
                    color = curses.color_pair(5)
                elif "Hunting" in log or "SDP" in log:
                    color = curses.color_pair(4)

                try:
                    stdscr.addnstr(1 + i, 2, log, w - 4, color)
                except curses.error:
                    pass

            stdscr.addstr(0, 2, " SYSTEM LOGS ", curses.A_BOLD)
            stdscr.hline(log_h, 0, curses.ACS_HLINE, w)

            chat_h = h - log_h - 3
            for i, chat in enumerate(self.chats[-(chat_h - 2):]):
                color = curses.color_pair(2) if "[Caller]" in chat else curses.color_pair(3)
                try:
                    stdscr.addnstr(log_h + 1 + i, 2, chat, w - 4, color)
                except curses.error:
                    pass

            if self.live_stt:
                try:
                    stdscr.addnstr(log_h + chat_h - 1, 2, f"STT State: {self.live_stt}", w - 4,
                                   curses.color_pair(4) | curses.A_DIM)
                except curses.error:
                    pass

            stdscr.addstr(log_h, 2, " TRANSCRIPTION & TTS ", curses.A_BOLD)
            stdscr.hline(h - 2, 0, curses.ACS_HLINE, w)

            stdscr.addstr(h - 1, 0, "Type response > ")
            stdscr.addstr(h - 1, 16, self.current_input)
            stdscr.refresh()


class EidolonOrchestrator:
    def __init__(self):
        self.ui_queue = queue.Queue()
        self.bt_controller = get_os_controller()

        self.original_stdout = sys.stdout
        sys.stdout = StdoutRedirector(self.ui_queue)

        self.ai = LocalAIEngine(self.bt_controller, self.ui_queue)
        self.tui = CursesTUI(self.ai, self.ui_queue)

    def run(self):
        try:
            self.ui_queue.put(("log", f"Detected OS -> {sys.platform}"))
            if not self.bt_controller.start_hfp_server():
                return

            threading.Thread(target=self.ai.process_incoming_audio, daemon=True).start()
            threading.Thread(target=self.ai.process_outgoing_text, daemon=True).start()
            threading.Thread(target=self.ai.process_bluetooth_events, daemon=True).start()

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
        finally:
            self.bt_controller.stop_server()
            sys.stdout = self.original_stdout


if __name__ == "__main__":
    app = EidolonOrchestrator()
    app.run()