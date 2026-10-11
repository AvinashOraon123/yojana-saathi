"""Single gateway to every Sarvam capability, so retries, timeouts, caching and usage logging live in one place."""
import base64
import hashlib
import json
import logging
import re
import sqlite3
import time

from . import settings

log = logging.getLogger("yojana.sarvam")

# Per-call text limits (conservative; split on sentence boundaries before calling)
TRANSLATE_CHUNK = 900
TTS_CHUNK = 2000


class SarvamError(Exception):
    pass


def chunk_text(text, limit):
    """Split on sentence boundaries into pieces of at most `limit` characters."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts, buf = [], ""
    for sent in re.split(r"(?<=[.!?।॥])\s+", text):
        while len(sent) > limit:  # a single oversize sentence: hard split on spaces
            cut = sent.rfind(" ", 0, limit) or limit
            parts.append((buf + " " + sent[:cut]).strip() if buf else sent[:cut])
            buf, sent = "", sent[cut:].strip()
        if len(buf) + len(sent) + 1 > limit:
            parts.append(buf)
            buf = sent
        else:
            buf = (buf + " " + sent).strip()
    if buf:
        parts.append(buf)
    return [p for p in parts if p]


def strip_reasoning(text):
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


class DiskCache:
    """Cache of (kind, text, language) -> result, so repeat translations and TTS cost nothing."""

    def __init__(self, path):
        settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("create table if not exists kv (k text primary key, v text)")

    def get(self, *parts):
        row = self.db.execute("select v from kv where k=?", (self._key(parts),)).fetchone()
        return row[0] if row else None

    def put(self, value, *parts):
        self.db.execute("insert or replace into kv values (?,?)", (self._key(parts), value))
        self.db.commit()

    @staticmethod
    def _key(parts):
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class Sarvam:
    def __init__(self, api_key=None, cache_path=None):
        from sarvamai import SarvamAI
        self.client = SarvamAI(api_subscription_key=api_key or settings.SARVAM_API_KEY, timeout=45)
        self.cache = DiskCache(cache_path or settings.CACHE_DIR / "sarvam_cache.db")

    # ---- plumbing -------------------------------------------------------
    def _call(self, name, fn, retries=3):
        """Exponential backoff on 429/5xx and network errors; other 4xx fail immediately."""
        delay = 1.5
        for attempt in range(retries):
            started = time.time()
            try:
                result = fn()
                log.info("sarvam %s ok %.1fs", name, time.time() - started)
                return result
            except Exception as e:  # SDK raises ApiError with status_code
                status = getattr(e, "status_code", None)
                retryable = status in (429, 500, 502, 503, 504) or status is None
                if not retryable or attempt == retries - 1:
                    body = getattr(e, "body", e)
                    raise SarvamError(f"{name} failed (status {status}): {body}") from e
                log.warning("sarvam %s retry %d after status %s", name, attempt + 1, status)
                time.sleep(delay)
                delay *= 2

    def _log_usage(self, label, usage):
        if not usage:
            return
        tokens = getattr(usage, "total_tokens", 0) or 0
        cost = tokens / 1000 * settings.COST_PER_1K_TOKENS
        log.info("usage %s tokens=%s est_cost=%.4f", label, tokens, cost)

    # ---- chat -----------------------------------------------------------
    def chat(self, system, user, temperature=0.1, label="chat", reasoning_effort=None):
        r = self._call(label, lambda: self.client.chat.completions(
            model=settings.CHAT_MODEL,
            temperature=temperature,
            reasoning_effort=reasoning_effort or settings.REASONING_EFFORT,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        ))
        self._log_usage(label, r.usage)
        return strip_reasoning(r.choices[0].message.content)

    # ---- language -------------------------------------------------------
    def detect_language(self, text):
        r = self._call("identify_language", lambda: self.client.text.identify_language(input=text))
        return r.language_code or "en-IN"

    def translate(self, text, source, target):
        if not text or source == target:
            return text
        cached = self.cache.get("tr", text, source, target)
        if cached is not None:
            return cached
        out = []
        for piece in chunk_text(text, TRANSLATE_CHUNK):
            out.append(self._call("translate", lambda p=piece: self.client.text.translate(
                input=p, source_language_code=source, target_language_code=target,
                mode="formal", numerals_format="international").translated_text))
        result = " ".join(out)
        self.cache.put(result, "tr", text, source, target)
        return result

    def transliterate(self, text, source, target):
        """Used for scheme names: keeps the proper noun but shows it in the reader's script."""
        if not text or source == target:
            return text
        cached = self.cache.get("tl", text, source, target)
        if cached is not None:
            return cached
        result = self._call("transliterate", lambda: self.client.text.transliterate(
            input=text, source_language_code=source, target_language_code=target,
            numerals_format="international").transliterated_text)
        self.cache.put(result, "tl", text, source, target)
        return result

    # ---- speech ---------------------------------------------------------
    def stt(self, audio_bytes, filename="audio.webm"):
        """Returns (transcript, language_code). codemix mode handles Hindi-English mixing."""
        r = self._call("stt", lambda: self.client.speech_to_text.transcribe(
            file=(filename, audio_bytes), model="saaras:v4", mode="codemix"))
        return r.transcript or "", r.language_code or None

    def tts(self, text, lang):
        """Returns base64 WAV. Long text is chunked; chunks are concatenated as WAV PCM."""
        cached = self.cache.get("tts", text, lang)
        if cached is not None:
            return cached
        wavs = []
        for piece in chunk_text(text, TTS_CHUNK):
            r = self._call("tts", lambda p=piece: self.client.text_to_speech.convert(
                text=p, language_code=lang, model="bulbul:v3", speaker="shubh"))
            wavs.append(base64.b64decode(r.audios[0]))
        audio = base64.b64encode(join_wav(wavs)).decode() if wavs else ""
        self.cache.put(audio, "tts", text, lang)
        return audio


def join_wav(chunks):
    """Concatenate WAV files that share a format by joining their data sections."""
    if len(chunks) == 1:
        return chunks[0]
    import io
    import wave
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        for i, c in enumerate(chunks):
            with wave.open(io.BytesIO(c)) as r:
                if i == 0:
                    w.setparams(r.getparams())
                w.writeframes(r.readframes(r.getnframes()))
    return out.getvalue()


def parse_json_loose(text):
    """Pull the first JSON object/array out of a model reply (handles ```json fences and chatter)."""
    text = strip_reasoning(text)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in model output")
