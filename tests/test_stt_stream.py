import asyncio
import json
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeSarvam
from yojana import stt_stream
from yojana.api import create_app
from yojana.store import Store, load_demo_data


def pcm(seconds, amplitude):
    n = int(16000 * seconds)
    return struct.pack(f"<{n}h", *([amplitude] * n))


class FakeSocket:
    """Stands in for Sarvam's streaming socket: records audio and flushes, replays scripted messages."""

    def __init__(self, script):
        self.script, self.audio_chunks, self.flushes = script, 0, 0
        self.queue = asyncio.Queue()

    async def transcribe(self, audio, encoding, sample_rate):
        self.audio_chunks += 1
        for after, msg in self.script:
            if after == self.audio_chunks:
                self.queue.put_nowait(msg)

    async def flush(self):
        self.flushes += 1

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        while True:
            yield await self.queue.get()


def data(text, lang="hi-IN"):
    return SimpleNamespace(type="data", data=SimpleNamespace(transcript=text, language_code=lang))


def event(signal):
    return SimpleNamespace(type="events", data=SimpleNamespace(signal_type=signal))


def client_with(sock):
    @asynccontextmanager
    async def connect(language_code):
        sock.language_code = language_code
        yield sock

    shared = Store(":memory:")
    load_demo_data(shared)
    app = create_app(store_factory=lambda: shared, sarvam=FakeSarvam(), demo=False, stt_connect=connect)
    return TestClient(app)


def test_text_streams_back_as_audio_arrives_then_done():
    sock = FakeSocket([(1, event("START_SPEECH")), (2, data("मैं किसान हूँ।"))])
    with client_with(sock).websocket_connect("/ws/stt?language=Hindi") as ws:
        ws.send_bytes(pcm(0.25, 1000))
        assert ws.receive_json() == {"type": "speech"}
        ws.send_bytes(pcm(0.25, 1000))
        msg = ws.receive_json()
        assert msg["type"] == "text" and msg["text"] == "मैं किसान हूँ।" and msg["full"] == "मैं किसान हूँ।"
        assert msg["language_code"] == "hi-IN"
        ws.send_text(json.dumps({"type": "stop"}))
        done = ws.receive_json()
        assert done["type"] == "done" and done["full"] == "मैं किसान हूँ।"
    assert sock.language_code == "hi-IN"


def test_unknown_language_name_means_auto_detect():
    sock = FakeSocket([])
    with client_with(sock).websocket_connect("/ws/stt?language=auto") as ws:
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert sock.language_code == "unknown"


def test_flushes_at_a_pause_not_in_the_middle_of_speech():
    sock = FakeSocket([])
    with client_with(sock).websocket_connect("/ws/stt?language=English") as ws:
        for _ in range(4):               # 1 s of loud speech: too early to flush
            ws.send_bytes(pcm(0.25, 6000))
        assert sock.flushes == 0
        for _ in range(3):               # speech continues past 1.5 s, still loud: no flush
            ws.send_bytes(pcm(0.25, 6000))
        assert sock.flushes == 0
        ws.send_bytes(pcm(0.25, 10))     # a quiet gap after 1.75 s of speech: flush here
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert sock.flushes == 1


def test_continuous_speech_is_flushed_after_the_maximum_window():
    sock = FakeSocket([])
    with client_with(sock).websocket_connect("/ws/stt?language=English") as ws:
        for _ in range(21):              # 5.25 s of unbroken loud speech
            ws.send_bytes(pcm(0.25, 6000))
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert sock.flushes >= 1


def test_repeated_tail_fragment_is_dropped():
    assert stt_stream.new_text("I am a farmer. Is there a scheme?", "a scheme?") == ""
    assert stt_stream.new_text("I am a farmer.", "Is there a scheme?") == "Is there a scheme?"
    assert stt_stream.new_text("", "  ") == ""


def test_rms_distinguishes_speech_from_pause():
    assert stt_stream.rms(pcm(0.1, 6000)) > stt_stream.PAUSE_RMS > stt_stream.rms(pcm(0.1, 10))


def test_demo_mode_refuses_voice():
    shared = Store(":memory:")
    load_demo_data(shared)
    c = TestClient(create_app(store_factory=lambda: shared, demo=True))
    with c.websocket_connect("/ws/stt") as ws:
        assert ws.receive_json()["type"] == "error"


def test_oversized_chunk_is_truncated_not_forwarded_whole():
    sock = FakeSocket([])
    with client_with(sock).websocket_connect("/ws/stt?language=English") as ws:
        ws.send_bytes(pcm(6.0, 10))      # 6 s in one message; the server caps a chunk at 2 s
        ws.send_text(json.dumps({"type": "stop"}))
        ws.receive_json()
    assert sock.audio_chunks >= 1
