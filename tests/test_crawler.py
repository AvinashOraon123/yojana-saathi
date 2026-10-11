import time

import pytest

from yojana import settings
from yojana.crawler import Crawler, candidate_links, discovery_links, html_text, is_official, iter_official_sources, load_sources


class Resp:
    def __init__(self, text="", status=200, ctype="text/html"):
        self.text, self.status_code, self.headers, self.content = text, status, {"content-type": ctype}, text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, pages):
        self.pages, self.headers, self.hits = pages, {}, []

    def get(self, url, timeout=None):
        self.hits.append((url, time.time()))
        return self.pages.get(url, Resp("", 404))


@pytest.fixture
def cfg():
    return load_sources()


def make(tmp_path, pages, delay=0.0):
    return Crawler(session=FakeSession(pages), delay=delay, cache_dir=tmp_path)


def test_robots_disallow_is_respected(tmp_path):
    pages = {"https://x.gov.in/robots.txt": Resp("User-agent: *\nDisallow: /private/"),
             "https://x.gov.in/public": Resp("<p>hello world</p>"), "https://x.gov.in/private/a": Resp("<p>secret</p>")}
    c = make(tmp_path, pages)
    assert c.fetch("https://x.gov.in/public")["ok"]
    blocked = c.fetch("https://x.gov.in/private/a")
    assert not blocked["ok"] and "robots" in blocked["error"]
    assert not any(u.endswith("/private/a") for u, _ in c.session.hits)


def test_rate_limit_spaces_requests_to_same_host(tmp_path):
    pages = {"https://x.gov.in/a": Resp("<p>a</p>"), "https://x.gov.in/b": Resp("<p>b</p>")}
    c = make(tmp_path, pages, delay=0.3)
    c.fetch("https://x.gov.in/a")
    c.fetch("https://x.gov.in/b")
    times = [t for u, t in c.session.hits if not u.endswith("robots.txt")]
    assert times[1] - times[0] >= 0.28


def test_pages_are_cached_with_fetch_timestamp(tmp_path):
    pages = {"https://x.gov.in/a": Resp("<p>a</p>")}
    c = make(tmp_path, pages)
    first = c.fetch("https://x.gov.in/a")
    n = len(c.session.hits)
    again = c.fetch("https://x.gov.in/a")
    assert len(c.session.hits) == n and again["fetched_at"] == first["fetched_at"]


def test_http_errors_do_not_raise(tmp_path):
    c = make(tmp_path, {})
    assert c.fetch("https://x.gov.in/missing")["ok"] is False


def test_html_text_strips_scripts_and_collects_links():
    html = "<nav>menu</nav><script>evil()</script><h1>Title</h1><a href='/n.pdf'>Notification</a>"
    text, links = html_text(html, "https://x.gov.in/")
    assert "evil" not in text and "Title" in text
    assert links[0]["url"] == "https://x.gov.in/n.pdf"


def test_official_domain_check(cfg):
    assert is_official("https://upsc.gov.in/x", cfg) and is_official("https://www.ibps.in/", cfg)
    assert not is_official("https://www.sarkariresult.com/x", cfg)


def test_candidate_links_stay_on_official_domains(cfg):
    doc = {"links": [{"url": "https://upsc.gov.in/recruitment-notification", "text": "Notification"},
                     {"url": "https://spam.example.com/apply-now", "text": "apply"}]}
    assert candidate_links(doc, cfg) == ["https://upsc.gov.in/recruitment-notification"]


def test_discovery_sources_only_yield_official_links(tmp_path, cfg):
    page = Resp("<a href='https://ssc.gov.in/notice'>exam notice</a><a href='https://ads.example.com/x'>ad</a>")
    c = make(tmp_path, {"https://www.sarkariresult.com/": page})
    assert discovery_links(c, cfg) == ["https://ssc.gov.in/notice"]


def test_discovery_sources_are_never_extracted(cfg):
    ids = {s["id"] for s in iter_official_sources(cfg)}
    assert "discovery_sarkariresult" not in ids


def test_eight_states_are_configured_and_adding_one_is_config_only(cfg):
    assert len(cfg["states"]) == 8
    cfg["states"]["kerala"] = {"name": "Kerala", "sites": [{"url": "https://kerala.gov.in/", "type_hint": "scheme"}]}
    assert any(s["state"] == "Kerala" for s in iter_official_sources(cfg))
