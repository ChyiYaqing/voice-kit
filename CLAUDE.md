# CLAUDE.md — AI Voice Assistant

## Project Overview

Raspberry Pi 3B + AIY Google Voice Kit V1 voice assistant.
STT: Vosk (offline). LLM: Ollama on remote Mac Mini M4. TTS: edge-tts (Microsoft neural, Chinese female).

## Key Files

- `assistant.py` — main loop (record → STT → LLM → TTS)
- `config.py`    — all tuneable parameters and credentials
- `setup.sh`     — one-shot install script
- `voice-assistant.service` — systemd unit file (deployed to `/etc/systemd/system/`)
- `vosk-model/`  — Vosk `small-cn-0.22` model (Mandarin Chinese, do not delete)
- `vosk-model-en-backup/` — original `small-en-us` backup

## Hardware

- **Sound card**: ALSA name `sndrpigooglevoi`, device `plughw:sndrpigooglevoi,0`
- **Button**: GPIO 23 (hold to record, release to process)
- **LED**: GPIO 25 (gpiozero `PWMLED`)
- Recording via `arecord`, playback via `aplay` — do NOT use sounddevice/PortAudio

## Ollama API

- Base URL: `https://llm.chyidl.com`
- Auth: HTTP Basic (credentials stored in `.env`, see `OLLAMA_USERNAME` / `OLLAMA_PASSWORD`)
- Default model: `gemma3:4b`
- Endpoint used: `POST /api/chat` (OpenAI-compatible messages format)
- Available models: `gemma3:4b`, `gemma3:12b`, `deepseek-r1:7b`, `qwen3:latest`

## TTS (edge-tts)

- Voice: `zh-CN-XiaoxiaoNeural` (Mandarin Chinese female, Microsoft neural)
- Pipeline: **3-stage threaded**:
  - Thread 1: LLM stream → sentence splitter → `sentence_q`
  - Thread 2: `sentence_q` → edge-tts + ffmpeg → `wav_q` (synthesises next while current plays)
  - Main thread: `wav_q` → aplay
- Sentence boundaries: `。！？!?.` and `，` (when followed by 20+ chars)
- Markdown stripped before TTS (`* # `` _ ~ > [ ] ( )`)
- Requires internet (same as Ollama)
- Config params: `TTS_VOICE`, `TTS_RATE` (e.g. `"+10%"`), `TTS_VOLUME` (e.g. `"-70%"`)
- Other Chinese female voices: `zh-CN-XiaochenNeural`, `zh-CN-XiaohanNeural`

## Python Environment

- Venv: `.venv/` (Python 3.13)
- Key packages: `vosk`, `requests`, `gpiozero`, `edge-tts`
- Activate: `source .venv/bin/activate`
- **RPi.GPIO**: do NOT use pip's `RPi.GPIO` (0.7.1 — broken on Linux 6.12+). Instead, symlink
  system's `python3-rpi-lgpio` (0.7.2) into the venv:
  ```bash
  pip uninstall RPi.GPIO
  SITE=.venv/lib/python3.13/site-packages
  ln -s /usr/lib/python3/dist-packages/RPi $SITE/RPi
  ln -s /usr/lib/python3/dist-packages/lgpio.py $SITE/lgpio.py
  ln -s /usr/lib/python3/dist-packages/_lgpio.cpython-313-aarch64-linux-gnu.so $SITE/_lgpio.cpython-313-aarch64-linux-gnu.so
  ```

## Systemd Service

- Unit file: `/etc/systemd/system/voice-assistant.service`
- Runs as user `chyiyaqing`, auto-starts on boot
- **Required env vars** in `[Service]` block:
  - `GPIOZERO_PIN_FACTORY=lgpio` — lgpio backend for Linux 6.12+ compatibility
  - `PYTHONUNBUFFERED=1` — disable Python stdout buffering so logs appear in journald in real time
- Manage: `sudo systemctl [start|stop|restart|status] voice-assistant`
- Logs: `journalctl -u voice-assistant -f -o short-precise`

## Development Notes

- Pi 3B is ARM64 (aarch64), 1 GB RAM — keep dependencies lightweight
- Vosk `small-cn-0.22` model (Mandarin Chinese); do not use `large` model on this hardware
- English backup at `vosk-model-en-backup/`; to switch back: `mv vosk-model vosk-model-cn && mv vosk-model-en-backup vosk-model`
- Vosk chunk size: `8000` bytes (0.25s/chunk) — balance between loop overhead and Pi 3B memory pressure
- aplay must use `-D plughw:sndrpigooglevoi,0` — default ALSA routing is unreliable due to conflicting card index in `/etc/asound.conf`
- Conversation history is maintained in-memory as a list; cleared on restart
- ffmpeg is available at `/usr/bin/ffmpeg` (system package)
- Linux 6.12+ broke RPi.GPIO 0.7.1 edge detection — always use `python3-rpi-lgpio` (system apt) + `GPIOZERO_PIN_FACTORY=lgpio`
- System prompt enforces 1-3 sentence replies and no markdown — model (gemma3:4b) tends to produce bullet lists without this constraint
- LLM uses `stream: true` so first sentence reaches TTS ~2-3s after query, before full response is ready
