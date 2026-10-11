"""Extraction precision against manually checked records.

  python scripts/eval_extraction.py eval/gold_30.json

gold_30.json is a list of hand-verified records ({"source_url": ..., "title": ..., plus the fields you checked}).
For each, the page is fetched, extracted with Sarvam, and every field in the gold record is compared:
  precision = correct extracted fields / extracted fields that the gold record also states.
There is NO gold file in this repo: the 30 records must be checked by a person against the official pages.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yojana.crawler import Crawler  # noqa: E402
from yojana.extractor import extract_page  # noqa: E402
from yojana.sarvam_client import Sarvam  # noqa: E402

FIELDS = ["type", "level", "state", "authority", "eligibility.min_age", "eligibility.max_age", "eligibility.education",
          "eligibility.income_limit", "dates.application_start", "dates.application_end", "dates.exam_date", "fee"]


def get(rec, path):
    for part in path.split("."):
        rec = (rec or {}).get(part)
    return rec


def norm(v):
    return str(v).strip().lower() if v is not None else None


def main(path):
    gold = json.loads(Path(path).read_text(encoding="utf-8"))
    crawler, sarvam = Crawler(), Sarvam()
    correct = total = 0
    for g in gold:
        doc = crawler.fetch(g["source_url"])
        if not doc.get("ok"):
            print("SKIP (fetch failed)", g["source_url"])
            continue
        src = {"url": g["source_url"], "level": g.get("level", "national"), "state": g.get("state")}
        recs, _ = extract_page(sarvam, doc["text"], src)
        match = max(recs, key=lambda r: _sim(r["title"], g["title"]), default=None)
        if not match:
            print("MISS (nothing extracted)", g["title"])
            total += sum(1 for f in FIELDS if get(g, f) is not None)
            continue
        for f in FIELDS:
            want = get(g, f)
            if want is None:
                continue
            total += 1
            ok = norm(get(match, f)) == norm(want)
            correct += ok
            if not ok:
                print(f"  wrong {f}: got {get(match, f)!r} want {want!r}  ({g['title']})")
    print(f"\nfield precision: {correct}/{total} = {correct / total:.1%}" if total else "no comparable fields")


def _sim(a, b):
    import difflib
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


if __name__ == "__main__":
    main(sys.argv[1])
