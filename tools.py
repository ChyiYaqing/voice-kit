"""Real-time tool functions for the voice assistant.

Provides time and weather data injected into LLM context
before queries — no second LLM call needed.
"""

import datetime
import re

import requests

import config


# ─── Time ─────────────────────────────────────────────────────────────────────

def get_current_time() -> str:
    """Return current local time as a human-readable string."""
    now = datetime.datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays[now.weekday()]
    return now.strftime(f"%Y年%m月%d日 {weekday} %H:%M:%S")


# ─── Weather ──────────────────────────────────────────────────────────────────

def get_weather(city: str) -> str:
    """Fetch current weather via wttr.in (no API key required).

    Args:
        city: City name in Chinese or English (e.g. "上海" or "Shanghai")

    Returns:
        One-line weather summary, or error message on failure.
    """
    try:
        resp = requests.get(
            f"https://wttr.in/{city}",
            params={"format": "3", "lang": "zh"},
            timeout=config.WEATHER_TIMEOUT,
            headers={"User-Agent": "curl/7.74.0"},  # wttr.in requires a UA
        )
        resp.raise_for_status()
        return resp.text.strip()
    except requests.exceptions.Timeout:
        return f"天气查询超时（{city}）"
    except Exception as e:
        return f"无法获取{city}天气"


# ─── Intent detection ─────────────────────────────────────────────────────────

_TIME_KEYWORDS = [
    "几点", "时间", "现在多少点", "几时", "what time", "current time",
    "now", "今天几号", "今天是", "几月几号", "星期几", "周几",
]

_WEATHER_KEYWORDS = [
    "天气", "weather", "下雨", "下雪", "温度", "气温", "冷不冷", "热不热",
    "需要带伞", "穿什么", "风力", "湿度", "晴", "阴", "cloudy", "rain", "snow",
]


def needs_time(text: str) -> bool:
    text_lower = text.lower()
    return any(k in text_lower for k in _TIME_KEYWORDS)


def needs_weather(text: str) -> bool:
    text_lower = text.lower()
    return any(k in text_lower for k in _WEATHER_KEYWORDS)


# ─── Context injection ────────────────────────────────────────────────────────

def enrich_query(user_text: str, user_city: str = "") -> str:
    """Detect if query needs real-time data; inject context if so.

    Returns the original text unchanged if no tool is triggered.
    Otherwise prepends [实时数据] block so the LLM has accurate facts.

    Args:
        user_text: Raw transcribed user query
        user_city: User's city (from USER.md or config.USER_CITY)

    Returns:
        Enriched text with injected real-time data, or original text.
    """
    context_lines = []

    if needs_time(user_text):
        context_lines.append(f"当前时间: {get_current_time()}")

    if needs_weather(user_text):
        city = user_city or config.USER_CITY
        if city:
            weather = get_weather(city)
            context_lines.append(f"天气信息: {weather}")
        else:
            context_lines.append("天气信息: 未配置城市，无法查询天气")

    if not context_lines:
        return user_text  # no tools triggered

    context_block = "[实时数据]\n" + "\n".join(context_lines)
    return f"{context_block}\n\n用户问题: {user_text}"


# ─── Extract city from USER.md ────────────────────────────────────────────────

def extract_city_from_user_profile(user_md: str) -> str:
    """Parse USER.md content to find user's city.

    Looks for lines like:
      - 城市: 上海
      - City: Shanghai
      - 所在城市: 北京

    Returns empty string if not found.
    """
    if not user_md:
        return ""
    for line in user_md.splitlines():
        m = re.search(r"(?:城市|city|所在城市|location)\s*[:：]\s*(\S+)", line, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""
