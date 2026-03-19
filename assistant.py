#!/usr/bin/env python3
"""
AI Voice Assistant — AIY Voice Kit V1 + Ollama (Mac Mini M4)

Flow: hold button → record → sherpa-onnx STT → Ollama LLM → edge-tts TTS → play
LED:  idle=off  recording=on  processing=fast-blink  speaking=slow-blink
"""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import edge_tts
import requests
import sherpa_onnx
from gpiozero import Button, PWMLED

import config
from memory_store import MemoryStore


# ─── LED helpers ─────────────────────────────────────────────────────────────

class StatusLED:
    def __init__(self, pin):
        self._led = PWMLED(pin)
        self._stop = threading.Event()
        self._thread = None

    def _blink_loop(self, on_time, off_time):
        while not self._stop.is_set():
            self._led.on()
            self._stop.wait(on_time)
            self._led.off()
            self._stop.wait(off_time)

    def on(self):
        self._cancel_blink()
        self._led.on()

    def off(self):
        self._cancel_blink()
        self._led.off()

    def blink(self, on_time=0.1, off_time=0.1):
        self._cancel_blink()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._blink_loop, args=(on_time, off_time), daemon=True
        )
        self._thread.start()

    def _cancel_blink(self):
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=1)
        self._stop.clear()

    def pulse_ready(self):
        """Short double-blink to signal ready."""
        for _ in range(2):
            self._led.on(); time.sleep(0.1)
            self._led.off(); time.sleep(0.1)


# ─── Audio ────────────────────────────────────────────────────────────────────

def record_and_transcribe(button: Button, recognizer: sherpa_onnx.OnlineRecognizer) -> str:
    """
    Record audio while button is held AND transcribe concurrently.
    arecord pipes raw PCM to stdout; sherpa-onnx processes each chunk as it arrives.
    By the time the button is released, most audio is already recognised.
    No temporary WAV file needed.
    """
    stream = recognizer.create_stream()

    cmd = [
        "arecord",
        "-D", config.ALSA_DEVICE,
        "-f", config.SAMPLE_FORMAT,
        "-r", str(config.SAMPLE_RATE),
        "-c", str(config.CHANNELS),
        "--buffer-size=512",   # 32ms ALSA period — lower capture latency
        "-",   # raw PCM to stdout (no WAV header)
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    # Stopper thread: wait for button release, then terminate arecord
    def _stopper():
        button.wait_for_release()
        proc.terminate()

    t = threading.Thread(target=_stopper, daemon=True)
    t.start()

    # Main: read PCM chunks and feed to sherpa-onnx while recording
    while True:
        data = proc.stdout.read(4000)   # 0.125s chunks — more responsive partial results
        if not data:
            break
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        stream.accept_waveform(config.SAMPLE_RATE, samples)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)

    proc.wait()
    t.join()

    # Flush zipformer right-context (~40ms); 0.1s is ample
    tail = np.zeros(int(0.1 * config.SAMPLE_RATE), dtype=np.float32)
    stream.accept_waveform(config.SAMPLE_RATE, tail)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)

    return recognizer.get_result(stream).strip()


# ─── LLM ─────────────────────────────────────────────────────────────────────

# Sentence boundary: Chinese/English punctuation + Chinese comma for long segments
_SENTENCE_END = re.compile(r'[。！？!?]+|(?<=[^0-9])\.(?=\s|$)|，(?=.{20,})')

# Markdown patterns to strip before TTS
_MARKDOWN = re.compile(r'[*#`_~>]|\[|\]|\(|\)')

def _stream_ollama(history: list, system_prompt: str = None):
    """Stream from Ollama; yield text chunks."""
    sys_prompt = system_prompt if system_prompt is not None else config.SYSTEM_PROMPT
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": sys_prompt}] + history,
        "stream": True,
    }
    resp = requests.post(
        f"{config.OLLAMA_HOST}/api/chat",
        json=payload,
        auth=(config.OLLAMA_USERNAME, config.OLLAMA_PASSWORD),
        timeout=config.OLLAMA_TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()

    for line in resp.iter_lines():
        if not line:
            continue
        data = json.loads(line)
        chunk = data.get("message", {}).get("content", "")
        if chunk:
            yield chunk
        if data.get("done"):
            break


def _stream_deepseek(history: list, system_prompt: str = None):
    """Stream from DeepSeek (OpenAI-compatible SSE); yield text chunks."""
    sys_prompt = system_prompt if system_prompt is not None else config.SYSTEM_PROMPT
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": sys_prompt}] + history,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        f"{config.DEEPSEEK_HOST}/chat/completions",
        json=payload,
        headers=headers,
        timeout=config.DEEPSEEK_TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()

    for line in resp.iter_lines():
        if not line:
            continue
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        if not text.startswith("data:"):
            continue
        payload_str = text[5:].strip()
        if payload_str == "[DONE]":
            break
        data = json.loads(payload_str)
        chunk = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
        if chunk:
            yield chunk


def stream_llm(user_text: str, history: list, memory_store: MemoryStore = None, system_prompt: str = None):
    """Dispatch to the configured LLM provider; yield chunks; update history.

    Args:
        user_text: User message text
        history: Conversation history (modified in-place)
        memory_store: Optional MemoryStore instance for persistence
        system_prompt: Optional custom system prompt (uses config.SYSTEM_PROMPT if None)
    """
    history.append({"role": "user", "content": user_text})

    # Save user message to disk if persistence enabled
    if memory_store:
        memory_store.save_message({
            "role": "user",
            "content": user_text,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })

    provider = config.LLM_PROVIDER.lower()
    if provider == "deepseek":
        gen = _stream_deepseek(history, system_prompt)
    else:
        gen = _stream_ollama(history, system_prompt)

    full_text = ""
    bootstrap_updates = {
        'memory': [],
        'user': [],
        'identity': [],
        'soul': []
    }  # Collect Bootstrap update markers

    for chunk in gen:
        full_text += chunk
        yield chunk

    # Extract Bootstrap updates (OpenClaw-inspired)
    if memory_store:
        lines = full_text.split('\n')
        for line in lines:
            # Memory updates
            if config.MEMORY_UPDATE_MARKER in line:
                update = _extract_marker_content(line, config.MEMORY_UPDATE_MARKER)
                if update:
                    bootstrap_updates['memory'].append(update)

            # User profile updates
            if config.USER_UPDATE_MARKER in line:
                update = _extract_marker_content(line, config.USER_UPDATE_MARKER)
                if update:
                    bootstrap_updates['user'].append(update)

            # Identity updates
            if config.IDENTITY_UPDATE_MARKER in line:
                update = _extract_marker_content(line, config.IDENTITY_UPDATE_MARKER)
                if update:
                    bootstrap_updates['identity'].append(update)

            # Soul updates
            if config.SOUL_UPDATE_MARKER in line:
                update = _extract_marker_content(line, config.SOUL_UPDATE_MARKER)
                if update:
                    bootstrap_updates['soul'].append(update)

    # Clean response (remove all Bootstrap markers before saving and speaking)
    clean_text = full_text
    all_markers = [
        config.MEMORY_UPDATE_MARKER,
        config.USER_UPDATE_MARKER,
        config.IDENTITY_UPDATE_MARKER,
        config.SOUL_UPDATE_MARKER
    ]
    for line in full_text.split('\n'):
        if any(marker in line for marker in all_markers):
            clean_text = clean_text.replace(line + '\n', '')
            clean_text = clean_text.replace(line, '')

    history.append({"role": "assistant", "content": clean_text})

    # Save assistant message to disk
    if memory_store:
        memory_store.save_message({
            "role": "assistant",
            "content": clean_text,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })

    # Apply Bootstrap updates
    if memory_store:
        _apply_bootstrap_updates(memory_store, bootstrap_updates)


def _extract_marker_content(line: str, marker: str) -> str:
    """Extract content after a Bootstrap update marker.

    Args:
        line: Line containing the marker
        marker: The marker string to extract after

    Returns:
        Extracted content, or empty string if invalid
    """
    parts = line.split(marker, 1)
    if len(parts) > 1:
        update = parts[1].strip()
        if update.startswith(':'):
            update = update[1:].strip()
        return update
    return ""


def _apply_bootstrap_updates(memory_store: MemoryStore, updates: dict):
    """Apply updates to Bootstrap files (OpenClaw-inspired).

    Args:
        memory_store: MemoryStore instance
        updates: Dict with keys 'memory', 'user', 'identity', 'soul',
                 each containing list of update strings
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    # Update MEMORY.md
    if updates['memory']:
        try:
            current_memory = memory_store.load_memory()
            lines = current_memory.split('\n') if current_memory else []

            # Remove old timestamp
            if lines and lines[-1].strip().startswith('Last updated:'):
                lines = lines[:-1]
                if lines and lines[-1].strip() == '---':
                    lines = lines[:-1]

            # Ensure "Recent Learnings" section
            if not any('## Recent Learnings' in line for line in lines):
                lines.append('')
                lines.append('## Recent Learnings')

            # Append updates
            for update in updates['memory']:
                lines.append(f"- [{timestamp}] {update}")

            lines.append('')
            lines.append('---')
            lines.append(f'Last updated: {timestamp}')

            memory_store.update_memory('\n'.join(lines))
            print(f"\n[bootstrap] Updated MEMORY.md with {len(updates['memory'])} entry(s)")
        except Exception as e:
            print(f"\n[error] Failed to update MEMORY.md: {e}")

    # Update USER.md
    if updates['user']:
        try:
            current_user = memory_store.load_user()
            lines = current_user.split('\n') if current_user else []

            # Remove old timestamp
            if lines and lines[-1].strip().startswith('Last updated:'):
                lines = lines[:-1]
                if lines and lines[-1].strip() == '---':
                    lines = lines[:-1]

            # Append updates
            for update in updates['user']:
                lines.append(f"- [{timestamp}] {update}")

            lines.append('')
            lines.append('---')
            lines.append(f'Last updated: {timestamp}')

            memory_store.update_user('\n'.join(lines))
            print(f"\n[bootstrap] Updated USER.md with {len(updates['user'])} entry(s)")
        except Exception as e:
            print(f"\n[error] Failed to update USER.md: {e}")

    # Update IDENTITY.md
    if updates['identity']:
        try:
            current_identity = memory_store.load_identity()
            lines = current_identity.split('\n') if current_identity else []

            # Remove old timestamp
            if lines and lines[-1].strip().startswith('首次创建:'):
                lines = lines[:-1]
                if lines and lines[-1].strip() == '---':
                    lines = lines[:-1]

            # Append updates
            lines.append('')
            lines.append('## 演化记录')
            for update in updates['identity']:
                lines.append(f"- [{timestamp}] {update}")

            lines.append('')
            lines.append('---')
            lines.append(f'Last updated: {timestamp}')

            memory_store.update_identity('\n'.join(lines))
            print(f"\n[bootstrap] Updated IDENTITY.md with {len(updates['identity'])} entry(s)")
        except Exception as e:
            print(f"\n[error] Failed to update IDENTITY.md: {e}")

    # Update SOUL.md (critical - notify user)
    if updates['soul']:
        try:
            current_soul = memory_store.load_soul()
            lines = current_soul.split('\n') if current_soul else []

            # Add evolution section
            lines.append('')
            lines.append('## 灵魂演化')
            for update in updates['soul']:
                lines.append(f"- [{timestamp}] {update}")

            memory_store.update_soul('\n'.join(lines))
            print(f"\n[bootstrap] ⚠️  SOUL.md updated with {len(updates['soul'])} entry(s)")
            print(f"[bootstrap] The assistant has evolved its core behavior.")
        except Exception as e:
            print(f"\n[error] Failed to update SOUL.md: {e}")


# ─── TTS ─────────────────────────────────────────────────────────────────────

import queue as _queue

def _synth_to_wav(text: str) -> str:
    """Synthesise text → WAV file via edge-tts + ffmpeg. Returns WAV path."""
    mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    mp3.close()
    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav.close()
    try:
        async def _run():
            communicate = edge_tts.Communicate(
                text, config.TTS_VOICE,
                rate=config.TTS_RATE, volume=config.TTS_VOLUME,
            )
            await communicate.save(mp3.name)
        asyncio.run(_run())
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3.name, "-ar", "16000", "-ac", "1", wav.name],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return wav.name
    finally:
        os.unlink(mp3.name)


def speak_streaming(text_stream):
    """
    3-stage pipeline:
      Thread-1 (LLM reader)  : reads stream → splits sentences → sentence_q
      Thread-2 (synthesiser) : sentence_q  → edge-tts+ffmpeg → wav_q
      Main thread (player)   : wav_q       → aplay (plays while next is synthesising)
    """
    sentence_q: _queue.Queue = _queue.Queue(maxsize=4)
    wav_q:      _queue.Queue = _queue.Queue(maxsize=2)

    # ── Thread 1: LLM → sentences ────────────────────────────────────────────
    def llm_reader():
        buf = ""
        for chunk in text_stream:
            buf += chunk
            while True:
                m = _SENTENCE_END.search(buf)
                if not m:
                    break
                sentence = _MARKDOWN.sub("", buf[:m.end()]).strip()
                buf = buf[m.end():]
                if sentence:
                    sentence_q.put(sentence)
        tail = _MARKDOWN.sub("", buf).strip()
        if tail:
            sentence_q.put(tail)
        sentence_q.put(None)  # sentinel

    # ── Thread 2: sentences → WAV files ──────────────────────────────────────
    def synthesiser():
        while True:
            sentence = sentence_q.get()
            if sentence is None:
                wav_q.put(None)
                break
            try:
                wav_path = _synth_to_wav(sentence)
                wav_q.put((sentence, wav_path))
            except Exception as e:
                print(f"\n[error] TTS synth: {e}")

    t1 = threading.Thread(target=llm_reader,  daemon=True)
    t2 = threading.Thread(target=synthesiser, daemon=True)
    t1.start()
    t2.start()

    # ── Main: play WAV files as they arrive ──────────────────────────────────
    first = True
    while True:
        item = wav_q.get()
        if item is None:
            break
        sentence, wav_path = item
        try:
            if first:
                print(f"AI  : {sentence}", end="", flush=True)
                first = False
            else:
                print(f" {sentence}", end="", flush=True)
            subprocess.run(
                ["aplay", "-D", config.ALSA_DEVICE, wav_path],
                check=True, stderr=subprocess.DEVNULL,
            )
        finally:
            os.unlink(wav_path)

    print()
    t1.join()
    t2.join()


# ─── Signal handlers ──────────────────────────────────────────────────────────

def cleanup_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    print("\n[shutdown] Exiting...")
    sys.exit(0)


signal.signal(signal.SIGTERM, cleanup_handler)
signal.signal(signal.SIGINT, cleanup_handler)


# ─── Memory system helpers ────────────────────────────────────────────────────

def build_system_prompt(soul: str = "", identity: str = "", user: str = "", memory: str = "") -> str:
    """Construct system prompt from Bootstrap files (OpenClaw-inspired).

    Bootstrap injection order:
    1. SOUL.md (core personality) - fallback to config.SYSTEM_PROMPT if empty
    2. IDENTITY.md (assistant identity)
    3. USER.md (user profile)
    4. MEMORY.md (long-term memory)
    5. Update protocols

    Args:
        soul: Content from SOUL.md
        identity: Content from IDENTITY.md
        user: Content from USER.md
        memory: Content from MEMORY.md

    Returns:
        Complete system prompt with all Bootstrap context
    """
    parts = []

    # ── 1. SOUL.md (Core Personality) ────────────────────────────────────────
    if soul:
        parts.append("# 你的灵魂\n")
        parts.append(soul)
    else:
        # Fallback to hardcoded system prompt if SOUL.md doesn't exist
        parts.append(config.SYSTEM_PROMPT)

    # ── 2. IDENTITY.md (Assistant Identity) ───────────────────────────────────
    if identity:
        parts.append("\n# 你的身份\n")
        parts.append(identity)

    # ── 3. USER.md (User Profile) ─────────────────────────────────────────────
    if user:
        parts.append("\n# 你在帮助的人\n")
        parts.append(user)

    # ── 4. MEMORY.md (Long-term Memory) ───────────────────────────────────────
    if memory:
        parts.append("\n# 长期记忆\n")
        parts.append("以下是你学到的重要信息：\n")
        parts.append(memory)

    # ── 5. Update Protocols ───────────────────────────────────────────────────
    parts.append("\n## 文件更新协议\n")
    parts.append("如果需要更新这些文件，在回答中包含对应标记：\n")
    parts.append(f"- {config.MEMORY_UPDATE_MARKER}: <学到的新信息>\n")
    parts.append(f"- {config.USER_UPDATE_MARKER}: <关于用户的新发现>\n")
    parts.append(f"- {config.IDENTITY_UPDATE_MARKER}: <关于你自己身份的调整>\n")
    parts.append(f"- {config.SOUL_UPDATE_MARKER}: <关于你核心行为的重要改变>\n")

    return "\n".join(parts)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Loading sherpa-onnx model …")
    if not os.path.isdir(config.SHERPA_MODEL_DIR):
        print(f"ERROR: sherpa-onnx model not found at {config.SHERPA_MODEL_DIR}")
        print("Run setup.sh first.")
        sys.exit(1)
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=os.path.join(config.SHERPA_MODEL_DIR, "tokens.txt"),
        encoder=os.path.join(config.SHERPA_MODEL_DIR, config.SHERPA_ENCODER),
        decoder=os.path.join(config.SHERPA_MODEL_DIR, config.SHERPA_DECODER),
        joiner=os.path.join(config.SHERPA_MODEL_DIR, config.SHERPA_JOINER),
        num_threads=2,
        sample_rate=config.SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
    )

    button = Button(config.BUTTON_PIN, pull_up=True)
    led    = StatusLED(config.LED_PIN)

    history: list = []   # conversation history for multi-turn context

    # ── Initialize memory store ────────────────────────────────────────────
    memory_store = None
    system_prompt = None  # Use default from config if None

    if config.MEMORY_ENABLED:
        try:
            memory_store = MemoryStore(config.MEMORY_DIR)

            # ── Load Bootstrap files (OpenClaw-inspired) ──────────────────────
            if config.BOOTSTRAP_ENABLED:
                # Load all Bootstrap files
                soul = memory_store.load_soul()
                identity = memory_store.load_identity()
                user = memory_store.load_user()
                memory_content = memory_store.load_memory()

                # Create defaults for missing files
                if not soul:
                    memory_store.create_default_soul()
                    soul = memory_store.load_soul()
                    print("[bootstrap] Created default SOUL.md")

                if not identity:
                    memory_store.create_default_identity()
                    identity = memory_store.load_identity()
                    print("[bootstrap] Created default IDENTITY.md")

                if not user:
                    memory_store.create_default_user()
                    user = memory_store.load_user()
                    print("[bootstrap] Created default USER.md")

                if not memory_content:
                    memory_store.create_default_memory()
                    memory_content = memory_store.load_memory()
                    print("[bootstrap] Created default MEMORY.md")

                # Build system prompt from Bootstrap files
                system_prompt = build_system_prompt(soul, identity, user, memory_content)

                # Calculate total size
                total_size = len(soul) + len(identity) + len(user) + len(memory_content)
                print(f"[bootstrap] Loaded Bootstrap files: "
                      f"SOUL({len(soul)}), IDENTITY({len(identity)}), "
                      f"USER({len(user)}), MEMORY({len(memory_content)}) "
                      f"= {total_size} chars total")

                # Warn if approaching limits
                if total_size > config.BOOTSTRAP_TOTAL_MAX_CHARS:
                    print(f"[warning] Bootstrap total size ({total_size}) exceeds "
                          f"limit ({config.BOOTSTRAP_TOTAL_MAX_CHARS}). "
                          "Consider summarizing files.")

            else:
                # Bootstrap disabled, use simple memory system
                memory_content = memory_store.load_memory()
                if memory_content:
                    system_prompt = build_system_prompt(memory=memory_content)
                    print(f"[memory] Loaded MEMORY.md ({len(memory_content)} chars)")
                else:
                    memory_store.create_default_memory()
                    print("[memory] Created default MEMORY.md template")

            # Load conversation history
            history = memory_store.load_history(max_messages=config.MAX_HISTORY_MESSAGES)
            if history:
                print(f"[memory] Loaded {len(history)} messages from previous session")

            # Rotate if needed
            memory_store.rotate_history(keep_lines=config.HISTORY_ROTATION_THRESHOLD)

        except Exception as e:
            print(f"[warning] Memory system initialization failed: {e}")
            print("[warning] Continuing without persistence")
            memory_store = None
            system_prompt = None

    provider = config.LLM_PROVIDER
    model = config.DEEPSEEK_MODEL if provider == "deepseek" else config.OLLAMA_MODEL
    host  = config.DEEPSEEK_HOST  if provider == "deepseek" else config.OLLAMA_HOST
    print(f"Ready. Provider: {provider}  model: {model} @ {host}")
    led.pulse_ready()

    while True:
        print("\n[idle] Hold button to speak …")
        button.wait_for_press()

        # ── Record + Transcribe (concurrent) ───────────────────────────────
        led.on()
        print("[recording + transcribing]")
        t0 = time.time()
        user_text = record_and_transcribe(button, recognizer)
        print(f"[transcribing done] {time.time()-t0:.1f}s")

        if not user_text:
            print("[skip] Nothing recognised.")
            led.off()
            continue

        print(f"You : {user_text}")

        # ── LLM + TTS (streamed, sentence-by-sentence) ────────────────────
        led.blink(0.3, 0.3)
        print(f"[querying {provider} + speaking]")
        t0 = time.time()
        try:
            speak_streaming(stream_llm(user_text, history, memory_store, system_prompt))
        except requests.exceptions.RequestException as e:
            print(f"[error] Ollama: {e}")
            try:
                wav = _synth_to_wav("抱歉，无法连接到 AI 服务器。")
                subprocess.run(["aplay", "-D", config.ALSA_DEVICE, wav],
                               check=True, stderr=subprocess.DEVNULL)
                os.unlink(wav)
            except Exception:
                pass
        except Exception as e:
            print(f"[error] TTS: {e}")
        print(f"[done] {time.time()-t0:.1f}s")

        led.off()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye.")
