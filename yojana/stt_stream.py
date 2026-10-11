"""Live speech-to-text: browser microphone -> our WebSocket -> Sarvam streaming STT -> text back as it is spoken.

The browser sends 16 kHz mono PCM16 chunks. Sarvam only returns text when its voice-activity detector ends a
segment, so while someone talks without pausing we flush every FLUSH_SECONDS of audio to get text roughly live.
"""
import array
import asyncio
import base64
import json
import logging
import re
from contextlib import asynccontextmanager

from . import settings

log = logging.getLogger("yojana.stt")

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
MIN_FLUSH_SECONDS = 1.5      # earliest we flush, and only at a pause in the audio
MAX_FLUSH_SECONDS = 5.0      # flush anyway after this much continuous speech
PAUSE_RMS = 350              # PCM16 loudness below this counts as a pause between words
MAX_SESSION_SECONDS = 60
MAX_CHUNK_BYTES = BYTES_PER_SECOND * 2
FINAL_WAIT_SECONDS = 2.5     # after the user stops, wait at most this long for the last segment


@asynccontextmanager
async def sarvam_connect(language_code):
    """Open Sarvam's streaming STT socket. `language_code` may be 'unknown' for auto-detect."""
    from sarvamai import AsyncSarvamAI
    client = AsyncSarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
    async with client.speech_to_text_streaming.connect(
            language_code=language_code, model="saaras:v4", mode="codemix", sample_rate=str(SAMPLE_RATE),
            input_audio_codec="pcm_s16le", vad_signals="true", flush_signal="true") as sock:
        yield sock


def rms(chunk):
    """Loudness of a PCM16 little-endian chunk (audioop is gone in newer Pythons)."""
    a = array.array("h")
    a.frombytes(chunk[: len(chunk) // 2 * 2])
    return (sum(x * x for x in a) / len(a)) ** 0.5 if a else 0.0


def _norm(s):
    return re.sub(r"[\s.,?!।॥]+", " ", s).strip().lower()


def new_text(previous, segment):
    """Drop a segment that only repeats the tail of what was already shown (flush artefact at the end)."""
    seg, prev = _norm(segment), _norm(previous)
    if not seg:
        return ""
    if prev.endswith(seg) and len(seg) <= 24:
        return ""
    return segment.strip()


async def relay(ws, connect=sarvam_connect, language_code="unknown"):
    """Pump audio from the browser socket `ws` to Sarvam and results back. Returns the full transcript."""
    transcript = ""
    done_evt = asyncio.Event()
    segment_evt = asyncio.Event()
    audio_seconds = 0.0

    async with connect(language_code) as sock:
        async def from_sarvam():
            nonlocal transcript
            try:
                async for msg in sock:
                    kind = getattr(msg, "type", None)
                    data = getattr(msg, "data", None)
                    if kind == "events" and getattr(data, "signal_type", None) == "START_SPEECH":
                        await ws.send_json({"type": "speech"})
                    elif kind == "data":
                        seg = new_text(transcript, getattr(data, "transcript", "") or "")
                        segment_evt.set()
                        if seg:
                            transcript = (transcript + " " + seg).strip()
                            await ws.send_json({"type": "text", "text": seg, "full": transcript,
                                                "language_code": getattr(data, "language_code", None)})
                    elif kind == "error":
                        await ws.send_json({"type": "error", "error": str(getattr(data, "message", data))[:200]})
            except Exception as e:  # socket closed by either side
                log.info("sarvam stream ended: %s", type(e).__name__)
            finally:
                done_evt.set()

        reader = asyncio.create_task(from_sarvam())
        since_flush = 0.0
        try:
            while audio_seconds < MAX_SESSION_SECONDS:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    chunk = msg["bytes"][:MAX_CHUNK_BYTES]
                    secs = len(chunk) / BYTES_PER_SECOND
                    audio_seconds += secs
                    since_flush += secs
                    await sock.transcribe(audio=base64.b64encode(chunk).decode(), encoding="audio/wav", sample_rate=SAMPLE_RATE)
                    # Flush at a pause so words are not cut in half; force it if someone talks without stopping.
                    if (since_flush >= MIN_FLUSH_SECONDS and rms(chunk) < PAUSE_RMS) or since_flush >= MAX_FLUSH_SECONDS:
                        await sock.flush()
                        since_flush = 0.0
                elif msg.get("text") and json.loads(msg["text"]).get("type") == "stop":
                    break
        finally:
            try:
                # Let Sarvam end the segment itself, as after a real pause, instead of forcing a decode of the tail.
                segment_evt.clear()
                if since_flush > 0:
                    silence = bytes(int(BYTES_PER_SECOND * 0.2))
                    for _ in range(5):
                        await sock.transcribe(audio=base64.b64encode(silence).decode(), encoding="audio/wav", sample_rate=SAMPLE_RATE)
                        await asyncio.sleep(0.2)
                        if segment_evt.is_set():
                            break
                await asyncio.wait_for(segment_evt.wait(), FINAL_WAIT_SECONDS)
            except (asyncio.TimeoutError, Exception):
                pass
            reader.cancel()
    return transcript
