"""Offline golden test: the 50 questions' English glosses must surface the expected record in the top 3.
(The native-language versions are exercised by tests/live_eval.py against the real Sarvam API.)"""
import json
from pathlib import Path

import pytest

from yojana import qa, settings
from yojana.search import search

GOLDEN = json.loads((Path(__file__).parent / "golden.json").read_text(encoding="utf-8"))


def test_golden_set_shape():
    assert len(GOLDEN) == 50
    assert {g["lang"] for g in GOLDEN} == {"hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN"}


@pytest.mark.parametrize("item", GOLDEN[:10], ids=lambda g: g["en"][:40])
def test_expected_record_in_top_3(store, item):
    prep = qa.prepare(store, None, item["en"], "en-IN")
    got = [r["title"] for r in prep["records"][:3]]
    assert item["expect"] in got, f"{item['en']!r} -> {got}"
