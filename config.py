"""Configuration for AI Voice Assistant - AIY Voice Kit V1"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (silently ignored if absent)
load_dotenv(Path(__file__).parent / ".env")

# ─── LLM provider: "ollama" or "deepseek" ────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")

# ─── Ollama (Mac Mini M4) ────────────────────────────────────────────────────
OLLAMA_HOST     = os.environ.get("OLLAMA_HOST",     "")
OLLAMA_MODEL    = os.environ.get("OLLAMA_MODEL",    "gemma3:4b")
OLLAMA_USERNAME = os.environ.get("OLLAMA_USERNAME", "")
OLLAMA_PASSWORD = os.environ.get("OLLAMA_PASSWORD", "")
OLLAMA_TIMEOUT  = int(os.environ.get("OLLAMA_TIMEOUT", "60"))

# ─── DeepSeek API ─────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_HOST    = os.environ.get("DEEPSEEK_HOST",    "https://api.deepseek.com")
DEEPSEEK_MODEL   = os.environ.get("DEEPSEEK_MODEL",   "deepseek-chat")
DEEPSEEK_TIMEOUT = int(os.environ.get("DEEPSEEK_TIMEOUT", "60"))

SYSTEM_PROMPT = (
    "你是树莓派上的语音助手，说话简短自然，像朋友聊天一样。"
    "必须遵守以下规则：\n"
    "1. 每次回答只说1到3句话，不要长篇大论。\n"
    "2. 绝对不用markdown格式，不用星号、井号、横线、列表或冒号引出列表。\n"
    "3. 不用任何特殊符号或格式字符。\n"
    "4. 用对话口语回答，不要书面语或说明文体。\n"
    "5. 用户说中文就回中文，说英文就回英文。\n"
    "如果问题太宽泛，简单说一句概括，再问用户想了解哪方面。"
)

# ─── Audio ───────────────────────────────────────────────────────────────────
# AIY Voice Kit V1 — card name stays stable across reboots
ALSA_CARD   = "sndrpigooglevoi"
ALSA_DEVICE = f"plughw:{ALSA_CARD},0"

SAMPLE_RATE   = 16000   # Vosk requires 16 kHz
CHANNELS      = 1
SAMPLE_FORMAT = "S16_LE"

# TTS (edge-tts — Microsoft neural voices)
TTS_VOICE  = os.environ.get("TTS_VOICE",  "zh-CN-XiaoxiaoNeural")
TTS_RATE   = os.environ.get("TTS_RATE",   "+0%")
TTS_VOLUME = os.environ.get("TTS_VOLUME", "-90%")

# ─── GPIO  (AIY Voice Kit V1) ────────────────────────────────────────────────
BUTTON_PIN = int(os.environ.get("BUTTON_PIN", "23"))
LED_PIN    = int(os.environ.get("LED_PIN",    "25"))

# ─── Vosk model ──────────────────────────────────────────────────────────────
VOSK_MODEL_DIR = os.path.join(os.path.dirname(__file__), "vosk-model")

# ─── Memory / persistence ────────────────────────────────────────────────────
from pathlib import Path

MEMORY_DIR = Path(os.environ.get(
    "MEMORY_DIR",
    os.path.join(os.path.dirname(__file__), "memory")
))
MEMORY_ENABLED = os.environ.get("MEMORY_ENABLED", "true").lower() == "true"

# History limits (Pi 3B: 1GB RAM — stay conservative)
MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "50"))
HISTORY_ROTATION_THRESHOLD = int(os.environ.get("HISTORY_ROTATION_THRESHOLD", "2000"))

# Memory update marker (LLM output trigger)
MEMORY_UPDATE_MARKER = os.environ.get("MEMORY_UPDATE_MARKER", "[UPDATE_MEMORY]")

# Bootstrap system (OpenClaw-inspired) ────────────────────────────────────────
# Enable Bootstrap injection (SOUL.md, IDENTITY.md, USER.md)
BOOTSTRAP_ENABLED = os.environ.get("BOOTSTRAP_ENABLED", "true").lower() == "true"

# Max chars per Bootstrap file (prevent context overflow)
BOOTSTRAP_MAX_CHARS = int(os.environ.get("BOOTSTRAP_MAX_CHARS", "20000"))

# Total max chars for all Bootstrap files combined
BOOTSTRAP_TOTAL_MAX_CHARS = int(os.environ.get("BOOTSTRAP_TOTAL_MAX_CHARS", "150000"))

# Bootstrap update markers (for LLM to modify Bootstrap files)
SOUL_UPDATE_MARKER = os.environ.get("SOUL_UPDATE_MARKER", "[UPDATE_SOUL]")
IDENTITY_UPDATE_MARKER = os.environ.get("IDENTITY_UPDATE_MARKER", "[UPDATE_IDENTITY]")
USER_UPDATE_MARKER = os.environ.get("USER_UPDATE_MARKER", "[UPDATE_USER]")
