"""Turn official page text into validated records. The model may only copy what the text states."""
import json
import logging
from datetime import datetime, timezone

from jsonschema import Draft7Validator

from . import settings
from .sarvam_client import parse_json_loose
from .store import make_id

log = logging.getLogger("yojana.extractor")

MAX_PAGE_CHARS = 14000
LOW_CONFIDENCE = 0.6

SYSTEM = (
    "You extract structured data from official Indian government pages. Output ONLY valid JSON matching the "
    "schema. Use ONLY information present in the text. If a field is not stated, use null. Never guess dates, "
    "amounts, or eligibility. Dates must be ISO 8601 (YYYY-MM-DD). Today's date is {today}.\n"
    "The page may describe zero, one or several distinct schemes, exams, scholarships or jobs. "
    'Return {{"records": [ ... ]}} with one object per item, or {{"records": []}} if the page has none.\n'
    "Fields per record: type (scheme|exam|scholarship|job), title, level (national|state), state (or null), "
    "authority, summary_en (plain language, max 60 words), eligibility {{min_age, max_age, gender "
    "(male|female|any), category [General|OBC|SC|ST|EWS|PwD|Minority], education, income_limit (annual INR "
    "number), occupation [], domicile, other}}, target_groups [student|youth|job_seeker|woman|farmer|worker|"
    "entrepreneur|senior|disabled|minority|child], benefit, how_to_apply, documents_required [], dates "
    "{{notification, application_start, application_end, exam_date, result_date}}, fee, official_url, "
    "confidence (0-1: how completely the text supports this record)."
)

_validator = Draft7Validator(json.loads(settings.SCHEMA_PATH.read_text(encoding="utf-8")))


def validation_errors(rec):
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in _validator.iter_errors(rec)]


def extract_page(sarvam, text, source, today=None):
    """Return (records, rejected). `source` carries level/state/url/type_hint from sources.yaml."""
    today = today or settings.today()
    user = (f"Source page URL: {source['url']}\nExpected level: {source['level']}"
            + (f", state: {source['state']}" if source.get("state") else "")
            + f"\n\nPAGE TEXT:\n{text[:MAX_PAGE_CHARS]}")
    system = SYSTEM.format(today=today.isoformat())
    raw = sarvam.chat(system, user, temperature=0.1, label="extract")
    parsed, errors = _parse_and_validate(raw)
    if errors:  # one retry with the validation error appended, then discard
        retry_user = user + "\n\nYour previous output was invalid:\n" + "\n".join(errors[:8]) + "\nReturn corrected JSON only."
        raw = sarvam.chat(system, retry_user, temperature=0.1, label="extract-retry")
        parsed, errors = _parse_and_validate(raw)
    if errors:
        log.warning("discarding %s: %s", source["url"], errors[:3])
        return [], [{"url": source["url"], "errors": errors[:5]}]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [finalise(r, source, now) for r in parsed], []


def _parse_and_validate(raw):
    try:
        data = parse_json_loose(raw)
    except ValueError as e:
        return [], [str(e)]
    recs = data.get("records") if isinstance(data, dict) else data
    if not isinstance(recs, list):
        return [], ["top level must be {\"records\": [...]}"]
    errors = []
    for i, r in enumerate(recs):
        r.setdefault("official_url", None)
        for e in validation_errors(r):
            errors.append(f"records[{i}]/{e}")
    return ([] if errors else recs), errors


def finalise(rec, source, fetched_at):
    rec = dict(rec)
    rec["source_url"] = source["url"]
    rec["fetched_at"] = fetched_at
    rec["last_verified_at"] = fetched_at
    rec["level"] = source["level"] if source["level"] == "state" else rec.get("level", "national")
    if rec["level"] == "state":
        rec["state"] = source.get("state") or rec.get("state")
    # An official_url must be on the page's own site or already official; fall back to the source page.
    rec["official_url"] = rec.get("official_url") or source["url"]
    rec["confidence"] = rec.get("confidence") if rec.get("confidence") is not None else 0.5
    rec["needs_verification"] = rec["confidence"] < LOW_CONFIDENCE
    rec["id"] = make_id(rec)
    return rec


def keep_open(rec, today=None):
    """Drop anything whose application window already closed."""
    today = (today or settings.today()).isoformat()
    end = (rec.get("dates") or {}).get("application_end")
    return not (end and end < today)
