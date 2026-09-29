"""Step 5: inject every fault through the real WebSocket, all scenarios in parallel.

Each connection is its own engine session, so they do not interfere.
Run:  python e2e_fault_test.py [seconds]      (default 360; faults start at t=60)
Writes e2e_results.json next to this file.
"""
import asyncio, json, sys, pathlib
import websockets

WS = "ws://127.0.0.1:8000/ws/telemetry"
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 360
T0 = 60

# (label, config). Rates as in the UI / sigma_generator; sensor-drift rates for
# mapSensor and lambdaSensor are OUR choice (hPa/min, lambda/min).
SCEN = [
    ("healthy",       {}),
    ("injector",      {"injector":    {"startT": T0, "cyl": 1, "rate": 0.045}}),
    ("misfire",       {"misfire":     {"startT": T0, "cyl": 2, "rate": 0.30}}),
    ("detonation",    {"detonation":  {"startT": T0, "cyl": 0, "rate": 0.50}}),
    ("turbo",         {"turbo":       {"startT": T0, "rate": 0.040}}),
    ("cooling",       {"cooling":     {"startT": T0, "rate": 0.035}}),
    ("coolantPump",   {"coolantPump": {"startT": T0, "rate": 0.10}}),
    ("bearing",       {"bearing":     {"startT": T0, "rate": 0.050}}),
    ("oilLeak",       {"oilLeak":     {"startT": T0, "rate": 0.045}}),
    ("ringWear",      {"ringWear":    {"startT": T0, "rate": 0.040}}),
    ("fuelFilter",    {"fuelFilter":  {"startT": T0, "rate": 0.035}}),
    ("chtSensor",     {"chtSensor":   {"startT": T0, "cyl": 2, "rate": 10.0}}),
    ("egtSensor",     {"egtSensor":   {"startT": T0, "cyl": 3, "rate": 8.0}}),
    ("mapSensor",     {"mapSensor":   {"startT": T0, "rate": 20.0}}),
    ("lambdaSensor",  {"lambdaSensor": {"startT": T0, "rate": 0.02}}),
    ("unmodelled",    {"unmodelled":  {"startT": T0, "rate": 0.10}}),
]


async def run(label, cfg):
    rows = []
    async with websockets.connect(WS, max_size=None) as ws:
        await ws.send(json.dumps({"type": "reset"}))
        await ws.send(json.dumps({"type": "fault_config", "config": cfg}))
        for _ in range(DUR):
            m = json.loads(await ws.recv())
            h = m["health"]
            an, dg = h.get("anomaly") or {}, h.get("diagnosis") or {}
            top = dg.get("top") or []
            rows.append({
                "t": h["t"],
                "alarm": bool((an.get("persistence") or {}).get("met")),
                "score": an.get("score"),
                "top": [(x.get("fault"), x.get("p"), x.get("cylinder")) for x in top[:2]],
                "sensor": dg.get("is_sensor_fault"),
                "ambiguous": dg.get("ambiguous"),
                "ml": (h.get("ml_status") or {}).get("active"),
                "novelty": h.get("novelty"),
            })
    return label, rows


def summarise(label, rows):
    fault_started = [r for r in rows if r["t"] >= T0]
    first = next((r for r in fault_started if r["alarm"]), None)
    pre_alarms = sum(1 for r in rows if r["t"] < T0 and r["alarm"])
    out = {"scenario": label, "ml_active": all(r["ml"] for r in rows),
           "alarms_before_fault_start": pre_alarms}
    if label == "healthy":
        out["alarm_ticks_total"] = sum(1 for r in rows if r["alarm"])
        return out
    if first is None:
        out["first_alarm_s_after_start"] = None
        return out
    # served diagnosis 30 s after alarm, and at the end
    later = [r for r in rows if r["t"] >= first["t"] + 30] or [rows[-1]]
    out.update({
        "first_alarm_s_after_start": first["t"] - T0,
        "served_at_alarm+30s": later[0]["top"], "sensor_fault_flag": later[0]["sensor"],
        "ambiguous": later[0]["ambiguous"], "final_top": rows[-1]["top"],
        "novelty_at_end": rows[-1]["novelty"],
    })
    return out


async def main():
    res = await asyncio.gather(*(run(l, c) for l, c in SCEN))
    summ = [summarise(l, r) for l, r in res]
    here = pathlib.Path(__file__).parent
    (here / "e2e_results.json").write_text(json.dumps(
        {"summary": summ, "raw": {l: r for l, r in res}}, indent=1, default=str))
    for s in summ:
        print(json.dumps(s, default=str))


asyncio.run(main())
