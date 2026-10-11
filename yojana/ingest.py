"""Ingestion: crawl official sources -> extract with Sarvam -> validate -> store.

  python -m yojana.ingest --check              # fetch each source root, report status (no Sarvam calls)
  python -m yojana.ingest --source upsc --limit 3
  python -m yojana.ingest --all
  python -m yojana.ingest --reverify           # re-extract records closing within 14 days
"""
import argparse
import logging
from datetime import datetime, timedelta, timezone

from . import settings
from .crawler import Crawler, candidate_links, discovery_links, iter_official_sources, load_sources
from .extractor import extract_page, keep_open
from .store import Store

log = logging.getLogger("yojana.ingest")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_sources(store, crawler, cfg):
    rows = []
    for s in iter_official_sources(cfg):
        doc = crawler.fetch(s["url"], force=True)
        ok = doc.get("ok")
        text_len = len(doc.get("text", "")) if ok else 0
        note = "ok" if ok and text_len > 500 else ("ok but little text (likely a JavaScript app)" if ok else doc.get("error"))
        store.set_source_status(s["id"], last_attempt=now_iso(), last_ok=now_iso() if ok else None,
                                pages_ok=int(bool(ok)), pages_failed=int(not ok), note=note)
        rows.append((s["id"], s["url"], note, text_len))
    return rows


def ingest_source(store, crawler, sarvam, source, cfg, max_pages=3):
    pages = [source["url"]]
    root = crawler.fetch(source["url"])
    if root.get("ok"):
        pages += candidate_links(root, cfg, limit=max_pages - 1)
    added = ok_pages = failed = 0
    for url in pages[:max_pages]:
        doc = root if url == source["url"] else crawler.fetch(url)
        if not doc.get("ok") or len(doc.get("text", "")) < 300:
            failed += 1
            continue
        ok_pages += 1
        recs, rejected = extract_page(sarvam, doc["text"], {**source, "url": url})
        for r in recs:
            if keep_open(r):
                store.upsert(r)
                added += 1
    store.set_source_status(source["id"], last_attempt=now_iso(), last_ok=now_iso() if ok_pages else None,
                            pages_ok=ok_pages, pages_failed=failed, records_added=added,
                            note=f"{ok_pages} pages extracted, {failed} skipped")
    return added


def ingest_all(store, crawler, sarvam, cfg, only=None, limit=None):
    sources = [s for s in iter_official_sources(cfg) if not only or s["id"] == only]
    if limit:
        sources = sources[:limit]
    total = 0
    for s in sources:
        try:
            total += ingest_source(store, crawler, sarvam, s, cfg)
        except Exception as e:  # one broken source must not stop the run
            log.exception("source %s failed", s["id"])
            store.set_source_status(s["id"], last_attempt=now_iso(), note=f"error: {e}"[:200])
    # Aggregator links are only hints; official pages found there are crawled like any other source.
    for url in discovery_links(crawler, cfg)[:5]:
        total += ingest_source(store, crawler, sarvam, {"id": "discovered", "name": "discovered", "level": "national",
                                                        "state": None, "type_hint": None, "url": url}, cfg, max_pages=1)
    if total:
        store.purge_demo()  # real records replace the bundled samples
    store.close_expired()
    return total


def reverify(store, crawler, sarvam, cfg):
    """Re-fetch records whose deadline is within the window; drop or update them if the source changed."""
    horizon = (settings.today() + timedelta(days=settings.REVERIFY_WINDOW_DAYS)).isoformat()
    today = settings.today().isoformat()
    due = [r for r in store.all() if (r.get("dates") or {}).get("application_end")
           and today <= r["dates"]["application_end"] <= horizon and r.get("source_url")]
    updated = 0
    for r in due:
        doc = crawler.fetch(r["source_url"], force=True)
        if not doc.get("ok"):
            continue
        source = {"url": r["source_url"], "level": r["level"], "state": r.get("state")}
        recs, _ = extract_page(sarvam, doc["text"], source)
        match = next((n for n in recs if n["id"] == r["id"]), None)
        if match and keep_open(match):
            match["last_verified_at"] = now_iso()
            store.upsert(match)
            updated += 1
    store.close_expired()
    return updated


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--source")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--reverify", action="store_true")
    args = ap.parse_args()

    cfg, store, crawler = load_sources(), Store(), Crawler()
    if args.check:
        for sid, url, note, n in check_sources(store, crawler, cfg):
            print(f"{sid:18} {n:7} chars  {note}  {url}")
        return
    if settings.DEMO_MODE:
        raise SystemExit("No SARVAM_API_KEY: extraction needs the Sarvam API. Use --check to test fetching only.")
    from .sarvam_client import Sarvam
    sarvam = Sarvam()
    if args.reverify:
        print("updated", reverify(store, crawler, sarvam, cfg))
    elif args.all or args.source:
        print("records added", ingest_all(store, crawler, sarvam, cfg, only=args.source, limit=args.limit))


if __name__ == "__main__":
    main()
