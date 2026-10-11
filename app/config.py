"""Settings, read once from the environment (.env is loaded if present)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


NIMBLE_API_KEY = os.getenv("NIMBLE_API_KEY", "").strip()
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()

# "gemini-flash-latest" is Google's alias for the current Flash model. The
# fallbacks are tried in order if the first name is rejected.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-3.8-flash,gemini-3.6-flash").split(",")
    if m.strip()
]

# Nimble Web Search Agent effort: low (10-30 s), medium (1-3 min), high (5-15 min).
AGENT_EFFORT = os.getenv("NIBBLE_AGENT_EFFORT", "low").strip()

# Retail sources. Each entry is a Nimble Extract Template that returns search
# results for a keyword. Edit the list to add stores; run scripts/preflight.py
# to see which templates your account can use.
STORE_TEMPLATES = [
    t.strip()
    for t in os.getenv("NIBBLE_STORE_TEMPLATES", "walmart_serp,heb_serp").split(",")
    if t.strip()
]

# "async" submits a task and polls it (reliable for slow retail pages);
# "sync" uses the realtime endpoint.
TEMPLATE_MODE = os.getenv("NIBBLE_TEMPLATE_MODE", "async").strip().lower()
TEMPLATE_TIMEOUT_S = int(os.getenv("NIBBLE_TEMPLATE_TIMEOUT_S", "60"))
# Ask Nimble to switch the store to the shopper's ZIP before pricing. Off by
# default: the localized Walmart run stayed pending past 75 s in testing, while
# the plain run finished in about 18 s. Each price shows the store it came from.
LOCALIZE = _flag("NIBBLE_LOCALIZE")
OFFER_CACHE_S = int(os.getenv("NIBBLE_OFFER_CACHE_S", "21600"))

# A small model run on this machine with Ollama. When set it is tried first
# and Gemini becomes the fallback. Example: NIBBLE_LOCAL_MODEL=gemma3:4b
LOCAL_MODEL = os.getenv("NIBBLE_LOCAL_MODEL", "").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
LOCAL_VISION = _flag("NIBBLE_LOCAL_VISION", "1")  # set 0 if the local model cannot read images
LOCAL_TIMEOUT_S = int(os.getenv("NIBBLE_LOCAL_TIMEOUT_S", "25"))

# A hosted model behind an OpenAI-compatible API (Groq by default; Cerebras,
# OpenRouter and others work by changing the base URL and model). When a key is
# set this is tried first, then the local model, then Gemini.
LLM_API_KEY = (os.getenv("NIBBLE_LLM_API_KEY") or os.getenv("GROQ_API_KEY") or "").strip()
LLM_BASE_URL = os.getenv("NIBBLE_LLM_BASE_URL", "https://api.groq.com/openai/v1").strip().rstrip("/")
LLM_MODEL = os.getenv("NIBBLE_LLM_MODEL", "openai/gpt-oss-120b").strip()
LLM_VISION = _flag("NIBBLE_LLM_VISION")  # set 1 only if that model reads images
LLM_TIMEOUT_S = int(os.getenv("NIBBLE_LLM_TIMEOUT_S", "30"))

FORCE_DEMO = _flag("NIBBLE_DEMO")
SEND_PROMPTS_TO_SENTRY = _flag("SENTRY_SEND_PROMPTS", "1")
ENVIRONMENT = os.getenv("NIBBLE_ENV", "hackathon")

DATA_DIR = ROOT / ".nibble"
DATA_DIR.mkdir(exist_ok=True)

NIMBLE_LIVE = bool(NIMBLE_API_KEY) and not FORCE_DEMO
GEMINI_LIVE = bool(GEMINI_API_KEY) and not FORCE_DEMO
LOCAL_LIVE = bool(LOCAL_MODEL) and not FORCE_DEMO
HOSTED_LIVE = bool(LLM_API_KEY) and not FORCE_DEMO


def llm_live() -> bool:
    """True when some model (hosted, local or Gemini) is configured."""
    return GEMINI_LIVE or LOCAL_LIVE or HOSTED_LIVE
SENTRY_LIVE = bool(SENTRY_DSN)


def status() -> dict:
    return {
        "nimble": "live" if NIMBLE_LIVE else "sample",
        "gemini": "live" if GEMINI_LIVE else "off",
        "local": LOCAL_MODEL if LOCAL_LIVE else "off",
        "hosted": LLM_MODEL if HOSTED_LIVE else "off",
        "sentry": "live" if SENTRY_LIVE else "off",
        "model": GEMINI_MODEL,
        "agent_effort": AGENT_EFFORT,
        "stores": STORE_TEMPLATES,
    }
