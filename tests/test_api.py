import json

import pytest
from fastapi.testclient import TestClient

from yojana import settings
from yojana.api import create_app
from yojana.store import Store, load_demo_data
from tests.conftest import FakeSarvam


def make_client(sarvam=None, demo=True):
    shared = Store(":memory:")
    load_demo_data(shared)
    return TestClient(create_app(store_factory=lambda: shared, sarvam=sarvam, demo=demo))


def stream(client, body):
    r = client.post("/api/ask", json=body)
    assert r.status_code == 200
    return [json.loads(l) for l in r.text.splitlines() if l]


def test_config_and_index():
    c = make_client()
    cfg = c.get("/api/config").json()
    assert cfg["demo_mode"] and cfg["sample_data"] and "Hindi" in cfg["languages"] and cfg["today"] == settings.today().isoformat()
    assert c.get("/").status_code == 200


def test_search_filters_by_chips_and_profile():
    c = make_client()
    r = c.post("/api/search", json={"q": "pension", "profile": {"age": 30}, "chips": {"type": "scheme"}}).json()
    titles = [x["title"] for x in r["cards"]]
    assert "Atal Pension Yojana" in titles and "Indira Gandhi National Old Age Pension Scheme" not in titles


def test_search_card_has_exact_structured_fields():
    c = make_client()
    card = c.post("/api/search", json={"q": "Atal Pension"}).json()["cards"][0]
    assert card["official_url"].startswith("https://") and "18 to 40" in card["eligibility"]


def test_ask_streams_results_then_text_then_done():
    events = stream(make_client(), {"q": "I am a farmer", "language": "English"})
    kinds = [e["event"] for e in events]
    assert kinds[0] == "results" and kinds[-1] == "done" and "text" in kinds
    assert events[0]["cards"][0]["title"] == "PM-KISAN"


def test_ask_unknown_scheme_reports_not_found():
    events = stream(make_client(), {"q": "Tell me about the Xyzzy Bharat Yojana", "language": "English"})
    assert events[0]["cards"] == [] and events[-1]["kind"] == "not_found"


def test_ask_in_hindi_translates_answer_and_cards():
    fake = FakeSarvam()
    events = stream(make_client(fake, demo=False), {"q": "मैं किसान हूँ", "language": "Hindi"})
    assert events[0]["cards"][0]["local"]["summary"].startswith("[hi-IN]")
    assert any(e["event"] == "text" and e["text"].startswith("[hi-IN]") for e in events)
    assert any(e["event"] == "english" for e in events)


def test_eligibility_endpoint():
    c = make_client()
    rid = c.post("/api/search", json={"q": "Atal Pension"}).json()["cards"][0]["id"]
    ok = c.post("/api/eligibility", json={"record_id": rid, "profile": {"age": 30}}).json()["checks"]
    bad = c.post("/api/eligibility", json={"record_id": rid, "profile": {"age": 55}}).json()["checks"]
    assert ok[0]["status"] == "ok" and bad[0]["status"] == "fail"
    assert c.post("/api/eligibility", json={"record_id": "nope", "profile": {}}).status_code == 404


def test_input_validation():
    c = make_client()
    assert c.post("/api/ask", json={"q": "x" * 600}).status_code == 422
    assert c.post("/api/search", json={"profile": {"age": 500}}).status_code == 422
    assert c.post("/api/search", json={"chips": {"type": "bogus"}}).status_code == 422


def test_voice_endpoints_need_a_key_in_demo_mode():
    c = make_client()
    assert c.post("/api/tts", json={"text": "hi"}).status_code == 503
    assert c.post("/api/stt", files={"audio": ("a.webm", b"123")}).status_code == 503


def test_stt_and_tts_with_service():
    c = make_client(FakeSarvam(), demo=False)
    assert c.post("/api/stt", files={"audio": ("a.webm", b"123")}).json()["transcript"] == "farmer scheme"
    assert c.post("/api/tts", json={"text": "hi", "language": "Hindi"}).json()["audio"]
    assert c.post("/api/stt", files={"audio": ("a.webm", b"x" * (5 * 1024 * 1024 + 5))}).status_code == 413


def test_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MIN", 3)
    c = make_client()
    codes = [c.post("/api/search", json={"q": "pension"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]


def test_sources_status():
    body = make_client().get("/api/sources/status").json()
    assert body["records"] >= 20 and body["demo_sample"] == body["records"]


def test_api_key_is_never_exposed():
    c = make_client()
    blob = c.get("/api/config").text + c.get("/").text + c.get("/static/app.js").text
    assert "SARVAM_API_KEY" not in blob and "api-subscription-key" not in blob.lower()
