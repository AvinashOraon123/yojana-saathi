"""Live evaluation against the real Sarvam API. Spends credits, so it is NOT part of `pytest`.

  python -m tests.live_eval golden [N]      native-language golden questions (default: all 50)
  python -m tests.live_eval roundtrip       translate 20 scheme names en -> hi -> en and compare
  python -m tests.live_eval hallucination   ask about schemes that do not exist
  python -m tests.live_eval speech          TTS then STT round trip on one sentence
"""
import difflib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yojana import qa, settings  # noqa: E402
from yojana.sarvam_client import Sarvam  # noqa: E402
from yojana.store import Store, load_demo_data  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "golden.json").read_text(encoding="utf-8"))


def demo_store():
    s = Store(":memory:")
    load_demo_data(s)
    return s


def golden(n):
    sarvam, store = Sarvam(), demo_store()
    hits1 = hits3 = 0
    items = GOLDEN[:n] if n >= len(GOLDEN) else GOLDEN[::max(1, len(GOLDEN) // n)][:n]  # spread across languages
    for g in items:
        t0 = time.time()
        prep = qa.prepare(store, sarvam, g["q"], g["lang"])
        got = [r["title"] for r in prep["records"]]
        in1, in3 = got[:1] == [g["expect"]], g["expect"] in got[:3]
        hits1, hits3 = hits1 + in1, hits3 + in3
        print(f"{'OK ' if in3 else 'MISS'} {g['lang']} {time.time() - t0:4.1f}s  {g['expect']!r:48} <- {prep['english']!r} -> {got[:3]}")
    print(f"\nlive golden: top-1 {hits1}/{len(items)}  top-3 {hits3}/{len(items)}")


def roundtrip():
    sarvam, store = Sarvam(), demo_store()
    names = [r["title"] for r in store.all()][:20]
    scores = []
    for name in names:
        hi = sarvam.translate(name, "en-IN", "hi-IN")
        back = sarvam.translate(hi, "hi-IN", "en-IN")
        ratio = difflib.SequenceMatcher(None, name.lower(), back.lower()).ratio()
        scores.append(ratio)
        print(f"{ratio:.2f}  {name!r} -> {hi!r} -> {back!r}")
    print(f"\nround-trip similarity: mean {sum(scores) / len(scores):.2f}, >=0.7 in {sum(s >= 0.7 for s in scores)}/{len(scores)}")


def hallucination():
    sarvam, store = Sarvam(), demo_store()
    for q in ["Tell me about the Xyzzy Bharat Yojana", "What is the last date for the Quantum Farmers Samriddhi Scheme?",
              "Explain the Pradhan Mantri Zorblax Scholarship"]:
        out = qa.run_pipeline(store, sarvam, q, "en-IN")
        print(("PASS" if out["kind"] == "not_found" else "FAIL"), q, "->", out["kind"], out["answer"][:90])


def speech():
    sarvam = Sarvam()
    text = "I am a farmer and I want income support."
    audio = sarvam.tts(text, "en-IN")
    import base64
    heard, lang = sarvam.stt(base64.b64decode(audio), "speech.wav")
    print("spoken :", text, "\nheard  :", heard, "\nlang   :", lang)


if __name__ == "__main__":
    if settings.DEMO_MODE:
        raise SystemExit("Set SARVAM_API_KEY in .env first.")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "golden"
    {"golden": lambda: golden(int(sys.argv[2]) if len(sys.argv) > 2 else 50), "roundtrip": roundtrip,
     "hallucination": hallucination, "speech": speech}[cmd]()
