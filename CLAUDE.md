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
- Conversation history now persisted to disk (see Memory System below)
- ffmpeg is available at `/usr/bin/ffmpeg` (system package)
- Linux 6.12+ broke RPi.GPIO 0.7.1 edge detection — always use `python3-rpi-lgpio` (system apt) + `GPIOZERO_PIN_FACTORY=lgpio`
- System prompt enforces 1-3 sentence replies and no markdown — model (gemma3:4b) tends to produce bullet lists without this constraint
- LLM uses `stream: true` so first sentence reaches TTS ~2-3s after query, before full response is ready

## Bootstrap + Memory System

Bootstrap injection system inspired by [OpenClaw](https://github.com/openclaw/openclaw) workspace architecture.

**Architecture:**
- Bootstrap injection system: 4 core files (SOUL, IDENTITY, USER, MEMORY) injected into every LLM call
- Storage location: `./memory/` (configurable via `MEMORY_DIR` in `.env`)
- History format: JSONL (one message per line, append-only, crash-resistant)
- History rotation: Triggered at 2000 lines (keeps most recent, backs up older to `.backup`)
- Size limits: 20KB per file, 150KB total (configurable)

**Bootstrap Files:**

1. **SOUL.md** - Core personality and behavior philosophy
   - Defines conversational style, values, boundaries
   - Replaces hardcoded `config.SYSTEM_PROMPT` when present
   - Can evolve via `[UPDATE_SOUL]:` marker (rare, critical changes)
   - Guideline: Clear and concise, ~200-500 lines

2. **IDENTITY.md** - Assistant self-awareness
   - Name, role, personality tags
   - Capabilities and limitations
   - Auto-created on first run, can be customized
   - Updates via `[UPDATE_IDENTITY]:` marker

3. **USER.md** - User profile
   - Name, communication preferences, interests
   - Accumulated over time through conversation
   - Updates via `[UPDATE_USER]:` marker
   - Guideline: <100 lines, focused on key context

4. **MEMORY.md** - Long-term decision log
   - User preferences, behavioral rules, learned patterns
   - Updates via `[UPDATE_MEMORY]:` marker (most common)
   - Guideline: <100 lines, keep focused

**Bootstrap Updates:**
- LLM triggers updates using markers in responses:
  - `[UPDATE_MEMORY]: <learning>` - Most common, user preferences
  - `[UPDATE_USER]: <user context>` - User profile changes
  - `[UPDATE_IDENTITY]: <identity adjustment>` - Identity refinement
  - `[UPDATE_SOUL]: <behavior change>` - Critical personality evolution
- Markers parsed by `stream_llm()` and processed by `_apply_bootstrap_updates()`
- Updates appended with timestamps
- Markers stripped from TTS output (not spoken)
- SOUL updates trigger warning notification to user

**Persistence Flow:**
1. Startup: `MemoryStore` initialized in `main()` → loads MEMORY.md + history
2. User message: Saved to `history.jsonl` immediately (via `save_message()`)
3. LLM response: Checked for memory markers, cleaned, saved to history
4. Shutdown: History already persisted incrementally (no final save needed)

**Integration Points in assistant.py:**
- Line 26: Import `MemoryStore`
- Lines 330-380: `build_system_prompt()` — Bootstrap injection (SOUL→IDENTITY→USER→MEMORY)
- Lines 220-290: `stream_llm()` — handles Bootstrap persistence and marker detection
- Lines 293-302: `_extract_marker_content()` — helper to parse markers
- Lines 305-400: `_apply_bootstrap_updates()` — updates all Bootstrap files
- Lines 430-490: `main()` — loads all Bootstrap files at startup

**Bootstrap Loading Flow:**
```
main() startup:
  └─> MemoryStore(config.MEMORY_DIR)
      ├─> load_soul()              → SOUL.md content
      ├─> load_identity()          → IDENTITY.md content
      ├─> load_user()              → USER.md content
      ├─> load_memory()            → MEMORY.md content
      └─> build_system_prompt(soul, identity, user, memory)
          └─> Injected into every LLM call
```

**Resource Overhead:**
- Startup: <20ms (load MEMORY.md + 50 messages)
- Per-turn: <3ms (append to JSONL)
- Memory footprint: +18 KB (50 messages + MEMORY.md in RAM)
- Disk: ~300 KB for 2000-line history

**Error Handling:**
- Graceful degradation: If files missing/corrupted, starts fresh with defaults
- Corrupted JSON lines: Skipped during load, logged as warnings
- Disk full / permission errors: Disables persistence, continues in-memory
- Never crashes on memory system errors

**Configuration (.env):**
```bash
# Memory system
MEMORY_ENABLED=true                  # Enable/disable entire system
MEMORY_DIR=./memory                  # Storage directory path
MAX_HISTORY_MESSAGES=50              # Max messages to load at startup
HISTORY_ROTATION_THRESHOLD=2000      # Lines before rotation

# Bootstrap system (OpenClaw-inspired)
BOOTSTRAP_ENABLED=true               # Enable Bootstrap injection
BOOTSTRAP_MAX_CHARS=20000            # Max chars per file
BOOTSTRAP_TOTAL_MAX_CHARS=150000     # Total max for all files

# Update markers
MEMORY_UPDATE_MARKER=[UPDATE_MEMORY]
USER_UPDATE_MARKER=[UPDATE_USER]
IDENTITY_UPDATE_MARKER=[UPDATE_IDENTITY]
SOUL_UPDATE_MARKER=[UPDATE_SOUL]     # Triggers warning to user
```

**Files:**
- `memory_store.py` — Storage abstraction class (~400 lines with Bootstrap support)
- `memory/SOUL.md` — Core personality (hand-editable)
- `memory/IDENTITY.md` — Assistant identity (hand-editable)
- `memory/USER.md` — User profile (auto-accumulated)
- `memory/MEMORY.md` — Long-term memory (auto-updated)
- `memory/history.jsonl` — Conversation history (JSONL format)
- `memory/history.jsonl.backup` — Rotated old messages

**Security:**
- Files mode 600 (user-only read/write)
- Directory mode 700 (user-only access)
- Excluded from git via `.gitignore`
- All data stays local on Pi (no cloud sync)
