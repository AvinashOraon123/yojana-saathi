import json
from datetime import timedelta

from yojana import settings
from yojana.extractor import extract_page, keep_open
from yojana.store import Store
from tests.conftest import FakeSarvam

SOURCE = {"url": "https://example.gov.in/notice", "level": "national", "state": None}


def good(**kw):
    rec = {"type": "scheme", "title": "Test Kisan Yojana", "level": "national", "state": None, "authority": "Dept of Tests",
           "summary_en": "A test scheme.", "official_url": "https://example.gov.in/yojana", "confidence": 0.9,
           "dates": {"application_end": (settings.today() + timedelta(days=20)).isoformat()}}
    rec.update(kw)
    return rec


def reply(*recs):
    return json.dumps({"records": list(recs)})


def test_valid_output_becomes_records_with_provenance():
    fake = FakeSarvam({"extract": reply(good())})
    recs, rejected = extract_page(fake, "page text", SOURCE)
    assert len(recs) == 1 and not rejected
    r = recs[0]
    assert r["source_url"] == SOURCE["url"] and r["fetched_at"] and r["last_verified_at"] and r["id"]


def test_prompt_contains_todays_date_and_no_guessing_rule():
    fake = FakeSarvam({"extract": reply(good())})
    extract_page(fake, "page text", SOURCE)
    system = next(c[2] for c in fake.calls if c[1] == "extract")
    assert settings.today().isoformat() in system
    assert "Never guess dates" in system and "Output ONLY valid JSON" in system


def test_invalid_then_valid_retries_once_with_error_appended():
    bad = reply(good(type="not-a-type"))
    fake = FakeSarvam({"extract": [bad], "extract-retry": [reply(good())]})
    recs, rejected = extract_page(fake, "page text", SOURCE)
    assert len(recs) == 1
    retry_user = next(c[3] for c in fake.calls if c[1] == "extract-retry")
    assert "invalid" in retry_user and "type" in retry_user


def test_invalid_twice_is_discarded():
    bad = reply(good(type="nope"))
    fake = FakeSarvam({"extract": [bad], "extract-retry": [bad]})
    recs, rejected = extract_page(fake, "page text", SOURCE)
    assert recs == [] and rejected and rejected[0]["url"] == SOURCE["url"]


def test_non_json_output_is_discarded():
    fake = FakeSarvam({"extract": ["I think this page has a scheme."], "extract-retry": ["Still no JSON, sorry."]})
    recs, rejected = extract_page(fake, "page text", SOURCE)
    assert recs == [] and rejected


def test_bad_date_format_is_rejected():
    fake = FakeSarvam({"extract": [reply(good(dates={"application_end": "31 March 2030"}))],
                       "extract-retry": [reply(good(dates={"application_end": "31 March 2030"}))]})
    recs, _ = extract_page(fake, "x", SOURCE)
    assert recs == []


def test_expired_records_are_dropped():
    past = good(dates={"application_end": (settings.today() - timedelta(days=1)).isoformat()})
    assert keep_open(past) is False
    assert keep_open(good()) is True
    assert keep_open(good(dates={})) is True


def test_low_confidence_is_flagged_for_verification():
    fake = FakeSarvam({"extract": reply(good(confidence=0.4), good(title="Another Test Yojana", confidence=0.9))})
    recs, _ = extract_page(fake, "x", SOURCE)
    flags = {r["title"]: r["needs_verification"] for r in recs}
    assert flags == {"Test Kisan Yojana": True, "Another Test Yojana": False}


def test_state_source_forces_state_level():
    src = {"url": "https://state.gov.in/x", "level": "state", "state": "Bihar"}
    fake = FakeSarvam({"extract": reply(good(level="national"))})
    recs, _ = extract_page(fake, "x", src)
    assert recs[0]["level"] == "state" and recs[0]["state"] == "Bihar"


def test_dedup_by_title_authority_year():
    s = Store(":memory:")
    fake = FakeSarvam({"extract": [reply(good()), reply(good(summary_en="Updated wording."))]})
    for _ in range(2):
        recs, _ = extract_page(fake, "x", SOURCE)
        s.upsert(recs[0])
    assert s.count() == 1
    assert s.all()[0]["summary_en"] == "Updated wording."
