# EIDOLON
**An open-source Bluetooth call proxy for live conversational AI**

> [!NOTE]  
> **Provisional README (v0.0.1-poc)**
> 
> Hey! Eidolon is currently in an early Proof of Concept stage. The core Linux native hardware injections are working, but there is still a lot of jank (please read the [Known Issues](#known-issues) before running this).
>
> **What's next:** Right now, the repo uses a temporary Terminal UI and a basic Whisper/Edge-TTS loop just to prove the audio routing works. The immediate next step is ripping that scaffolding out and replacing it with true full-duplex conversational models (like OpenAI Realtime or Gemini Live), managed via a mobile companion app. Oh, and the ESP32 audio is currently very choppy. I'm working on it!

[![Version](https://img.shields.io/badge/Version-v0.0.1--poc-blue?style=flat-square)](#)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-green.svg?style=flat-square)](#)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg?style=flat-square)](#)

Mobile ecosystems like iOS and Android are notoriously locked down. If you want to integrate a live, conversational AI directly into a standard cellular phone call, you usually hit a wall. You either have to rely on clunky VoIP workarounds, break real-time latency constraints, or jailbreak/root your device.

Eidolon is an attempt to bypass all of that.

By making your computer (or an ESP32) pretend to be a standard Bluetooth Hands-Free Profile (HFP) headset, Eidolon seamlessly intercepts your phone's calls at the native hardware level. It lets you pipe audio into a terminal UI, transcribe what the caller is saying, type responses, and eventually, let an AI handle the entire conversation for you.

---

<h3 align="center">Contents</h2>

<p align="center">
  <a href="#how-it-works">How It Works</a> •
  <a href="#deployment-modes">Deployment</a> •
  <a href="#demonstrations">Demonstrations</a> •
  <a href="#known-issues">Known Issues</a>
  <br>
  <a href="#roadmap">Roadmap</a> •
  <a href="#technical-stack">Tech Stack</a> •
  <a href="#build-instructions">Build</a> •
  <a href="#contact">Contact</a>
</p>

---

## How It Works

### **The Handshake**
When you start Eidolon, it hunts down your paired phone and connects to its Audio Gateway. It then runs through a strict AT command handshake to establish a Service Level Connection (SLC). Once that's done, your host machine officially has the authority to answer calls, reject them, and inject raw audio directly into the cellular line.

### **The OS Controllers**
Getting low-level Bluetooth audio to behave is wildly different depending on your OS. I had to build custom controllers to handle the quirks:
1. **Linux Native (The True Magic):** This is where the project shines. Validated on Arch Linux against a Pixel 9, this bypasses PulseAudio/PipeWire entirely. It binds directly to `AF_BLUETOOTH` sockets to read and write raw CVSD audio packets straight from the Linux kernel.
2. **macOS Bypass:** Apple's kernel is heavily restricted, so the macOS controller uses Objective-C (`IOBluetooth`) to handle the AT handshake, but falls back to an acoustic bypass via `PyAudio` for the actual sound.
3. **ESP32 Bridge:** For platforms that absolutely refuse to cooperate, the hardware interaction is offloaded to a cheap ESP32 microcontroller, which streams the audio payloads back to your computer over a TCP Wi-Fi bridge.

---

## Deployment Modes

To avoid memory crashes and give users options based on their privacy needs, I'm structuring Eidolon around three deployment paths:

- **Tier 1 (Local FOSS):** Completely offline. Runs entirely on your host PC, executing local models. Zero telemetry, zero data leaks.
- **Tier 2 (Bring Your Own Key):** A lightweight setup that routes the conversation through your own API keys (OpenAI, Gemini). Perfect for developers who want state-of-the-art conversational logic without hardware limitations.
- **Tier 3 (Hardware Delegate):** Because Bluetooth requires your phone to be physically near the host, this tier uses the ESP32 as a dedicated plug-and-play node. You leave the ESP32 near your phone, and it routes everything over Wi-Fi to your PC or a server.

---

## Demonstrations

### Direct Kernel Injection (Linux Native)
Here is Eidolon intercepting a live call natively on Linux. The Curses TUI handles the transcriptions and lets me inject generated TTS responses right back into the call.

<div align="center">
  <video src="media/compressed_linux.mp4" width="100%" controls></video>
</div>

### Untethered Hardware Bypass (ESP32 Mode)
Here is the system controlling a phone using an ESP32 powered by an external 9V battery. The ESP32 handles the Bluetooth stack and communicates with the Python engine over Wi-Fi. *(Warning: The audio routing is currently super choppy, but the data is flowing!)*

<div align="center">
  <video src="media/compressed_esp32.mp4" width="100%" controls></video>
</div>

---

## Known Issues

- **ESP32 Audio is extremely choppy:** The Wi-Fi audio bridge works, but it suffers from awful packet latency. It is barely legible right now. I need to spend some time optimizing the MTU and TCP chunking logic in the C firmware.
- **No Windows Controller:** I haven't figured out native Windows Bluetooth sockets yet. If you are on Windows, the setup script will default to requiring the ESP32 hardware bridge.
- **Basic STT/TTS loop:** The current pipeline uses local Faster-Whisper and Edge-TTS. It's a fun proof of concept, but it is nowhere near a fluid AI agent yet.

---

## Roadmap

This is a massive undertaking, but here is what I am currently working on for the next major ship:

- **Full-Duplex AI Integration:** Swapping the basic STT/TTS out for live, full-duplex APIs to kill the sub-second audio echoes and latency.
- **Dedicated Companion App:** Engineering an Android (and potentially iOS) application that communicates with the desktop orchestrator or ESP32 delegate over local Wi-Fi. This will provide a UI for live call transcripts, visual voicemail, and manual override controls—allowing you to guide the AI mid-conversation or instruct it to make proactive outbound calls on your behalf.
- **ESP32 Polish:** Fixing the Wi-Fi fragmentation so 16kHz audio actually sounds good.
- **Windows Support:** Researching native OS hooks so Windows users don't strictly need an ESP32.

---

## Technical Stack

- **Language:** Python 3.10+ (Core Orchestrator), C (ESP32 Firmware)
- **UI:** Curses (Terminal Interface)
- **AI/Audio:** Faster-Whisper, Edge-TTS, NumPy, FFmpeg, PyAudio
- **Bluetooth:** OS Native Sockets (`AF_BLUETOOTH`), `pyobjc` (macOS), ESP-IDF 

---

## Build Instructions

Ensure you have Python 3.10+ installed. If you are on Linux, make sure `bluez` and `ffmpeg` are installed via your package manager (the setup script will try to handle this).

```bash
git clone https://github.com/Animesh-Varma/Eidolon.git
cd Eidolon

# 1. Create and activate a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Run the automated setup script to install dependencies
# (Note: Sudo is required on Linux, so we pass the absolute path of the venv's Python binary)
sudo $(pwd)/.venv/bin/python setup.py

# 3. Launch the orchestrator 
# (Requires sudo on Linux to access the AF_BLUETOOTH sockets)
sudo $(pwd)/.venv/bin/python main.py
```

---

## Contact

**Note:** I'm a high school student building this in my spare time. I'm learning low-level OS/hardware routing as I go. Contributors, pull requests, and general advice are always super welcome!

Email: `eidolon@animeshvarma.dev`