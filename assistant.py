#!/usr/bin/env python3
"""
AI Voice Assistant — AIY Voice Kit V1 + Ollama (Mac Mini M4)

Flow: hold button → record → sherpa-onnx STT → LLM → local TTS → play
LED:  idle=off  recording=on  processing=fast-blink  speaking=slow-blink
"""

import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave

import numpy as np
import requests
import sherpa_onnx
from gpiozero import Button, PWMLED

import config
from memory_store import MemoryStore
import tools


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
_SOFT_TTS_BOUNDARY = re.compile(r'[，,；;、\n]\s*')


def _trim_text(text: str, max_chars: int) -> str:
    """Keep the tail of long context blocks; recent details are usually hotter."""
    if not text or max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _trim_history_for_llm(history: list, max_chars: int = None) -> list:
    """Keep recent chat messages within a rough character budget."""
    max_chars = max_chars or config.LLM_CONTEXT_MAX_CHARS
    if max_chars <= 0:
        kept = history
    else:
        kept = []
        used = 0
        for msg in reversed(history):
            content = msg.get("content", "")
            cost = len(content)
            if kept and used + cost > max_chars:
                break
            kept.append(msg)
            used += cost
        kept.reverse()

    messages = [
        {"role": msg["role"], "content": msg.get("content", "")}
        for msg in kept
        if msg.get("role") in ("user", "assistant") and msg.get("content")
    ]
    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)
    return messages


def _pop_tts_segment(buf: str, first: bool) -> tuple[str | None, str]:
    """Return a speakable segment as soon as enough text is available."""
    m = _SENTENCE_END.search(buf)
    if m:
        return buf[:m.end()], buf[m.end():]

    limit = config.TTS_FIRST_CHUNK_CHARS if first else config.TTS_CHUNK_CHARS
    if len(buf) < limit:
        return None, buf

    soft_end = None
    for match in _SOFT_TTS_BOUNDARY.finditer(buf[:limit + 8]):
        soft_end = match.end()
    if soft_end and soft_end >= max(8, limit // 2):
        return buf[:soft_end], buf[soft_end:]

    return buf[:limit], buf[limit:]

def _stream_ollama(history: list, system_prompt: str = None):
    """Stream from Ollama; yield text chunks."""
    sys_prompt = system_prompt if system_prompt is not None else config.SYSTEM_PROMPT
    messages = _trim_history_for_llm(history)
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": sys_prompt}] + messages,
        "stream": True,
        "keep_alive": config.OLLAMA_KEEP_ALIVE,
        "options": {
            "num_predict": config.OLLAMA_NUM_PREDICT,
        },
    }
    resp = requests.post(
        f"{config.OLLAMA_HOST}/api/chat",
        json=payload,
        auth=(config.OLLAMA_USERNAME, config.OLLAMA_PASSWORD),
        timeout=config.OLLAMA_TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()

    for line in resp.iter_lines(chunk_size=config.LLM_STREAM_CHUNK_SIZE):
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
    messages = _trim_history_for_llm(history)
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": sys_prompt}] + messages,
        "stream": True,
        "max_tokens": config.DEEPSEEK_MAX_TOKENS,
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
    if not resp.ok:
        body = resp.text[:500]
        print(f"[DeepSeek] HTTP {resp.status_code}: {body}", flush=True)
        raise RuntimeError(f"DeepSeek {resp.status_code}: {body}")

    for line in resp.iter_lines(chunk_size=config.LLM_STREAM_CHUNK_SIZE):
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


def _load_claude_oauth_token() -> str:
    """Load OAuth bearer token from credentials/auth-profiles.json (OpenClaw format).

    Returns token string if found and valid, empty string otherwise.
    """
    try:
        path = os.path.expanduser(config.ANTHROPIC_OAUTH_CREDENTIALS)
        if not os.path.exists(path):
            return ""
        with open(path) as f:
            profiles = json.load(f)
        profile = profiles.get("anthropic:claude-cli", {})
        if profile.get("type") == "oauth" and profile.get("provider") == "anthropic":
            token = profile.get("token", "")
            if token and not token.startswith("sk-ant-oau04-YOUR_"):
                return token
    except Exception as e:
        print(f"[claude] OAuth credentials load error: {e}", flush=True)
    return ""


def _stream_claude(history: list, system_prompt: str = None):
    """Stream from Anthropic Claude API (SSE); yield text chunks.

    Auth: OAuth bearer token (credentials/auth-profiles.json) preferred;
    falls back to ANTHROPIC_API_KEY if OAuth token not available.
    """
    sys_prompt = system_prompt if system_prompt is not None else config.SYSTEM_PROMPT
    # Claude API: system is a top-level param, messages must be user/assistant only
    messages = _trim_history_for_llm(history)

    oauth_token = _load_claude_oauth_token()
    if oauth_token:
        headers = {
            "Authorization": f"Bearer {oauth_token}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        print("[claude] Using OAuth token auth", flush=True)
    else:
        headers = {
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    payload = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": config.ANTHROPIC_MAX_TOKENS,
        "system": sys_prompt,
        "messages": messages,
        "stream": True,
    }
    resp = requests.post(
        f"{config.ANTHROPIC_HOST}/v1/messages",
        json=payload,
        headers=headers,
        timeout=config.ANTHROPIC_TIMEOUT,
        stream=True,
    )
    if not resp.ok:
        body = resp.text[:500]
        print(f"[Claude] HTTP {resp.status_code}: {body}", flush=True)
        raise RuntimeError(f"Claude {resp.status_code}: {body}")

    for line in resp.iter_lines(chunk_size=config.LLM_STREAM_CHUNK_SIZE):
        if not line:
            continue
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        if not text.startswith("data:"):
            continue
        data_str = text[5:].strip()
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                chunk = delta.get("text", "")
                if chunk:
                    yield chunk


def stream_llm(user_text: str, history: list, memory_store: MemoryStore = None,
               system_prompt: str = None, interrupt: threading.Event = None):
    """Dispatch to the configured LLM provider; yield chunks; update history.

    Args:
        user_text: User message text
        history: Conversation history (modified in-place)
        memory_store: Optional MemoryStore instance for persistence
        system_prompt: Optional custom system prompt (uses config.SYSTEM_PROMPT if None)
        interrupt: Optional event; when set, stops yielding early
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
    elif provider == "claude":
        gen = _stream_claude(history, system_prompt)
    else:
        gen = _stream_ollama(history, system_prompt)

    full_text = ""
    _gen_provider = provider
    bootstrap_updates = {
        'memory': [],
        'user': [],
        'identity': [],
        'soul': []
    }  # Collect Bootstrap update markers

    try:
        for chunk in gen:
            if interrupt and interrupt.is_set():
                break
            full_text += chunk
            yield chunk
    except Exception as e:
        print(f"[LLM] {_gen_provider} error: {e}", flush=True)
        if _gen_provider in ("deepseek", "claude"):
            print(f"[LLM] Falling back to Ollama", flush=True)
            fallback_history = history[:-1]  # remove user msg already appended
            for chunk in _stream_ollama(fallback_history + [{"role": "user", "content": user_text}], system_prompt):
                full_text += chunk
                yield chunk
        else:
            yield "抱歉，LLM 服务暂时不可用，请稍后再试。"
            full_text = "抱歉，LLM 服务暂时不可用，请稍后再试。"

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

_piper_voice = None
_piper_lock = threading.Lock()


@contextlib.contextmanager
def _suppress_native_output():
    """Suppress Python and native library stdout/stderr for noisy TTS deps."""
    sys.stdout.flush()
    sys.stderr.flush()
    old_stdout = os.dup(1)
    old_stderr = os.dup(2)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), 1)
            os.dup2(devnull.fileno(), 2)
            yield
    finally:
        os.dup2(old_stdout, 1)
        os.dup2(old_stderr, 2)
        os.close(old_stdout)
        os.close(old_stderr)


def _get_piper_voice():
    """Lazy-load Piper once and reuse the ONNX session for all TTS segments."""
    global _piper_voice
    if _piper_voice is not None:
        return _piper_voice

    with _piper_lock:
        if _piper_voice is None:
            with _suppress_native_output():
                from piper import PiperVoice

                _piper_voice = PiperVoice.load(
                    config.PIPER_MODEL,
                    config_path=config.PIPER_CONFIG,
                    use_cuda=False,
                )
        return _piper_voice

def _synth_edge_tts(text: str, wav_path: str) -> None:
    """Synthesise text via Microsoft edge-tts Neural TTS, convert MP3→WAV with ffmpeg."""
    import asyncio
    import edge_tts

    mp3_path = wav_path[:-4] + ".mp3"
    try:
        async def _gen():
            communicate = edge_tts.Communicate(
                text,
                config.TTS_VOICE,
                rate=config.TTS_RATE,
                volume=config.TTS_VOLUME,
            )
            await communicate.save(mp3_path)

        asyncio.run(_gen())
        # mpg123 decodes MP3→WAV in ~60ms on Pi 3B; ffmpeg takes 20+ seconds.
        subprocess.run(
            ["mpg123", "-q", "-w", wav_path, mp3_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        try:
            os.unlink(mp3_path)
        except OSError:
            pass


def _synth_to_wav(text: str) -> str:
    """Synthesise text to a local WAV file. Returns WAV path."""
    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav.close()
    try:
        if config.TTS_ENGINE == "edge":
            _synth_edge_tts(text, wav.name)
        elif config.TTS_ENGINE == "piper":
            with _suppress_native_output():
                from piper import SynthesisConfig

                voice = _get_piper_voice()
                syn_config = SynthesisConfig(volume=config.PIPER_VOLUME)
            with wave.open(wav.name, "wb") as wav_file:
                with _suppress_native_output():
                    voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        else:
            subprocess.run(
                [
                    "espeak-ng",
                    "-v", config.LOCAL_TTS_VOICE,
                    "-s", str(config.LOCAL_TTS_SPEED),
                    "-a", str(config.LOCAL_TTS_AMPLITUDE),
                    "-w", wav.name,
                    text,
                ],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        return wav.name
    except Exception:
        try:
            os.unlink(wav.name)
        except OSError:
            pass
        raise


def speak_streaming(text_stream, interrupt: threading.Event = None, t0: float = None):
    """
    3-stage pipeline:
      Thread-1 (LLM reader)  : reads stream → splits sentences → sentence_q
      Thread-2 (synthesiser) : sentence_q  → local TTS WAV → wav_q
      Main thread (player)   : wav_q       → aplay (plays while next is synthesising)

    t0: query start time (from time.time()) for milestone logging; omit to skip.
    Returns True if completed normally, False if interrupted by button press.
    """
    sentence_q: _queue.Queue = _queue.Queue(maxsize=4)
    wav_q:      _queue.Queue = _queue.Queue(maxsize=2)
    _stop = threading.Event()  # signals threads to wind down on interrupt

    # ── Thread 1: LLM → sentences ────────────────────────────────────────────
    def llm_reader():
        buf = ""
        first_segment = True
        first_token = True
        try:
            for chunk in text_stream:
                if _stop.is_set():
                    break
                if first_token and chunk.strip():
                    if t0 is not None:
                        print(f"[first token] {time.time()-t0:.2f}s", flush=True)
                    first_token = False
                buf += chunk
                while True:
                    segment, buf = _pop_tts_segment(buf, first_segment)
                    if segment is None:
                        break
                    sentence = _MARKDOWN.sub("", segment).strip()
                    if sentence:
                        sentence_q.put(sentence)
                        first_segment = False
        finally:
            if not _stop.is_set():
                tail = _MARKDOWN.sub("", buf).strip()
                if tail:
                    sentence_q.put(tail)
            sentence_q.put(None)  # sentinel (always)

    # ── Thread 2: sentences → WAV files ──────────────────────────────────────
    def synthesiser():
        while True:
            try:
                sentence = sentence_q.get(timeout=0.5)
            except _queue.Empty:
                if _stop.is_set():
                    break
                continue
            if sentence is None:
                break
            if _stop.is_set():
                continue  # drain without synthesising
            try:
                wav_path = _synth_to_wav(sentence)
                if _stop.is_set():
                    os.unlink(wav_path)
                else:
                    wav_q.put((sentence, wav_path))
            except Exception as e:
                print(f"\n[error] TTS synth: {e}")
        # Keep retrying until sentinel lands or player already exited (_stop).
        # A timeout=1 was silently dropping the sentinel when the player was
        # mid-sentence (3-10s), leaving the player loop spinning forever.
        while True:
            try:
                wav_q.put(None, timeout=0.5)
                break
            except _queue.Full:
                if _stop.is_set():
                    break  # player already exited due to interrupt, no need

    t1 = threading.Thread(target=llm_reader,  daemon=True)
    t2 = threading.Thread(target=synthesiser, daemon=True)
    t1.start()
    t2.start()

    # ── Main: play WAV files as they arrive ──────────────────────────────────
    first = True
    interrupted = False

    while True:
        try:
            item = wav_q.get(timeout=0.1)
        except _queue.Empty:
            if interrupt and interrupt.is_set():
                interrupted = True
                _stop.set()
                break
            continue

        if item is None:
            break

        sentence, wav_path = item
        try:
            if first:
                if t0 is not None:
                    print(f"[first audio] {time.time()-t0:.2f}s", flush=True)
                print(f"AI  : {sentence}", end="", flush=True)
                first = False
            else:
                print(f" {sentence}", end="", flush=True)

            # Use Popen so we can kill it mid-play on interrupt
            proc = subprocess.Popen(
                ["aplay", "-D", config.ALSA_DEVICE, wav_path],
                stderr=subprocess.DEVNULL,
            )
            while proc.poll() is None:
                if interrupt and interrupt.is_set():
                    proc.kill()
                    proc.wait()
                    interrupted = True
                    break
                time.sleep(0.05)
            if not interrupted:
                proc.wait()
        finally:
            try:
                os.unlink(wav_path)
            except OSError:
                pass

        if interrupted:
            _stop.set()
            break

    if not first:
        print()

    # Drain remaining wav files to clean up temp files
    while True:
        try:
            item = wav_q.get_nowait()
            if item is not None:
                _, wav_path = item
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
        except _queue.Empty:
            break

    t1.join(timeout=2)
    t2.join(timeout=2)

    return not interrupted


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
    max_chars = config.BOOTSTRAP_MAX_CHARS
    soul = _trim_text(soul, max_chars)
    identity = _trim_text(identity, max_chars)
    user = _trim_text(user, max_chars)
    memory = _trim_text(memory, max_chars)

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
        decoding_method="modified_beam_search",
        max_active_paths=4,
    )

    button = Button(config.BUTTON_PIN, pull_up=True)
    led    = StatusLED(config.LED_PIN)

    history: list = []   # conversation history for multi-turn context
    user_city: str = config.USER_CITY  # for weather queries

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

                # Extract user city from USER.md for weather tool
                city_from_profile = tools.extract_city_from_user_profile(user)
                if city_from_profile:
                    user_city = city_from_profile
                    print(f"[tools] User city from USER.md: {user_city}")

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
    if config.TTS_ENGINE == "edge":
        print(f"[tts] engine=edge-tts  voice={config.TTS_VOICE}  rate={config.TTS_RATE}  vol={config.TTS_VOLUME}")
    elif config.TTS_ENGINE == "piper":
        print("[tts] Loading Piper voice …")
        tts_t0 = time.time()
        _get_piper_voice()
        print(f"[tts] Piper ready ({time.time()-tts_t0:.1f}s)")
    led.pulse_ready()

    skip_wait = False  # True when button press that interrupted TTS starts next recording

    while True:
        if not skip_wait:
            print("\n[idle] Hold button to speak …")
            led.off()
            button.wait_for_press()
        skip_wait = False

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

        # ── Tool enrichment (time / weather) ──────────────────────────────
        llm_input = tools.enrich_query(user_text, user_city)
        if llm_input != user_text:
            print(f"[tools] Injected real-time context")

        # ── LLM + TTS (streamed, sentence-by-sentence) ────────────────────
        interrupt = threading.Event()
        _tts_stop = threading.Event()

        def _watch_button():
            """Poll button state during TTS; set interrupt on press.

            Polling is more reliable than when_pressed edge detection with
            lgpio after multiple rapid interrupt/record cycles.
            """
            time.sleep(0.15)  # let button settle after recording release
            while not _tts_stop.is_set():
                if button.is_pressed:
                    interrupt.set()
                    return
                time.sleep(0.05)

        _watcher = threading.Thread(target=_watch_button, daemon=True)
        _watcher.start()
        led.blink(0.3, 0.3)
        print(f"[querying {provider} + speaking]")
        t0 = time.time()
        completed = True
        try:
            completed = speak_streaming(
                stream_llm(llm_input, history, memory_store, system_prompt, interrupt),
                interrupt,
                t0=t0,
            )
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
        finally:
            _tts_stop.set()
            _watcher.join(timeout=0.5)

        print(f"[done] {time.time()-t0:.1f}s")

        if not completed:
            # Button was pressed during playback — treat as start of new recording
            print("[interrupted] 重新录音 …")
            led.on()
            skip_wait = True
        else:
            led.off()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye.")
