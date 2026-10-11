import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yojana import qa, settings  # noqa: E402
from yojana.store import Store, load_demo_data  # noqa: E402


PHRASES = {"मैं किसान हूँ": "I am a farmer"}


class FakeSarvam:
    """Deterministic stand-in for yojana.sarvam_client.Sarvam. Records every call so tests can assert on them."""

    def __init__(self, chat_replies=None):
        self.calls = []
        self.chat_replies = chat_replies or {}

    def chat(self, system, user, temperature=0.1, label="chat", reasoning_effort=None):
        self.calls.append(("chat", label, system, user))
        if label in self.chat_replies:
            reply = self.chat_replies[label]
            return reply.pop(0) if isinstance(reply, list) else reply
        if label == "slots":
            return json.dumps(qa.heuristic_slots(user))
        if label == "answer":
            ctx = json.loads(user.split("CONTEXT:\n", 1)[1].split("\n\nQUESTION:")[0])
            return "You may look at " + ", ".join(r["title"] for r in ctx[:3]) + ". Please confirm all details on the official site before applying."
        if label == "verify":
            return user.split("DRAFT:\n", 1)[1]
        return "{}"

    def chat_stream(self, system, user, temperature=0.2, label="answer-stream", reasoning_effort=None):
        self.calls.append(("chat", "answer-stream", system, user))
        if "answer-stream" in self.chat_replies:
            text = self.chat_replies["answer-stream"]
        else:
            ctx = json.loads(user.split("CONTEXT:\n", 1)[1].split("\n\nQUESTION:")[0])
            text = "You may look at " + ", ".join(r["title"] for r in ctx[:3]) + ". They are described in the records."
        for i in range(0, len(text), 5):
            yield text[i:i + 5]

    def detect_language(self, text):
        self.calls.append(("detect", text))
        return "hi-IN" if any("ऀ" <= ch <= "ॿ" for ch in text) else "en-IN"

    def translate(self, text, src, tgt):
        self.calls.append(("translate", text, src, tgt))
        if src == tgt:
            return text
        if tgt == "en-IN":  # tiny phrase table so Hindi input becomes searchable English
            return PHRASES.get(text, text)
        return f"[{tgt}] {text}"

    def transliterate(self, text, src, tgt):
        return f"[{tgt}] {text}"

    def stt(self, data, filename="a.webm"):
        return "farmer scheme", "en-IN"

    def tts(self, text, lang):
        return "UklGRg=="

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind or (c[0] == "chat" and c[1] == kind))


def dated(title, days, **kw):
    """A record whose application_end is `days` from today (negative = already closed)."""
    end = (settings.today() + timedelta(days=days)).isoformat()
    rec = {"type": "scholarship", "title": title, "level": "national", "state": None, "authority": "Test Authority",
           "summary_en": f"{title} test scholarship record", "eligibility": {}, "target_groups": ["student"],
           "benefit": "Test benefit", "how_to_apply": "Apply online", "dates": {"application_end": end},
           "official_url": "https://example.gov.in", "source_url": "https://example.gov.in", "confidence": 0.9}
    rec.update(kw)
    return rec


@pytest.fixture
def store():
    s = Store(":memory:")
    load_demo_data(s)
    return s


@pytest.fixture
def dated_store():
    s = Store(":memory:")
    s.upsert(dated("Open Merit Scholarship", 30))
    s.upsert(dated("Closing Soon Fellowship", 5))
    s.upsert(dated("Expired Grant Scholarship", -1))
    s.upsert(dated("Evergreen Merit Scholarship", 0, dates={}))
    return s


@pytest.fixture
def fake():
    return FakeSarvam()
