"""
Mission history: replay of past (completed) missions — PS requirement E.

File-based, consistent with the rest of the project (no database anywhere). Every websocket session
records a COMPACT frame each tick (only what a replay/report needs, not the full wire frame) and, when
that run ends (a 'reset' message, or the socket closing), writes it as one JSON file under
data/missions/. Nothing here can affect what is served live: every call from main.py is wrapped so a
bug in recording never breaks the live telemetry stream.

    GET  /missions        -> list of past missions, newest first (metadata only)
    GET  /missions/{id}   -> one mission's full recorded frames, for the replay drawer

This is a flight recorder, not live evidence: recording start/stop is driven by user actions (reset,
disconnect), so a saved mission's "worst finding" is exactly what the ML actually alarmed live — nothing
here reads the injected fault to summarise it.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "missions"
MIN_TICKS_TO_SAVE = 8          # shorter than this is a false start, not a mission
MAX_LISTED = 50


class MissionRecorder:
    """One instance per websocket connection. append() every tick; flush() on reset or disconnect."""

    def __init__(self, engine_id: str):
        self.engine_id = engine_id
        self.frames: list[dict] = []
        self.started_wall = time.time()

    def append(self, t: float, slow: dict, health: dict) -> None:
        d = health.get("diagnosis") or {}
        top = (d.get("top") or [{}])[0]
        self.frames.append({
            "t": t,
            "rpm": slow.get("rpm"), "map_hPa": slow.get("map_hPa"),
            "cht_C": slow.get("cht_C"), "egt_C": slow.get("egt_C"),
            "oil_press_bar": slow.get("oil_press_bar"), "oil_temp_C": slow.get("oil_temp_C"),
            "altitude_ft": slow.get("altitude_ft"), "throttle_pct": slow.get("throttle_pct"),
            "fault": top.get("fault"), "fault_p": top.get("p"), "cylinder": top.get("cylinder"),
            "is_sensor_fault": d.get("is_sensor_fault"), "alarm": bool(((health.get("anomaly") or {}).get("persistence") or {}).get("met")),
            "limits_state": health.get("limits_state"),
            "recommended": (health.get("mission") or {}).get("recommended"),
            "reported_h": (health.get("rul") or {}).get("reported_h"),
        })

    def flush(self) -> str | None:
        """Write the recording if it is long enough to be a real mission. Returns the saved id, or None."""
        if len(self.frames) < MIN_TICKS_TO_SAVE:
            self.frames = []
            return None
        fault_ticks = [f for f in self.frames if f["fault"] and f["fault"] not in ("healthy", "unknown")]
        first_alarm = next((f for f in self.frames if f["alarm"]), None)
        worst = max(fault_ticks, key=lambda f: f.get("fault_p") or 0) if fault_ticks else None
        meta = {
            "id": uuid.uuid4().hex[:12],
            "engine": self.engine_id,
            "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started_wall)),
            "duration_s": round(self.frames[-1]["t"] - self.frames[0]["t"], 1),
            "n_ticks": len(self.frames),
            "healthy": worst is None,
            "top_fault": worst["fault"] if worst else None,
            "top_cylinder": worst["cylinder"] if worst else None,
            "first_alarm_s": (first_alarm["t"] - self.frames[0]["t"]) if first_alarm else None,
            "final_recommendation": self.frames[-1]["recommended"],
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / f"{meta['id']}.json").write_text(
            json.dumps({"meta": meta, "frames": self.frames}, indent=0), encoding="utf-8")
        self.frames = []
        return meta["id"]


def list_missions() -> list[dict]:
    if not DATA_DIR.exists():
        return []
    out = []
    for p in DATA_DIR.glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8"))["meta"])
        except Exception:
            continue
    out.sort(key=lambda m: m.get("recorded_at", ""), reverse=True)
    return out[:MAX_LISTED]


def load_mission(mission_id: str) -> dict | None:
    p = DATA_DIR / f"{mission_id}.json"
    if not p.exists() or ".." in mission_id or "/" in mission_id or "\\" in mission_id:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
