#!/usr/bin/env python3
"""
AI Voice Assistant — AIY Voice Kit V1 + Ollama (Mac Mini M4)

Flow: hold button → record → Vosk STT → Ollama LLM → espeak-ng TTS → play
LED:  idle=off  recording=on  processing=fast-blink  speaking=slow-blink
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

import edge_tts
import requests
from gpiozero import Button, PWMLED
from vosk import KaldiRecognizer, Model

import config


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

def record_and_transcribe(button: Button, model: Model) -> str:
    """
    Record audio while button is held AND transcribe concurrently.
    arecord pipes raw PCM to stdout; Vosk processes each chunk as it arrives.
    By the time the button is released, most audio is already recognised.
    No temporary WAV file needed.
    """
    rec = KaldiRecognizer(model, config.SAMPLE_RATE)
    rec.SetWords(False)

    cmd = [
        "arecord",
        "-D", config.ALSA_DEVICE,
        "-f", config.SAMPLE_FORMAT,
        "-r", str(config.SAMPLE_RATE),
        "-c", str(config.CHANNELS),
        "-",   # raw PCM to stdout (no WAV header)
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    # Stopper thread: wait for button release, then terminate arecord
    def _stopper():
        button.wait_for_release()
        proc.terminate()

    t = threading.Thread(target=_stopper, daemon=True)
    t.start()

    # Main: read PCM chunks and feed to Vosk while recording
    while True:
        data = proc.stdout.read(8000)   # blocks until data or EOF after terminate()
        if not data:
            break
        rec.AcceptWaveform(data)

    proc.wait()
    t.join()

    result = json.loads(rec.FinalResult())
    return result.get("text", "").strip()


# ─── LLM ─────────────────────────────────────────────────────────────────────

# Sentence boundary: Chinese/English punctuation + Chinese comma for long segments
_SENTENCE_END = re.compile(r'[。！？!?]+|(?<=[^0-9])\.(?=\s|$)|，(?=.{20,})')

# Markdown patterns to strip before TTS
_MARKDOWN = re.compile(r'[*#`_~>]|\[|\]|\(|\)')

def _stream_ollama(history: list):
    """Stream from Ollama; yield text chunks."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": config.SYSTEM_PROMPT}] + history,
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


def _stream_deepseek(history: list):
    """Stream from DeepSeek (OpenAI-compatible SSE); yield text chunks."""
    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": config.SYSTEM_PROMPT}] + history,
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


def stream_llm(user_text: str, history: list):
    """Dispatch to the configured LLM provider; yield chunks; update history."""
    history.append({"role": "user", "content": user_text})

    provider = config.LLM_PROVIDER.lower()
    if provider == "deepseek":
        gen = _stream_deepseek(history)
    else:
        gen = _stream_ollama(history)

    full_text = ""
    for chunk in gen:
        full_text += chunk
        yield chunk

    history.append({"role": "assistant", "content": full_text})


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


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("Loading Vosk model …")
    if not os.path.isdir(config.VOSK_MODEL_DIR):
        print(f"ERROR: Vosk model not found at {config.VOSK_MODEL_DIR}")
        print("Run setup.sh first.")
        sys.exit(1)
    vosk_model = Model(config.VOSK_MODEL_DIR)

    button = Button(config.BUTTON_PIN, pull_up=True)
    led    = StatusLED(config.LED_PIN)

    history: list = []   # conversation history for multi-turn context

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
        user_text = record_and_transcribe(button, vosk_model)
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
            speak_streaming(stream_llm(user_text, history))
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
