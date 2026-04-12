#!/usr/bin/env python3
"""
Web UI for AI Voice Assistant.

Endpoints:
  GET  /                          → index.html
  POST /api/chat                  → SSE: LLM streaming response
  GET  /api/logs                  → SSE: journalctl -f output
  GET  /api/history?limit&offset  → JSON: conversation history (newest first)
  GET  /api/status                → JSON: voice-assistant service status
"""

import asyncio
import json
import os
import signal as _signal
import sys
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from memory_store import MemoryStore
import tools

# ── Import LLM functions from assistant.py ────────────────────────────────────
# assistant.py registers SIGTERM/SIGINT handlers on import; save & restore them
# so uvicorn can handle signals for graceful shutdown.
_sigterm_before = _signal.getsignal(_signal.SIGTERM)
_sigint_before  = _signal.getsignal(_signal.SIGINT)
from assistant import stream_llm, build_system_prompt   # noqa: E402
_signal.signal(_signal.SIGTERM, _sigterm_before)
_signal.signal(_signal.SIGINT,  _sigint_before)
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="AI 语音助手 Web UI")

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR    = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Global: MemoryStore + system prompt (initialised once on startup)
_memory_store:  MemoryStore | None = None
_system_prompt: str | None         = None


@app.on_event("startup")
async def _startup():
    global _memory_store, _system_prompt
    if not config.MEMORY_ENABLED:
        print("[web] Memory disabled")
        return
    try:
        _memory_store = MemoryStore(config.MEMORY_DIR)
        if config.BOOTSTRAP_ENABLED:
            soul     = _memory_store.load_soul()
            identity = _memory_store.load_identity()
            user     = _memory_store.load_user()
            memory   = _memory_store.load_memory()
            _system_prompt = build_system_prompt(soul, identity, user, memory)
            print(f"[web] Bootstrap loaded ({len(_system_prompt)} chars)")
        else:
            mem = _memory_store.load_memory()
            if mem:
                _system_prompt = build_system_prompt(memory=mem)
    except Exception as e:
        print(f"[web] Memory init error: {e}")
    print(f"[web] Ready — provider: {config.LLM_PROVIDER}")


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(TEMPLATES_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


# ── Chat (SSE) ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    text: str


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Stream LLM reply as SSE.  Each event: data: {"chunk":"..."}
    Final event: data: {"done": true}
    """
    user_text = req.text.strip()
    if not user_text:
        return JSONResponse({"error": "empty input"}, status_code=400)

    # Real-time context injection (time / weather)
    user_city = config.USER_CITY
    if _memory_store:
        try:
            city = tools.extract_city_from_user_profile(_memory_store.load_user())
            if city:
                user_city = city
        except Exception:
            pass
    llm_input = tools.enrich_query(user_text, user_city)

    # Load history fresh from disk so web & voice share context
    history: list = []
    if _memory_store:
        try:
            history = _memory_store.load_history(
                max_messages=config.MAX_HISTORY_MESSAGES
            )
        except Exception:
            pass

    async def _generate():
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _run():
            print(f"[web] You : {user_text}", flush=True)
            full = ""
            try:
                for chunk in stream_llm(
                    llm_input, history, _memory_store, _system_prompt
                ):
                    full += chunk
                    loop.call_soon_threadsafe(q.put_nowait, ("chunk", chunk))
            except Exception as exc:
                print(f"[web] LLM error: {exc}", flush=True)
                loop.call_soon_threadsafe(q.put_nowait, ("error", str(exc)))
            finally:
                preview = full[:80].replace('\n', ' ')
                print(f"[web] AI  : {preview}{'…' if len(full)>80 else ''}", flush=True)
                loop.call_soon_threadsafe(q.put_nowait, ("done", None))

        threading.Thread(target=_run, daemon=True).start()

        try:
            while True:
                kind, val = await q.get()
                if kind == "chunk":
                    yield f"data: {json.dumps({'chunk': val})}\n\n"
                elif kind == "error":
                    yield f"data: {json.dumps({'error': val, 'done': True})}\n\n"
                    break
                else:  # done
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── Logs (SSE) ────────────────────────────────────────────────────────────────

@app.get("/api/logs")
async def logs():
    """Stream voice-assistant + web-assistant journal logs as SSE."""
    async def _stream():
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "-u", "voice-assistant", "-u", "web-assistant",
            "-f", "-n", "50", "--output=short-precise",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=20)
                    if not line:
                        break
                    yield (
                        f"data: {json.dumps({'line': line.decode('utf-8', errors='replace').rstrip()})}\n\n"
                    )
                except asyncio.TimeoutError:
                    # keepalive ping so browsers don't close the connection
                    yield "data: {\"ka\":1}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── History (JSON) ────────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history(limit: int = 50, offset: int = 0):
    """Return paginated history from history.jsonl, newest messages first."""
    path = os.path.join(config.MEMORY_DIR, "history.jsonl")
    if not os.path.exists(path):
        return {"messages": [], "total": 0}
    try:
        with open(path, encoding="utf-8") as f:
            raw = [l.strip() for l in f if l.strip()]
        total = len(raw)
        end   = max(0, total - offset)
        start = max(0, end - limit)
        msgs  = []
        for line in reversed(raw[start:end]):
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return {"messages": msgs, "total": total}
    except Exception as exc:
        return {"messages": [], "total": 0, "error": str(exc)}


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "voice-assistant",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        active = out.decode().strip() == "active"
    except Exception:
        active = False
    return {"voice_service": "active" if active else "inactive",
            "llm_provider": config.LLM_PROVIDER,
            "model": config.OLLAMA_MODEL}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
