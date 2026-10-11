"""Runtime settings. The Sarvam key is read from the server environment only."""
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

PLACEHOLDER_KEYS = {"", "your_sarvam_api_key_here"}

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1" or SARVAM_API_KEY in PLACEHOLDER_KEYS
CHAT_MODEL = os.getenv("SARVAM_CHAT_MODEL", "sarvam-105b")
REASONING_EFFORT = os.getenv("SARVAM_REASONING_EFFORT", "low")
COST_PER_1K_TOKENS = float(os.getenv("COST_PER_1K_TOKENS", "0") or 0)
# Second model call that rewrites the draft answer. Slower (~10 s); the code-level claim check always runs.
VERIFY_WITH_LLM = os.getenv("VERIFY_WITH_LLM", "0") == "1"
ENABLE_SCHEDULER =os.getenv("ENABLE_SCHEDULER", "0") == "1"
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))

DB_PATH = Path(os.getenv("YOJANA_DB", ROOT / "data" / "yojana.db"))
CACHE_DIR = Path(os.getenv("YOJANA_CACHE", ROOT / "data" / "cache"))
SEED_PATH = ROOT / "seed" / "demo_records.json"
SOURCES_PATH = ROOT / "sources.yaml"
SCHEMA_PATH = ROOT / "yojana" / "record.schema.json"

USER_AGENT = "YojanaSaathiBot/1.0 (+official-pages-only; contact: set-in-README)"
REQUEST_TIMEOUT = 20
CRAWL_DELAY_SECONDS = 2.0
PAGE_CACHE_TTL_HOURS = 6
REVERIFY_WINDOW_DAYS = 14

# UI language -> Sarvam language code
LANGUAGES = {
    "English": "en-IN", "Hindi": "hi-IN", "Bengali": "bn-IN", "Tamil": "ta-IN",
    "Telugu": "te-IN", "Kannada": "kn-IN", "Malayalam": "ml-IN", "Marathi": "mr-IN",
    "Gujarati": "gu-IN", "Punjabi": "pa-IN", "Odia": "od-IN",
}


def today() -> date:
    """The real current date. Injected into every prompt and freshness check."""
    return date.today()
