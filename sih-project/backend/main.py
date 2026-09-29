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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from parity.residuals import EXTRA_NAMES
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


def _target_map_hpa(power_frac: float) -> float:
    """The FADEC's commanded MAP at a power fraction — the same schedule
    twin/mvem.py's wastegate loop runs, on the published limits."""
    cruise_frac = 0.90
    if power_frac <= cruise_frac:
        return power_frac * _MAP_LIMIT
    extra = (power_frac - cruise_frac) / (1.0 - cruise_frac)
    return _MAP_LIMIT + extra * (_MAP_TAKEOFF - _MAP_LIMIT)


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


def _limits_state(slow_vals: dict) -> str:
    """'green' | 'caution' | 'exceeded' — what a threshold-only system shows."""
    cht_max = max(slow_vals["cht_C"])
    if cht_max > _CHT_LIMIT:
        return "exceeded"
    if cht_max > _CHT_LIMIT * 0.90:
        return "caution"
    if slow_vals["map_hPa"] > _MAP_LIMIT:
        return "exceeded"
    if slow_vals["rpm"] > _RPM_LIMIT:
        return "caution"
    if slow_vals["oil_press_bar"] < _OIL_P_MIN and slow_vals["rpm"] > 2500:
        return "caution"
    if slow_vals["oil_temp_C"] > _OIL_T_LIMIT:
        return "exceeded"
    gb_oil = slow_vals.get("gearbox_oil_C")
    if gb_oil is not None and gb_oil > _GB_OIL_LIMIT:
        return "exceeded"
    coolant = slow_vals.get("coolant_temp_C")
    if coolant is not None and coolant > _COOLANT_LIMIT:
        return "exceeded"
    if coolant is not None and coolant > _COOLANT_LIMIT * 0.95:
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
    session = EngineSession(cfg, sigma_vec, sigma_ext)
    ml, ml_error = None, ML_IMPORT_ERROR
    if InferencePipeline is not None:
        try:
            ml = InferencePipeline(installation=INSTALLATION_A)
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
                lim_state = _limits_state(limits_vals)

                # Fuel state from the METERED flow — the flow meter sees a
                # degraded engine's real consumption; the nominal twin's
                # predicted flow (used before) never reacted to a fault.
                ff = max(mA["fuel_flow_kgps"], 1e-9)
                fuel["burned_kg"] += ff * 1.0
                remaining = max(0.0, MISSION_FUEL_KG - fuel["burned_kg"])
                usable = max(0.0, remaining - FUEL_RESERVE_KG)
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
                                        for i in range(N_CYL)],
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
                    "recommended_boost_hPa": _target_map_hpa(rec_power / 100.0) if rec else None,
                    "point_of_no_return_s": float(endurance_s),
                    "method": "monte_carlo", "m": 200,
                    "survival_continue": (inferred or {}).get("survival_continue"),
                    "assumed_fields": ["derate_cost_min_on_station"],
                    "assumed_inputs": ASSUMED_MISSION_INPUTS,
                }

                health = {
                    "schema": "pramana.health.v1", "t": float(t), "engine_id": "A",
                    "rho": {
                        "rho1_sd_vs_comp": rho[0], "rho2_sd_vs_lambda": rho[1],
                        "rho3_sd_vs_restr": rho[2], "rho4_energy": rho[3],
                        "rho5_power": rho[4], "rho6_9_cyl_dev": rho[5:9],
                        "rho10_oil": rho[9], "rho11_ripple": rho[10],
                    },
                    "rho_ext": dict(zip(EXTRA_NAMES, rho_ext)),
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

                await websocket.send_json(_json_safe({
                    "slow": slow, "fast": fast, "health": health,
                    "predicted": predicted, "slowB": slowB,
                }))
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


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
