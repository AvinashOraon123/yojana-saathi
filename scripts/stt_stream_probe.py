"""Probe Sarvam's streaming speech-to-text: stream a synthesized clip in real time and print every event with timing.

  python scripts/stt_stream_probe.py "text to speak" hi-IN
"""
import asyncio
import audioop
import base64
import io
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sarvamai import AsyncSarvamAI  # noqa: E402

from yojana import settings  # noqa: E402
from yojana.sarvam_client import Sarvam  # noqa: E402


def pcm16k(text, lang):
    wav = base64.b64decode(Sarvam().tts(text, lang))
    with wave.open(io.BytesIO(wav)) as w:
        pcm, rate, ch = w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
    if ch == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 16000, None)
    return pcm


FLUSH_EVERY = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 0  # flush every N chunks (4 = 1 s)


async def main(text, lang):
    pcm = pcm16k(text, lang)
    print(f"clip: {len(pcm) / 32000:.1f}s of 16 kHz PCM")
    client = AsyncSarvamAI(api_subscription_key=settings.SARVAM_API_KEY)
    t0 = time.time()
    async with client.speech_to_text_streaming.connect(
            language_code=lang, model="saaras:v4", mode="transcribe", sample_rate="16000",
            input_audio_codec="pcm_s16le", vad_signals="true", flush_signal="true") as ws:

        async def reader():
            try:
                async for msg in ws:
                    d = getattr(msg, "data", None)
                    body = getattr(d, "transcript", None) or getattr(d, "signal_type", None) or d
                    print(f"{time.time() - t0:5.1f}s  {msg.type:7} {str(body)[:120]!r}")
            except Exception as e:
                print("reader ended:", type(e).__name__, str(e)[:100])

        task = asyncio.create_task(reader())
        chunk = 8000  # 250 ms of 16 kHz mono PCM16
        for i in range(0, len(pcm), chunk):
            await ws.transcribe(audio=base64.b64encode(pcm[i:i + chunk]).decode(), encoding="audio/wav", sample_rate=16000)
            await asyncio.sleep(0.25)  # real-time pace, like a microphone
            if FLUSH_EVERY and (i // chunk + 1) % FLUSH_EVERY == 0:
                await ws.flush()
        await ws.flush()
        await asyncio.sleep(6)
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
