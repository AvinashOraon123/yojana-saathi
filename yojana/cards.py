"""Build result cards: structured facts stay exact; only prose is translated, scheme names are transliterated."""
from concurrent.futures import ThreadPoolExecutor

from . import settings
from .search import check_eligibility, days_left, Filters

UI_STRINGS_EN = {
    "eligible": "Who can apply", "benefit": "Benefit", "how": "How to apply", "deadline": "Last date",
    "not_announced": "Not announced", "days_left": "days left", "closed": "Closed", "check": "Check eligibility",
    "listen": "Listen", "pause": "Pause", "official": "Official site", "english": "Show English original",
    "hide_english": "Hide English", "verify": "Verify on the official site", "reminder": "Add reminder",
    "scheme": "Scheme", "exam": "Exam", "scholarship": "Scholarship", "job": "Job", "national": "National",
    "state": "State", "search": "Search", "speak": "Speak your question", "placeholder": "Ask about a scheme, exam, scholarship or job",
    "all": "All", "closing_soon": "Closing soon", "profile": "Find what fits me", "no_results": "No matching entries found.",
}


def eligibility_sentence(rec):
    """Deterministic English summary of the structured eligibility, so numbers never pass through a model."""
    e = rec.get("eligibility") or {}
    bits = []
    lo, hi = e.get("min_age"), e.get("max_age")
    if lo is not None and hi is not None:
        bits.append(f"Age {lo} to {hi} years.")
    elif lo is not None:
        bits.append(f"Age {lo} years or above.")
    elif hi is not None:
        bits.append(f"Age up to {hi} years.")
    if e.get("gender") in ("male", "female"):
        bits.append("For women and girls only." if e["gender"] == "female" else "For men only.")
    if e.get("category"):
        bits.append("Categories: " + ", ".join(e["category"]) + ".")
    if e.get("education"):
        bits.append(f"Education: {e['education']}.")
    if e.get("income_limit") is not None:
        bits.append(f"Annual family income up to Rs {e['income_limit']:g}.")
    if e.get("domicile"):
        bits.append(f"Resident of {e['domicile']}.")
    if e.get("other"):
        bits.append(e["other"])
    return " ".join(bits) or "See the official notification for eligibility."


def english_card(rec, today):
    dl = days_left(rec, today)
    end = (rec.get("dates") or {}).get("application_end")
    return {
        "id": rec["id"], "type": rec["type"], "level": rec["level"], "state": rec.get("state"),
        "title": rec["title"], "authority": rec["authority"], "summary": rec.get("summary_en") or "",
        "eligibility": eligibility_sentence(rec), "benefit": rec.get("benefit") or "",
        "how_to_apply": rec.get("how_to_apply") or "", "deadline": end, "days_left": dl,
        "dates": {k: v for k, v in (rec.get("dates") or {}).items() if v},
        "fee": rec.get("fee"), "official_url": rec["official_url"], "source_url": rec.get("source_url"),
        "last_verified_at": rec.get("last_verified_at"),
        "needs_verification": bool(rec.get("needs_verification")) or (rec.get("confidence") or 0) < 0.6,
        "demo_sample": bool(rec.get("demo_sample")),
    }


def localise_card(sarvam, card, lang):
    """Return {title, summary, eligibility, benefit, how_to_apply} in `lang`. Dates, amounts, URLs stay in `card`."""
    if sarvam is None or lang == "en-IN":
        return None
    out = {"title": sarvam.transliterate(card["title"], "en-IN", lang) if lang in _TRANSLIT else card["title"]}
    for f in ("summary", "eligibility", "benefit", "how_to_apply"):
        out[f] = sarvam.translate(card[f], "en-IN", lang) if card[f] else ""
    return out


_TRANSLIT = {"hi-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}


def build_cards(sarvam, records, lang, today):
    cards = [english_card(r, today) for r in records]
    if sarvam is not None and lang != "en-IN" and cards:
        with ThreadPoolExecutor(max_workers=5) as ex:
            locals_ = list(ex.map(lambda c: _safe_localise(sarvam, c, lang), cards))
        for c, loc in zip(cards, locals_):
            c["local"] = loc
    return cards


def _safe_localise(sarvam, card, lang):
    try:
        return localise_card(sarvam, card, lang)
    except Exception:  # a translation failure must not hide the card; the English text is still shown
        return None


def ui_strings(sarvam, lang):
    if sarvam is None or lang == "en-IN":
        return UI_STRINGS_EN
    def tr(item):
        k, v = item
        try:
            return k, sarvam.translate(v, "en-IN", lang)
        except Exception:
            return k, v
    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(tr, UI_STRINGS_EN.items()))


def eligibility_check(rec, profile):
    f = Filters(age=_num(profile.get("age"), int), category=profile.get("category") or None, gender=profile.get("gender") or None,
                education=profile.get("education") or None, income=_num(profile.get("income"), float), state=profile.get("state") or None)
    return check_eligibility(rec, f)


def _num(v, cast):
    try:
        return cast(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
