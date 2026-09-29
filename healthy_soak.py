"""Healthy soak: N parallel healthy sessions, S seconds each. Counts false-alarm episodes.
Run: python healthy_soak.py [sessions=6] [seconds=900]
"""
import asyncio, json, sys
import websockets

WS = "ws://127.0.0.1:8000/ws/telemetry"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
S = int(sys.argv[2]) if len(sys.argv) > 2 else 900


async def one(i):
    ticks = []
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "reset"}))
        await ws.send(json.dumps({"type": "fault_config", "config": {}}))
        for _ in range(S):
            h = json.loads(await ws.recv())["health"]
            ticks.append((h["t"], bool(((h.get("anomaly") or {}).get("persistence") or {}).get("met"))))
    eps, cur = [], None
    for t, a in ticks:
        if a and cur is None:
            cur = [t, t]
        elif a:
            cur[1] = t
        elif cur:
            eps.append(tuple(cur)); cur = None
    if cur:
        eps.append(tuple(cur))
    return i, eps


async def main():
    res = await asyncio.gather(*(one(i) for i in range(N)))
    total = 0
    for i, eps in res:
        total += len(eps)
        print(f"session {i}: {len(eps)} episodes {eps}")
    hrs = N * S / 3600
    print(f"TOTAL {total} false-alarm episodes in {N} x {S}s = {hrs:.2f} engine-hours -> {total/hrs:.2f} per hour")

asyncio.run(main())
