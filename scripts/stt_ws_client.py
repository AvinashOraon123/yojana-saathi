"""Act like the browser microphone: stream a synthesized clip to our /ws/stt and print what comes back, with timing.

  python scripts/stt_ws_client.py ws://localhost:5001/ws/stt?language=Hindi "text to speak" hi-IN
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets  # noqa: E402

from scripts.stt_stream_probe import pcm16k  # noqa: E402


async def main(url, text, lang):
    pcm = pcm16k(text, lang)
    t0 = time.time()
    async with websockets.connect(url) as ws:
        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                print(f"{time.time() - t0:5.1f}s", {k: v for k, v in m.items() if k != "type"} if m["type"] != "speech" else "(speech started)", m["type"])
        task = asyncio.create_task(reader())
        for i in range(0, len(pcm), 8192):  # 256 ms chunks, real-time pace
            await ws.send(pcm[i:i + 8192])
            await asyncio.sleep(0.256)
        await ws.send(json.dumps({"type": "stop"}))
        await asyncio.wait_for(task, 15)


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
