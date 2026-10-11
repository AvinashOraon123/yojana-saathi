"""FastAPI app. The Sarvam key stays on the server; the browser only talks to these endpoints."""
import json
import logging
import re
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cards as cardlib
from . import qa, settings, stt_stream
from .search import Filters, search
from .store import Store, load_demo_data

log = logging.getLogger("yojana.api")
STATIC = settings.ROOT / "static"
MAX_AUDIO_BYTES = 5 * 1024 * 1024

STATE_LIST = ["Andhra Pradesh", "Bihar", "Gujarat", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Odisha",
              "Punjab", "Rajasthan", "Tamil Nadu", "Telangana", "Uttar Pradesh", "West Bengal"]


# ---- models ------------------------------------------------------------
class Profile(BaseModel):
    age: int | None = Field(None, ge=0, le=120)
    state: str | None = Field(None, max_length=40)
    gender: str | None = Field(None, pattern="^(male|female)$")
    category: str | None = Field(None, pattern="^(General|OBC|SC|ST|EWS|PwD|Minority)$")
    education: str | None = Field(None, max_length=40)
    occupation: str | None = Field(None, max_length=40)
    income: float | None = Field(None, ge=0, le=1e10)


class Chips(BaseModel):
    type: str | None = Field(None, pattern="^(scheme|exam|scholarship|job)$")
    level: str | None = Field(None, pattern="^(national|state)$")
    state: str | None = Field(None, max_length=40)
    target_group: str | None = Field(None, max_length=20)
    closing_soon: bool = False
    include_closed: bool = False


class SearchBody(BaseModel):
    q: str = Field("", max_length=300)
    language: str = "English"
    profile: Profile | None = None
    chips: Chips = Chips()


class AskBody(SearchBody):
    q: str = Field(..., min_length=2, max_length=500)


class EligibilityBody(BaseModel):
    record_id: str = Field(..., max_length=200)
    profile: Profile


class TTSBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    language: str = "English"


# ---- rate limit --------------------------------------------------------
class RateLimiter:
    def __init__(self, per_minute):
        self.per_minute, self.hits, self.lock = per_minute, defaultdict(deque), threading.Lock()

    def allow(self, ip):
        now = time.time()
        with self.lock:
            q = self.hits[ip]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


def lang_code(name):
    return settings.LANGUAGES.get(name, "en-IN")


def create_app(store_factory=None, sarvam=None, demo=None, stt_connect=None):
    """`sarvam` / `store_factory` can be injected (tests use fakes so no credits are spent)."""
    app = FastAPI(title="Yojana Saathi")
    demo_mode = settings.DEMO_MODE if demo is None else demo
    if sarvam is None and not demo_mode:
        from .sarvam_client import Sarvam
        sarvam = Sarvam()
    app.state.sarvam = None if demo_mode else sarvam
    app.state.store_factory = store_factory or Store
    app.state.stt_connect = stt_connect or stt_stream.sarvam_connect
    limiter = RateLimiter(settings.RATE_LIMIT_PER_MIN)

    boot = app.state.store_factory()
    if boot.count() == 0:
        load_demo_data(boot)
    boot.close_expired()

    def store():
        return app.state.store_factory()

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method == "POST":
            ip = request.client.host if request.client else "?"
            if not limiter.allow(ip):
                return JSONResponse({"error": "Too many requests. Please wait a minute."}, status_code=429)
        return await call_next(request)

    # ---- config + UI strings ------------------------------------------
    @app.get("/api/config")
    def config():
        st = store()
        recs = st.all(include_closed=True)
        return {"languages": settings.LANGUAGES, "states": STATE_LIST, "demo_mode": demo_mode,
                "live": app.state.sarvam is not None, "today": settings.today().isoformat(),
                "sample_data": any(r.get("demo_sample") for r in recs), "records": len(recs)}

    @app.get("/api/ui-strings")
    def ui_strings(language: str = "English"):
        return cardlib.ui_strings(app.state.sarvam, lang_code(language))

    # ---- deterministic search -----------------------------------------
    @app.post("/api/search")
    def api_search(body: SearchBody):
        st, today = store(), settings.today()
        prof = body.profile.model_dump() if body.profile else {}
        f = Filters(q=body.q, type=body.chips.type, level=body.chips.level, state=body.chips.state or prof.get("state"),
                    target_group=body.chips.target_group, age=prof.get("age"), category=prof.get("category"),
                    gender=prof.get("gender"), education=prof.get("education"), income=prof.get("income"),
                    closing_soon=body.chips.closing_soon, include_closed=body.chips.include_closed, limit=12)
        recs = search(st, f, today)
        return {"cards": cardlib.build_cards(app.state.sarvam, recs, lang_code(body.language), today)}

    @app.post("/api/eligibility")
    def api_eligibility(body: EligibilityBody):
        rec = store().get(body.record_id)
        if not rec:
            raise HTTPException(404, "Unknown record")
        return {"checks": cardlib.eligibility_check(rec, body.profile.model_dump())}

    # ---- conversational answer (NDJSON stream) -------------------------
    @app.post("/api/ask")
    def api_ask(body: AskBody):
        sarvam, today = app.state.sarvam, settings.today()
        ui_lang = lang_code(body.language)

        def events():
            def emit(**kw):
                return json.dumps(kw, ensure_ascii=False) + "\n"
            try:
                st = store()
                prof = body.profile.model_dump() if body.profile else {}
                prep = qa.prepare(st, sarvam, body.q, ui_lang, profile=prof, include_closed=body.chips.include_closed,
                                  closing_soon=body.chips.closing_soon, overrides=body.chips.model_dump(), today=today)
                lang = prep["lang"]
                cards = cardlib.build_cards(sarvam, prep["records"], lang, today)
                yield emit(event="results", cards=cards, language=lang, query_english=prep["english"])
                if prep["kind"] == "answer" and sarvam is not None and not settings.VERIFY_WITH_LLM:
                    # Live path: sentences are shown as the model writes them, each checked in code first.
                    parts, removed = [], []
                    for sent, bad in qa.stream_answer(sarvam, prep):
                        removed += bad
                        if not sent:
                            continue
                        parts.append(sent)
                        yield emit(event="text", text=sent if lang == "en-IN" else sarvam.translate(sent, "en-IN", lang) + " ")
                    yield emit(event="english", text="".join(parts))
                    yield emit(event="done", kind="answer", removed_claims=removed)
                else:
                    done = qa.finish(sarvam, prep)
                    english = done["answer"]
                    yield emit(event="english", text=english)
                    if sarvam is not None and lang != "en-IN":
                        from .sarvam_client import chunk_text
                        for piece in chunk_text(english, 300):
                            yield emit(event="text", text=sarvam.translate(piece, "en-IN", lang) + " ")
                    else:
                        for piece in re.findall(r"\S+\s*", english):
                            yield emit(event="text", text=piece)
                    yield emit(event="done", kind=done["kind"], removed_claims=done.get("removed_claims", []))
            except Exception as e:
                log.exception("ask failed")
                yield emit(event="error", error=f"Something went wrong: {str(e)[:200]}")

        return StreamingResponse(events(), media_type="application/x-ndjson")

    # ---- speech ---------------------------------------------------------
    @app.websocket("/ws/stt")
    async def ws_stt(ws: WebSocket, language: str = "auto"):
        """Live transcription: the browser streams 16 kHz PCM16 audio and gets text back as it is spoken."""
        await ws.accept()
        ip = ws.client.host if ws.client else "?"
        if app.state.sarvam is None:
            await ws.send_json({"type": "error", "error": "Voice needs a Sarvam API key (demo mode is text only)."})
            await ws.close()
            return
        if not limiter.allow(ip):
            await ws.send_json({"type": "error", "error": "Too many requests. Please wait a minute."})
            await ws.close()
            return
        code = settings.LANGUAGES.get(language, "unknown")  # unknown -> Sarvam auto-detects the language
        try:
            full = await stt_stream.relay(ws, app.state.stt_connect, code)
            await ws.send_json({"type": "done", "full": full})
        except Exception as e:
            log.exception("live stt failed")
            try:
                await ws.send_json({"type": "error", "error": f"Speech service error: {str(e)[:150]}"})
            except Exception:
                pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    @app.post("/api/stt")
    async def api_stt(audio: UploadFile = File(...)):
        if app.state.sarvam is None:
            raise HTTPException(503, "Voice needs a Sarvam API key (demo mode is text only).")
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Audio too large (max 5 MB).")
        if not data:
            raise HTTPException(400, "Empty audio.")
        try:
            transcript, lang = app.state.sarvam.stt(data, audio.filename or "audio.webm")
        except Exception as e:
            raise HTTPException(502, f"Speech service error: {str(e)[:200]}")
        return {"transcript": transcript, "language_code": lang}

    @app.post("/api/tts")
    def api_tts(body: TTSBody):
        if app.state.sarvam is None:
            raise HTTPException(503, "Voice needs a Sarvam API key (demo mode is text only).")
        try:
            return {"audio": app.state.sarvam.tts(body.text, lang_code(body.language))}
        except Exception as e:
            raise HTTPException(502, f"Speech service error: {str(e)[:200]}")

    # ---- sources --------------------------------------------------------
    @app.get("/api/sources/status")
    def sources_status():
        st = store()
        recs = st.all(include_closed=True)
        return {"records": len(recs), "open": sum(1 for r in recs if r["status"] == "open"),
                "demo_sample": sum(1 for r in recs if r.get("demo_sample")), "sources": st.source_statuses()}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    if settings.ENABLE_SCHEDULER and app.state.sarvam is not None:
        start_scheduler(app)
    return app


def start_scheduler(app):
    """Daily full crawl; deadline re-verification every 6 hours."""
    from .crawler import Crawler, load_sources
    from .ingest import ingest_all, reverify

    def loop():
        cfg, crawler = load_sources(), Crawler()
        last_full = 0.0
        while True:
            try:
                st = app.state.store_factory()
                if time.time() - last_full > 24 * 3600:
                    ingest_all(st, crawler, app.state.sarvam, cfg)
                    last_full = time.time()
                reverify(st, crawler, app.state.sarvam, cfg)
            except Exception:
                log.exception("scheduler pass failed")
            time.sleep(6 * 3600)

    threading.Thread(target=loop, daemon=True, name="yojana-scheduler").start()


def get_app():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    return create_app()
