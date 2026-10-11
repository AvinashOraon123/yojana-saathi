"""Polite crawler: robots.txt respected, per-host rate limit, raw pages cached with fetch timestamps."""
import hashlib
import io
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

from . import settings

log = logging.getLogger("yojana.crawler")


def load_sources():
    return yaml.safe_load(settings.SOURCES_PATH.read_text(encoding="utf-8"))


def is_official(url, cfg):
    host = (urlparse(url).hostname or "").lower()
    if host in cfg.get("official_hosts", []):
        return True
    return any(host.endswith(s) for s in cfg.get("official_domain_suffixes", []))


def iter_official_sources(cfg, states=None):
    """Yield dicts {id, name, level, state, type_hint, url} for every official URL, national then state."""
    for s in cfg.get("national", []):
        if s["kind"] != "official":
            continue
        for u in s["urls"]:
            yield {"id": s["id"], "name": s["name"], "level": "national", "state": None,
                   "type_hint": s.get("type_hint"), "url": u}
    for key, st in (cfg.get("states") or {}).items():
        if states and key not in states:
            continue
        for i, site in enumerate(st["sites"]):
            yield {"id": f"{key}_{i}", "name": f"{st['name']} ({urlparse(site['url']).hostname})", "level": "state",
                   "state": st["name"], "type_hint": site.get("type_hint"), "url": site["url"]}


class Crawler:
    def __init__(self, session=None, delay=None, cache_dir=None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = settings.USER_AGENT
        self.delay = settings.CRAWL_DELAY_SECONDS if delay is None else delay
        self.cache_dir = cache_dir or settings.CACHE_DIR / "pages"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._robots = {}
        self._last_hit = {}

    # ---- robots + politeness -------------------------------------------
    def allowed(self, url):
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=settings.REQUEST_TIMEOUT)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                elif r.status_code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.parse([])  # no robots.txt -> allowed
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        return rp.can_fetch(settings.USER_AGENT, url)

    def _wait(self, host):
        gap = time.time() - self._last_hit.get(host, 0)
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_hit[host] = time.time()

    # ---- cache ----------------------------------------------------------
    def _cache_path(self, url):
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")

    def _cached(self, url, ttl_hours):
        p = self._cache_path(url)
        if p.exists():
            doc = json.loads(p.read_text(encoding="utf-8"))
            age = datetime.now(timezone.utc) - datetime.fromisoformat(doc["fetched_at"])
            if age < timedelta(hours=ttl_hours):
                return doc
        return None

    # ---- fetch ----------------------------------------------------------
    def fetch(self, url, force=False):
        """Return {url, fetched_at, text, links, ok, error}. Never raises."""
        if not force:
            doc = self._cached(url, settings.PAGE_CACHE_TTL_HOURS)
            if doc:
                return doc
        if not self.allowed(url):
            return {"url": url, "ok": False, "error": "disallowed by robots.txt"}
        self._wait(urlparse(url).netloc)
        try:
            r = self.session.get(url, timeout=settings.REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            return {"url": url, "ok": False, "error": str(e)[:200]}
        ctype = r.headers.get("content-type", "").lower()
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            text, links = pdf_text(r.content), []
        else:
            text, links = html_text(r.text, url)
        doc = {"url": url, "ok": True, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "text": text, "links": links}
        self._cache_path(url).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return doc


def html_text(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg"]):
        tag.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if href.startswith("http"):
            links.append({"url": href.split("#")[0], "text": a.get_text(" ", strip=True)[:120]})
    text = "\n".join(line for line in (l.strip() for l in soup.get_text("\n").splitlines()) if line)
    return text, links


def pdf_text(content):
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:30])
    except Exception as e:  # corrupt or scanned PDFs
        log.warning("pdf parse failed: %s", e)
        return ""


KEYWORDS = ("scheme", "yojana", "scholarship", "recruitment", "notification", "apply", "exam", "vacancy", "admission", "advertisement")


def candidate_links(doc, cfg, limit=8):
    """Notification-looking links on an official page, restricted to official domains."""
    out, seen = [], set()
    for l in doc.get("links", []):
        blob = (l["text"] + " " + l["url"]).lower()
        if l["url"] in seen or not is_official(l["url"], cfg) or not any(k in blob for k in KEYWORDS):
            continue
        seen.add(l["url"])
        out.append(l["url"])
        if len(out) >= limit:
            break
    return out


def discovery_links(crawler, cfg):
    """Aggregators are used only to find official links; their text is never extracted into records."""
    found = []
    for s in cfg.get("national", []):
        if s["kind"] != "discovery":
            continue
        for u in s["urls"]:
            doc = crawler.fetch(u)
            if doc.get("ok"):
                found += [l["url"] for l in doc["links"] if is_official(l["url"], cfg)]
    return list(dict.fromkeys(found))
