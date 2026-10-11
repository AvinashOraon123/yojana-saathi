"""Knowledge store: SQLite with an FTS5 (BM25) index over record text."""
import json
import re
import sqlite3
from datetime import date

from . import settings

SCHEMA = """
create table if not exists records (
  id text primary key, type text, level text, state text,
  application_end text, confidence real, status text default 'open',
  fetched_at text, last_verified_at text, source_url text, is_demo integer default 0,
  data text not null
);
create virtual table if not exists records_fts using fts5(id unindexed, body, tokenize='porter unicode61');
create table if not exists source_status (
  source_id text primary key, last_attempt text, last_ok text, pages_ok integer, pages_failed integer,
  records_added integer, note text
);
"""


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def record_year(rec):
    for k in ("application_end", "application_start", "exam_date", "notification"):
        v = (rec.get("dates") or {}).get(k)
        if v:
            return v[:4]
    return (rec.get("fetched_at") or "")[:4] or "na"


def make_id(rec):
    """Dedup key: title + authority + year."""
    return slug(rec["title"])[:60] + "--" + slug(rec["authority"])[:30] + "--" + record_year(rec)


def searchable_text(rec):
    e = rec.get("eligibility") or {}
    bits = [rec["title"], rec.get("authority"), rec.get("state"), rec.get("summary_en"), rec.get("benefit"),
            rec.get("how_to_apply"), " ".join(rec.get("target_groups") or []), rec["type"],
            e.get("education"), e.get("other"), " ".join(e.get("occupation") or [])]
    return " ".join(b for b in bits if b)


class Store:
    def __init__(self, path=None):
        path = path or settings.DB_PATH
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def upsert(self, rec, is_demo=False):
        rec = dict(rec)
        rec["id"] = rec.get("id") or make_id(rec)
        end = (rec.get("dates") or {}).get("application_end")
        self.db.execute(
            "insert or replace into records (id,type,level,state,application_end,confidence,status,fetched_at,"
            "last_verified_at,source_url,is_demo,data) values (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec["id"], rec["type"], rec["level"], rec.get("state"), end, rec.get("confidence"), "open",
             rec.get("fetched_at"), rec.get("last_verified_at"), rec.get("source_url"), int(is_demo),
             json.dumps(rec, ensure_ascii=False)))
        self.db.execute("delete from records_fts where id=?", (rec["id"],))
        self.db.execute("insert into records_fts (id, body) values (?,?)", (rec["id"], searchable_text(rec)))
        self.db.commit()
        return rec["id"]

    def get(self, rec_id):
        row = self.db.execute("select data,status from records where id=?", (rec_id,)).fetchone()
        return self._load(row) if row else None

    def all(self, include_closed=False):
        sql = "select data,status from records" + ("" if include_closed else " where status='open'")
        return [self._load(r) for r in self.db.execute(sql)]

    def bm25(self, query, limit=200):
        """Return {id: score} where a higher score is a better match."""
        terms = re.findall(r"\w+", query.lower())
        if not terms:
            return {}
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self.db.execute(
            "select id, bm25(records_fts) as s from records_fts where records_fts match ? order by s limit ?",
            (match, limit)).fetchall()
        return {r["id"]: -r["s"] for r in rows}  # FTS5 bm25 is lower-is-better

    def vocabulary(self):
        """Lower-cased words that appear anywhere in stored records."""
        words = set()
        for r in self.all(include_closed=True):
            words.update(re.findall(r"\w+", searchable_text(r).lower()))
        return words

    def close_expired(self, today=None):
        """Mark records whose application_end passed. Never deleted, so 'past items' can be shown on request."""
        today = (today or settings.today()).isoformat()
        cur = self.db.execute(
            "update records set status='closed' where status='open' and application_end is not null and application_end < ?",
            (today,))
        self.db.commit()
        return cur.rowcount

    def purge_demo(self):
        self.db.execute("delete from records_fts where id in (select id from records where is_demo=1)")
        self.db.execute("delete from records where is_demo=1")
        self.db.commit()

    def count(self):
        return self.db.execute("select count(*) from records").fetchone()[0]

    def set_source_status(self, source_id, **kw):
        cur = self.db.execute("select * from source_status where source_id=?", (source_id,)).fetchone()
        row = dict(cur) if cur else {"source_id": source_id}
        row.update(kw)
        cols = ["source_id", "last_attempt", "last_ok", "pages_ok", "pages_failed", "records_added", "note"]
        self.db.execute(f"insert or replace into source_status ({','.join(cols)}) values ({','.join('?' * len(cols))})",
                        [row.get(c) for c in cols])
        self.db.commit()

    def source_statuses(self):
        return [dict(r) for r in self.db.execute("select * from source_status order by source_id")]

    @staticmethod
    def _load(row):
        rec = json.loads(row["data"])
        rec["status"] = row["status"]
        return rec


def load_demo_data(store, path=None):
    """Load the bundled sample records (flagged is_demo so real data can replace them)."""
    path = path or settings.SEED_PATH
    for rec in json.loads(path.read_text(encoding="utf-8")):
        store.upsert(rec, is_demo=True)
    return store.count()
