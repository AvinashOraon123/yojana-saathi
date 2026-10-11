"""Deterministic search. Filtering is plain code; the LLM never decides who is eligible."""
from dataclasses import dataclass, field
import re
from datetime import date

CLOSING_SOON_DAYS = 14

STOPWORDS = set("""a an the i am is are was be to for of and or in on at by with from me my we our you your what which who
whom how can could should would will do does did want need get give tell show find about any some there this that these those
please help looking look know like also not no yes if it its as into than then so such very more most other over under""".split())
GENERIC = set("scheme schemes yojana exam exams scholarship scholarships job jobs apply application government india indian sarkari".split())


def query_terms(q):
    return [t for t in re.findall(r"\w+", q.lower()) if t not in STOPWORDS and len(t) > 1]

# Ordinal education scale so "needs Class 12" can be compared with "I have a graduate degree".
EDU_LEVELS = [
    ("postgraduate", 7, ("postgraduate", "post graduate", "post-graduate", "master", "m.tech", "mtech", "m.sc", "mba", "pg ")),
    ("graduate", 6, ("graduate", "graduation", "bachelor", "degree", "b.tech", "btech", "b.sc", "b.a", "b.com", "mbbs", "engineering degree")),
    ("diploma", 5, ("diploma", "polytechnic", "iti")),
    ("class12", 4, ("class 12", "12th", "intermediate", "higher secondary", "xii", "plus two", "10+2", "senior secondary")),
    ("class10", 3, ("class 10", "10th", "matric", "secondary", "x pass", "ssc pass")),
    ("class8", 2, ("class 8", "8th", "middle")),
    ("none", 0, ()),
]
EDU_RANK = {name: rank for name, rank, _ in EDU_LEVELS}


def education_rank(text):
    """Rank a free-text education requirement or answer. None when it can't be determined."""
    if not text:
        return None
    t = text.lower()
    for _, rank, keys in EDU_LEVELS:
        if any(k in t for k in keys):
            return rank
    return None


@dataclass
class Filters:
    q: str = ""
    type: str | None = None            # scheme | exam | scholarship | job
    level: str | None = None           # national | state
    state: str | None = None
    target_group: str | None = None
    age: int | None = None
    category: str | None = None        # General | OBC | SC | ST | EWS | PwD | Minority
    gender: str | None = None          # male | female
    education: str | None = None
    income: float | None = None        # annual, INR
    closing_soon: bool = False
    include_closed: bool = False
    limit: int = 10
    # profile-derived extras the UI may send (all optional)
    occupation: str | None = None
    extras: dict = field(default_factory=dict)


def days_left(rec, today):
    end = (rec.get("dates") or {}).get("application_end")
    if not end:
        return None
    return (date.fromisoformat(end) - today).days


def is_open(rec, today):
    d = days_left(rec, today)
    return d is None or d >= 0


def check_eligibility(rec, f):
    """Return a list of (criterion, status, detail). status: ok | fail | unknown. Used by filters and the UI."""
    e = rec.get("eligibility") or {}
    out = []

    def add(name, status, detail):
        out.append({"criterion": name, "status": status, "detail": detail})

    lo, hi = e.get("min_age"), e.get("max_age")
    if lo is not None or hi is not None:
        rng = f"{lo if lo is not None else '-'} to {hi if hi is not None else '-'}"
        if f.age is None:
            add("age", "unknown", rng)
        else:
            ok = (lo is None or f.age >= lo) and (hi is None or f.age <= hi)
            add("age", "ok" if ok else "fail", rng)

    cats = e.get("category")
    if cats:
        if not f.category:
            add("category", "unknown", ", ".join(cats))
        else:
            add("category", "ok" if f.category in cats else "fail", ", ".join(cats))

    g = e.get("gender")
    if g and g != "any":
        if not f.gender:
            add("gender", "unknown", g)
        else:
            add("gender", "ok" if f.gender == g else "fail", g)

    need = education_rank(e.get("education"))
    if need is not None:
        have = education_rank(f.education) if f.education else None
        if have is None:
            add("education", "unknown", e.get("education"))
        else:
            add("education", "ok" if have >= need else "fail", e.get("education"))

    cap = e.get("income_limit")
    if cap is not None:
        if f.income is None:
            add("income", "unknown", f"up to {cap:g}")
        else:
            add("income", "ok" if f.income <= cap else "fail", f"up to {cap:g}")

    dom = e.get("domicile")
    if dom and rec.get("level") == "state" and rec.get("state"):
        if not f.state:
            add("domicile", "unknown", dom)
        else:
            add("domicile", "ok" if f.state.lower() == rec["state"].lower() else "fail", dom)
    return out


def passes_hard_filters(rec, f, today):
    if rec["type"] != (f.type or rec["type"]):
        return False
    if f.level and rec["level"] != f.level:
        return False
    if f.state and rec["level"] == "state" and (rec.get("state") or "").lower() != f.state.lower():
        return False
    if f.level == "state" and f.state and (rec.get("state") or "").lower() != f.state.lower():
        return False
    if f.target_group and f.target_group not in (rec.get("target_groups") or []):
        return False
    if rec.get("status") == "closed" and not f.include_closed:
        return False
    if not f.include_closed and not is_open(rec, today):
        return False
    if f.closing_soon:
        d = days_left(rec, today)
        if d is None or d > CLOSING_SOON_DAYS:
            return False
    # A definite "fail" on any stated criterion removes the record; "unknown" never does.
    return all(c["status"] != "fail" for c in check_eligibility(rec, f))


def rank(rec, bm25_score, max_bm25, f, today):
    """Higher is better. Components: text match, profile match, deadline urgency, source confidence."""
    text = (bm25_score / max_bm25) if max_bm25 else 0.0
    checks = check_eligibility(rec, f)
    matched = sum(1 for c in checks if c["status"] == "ok")
    profile = matched / len(checks) if checks else 0.0
    if f.state and (rec.get("state") or "").lower() == f.state.lower():
        profile += 0.5  # a state-specific scheme for my state beats a generic one
    elif not f.state and rec["level"] == "national":
        profile += 0.3  # with no state chosen, nationally available options come first
    d = days_left(rec, today)
    urgency = 0.0 if d is None else max(0.0, 1.0 - d / 60) if d <= 60 else 0.0
    conf = rec.get("confidence") if rec.get("confidence") is not None else 0.5
    return 3.0 * text + 1.5 * profile + 1.0 * urgency + 0.5 * conf


def search(store, f, today):
    terms = query_terms(f.q)
    scores = store.bm25(" ".join(terms)) if terms else {}
    recs = [r for r in store.all(include_closed=f.include_closed) if passes_hard_filters(r, f, today)]
    if terms:
        # Free text must match something; otherwise "nonexistent scheme" queries would return everything.
        recs = [r for r in recs if r["id"] in scores]
    mx = max(scores.values()) if scores else 0.0
    ranked = sorted(recs, key=lambda r: -rank(r, scores.get(r["id"], 0.0), mx, f, today))
    return ranked[: f.limit]
