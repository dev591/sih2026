"""
PRAMANA — WebSocket telemetry server.

Serves a 1 Hz stream of {slow, fast, health, predicted, slowB} frames at
ws://localhost:8000/ws/telemetry. The receive loop runs concurrently with the
send loop, so the Fault Console injects faults over the same socket.

Fault state is per-connection: two browser tabs cannot fault each other.

WHAT A FRAME MAY CONTAIN. Every value is either a (simulated) sensor reading,
something the twin computed from those readings, or explicitly null with its
field listed as unmodelled. The injected fault configuration drives ONLY the
simulated engine's physics; no served field reads it. Until 2026-09-24 three
did — mission probabilities, the RUL component and damage, and a fallback
"diagnosis" — i.e. the dashboard could show the answer key.
"""

import asyncio
import json
import math
import sys
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from parity.residuals import EXTRA_NAMES, extra_names
from twin.profiles import load_engine_profile
from twin.session import EngineSession

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from ml.inference import InferencePipeline
    ML_IMPORT_ERROR: str | None = None
except Exception as exc:                      # torch missing, weights absent…
    print(f"[main] ML layer unavailable ({exc}); serving physics-only frames.")
    InferencePipeline = None
    ML_IMPORT_ERROR = str(exc)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# Most recent frame sent to ANY websocket client, for read-only consumers (the
# explain assistant). Each websocket owns its own engine session, so a consumer
# that opened its own connection would see a fresh healthy engine, not the one
# on screen. Holds exactly what was sent; nothing is derived or added here.
_LATEST: dict = {"frame": None}
_assistant = None   # set below once assistant_api imports


@app.get("/latest")
def latest_frame():
    if _LATEST["frame"] is None:
        return {"available": False, "frame": None}
    return {"available": True, "frame": _LATEST["frame"]}


cfg = load_engine_profile()   # reads PRAMANA_ENGINE env var, defaults to vrde_180
N_CYL: int = cfg["geometry"]["cylinders"]
_CFG_DIR = Path(__file__).parent / "config"
# Engine-specific σ vectors live under config/engines/<id>/; fall back to the
# legacy shared location so existing deployments are not broken on upgrade.
_ENG_ID = cfg["profile"]["id"]
_ENG_SIGMA_DIR = _CFG_DIR / "engines" / _ENG_ID
_sigma_dir = _ENG_SIGMA_DIR if _ENG_SIGMA_DIR.exists() else _CFG_DIR
sigma_vec: list[float] = json.loads((_sigma_dir / "sigma_vector.json").read_text())
sigma_ext: list[float] = json.loads((_sigma_dir / "sigma_ext.json").read_text())["sigma"]

# The simulated engine A is one specific installation (its sensor offsets come
# from this seed). The ML layer needs that installation's commissioning
# baseline — see ml/commission.py.
INSTALLATION_A = 42

_CHT_LIMIT   = cfg["limits"]["cht_C"]
_MAP_LIMIT   = cfg["limits"]["manifold_pressure_hPa"]["max_continuous"]
_MAP_TAKEOFF = cfg["limits"]["manifold_pressure_hPa"].get("takeoff", _MAP_LIMIT)
_RPM_LIMIT   = cfg["ratings"]["max_continuous_rpm"]
_OIL_P_MIN   = cfg["limits"]["oil_pressure_bar"]["min_above_2500rpm"]
_OIL_T_LIMIT = cfg["limits"]["oil_temp_C"]
_GB_OIL_LIMIT = cfg["limits"]["gearbox_oil_C"]
_COOLANT_LIMIT = cfg["limits"]["coolant_C"]


def _target_map_hpa(power_frac: float, map_limit: float = None, map_takeoff: float = None) -> float:
    """The FADEC's commanded MAP at a power fraction — the same schedule
    twin/mvem.py's wastegate loop runs, on the published limits."""
    ml_, mt_ = map_limit or _MAP_LIMIT, map_takeoff or _MAP_TAKEOFF
    cruise_frac = 0.90
    if power_frac <= cruise_frac:
        return power_frac * ml_
    extra = (power_frac - cruise_frac) / (1.0 - cruise_frac)
    return ml_ + extra * (mt_ - ml_)


# ---------------------------------------------------------------------------
# UNMODELLED CHANNELS
# ---------------------------------------------------------------------------
# The schema carries these fields (PS component B names battery, alternator and
# injection timing), but nothing in the engine model computes them. They used to
# be served as frozen numbers (27.8 V, 14.2 A, 12.4°, 900 bar, 0.42 g, 140 bar,
# 6.4°) that the dashboard displayed as live readings. They are now null, and
# every frame lists them, so the dashboard can say "not modelled".
UNMODELLED_FIELDS = [
    "slow.fuel_rail_bar", "slow.inj_timing_deg", "slow.bus_voltage_V",
    "slow.alternator_A", "slow.vib_rms_g", "fast.order_0p5_phase_deg",
    "fast.order_1p0_mag", "fast.order_2p0_mag", "fast.vib_band_rms",
    "health.virtual.peak_cyl_press_bar", "health.virtual.knock_margin_deg",
    "health.virtual.comb_efficiency", "health.mission.derate_cost_min_on_station",
]
UNMODELLED_SLOW = {"fuel_rail_bar": None, "inj_timing_deg": None,
                   "bus_voltage_V": None, "alternator_A": None}

# ---------------------------------------------------------------------------
# MISSION INPUTS — scenario assumptions, not engine facts
# ---------------------------------------------------------------------------
# Nothing published gives tank size or mission geometry (airframe specs, not
# engine specs). provenance: assumed. Everything downstream is computed.
MISSION_FUEL_ENDURANCE_H = 3.0
MISSION_FUEL_KG = cfg["fuel"]["rated_fuel_flow_kgps"] * 3600.0 * MISSION_FUEL_ENDURANCE_H
FUEL_RESERVE_KG = cfg["fuel"]["rated_fuel_flow_kgps"] * 60.0 * 30.0
RTB_TRANSIT_S = 45 * 60.0
ASSUMED_MISSION_INPUTS = {
    "fuel_load_kg": round(MISSION_FUEL_KG, 1), "reserve_kg": round(FUEL_RESERVE_KG, 1),
    "rtb_transit_min": RTB_TRANSIT_S / 60.0,
    "degradation_rate_independent_of_power": True,
}


class EngineCtx:
    """Everything engine-specific one websocket session needs, read from that engine's profile.

    The default engine (PRAMANA_ENGINE, else vrde_180) keeps using the module-level values above, so a
    connection without ?engine= behaves exactly as before. Any other engine gets ITS OWN profile, noise
    levels and trained model; if it has no noise levels there is no session, and if it has no trained
    model the ML layer reports 'no diagnosis' — it never borrows another engine's."""

    def __init__(self, engine_id: str):
        self.cfg = load_engine_profile(engine_id)
        self.id = self.cfg["profile"]["id"]
        self.is_default = self.id == _ENG_ID
        lim, rat = self.cfg["limits"], self.cfg["ratings"]
        if self.is_default:
            self.sigma_vec, self.sigma_ext = sigma_vec, sigma_ext
            self.weights_dir = None                     # InferencePipeline's own default (ml/weights/v2)
        else:
            d = _CFG_DIR / "engines" / self.id
            if not (d / "sigma_vector.json").exists():
                raise FileNotFoundError(f"engine '{self.id}' has no noise levels yet (backend/config/engines/{self.id}); "
                                        "add it from the app so they are generated")
            self.sigma_vec = json.loads((d / "sigma_vector.json").read_text())
            self.sigma_ext = json.loads((d / "sigma_ext.json").read_text())["sigma"]
            wd = Path(__file__).resolve().parent.parent / "ml" / "weights" / "engines" / self.id
            self.weights_dir = wd if (wd / "config.json").exists() else None
        self.n_cyl = int(self.cfg["geometry"]["cylinders"])
        if not 2 <= self.n_cyl <= 8:
            raise ValueError(f"engine '{self.id}' has {self.n_cyl} cylinders; the residual layout, ML and 3D model are built for 2 to 8 cylinders")
        self.cht_limit = lim["cht_C"]
        mp = lim["manifold_pressure_hPa"]
        self.map_limit = mp["max_continuous"]
        self.map_takeoff = mp.get("takeoff", self.map_limit)
        self.rpm_limit = rat["max_continuous_rpm"]
        self.oil_p_min = (lim.get("oil_pressure_bar") or {}).get("min_above_2500rpm")
        self.oil_t_limit = lim.get("oil_temp_C")
        self.gb_oil_limit = lim.get("gearbox_oil_C")        # None on an engine with no gearbox oil node
        self.coolant_limit = lim.get("coolant_C")           # None on an air-cooled engine
        ff = self.cfg["fuel"]["rated_fuel_flow_kgps"]
        self.fuel_kg = ff * 3600.0 * MISSION_FUEL_ENDURANCE_H
        self.reserve_kg = ff * 60.0 * 30.0
        self.assumed_inputs = {**ASSUMED_MISSION_INPUTS, "fuel_load_kg": round(self.fuel_kg, 1),
                               "reserve_kg": round(self.reserve_kg, 1)}


_CTX: dict[str, EngineCtx] = {}


def get_ctx(engine_id: str | None) -> EngineCtx:
    key = engine_id or _ENG_ID
    if key not in _CTX:
        _CTX[key] = EngineCtx(key)
    return _CTX[key]


def _limits_state(slow_vals: dict, ctx: "EngineCtx | None" = None) -> str:
    """'green' | 'caution' | 'exceeded' — what a threshold-only system shows."""
    c = ctx or get_ctx(None)
    cht_max = max(slow_vals["cht_C"])
    if cht_max > c.cht_limit:
        return "exceeded"
    if cht_max > c.cht_limit * 0.90:
        return "caution"
    if slow_vals["map_hPa"] > c.map_limit:
        return "exceeded"
    if slow_vals["rpm"] > c.rpm_limit:
        return "caution"
    if c.oil_p_min is not None and slow_vals["oil_press_bar"] < c.oil_p_min and slow_vals["rpm"] > 2500:
        return "caution"
    if c.oil_t_limit is not None and slow_vals["oil_temp_C"] > c.oil_t_limit:
        return "exceeded"
    gb_oil = slow_vals.get("gearbox_oil_C")
    if gb_oil is not None and c.gb_oil_limit is not None and gb_oil > c.gb_oil_limit:
        return "exceeded"
    coolant = slow_vals.get("coolant_temp_C")
    if coolant is not None and c.coolant_limit is not None:
        if coolant > c.coolant_limit:
            return "exceeded"
        if coolant > c.coolant_limit * 0.95:
            return "caution"
    return "green"


def _json_safe(obj):
    """numpy scalars → Python; NaN/±inf → null (they are not valid JSON and
    browsers reject the whole frame)."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    return obj


def _slow_frame(t, engine_id, m, tick, throttle_pct):
    return {
        "schema": "pramana.slow.v1", "t": float(t), "engine_id": engine_id, "seq": int(t),
        "rpm": m["rpm"], "map_hPa": m["map_hPa"], "iat_K": m["iat_K"],
        "cht_C": m["cht_C"], "egt_C": m["egt_C"],
        "oil_press_bar": m["oil_press_bar"], "oil_temp_C": m["oil_temp_C"],
        "fuel_flow_kgps": m["fuel_flow_kgps"], "lambda": m["lambda_val"],
        "turbo_rpm": m["turbo_rpm"], "comp_out_p_hPa": m["comp_out_p_hPa"],
        "comp_out_T_K": m["comp_out_T_K"], "throttle_pct": float(throttle_pct),
        "vib_rms_g": None, "altitude_ft": float(tick.altitude_ft),
        "oat_K": m["oat_K"], "p_amb_hPa": m["p_amb_hPa"], "tas_mps": m["tas_mps"],
        "prop_rpm": m["prop_rpm"], "blade_angle_deg": m["blade_angle_deg"],
        "gearbox_oil_C": m["gearbox_oil_C"], "coolant_temp_C": m["coolant_temp_C"],
        **UNMODELLED_SLOW,
    }


def _ml_unavailable_blocks(reason: str) -> dict:
    """What the frame says when the ML layer is down: that it is down. It used
    to fall back to a 'diagnosis' built from the injected fault — the answer
    key, dressed as a classifier output."""
    return {
        "anomaly": {"score": None, "threshold": None,
                    "persistence": {"n": 0, "of": 5, "met": False}},
        "diagnosis": {"top": [{"fault": "unknown", "p": 0.0, "cylinder": None,
                               "source": "classifier"}],
                      "is_sensor_fault": False, "ambiguous": False, "probe": None,
                      "unavailable": True},
        "theta": None,
        "rul": {"component": "none", "physics_h": None, "network_h": None, "p10_h": None,
                "p50_h": None, "p90_h": None, "reported_h": None, "heads_disagree": False},
        "mission_p": None,
    }


@app.websocket("/ws/telemetry")
async def telemetry_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    # ?engine=<id> picks the engine for THIS connection; without it, the default engine as before.
    try:
        ctx = get_ctx(websocket.query_params.get("engine"))
    except Exception as exc:
        await websocket.send_json({"error": f"cannot start engine: {exc}"})
        await websocket.close(code=4004)
        return
    client_id = websocket.query_params.get("client")     # identifies this browser tab for the assistant
    session = EngineSession(ctx.cfg, ctx.sigma_vec, ctx.sigma_ext)
    ml, ml_error = None, ML_IMPORT_ERROR
    if InferencePipeline is not None:
        if not ctx.is_default and ctx.weights_dir is None:
            ml_error = f"no trained model for engine '{ctx.id}' yet — train it from the app (high level)"
        else:
            try:
                ml = (InferencePipeline(installation=INSTALLATION_A) if ctx.weights_dir is None else
                      InferencePipeline(installation=INSTALLATION_A, weights_dir=ctx.weights_dir))
            except Exception as exc:
                print(f"[main] ML pipeline failed to load ({exc})")
                ml_error = str(exc)
    fuel = {"burned_kg": 0.0}

    async def _receive() -> None:
        try:
            async for msg in websocket.iter_json():
                kind = msg.get("type")
                if kind == "fault_config":
                    # Physics only. The detector is NOT reset: a real engine
                    # does not tell its monitoring system a fault has started.
                    session.fault_config = msg.get("config", {}) or {}
                elif kind == "altitude":
                    session.command_altitude(float(msg.get("ft", 11000.0)))
                elif kind == "reset":
                    session.fault_config = {}
                    session.alt_cmd = None
                    if ml is not None:
                        ml.reset()
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def _send() -> None:
        try:
            while True:
                tick = session.step()
                t, mA, pA = tick.t, tick.measuredA, tick.predictedA
                rho, rho_ext = tick.rho, tick.rho_ext

                limits_vals = {k: mA[k] for k in ("cht_C", "map_hPa", "rpm", "oil_press_bar",
                                                  "oil_temp_C", "gearbox_oil_C", "coolant_temp_C")}
                lim_state = _limits_state(limits_vals, ctx)

                # Fuel state from the METERED flow — the flow meter sees a
                # degraded engine's real consumption; the nominal twin's
                # predicted flow (used before) never reacted to a fault.
                ff = max(mA["fuel_flow_kgps"], 1e-9)
                fuel["burned_kg"] += ff * 1.0
                remaining = max(0.0, ctx.fuel_kg - fuel["burned_kg"])
                usable = max(0.0, remaining - ctx.reserve_kg)
                endurance_s = usable / ff
                # No torque sensor: power is the twin's estimate for this
                # operating point, so metered fuel over it is the DEGRADED bsfc.
                kW = max(tick.out_twinA["brake_power_kW"], 0.01)
                bsfc = (ff * 3_600_000.0) / kW

                slow = _slow_frame(t, "A", mA, tick, tick.throttle_pct)
                slowB = _slow_frame(t, "B", tick.measuredB, tick, tick.throttle_pct) if tick.measuredB else None
                predicted = {k: pA[k] for k in ("egt_C", "cht_C", "oil_press_bar",
                                                "map_hPa", "fuel_flow_kgps")}
                det = tick.plant_params["detonation_sev"]
                fast = {
                    "schema": "pramana.fastfeat.v1", "t": float(t), "engine_id": "A",
                    "order_0p5_mag": mA["ripple"],
                    # A knock sensor reads the knock that is really happening.
                    "knock_intensity": [float(max(0.0, det[i] + np.random.normal(0, 0.01)))
                                        for i in range(ctx.n_cyl)],
                    "order_0p5_phase_deg": None, "order_1p0_mag": None,
                    "order_2p0_mag": None, "vib_band_rms": None,
                }

                ml_status = {"active": False, "reason": ml_error or "ml pipeline not constructed"}
                inferred = None
                if ml is not None:
                    try:
                        inferred = ml.run(rho, rho_ext, tick.altitude_ft, tick.throttle_pct,
                                          {"continue": endurance_s, "derate": endurance_s,
                                           "rtb": RTB_TRANSIT_S})
                        ml_status = {"active": True, "reason": None,
                                     "commissioned": ml.commissioned}
                    except Exception as exc:
                        print(f"[main] inference failed: {exc}")
                        ml_status = {"active": False, "reason": str(exc)}
                blocks = inferred or _ml_unavailable_blocks(ml_status["reason"])

                mp = blocks.get("mission_p")
                if mp:
                    p_cont, p_der, p_rtb = mp["continue"], mp["derate"], mp["rtb"]
                    rec = ("continue" if p_cont >= 0.9 else
                           "derate" if p_der >= 0.9 else
                           max(("continue", p_cont), ("derate", p_der), ("rtb", p_rtb),
                               key=lambda kv: kv[1])[0])
                else:
                    p_cont = p_der = p_rtb = None
                    rec = None
                rec_power = 100.0 if rec == "continue" else 78.0
                mission = {
                    "p_complete_continue": p_cont, "p_complete_derate": p_der,
                    "p_complete_rtb": p_rtb, "derate_cost_min_on_station": None,
                    "recommended": rec, "recommended_power_pct": rec_power if rec else None,
                    "recommended_boost_hPa": _target_map_hpa(rec_power / 100.0, ctx.map_limit, ctx.map_takeoff) if rec else None,
                    "point_of_no_return_s": float(endurance_s),
                    "method": "monte_carlo", "m": 200,
                    "survival_continue": (inferred or {}).get("survival_continue"),
                    "assumed_fields": ["derate_cost_min_on_station"],
                    "assumed_inputs": ctx.assumed_inputs,
                }

                health = {
                    "schema": "pramana.health.v1", "t": float(t), "engine_id": "A",
                    "rho": {
                        "rho1_sd_vs_comp": rho[0], "rho2_sd_vs_lambda": rho[1],
                        "rho3_sd_vs_restr": rho[2], "rho4_energy": rho[3],
                        "rho5_power": rho[4], "rho6_9_cyl_dev": rho[5:5 + ctx.n_cyl],
                        "rho10_oil": rho[5 + ctx.n_cyl], "rho11_ripple": rho[6 + ctx.n_cyl],
                    },
                    "rho_ext": dict(zip(extra_names(ctx.n_cyl), rho_ext)),
                    "theta": blocks["theta"],
                    "virtual": {
                        "peak_cyl_press_bar": None, "knock_margin_deg": None,
                        "comb_efficiency": None,
                        "turbo_shaft_rpm_est": pA["turbo_rpm"],
                        "air_mass_flow_kgps": pA["air_mass_flow"],
                        "brake_power_kW": tick.out_twinA["brake_power_kW"],
                        "bsfc_g_per_kWh": float(bsfc),
                    },
                    "anomaly": blocks["anomaly"], "diagnosis": blocks["diagnosis"],
                    "rul": blocks["rul"], "mission": mission,
                    "limits_state": lim_state, "ml_status": ml_status,
                    "unmodelled": UNMODELLED_FIELDS,
                }
                for k in ("novelty", "twin_confidence", "kf_nis", "explain"):
                    if inferred and k in inferred:
                        health[k] = inferred[k]

                frame_out = _json_safe({
                    "slow": slow, "fast": fast, "health": health,
                    "predicted": predicted, "slowB": slowB,
                })
                _LATEST["frame"] = frame_out
                if _assistant is not None:
                    _assistant.publish(ctx.id, frame_out, client_id)
                await websocket.send_json(frame_out)
                await asyncio.sleep(1.0)
        except (WebSocketDisconnect, RuntimeError):
            pass

    # Either task finishing ends the connection; waiting for both left a
    # sender looping forever after a client vanished without a close frame.
    receiver = asyncio.create_task(_receive())
    sender = asyncio.create_task(_send())
    try:
        _, pending = await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except Exception:
        pass



# ---------------------------------------------------------------------------
# ENGINE PICKER + ADD-AN-ENGINE (used by the landing screen)
# ---------------------------------------------------------------------------
# Wraps tools/onboard.py. Generating a profile writes files under runs/onboarding only; training runs in an
# isolated copy of the project; "add to app" only ADDS files for the new engine's own id. None of it touches
# a running session, the default engine's model, or injects a fault.
import threading
import time as _time
import uuid as _uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
try:
    import onboard as _onboard
    ONBOARD_ERROR: str | None = None
except Exception as exc:                      # keep the telemetry server up even if tooling is broken
    _onboard, ONBOARD_ERROR = None, str(exc)

_JOBS: dict[str, dict] = {}
_ML_ROOT = Path(__file__).resolve().parent.parent / "ml" / "weights"


def _need_onboard():
    if _onboard is None:
        raise HTTPException(503, f"onboarding tools unavailable: {ONBOARD_ERROR}")


def _card(eid: str, c: dict) -> dict:
    g, r, p = c["geometry"], c["ratings"], c["profile"]
    from twin.validate_profile import validate
    errs, warns = validate(c)
    has_sigma = eid == _ENG_ID or (_CFG_DIR / "engines" / eid / "sigma_vector.json").exists()
    cyl_ok = 2 <= int(g["cylinders"]) <= 8
    runnable = has_sigma and not errs and cyl_ok   # the live engine model needs every required field; we never invent them
    ml = ((_ML_ROOT / "v2" / "config.json").exists() if eid == _ENG_ID
          else (_ML_ROOT / "engines" / eid / "config.json").exists())
    gen = c.get("generated")
    return {"id": eid, "name": p.get("name", eid), "developer": p.get("developer"), "cycle": p.get("cycle"),
            "aspiration": p.get("aspiration"), "cylinders": g["cylinders"],
            "displacement_L": round(g["displacement_m3"] * 1000, 3), "bore_m": g.get("bore_m"), "stroke_m": g.get("stroke_m"),
            "rated_kW": r.get("rated_power_kW") or r.get("max_continuous_power_kW"),
            "rated_hp": r.get("rated_power_hp"), "rated_rpm": r.get("rated_speed_rpm") or r.get("max_continuous_rpm"),
            "max_rpm": r.get("max_continuous_rpm"), "critical_altitude_ft": r.get("critical_altitude_ft"),
            "cht_limit_C": c["limits"].get("cht_C"), "generated": bool(gen), "base_profile": (gen or {}).get("base_profile"),
            "runnable": bool(runnable), "ml_ready": bool(ml), "has_noise_levels": bool(has_sigma),
            "profile_errors": [e.strip() for e in errs], "assumed_fields": len(warns),
            "cylinders_supported": cyl_ok,
            "unsupported_reason": None if cyl_ok else f"{g['cylinders']}-cylinder engines are not supported: the residual layout, ML and 3D model are built for 2 to 8 cylinders"}


@app.get("/engines")
def list_engine_cards():
    """One card per installed profile, plus what state each is in (runnable / has a trained model)."""
    _need_onboard()
    from twin.profiles import list_engines
    out = []
    for eid in list_engines():
        try:
            out.append(_card(eid, load_engine_profile(eid)))
        except Exception as exc:
            out.append({"id": eid, "name": eid, "error": str(exc), "runnable": False, "ml_ready": False})
    return {"default": _ENG_ID, "engines": out}


@app.get("/engines/spec")
def engine_spec_fields():
    _need_onboard()
    return {"fields": {k: {"label": v[1], "unit": v[2]} for k, v in _onboard.SPEC_FIELDS.items()},
            "required": _onboard.REQUIRED_SPEC, "limits": _onboard.LIMITS}


@app.post("/engines/generate")
def engine_generate(payload: dict = Body(...)):
    """LOW level: write the YAML + validation + a 60 s physics smoke test. Files only."""
    _need_onboard()
    spec = payload.get("spec") or {}
    try:
        res = _onboard.write_low_level(spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    c = res["check"]
    return {"id": str(spec["id"]).strip().lower(), "yaml": res["yaml"], "validation": res["validation"],
            "errors": c["errors"], "warnings": c["warnings"], "user_specified": c.get("user_specified", []),
            "smoke": c["smoke"], "base": res["provenance"]["base"], "derived": res["provenance"]["derived_fields"],
            "limits": _onboard.LIMITS}


def _start_job(kind: str, fn) -> dict:
    jid = _uuid.uuid4().hex[:8]
    job = {"id": jid, "kind": kind, "status": "running", "started": _time.time(), "log": [], "result": None, "error": None}
    _JOBS[jid] = job

    def work():
        try:
            job["result"] = fn(lambda m: job["log"].append(m))
            job["status"] = "done"
        except Exception as exc:
            job["status"], job["error"] = "failed", str(exc)
    threading.Thread(target=work, daemon=True).start()
    return {"job": jid}


@app.post("/engines/install")
def engine_install(payload: dict = Body(...)):
    """Make a generated engine selectable: profile, noise levels (generated if needed), trained model if there is one."""
    _need_onboard()
    eid = str(payload.get("id", "")).strip().lower()
    if not eid or not (Path(_onboard.RUNS) / eid / f"engine_{eid}.yaml").exists():
        raise HTTPException(404, f"no generated profile '{eid}' — generate it first")
    return _start_job("install", lambda log: _onboard.install_engine(eid, log))


def _cuda() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


@app.get("/engines/plan")
def engine_plan(id: str, size: str = "quick"):
    _need_onboard()
    if size not in _onboard.SIZES:
        raise HTTPException(400, f"size must be one of {list(_onboard.SIZES)}")
    p = _onboard.plan(id.lower(), size, device="cuda" if _cuda() else "cpu")
    return {"engine": p["engine"], "size": size, "total_s": p["total_s"], "total": p["total"], "basis": p["basis"],
            "steps": [{"name": s["name"], "desc": s["desc"], "est_s": s["est_s"]} for s in p["steps"]], "limits": p["limits"]}


@app.post("/engines/train")
def engine_train(payload: dict = Body(...)):
    """HIGH level: full pipeline in an isolated copy. The caller must have shown the plan and got a yes."""
    _need_onboard()
    eid, size = str(payload.get("id", "")).strip().lower(), payload.get("size", "quick")
    if payload.get("confirmed") is not True:
        raise HTTPException(400, "not confirmed: show the plan and time estimate, then send confirmed=true")
    if size not in _onboard.SIZES:
        raise HTTPException(400, f"size must be one of {list(_onboard.SIZES)}")
    if not (Path(_onboard.RUNS) / eid / f"engine_{eid}.yaml").exists():
        raise HTTPException(404, f"no generated profile '{eid}' — generate it first")
    if any(j["status"] == "running" and j["kind"] == "train" for j in _JOBS.values()):
        raise HTTPException(409, "a training run is already in progress")
    plan = _onboard.plan(eid, size, device="cuda" if _cuda() else "cpu")
    return {**_start_job("train", lambda log: _onboard.run(plan, log=log)), "estimated_s": plan["total_s"]}


@app.get("/engines/job/{jid}")
def engine_job(jid: str):
    j = _JOBS.get(jid)
    if not j:
        raise HTTPException(404, "unknown job")
    res = j["result"]
    if j["kind"] == "train" and j["status"] == "done" and res and res.get("report"):
        try:
            t = json.loads(Path(res["report"]).read_text())["test"]
            tr = sum(v["runs"] for v in t["per_class"].values())
            td = sum(v["detected_runs"] for v in t["per_class"].values())
            res = {**res, "metrics": {"recall": td / tr, "faulty_runs": tr, "top1_after_alarm": t["top1_after_alarm_all"],
                                       "false_alarms_per_hour": t["false_alarm_episodes_per_hour"],
                                       "healthy_runs": t["n_healthy_runs"]}}
        except Exception:
            pass
    return {"id": j["id"], "kind": j["kind"], "status": j["status"], "elapsed_s": round(_time.time() - j["started"]),
            "log": j["log"][-8:], "result": res, "error": j["error"]}


# In-app chat + push-to-talk (backend/assistant_api.py). Optional: the telemetry server stays up without it.
try:
    import assistant_api as _assistant
    app.include_router(_assistant.router)
    ASSISTANT_ERROR: str | None = None
except Exception as exc:
    _assistant, ASSISTANT_ERROR = None, str(exc)
    print(f"[main] assistant API unavailable ({exc})")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
