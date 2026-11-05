import os
import re
import math
import time
import logging
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types

from config import IS_PRODUCTION

try:
    from zoneinfo import ZoneInfo
    _PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:
    _PACIFIC = timezone(timedelta(hours=-8))

logger = logging.getLogger("quizpup")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    if IS_PRODUCTION:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set. Refusing to start in production.")
    GEMINI_API_KEY = "missing-gemini-key-dev-only"
ai_client = genai.Client(api_key=GEMINI_API_KEY)


# Limits per model (free tier): (requests per minute, requests per day)
MODEL_LIMITS = {
    "gemini-3.8-flash": (5, 20),
    "gemini-3.7-flash": (5, 20),
    "gemini-3.6-flash": (5, 20),
    "gemini-3.5-flash-lite": (15, 500),
    "gemini-3.1-flash-lite": (15, 500),
    "gemini-2.5-flash-lite": (10, 20),
}
DEFAULT_LIMITS = (5, 20)
RPM_MARGIN = 1

INTERACTIVE_MAX_WAIT = 75
BACKGROUND_MAX_WAIT = 300
PRIMARY_PATIENCE = 15
MAX_ATTEMPTS = 8
MAX_PARALLEL = 4
LONG_SOURCE_CHARS = 15000


# Task chains: ordered (model, thinking level) candidates per kind of work
TASKS = {
    "quiz_god": [
        ("gemini-3.8-flash", "medium"),
        ("gemini-3.7-flash", "medium"),
        ("gemini-3.6-flash", "medium"),
        ("gemini-3.5-flash-lite", "high"),
        ("gemini-3.1-flash-lite", "high"),
    ],
    "quiz_hard": [
        ("gemini-3.5-flash-lite", "high"),
        ("gemini-3.1-flash-lite", "high"),
        ("gemini-3.6-flash", "low"),
    ],
    "quiz_std": [
        ("gemini-3.5-flash-lite", "low"),
        ("gemini-3.1-flash-lite", "low"),
        ("gemini-2.5-flash-lite", "none"),
    ],
    "flashcards": [
        ("gemini-3.5-flash-lite", "low"),
        ("gemini-3.1-flash-lite", "low"),
        ("gemini-2.5-flash-lite", "none"),
    ],
    "ocr": [
        ("gemini-3.5-flash-lite", "low"),
        ("gemini-3.1-flash-lite", "low"),
        ("gemini-3.6-flash", "minimal"),
    ],
    "summary": [
        ("gemini-3.5-flash-lite", "medium"),
        ("gemini-3.1-flash-lite", "medium"),
        ("gemini-3.6-flash", "low"),
    ],
}

_BUDGETS_25 = {"none": 0, "minimal": 0, "low": 1024, "medium": 4096, "high": 8192}


# Cooldown error
def format_wait(seconds):
    seconds = int(math.ceil(seconds))
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = math.ceil(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rem = divmod(minutes, 60)
    return f"{hours} hr {rem} min" if rem else f"{hours} hr"


class GeminiCooldown(Exception):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(math.ceil(retry_after)))
        super().__init__(self.message)

    @property
    def message(self):
        return f"AI limits are on cooldown. Please try again after {format_wait(self.retry_after)}."


# Limiter state
_lock = threading.Lock()
_states = {}


def _state(model):
    st = _states.get(model)
    if st is None:
        st = {
            "window": deque(),
            "day": None,
            "day_count": 0,
            "blocked_until": 0.0,
            "no_thinking": False,
        }
        _states[model] = st
    return st


def _pacific_day(now):
    return datetime.fromtimestamp(now, _PACIFIC).strftime("%Y-%m-%d")


def _seconds_to_pacific_midnight(now):
    current = datetime.fromtimestamp(now, _PACIFIC)
    next_midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (next_midnight - current).total_seconds())


def _wait_seconds(model, now):
    st = _state(model)
    rpm, rpd = MODEL_LIMITS.get(model, DEFAULT_LIMITS)
    wait = st["blocked_until"] - now

    today = _pacific_day(now)
    if st["day"] != today:
        st["day"] = today
        st["day_count"] = 0
    daily_cap = max(1, rpd - max(1, int(rpd * 0.05)))
    if st["day_count"] >= daily_cap:
        wait = max(wait, _seconds_to_pacific_midnight(now))

    window = st["window"]
    while window and now - window[0] >= 60:
        window.popleft()
    if len(window) >= max(1, rpm - RPM_MARGIN):
        wait = max(wait, window[0] + 60 - now)

    return max(0.0, wait)


def _reserve(model, now):
    st = _state(model)
    st["window"].append(now)
    st["day_count"] += 1


def _block(model, seconds):
    with _lock:
        st = _state(model)
        st["blocked_until"] = max(st["blocked_until"], time.time() + seconds)


def _block_until_reset(model):
    with _lock:
        now = time.time()
        st = _state(model)
        st["blocked_until"] = max(st["blocked_until"], now + _seconds_to_pacific_midnight(now) + 5)


def _acquire(task, deadline):
    chain = TASKS[task]
    while True:
        sleep_for = None
        with _lock:
            now = time.time()
            waits = [_wait_seconds(model, now) for model, _ in chain]
            remaining = deadline - now
            chosen = None

            if waits[0] <= 0:
                chosen = 0
            elif waits[0] <= PRIMARY_PATIENCE and waits[0] <= remaining:
                sleep_for = waits[0]
            else:
                ready = [i for i, w in enumerate(waits) if w <= 0]
                if ready:
                    chosen = ready[0]
                else:
                    soonest = min(waits)
                    if soonest > remaining:
                        raise GeminiCooldown(soonest)
                    sleep_for = soonest

            if chosen is not None:
                model, level = chain[chosen]
                _reserve(model, now)
                return model, level

        time.sleep(sleep_for + 0.2)


# Error handling
def _classify_error(exc):
    text = str(exc)
    upper = text.upper()
    code = getattr(exc, "code", None)

    if code == 429 or "RESOURCE_EXHAUSTED" in upper:
        compact = upper.replace("_", "").replace(" ", "")
        daily = "PERDAY" in compact
        retry = None
        match = re.search(r"retryDelay\W+(\d+(?:\.\d+)?)s", text) or re.search(r"retry in (\d+(?:\.\d+)?)s", text, re.I)
        if match:
            retry = float(match.group(1))
        return "quota", daily, retry

    if code in (500, 502, 503, 504) or "UNAVAILABLE" in upper or "OVERLOADED" in upper or "DEADLINE_EXCEEDED" in upper:
        return "transient", False, None

    if code == 404 or "NOT_FOUND" in upper:
        return "missing", False, None

    if code == 400 and "thinking" in text.lower():
        return "thinking", False, None

    return "fatal", False, None


def _thinking_config(model, level):
    if model.startswith("gemini-2.5"):
        return types.ThinkingConfig(thinking_budget=_BUDGETS_25.get(level, 1024))
    level = "minimal" if level == "none" else level
    return types.ThinkingConfig(thinking_level=level)


def _build_config(model, level, config):
    cfg = config.model_copy() if config is not None else types.GenerateContentConfig()
    if not model.startswith("gemini-2."):
        cfg.temperature = None
        cfg.top_p = None
        cfg.top_k = None
    if not _state(model)["no_thinking"]:
        cfg.thinking_config = _thinking_config(model, level)
    return cfg


# Public API
def quiz_task(difficulty, source_chars=0):
    if difficulty == "god mode":
        return "quiz_god"
    if difficulty == "hard" or source_chars > LONG_SOURCE_CHARS:
        return "quiz_hard"
    return "quiz_std"


def call_gemini(task, contents, config=None, max_wait=INTERACTIVE_MAX_WAIT):
    if task not in TASKS:
        raise ValueError(f"Unknown Gemini task: {task}")

    deadline = time.time() + max_wait
    last_error = None

    for _ in range(MAX_ATTEMPTS):
        model, level = _acquire(task, deadline)
        try:
            logger.info("Gemini call task=%s model=%s thinking=%s", task, model, level)
            return ai_client.models.generate_content(
                model=model,
                contents=contents,
                config=_build_config(model, level, config)
            )
        except Exception as exc:
            last_error = exc
            kind, daily, retry = _classify_error(exc)

            if kind == "quota":
                if daily:
                    _block_until_reset(model)
                else:
                    _block(model, (retry or 30) + 1)
                logger.warning("Gemini quota hit on %s (daily=%s, retry=%s)", model, daily, retry)
                continue
            if kind == "transient":
                _block(model, 8)
                logger.warning("Gemini transient error on %s: %s", model, exc)
                continue
            if kind == "missing":
                _block(model, 6 * 3600)
                logger.warning("Gemini model unavailable, skipping %s: %s", model, exc)
                continue
            if kind == "thinking":
                with _lock:
                    _state(model)["no_thinking"] = True
                logger.warning("Gemini rejected thinking config on %s, retrying without it", model)
                continue
            raise

    raise last_error