from yojana import settings
from yojana.search import Filters, education_rank, search

TODAY = settings.today()


def titles(store, **kw):
    return [r["title"] for r in search(store, Filters(**kw), TODAY)]


def test_age_filter_excludes_out_of_range_and_keeps_unknown(store):
    assert "Atal Pension Yojana" not in titles(store, q="pension", age=55)
    assert "Atal Pension Yojana" in titles(store, q="pension", age=30)
    assert "Atal Pension Yojana" in titles(store, q="pension")  # age unknown -> never excluded


def test_old_age_pension_needs_60(store):
    assert "Indira Gandhi National Old Age Pension Scheme" not in titles(store, q="pension", age=40)
    assert "Indira Gandhi National Old Age Pension Scheme" in titles(store, q="pension", age=65)


def test_category_filter(store):
    assert "Stand-Up India" not in titles(store, q="loan enterprise", category="General")
    assert "Stand-Up India" in titles(store, q="loan enterprise", category="SC")


def test_gender_filter(store):
    assert "PM Ujjwala Yojana" not in titles(store, q="LPG", gender="male")
    assert "PM Ujjwala Yojana" in titles(store, q="LPG", gender="female")


def test_state_filter_hides_other_states_but_keeps_national(store):
    wb = titles(store, q="girls", state="West Bengal")
    assert "Kanyashree Prakalpa" in wb
    assert "Pudhumai Penn" not in wb
    assert "AICTE Pragati Scholarship for Girls" in wb  # national stays visible


def test_level_filter(store):
    assert all(r["level"] == "state" for r in search(store, Filters(level="state"), TODAY))


def test_type_and_target_group(store):
    assert {r["type"] for r in search(store, Filters(type="exam"), TODAY)} == {"exam"}
    assert all("farmer" in r["target_groups"] for r in search(store, Filters(target_group="farmer"), TODAY))


def test_education_ordering():
    assert education_rank("Graduate") > education_rank("Class 12") > education_rank("Class 10")
    assert education_rank("nonsense") is None


def test_education_filter(store):
    assert "UPSC Civil Services Examination" not in titles(store, q="IAS", education="Class 12")
    assert "UPSC Civil Services Examination" in titles(store, q="IAS", education="Graduate")


def test_income_filter(dated_store):
    s = dated_store
    from tests.conftest import dated
    s.upsert(dated("Low Income Scholarship", 20, eligibility={"income_limit": 100000}))
    assert "Low Income Scholarship" not in titles(s, q="income", income=500000)
    assert "Low Income Scholarship" in titles(s, q="income", income=50000)


def test_expired_items_never_appear_as_open(dated_store):
    got = titles(dated_store, q="scholarship")
    assert "Expired Grant Scholarship" not in got
    assert "Open Merit Scholarship" in got and "Evergreen Merit Scholarship" in got


def test_expired_only_with_include_closed(dated_store):
    assert "Expired Grant Scholarship" in titles(dated_store, q="scholarship", include_closed=True)


def test_close_expired_marks_status(dated_store):
    assert dated_store.close_expired() == 1
    assert dated_store.get(next(r["id"] for r in dated_store.all(include_closed=True) if "Expired" in r["title"]))["status"] == "closed"


def test_closing_soon(dated_store):
    assert titles(dated_store, q="fellowship scholarship", closing_soon=True) == ["Closing Soon Fellowship"]


def test_ranking_prefers_urgent_deadline(dated_store):
    got = titles(dated_store, q="scholarship fellowship")
    assert got.index("Closing Soon Fellowship") < got.index("Open Merit Scholarship")


def test_free_text_with_no_match_returns_nothing(store):
    assert titles(store, q="zzzqqq") == []
