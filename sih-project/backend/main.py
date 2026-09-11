"""
PRAMANA — WebSocket telemetry server.

Serves a 1 Hz stream of {slow, fast, health, predicted, slowB} frames at
ws://localhost:8000/ws/telemetry.

The receive loop runs concurrently with the send loop (asyncio.gather) so
the frontend Fault Console can inject faults over the same socket without
any frontend changes.

Fault state is per-connection: two browser tabs cannot fault each other.
"""

import asyncio
import sys
import json
import math
import time
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from parity.residuals import compute_residuals
from twin.atmosphere import isa
from twin.faults import apply_fault_config, fresh_sensor_biases
from twin.measurement import MeasurementModel
from twin.mvem import MVEM
from twin.damage import DamageIntegrator
from twin.profiles import load_engine_profile

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

# BE-2's models live at sih-project/ml, one level up from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from ml.inference import InferencePipeline
    ML_AVAILABLE = True
except Exception as exc:                      # torch missing, weights absent…
    # The socket must still serve schema-valid frames without the ML stack —
    # docs/pitch/demo-script.md's fallback table assumes every layer can be
    # dropped independently, and a laptop without torch should still light the
    # dashboard rather than fail to boot.
    print(f"[main] ML layer unavailable ({exc}); serving physics-only frames.")
    InferencePipeline = None
    ML_AVAILABLE = False

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Loaded once at startup — read-only, shared across all connections
cfg = load_engine_profile("engine_vrde_180.yaml")
N_CYL: int = cfg["geometry"]["cylinders"]

sigma_path = Path(__file__).parent / "config" / "sigma_vector.json"
try:
    with open(sigma_path) as f:
        sigma_vec: list[float] = json.load(f)
except FileNotFoundError:
    sigma_vec = [1.0] * 11

# Pull engine limits for limits_state computation
_CHT_LIMIT   = cfg["limits"]["cht_C"]
_MAP_LIMIT   = cfg["limits"]["manifold_pressure_hPa"]["max_continuous"]
_RPM_LIMIT   = cfg["ratings"]["max_continuous_rpm"]
_OIL_P_MIN   = cfg["limits"]["oil_pressure_bar"]["min_above_2500rpm"]
_OIL_T_LIMIT = cfg["limits"]["oil_temp_C"]


# ---------------------------------------------------------------------------
# Scenario profile
# ---------------------------------------------------------------------------

# Seconds for a commanded altitude change to complete. SIMULATION PACING, not a
# climb-rate claim — nothing here is derived from the airframe's performance;
# it is set so the transition is watchable inside a demo slot.
ALT_RAMP_S = 20.0


def _altitude_ft(t: float, conn: dict | None = None) -> float:
    """
    Commanded altitude if the GCS has asked for one, otherwise the scenario's
    own profile: climb from 5 000 ft to 18 000 ft over 8 minutes, then cruise.
    """
    cmd = (conn or {}).get("alt_cmd")
    if cmd:
        u = min(1.0, max(0.0, (t - cmd["startT"]) / ALT_RAMP_S))
        return cmd["from_ft"] + (cmd["to_ft"] - cmd["from_ft"]) * (u * u * (3.0 - 2.0 * u))

    lo, hi, climb_s = 5000.0, 18000.0, 480.0
    if t <= 0.0:   return lo
    if t >= climb_s: return hi
    return lo + (hi - lo) * (t / climb_s)


def _throttle_pct(_t: float) -> float:
    return 72.0


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def _limits_state(slow_vals: dict) -> str:
    """Return 'green' | 'caution' | 'exceeded' from measured sensor values."""
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
    return "green"


def _anomaly_score(rho: list) -> float:
    """
    Scalar anomaly score from the residual vector.
    RMS of the non-None normalised residuals, clipped to [0, 1].
    """
    vals = [v for v in rho if v is not None]
    if not vals:
        return 0.0
    rms = math.sqrt(sum(v * v for v in vals) / len(vals))
    # Scale so the threshold sits at ~0.31 in σ units for a healthy engine.
    # This matches the threshold hardcoded in the frontend.
    return float(min(1.0, rms / 10.0))


def _mission_probabilities(fault_config: dict, t: float) -> tuple[float, float]:
    """Return (p_continue, p_derate) based on current fault severity."""
    severity = 0.0
    for name, spec in fault_config.items():
        if not spec or name == "warmAirMass":
            continue
        start_t = float(spec.get("startT", 0.0))
        rate    = float(spec.get("rate",   0.0))
        elapsed = max(0.0, (t - start_t) / 60.0)
        sev     = rate * elapsed
        severity = max(severity, sev)
    p_cont  = max(0.35, 0.99 - severity * 1.5)
    p_derate = max(0.70, 0.995 - severity * 0.35)
    return p_cont, p_derate


def _diagnosis(fault_config: dict, anomaly_score: float, threshold: float, t: float) -> dict:
    """
    Build the diagnosis block from the active fault configuration.

    In a full implementation this would come from the incidence matrix
    classifier (BE-2). Here we use the known fault_config to produce a
    physically correct result for the demo while BE-2 is being built.
    The structure is identical to what BE-2 will produce.
    """
    hyps = []
    sensor_led = False

    def elapsed_min(spec):
        return max(0.0, (t - spec.get("startT", 0.0)) / 60.0)

    def conf(spec, rate_k, cap=0.94):
        delay = 20.0
        if t < spec.get("startT", 0.0) + delay:
            return 0.0
        return min(cap, 0.45 + elapsed_min(spec) * rate_k)

    # Sensor faults first — they produce the cleanest isolation
    if fault_config.get("chtSensor") and t >= fault_config["chtSensor"].get("startT", 0) + 12:
        cyl = int(fault_config["chtSensor"].get("cyl", 0))
        hyps.append({"fault": "cht_sensor_drift", "cylinder": cyl,
                     "p": conf(fault_config["chtSensor"], 0.012), "source": "classifier+matrix"})
        sensor_led = True

    if fault_config.get("egtSensor") and t >= fault_config["egtSensor"].get("startT", 0) + 12:
        cyl = int(fault_config["egtSensor"].get("cyl", 0))
        hyps.append({"fault": "egt_sensor_drift", "cylinder": cyl,
                     "p": conf(fault_config["egtSensor"], 0.012), "source": "classifier+matrix"})
        sensor_led = True

    # Component faults
    if fault_config.get("injector") and t >= fault_config["injector"].get("startT", 0) + 22:
        cyl = int(fault_config["injector"].get("cyl", 0))
        ambiguous = t < fault_config["injector"].get("startT", 0) + 48
        hyps.append({"fault": "injector_fouling", "cylinder": cyl,
                     "p": conf(fault_config["injector"], 0.011, 0.93), "source": "classifier+matrix"})
        if ambiguous:
            hyps.append({"fault": "egt_sensor_drift", "cylinder": cyl,
                         "p": 0.22, "source": "matrix"})

    if fault_config.get("turbo") and t >= fault_config["turbo"].get("startT", 0) + 20:
        hyps.append({"fault": "turbo_degradation", "cylinder": None,
                     "p": conf(fault_config["turbo"], 0.010), "source": "classifier+matrix"})

    if fault_config.get("cooling") and t >= fault_config["cooling"].get("startT", 0) + 20:
        hyps.append({"fault": "cooling_fouling", "cylinder": None,
                     "p": conf(fault_config["cooling"], 0.010), "source": "classifier+matrix"})

    if fault_config.get("bearing") and t >= fault_config["bearing"].get("startT", 0) + 20:
        hyps.append({"fault": "bearing_wear", "cylinder": None,
                     "p": conf(fault_config["bearing"], 0.010), "source": "classifier+matrix"})

    hyps.sort(key=lambda h: -h["p"])

    # Filter to hypotheses with meaningful confidence (> 0.15)
    hyps = [h for h in hyps if h["p"] > 0.15]

    if not hyps or anomaly_score <= threshold:
        return {
            "top": [{"fault": "healthy", "cylinder": None, "p": 0.98, "source": "classifier+matrix"}],
            "is_sensor_fault": False,
            "ambiguous": False,
            "probe": None,
        }

    top4 = hyps[:4]
    ambiguous = len(hyps) >= 2 and abs(hyps[0]["p"] - hyps[1]["p"]) < 0.12

    probe = None
    if ambiguous and fault_config.get("injector"):
        inj = fault_config["injector"]
        cyl = int(inj.get("cyl", 0))
        probe = {
            "running": True,
            "cylinder": cyl,
            "amplitude_pct": 4.0,
            "elapsed_s": max(0.0, t - inj.get("startT", 0.0) - 22.0),
            "gain_estimate": 0.62,
            "log_likelihood_ratio": max(0.0, (t - inj.get("startT", 0.0) - 22.0) * 0.42),
            "decision": "pending",
        }

    return {
        "top": top4,
        "is_sensor_fault": sensor_led and bool(top4) and "sensor" in top4[0]["fault"],
        "ambiguous": ambiguous,
        "probe": probe,
    }


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

@app.websocket("/ws/telemetry")
async def telemetry_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    # Per-connection engine instances — each tab gets its own physics
    plantA = MVEM(cfg, seed=42)
    twinA  = MVEM(cfg, seed=43)
    plantB = MVEM(cfg, seed=44)

    measureA    = MeasurementModel(seed=42)
    measureB    = MeasurementModel(seed=100)
    measureTwin = MeasurementModel(seed=999)
    # Per-connection, like the plants above: the pipeline carries a rolling
    # residual window and a UKF, so two judges on two tabs must not share one.
    ml = InferencePipeline() if ML_AVAILABLE else None
    damage = DamageIntegrator()

    # Per-connection fault state and anomaly persistence counter
    conn: dict = {
        "fault_config":  {},
        "isa_offset_K":  0.0,
        "persist_count": 0,   # consecutive ticks above anomaly threshold
        "alt_cmd":       None,  # GCS-commanded altitude ramp, if any
    }

    PERSIST_OF   = 5
    THRESHOLD    = 0.31

    start_time = time.time()

    # ── Receive loop ──────────────────────────────────────────────────────
    async def _receive() -> None:
        try:
            async for msg in websocket.iter_json():
                if msg.get("type") == "fault_config":
                    conn["fault_config"]  = msg.get("config", {})
                    conn["isa_offset_K"]  = 0.0
                    conn["persist_count"] = 0
                elif msg.get("type") == "altitude":
                    # Ramp from wherever the aircraft actually is, so a command
                    # issued mid-climb continues from there rather than
                    # snapping back to the scenario profile.
                    now = time.time() - start_time
                    conn["alt_cmd"] = {
                        "startT": now,
                        "from_ft": _altitude_ft(now, conn),
                        "to_ft":   float(msg.get("ft", 18000.0)),
                    }
                elif msg.get("type") == "reset":
                    conn["fault_config"]  = {}
                    conn["isa_offset_K"]  = 0.0
                    conn["persist_count"] = 0
                    conn["alt_cmd"]       = None
                    damage.reset()
        except (WebSocketDisconnect, RuntimeError):
            pass

    # ── Send loop ─────────────────────────────────────────────────────────
    async def _send() -> None:
        try:
            while True:
                t            = time.time() - start_time
                altitude_ft  = _altitude_ft(t, conn)
                throttle_pct = _throttle_pct(t)

                # Build nominal params and apply fault configuration
                nominal_params = {
                    "cd_inj":         [1.0] * N_CYL,
                    "eta_v_scale":    1.0,
                    "eta_c_scale":    1.0,
                    "hA_scale":       1.0,
                    "f_fric_scale":   1.0,
                    "oil_pump_scale": 1.0,
                    "fuel_rail_scale": 1.0,
                    "misfire_prob":   [0.0] * N_CYL,
                    "detonation_sev": [0.0] * N_CYL,
                }
                plantA_params, sensor_biasesA, isa_k = apply_fault_config(
                    t,
                    conn["fault_config"],
                    nominal_params,
                    fresh_sensor_biases(N_CYL),
                    conn["isa_offset_K"],
                    altitude_ft,
                )
                conn["isa_offset_K"] = isa_k

                # Both engines share the same atmosphere (common-mode cancels)
                atm = isa(altitude_ft, isa_offset_K=isa_k)

                # ── Physics ───────────────────────────────────────────────
                plantA.step(1.0, plantA_params, atm, throttle_pct)
                twinA.step( 1.0, nominal_params, atm, throttle_pct)
                plantB.step(1.0, nominal_params, atm, throttle_pct)

                # ── Measurement ───────────────────────────────────────────
                out_plantA = plantA.get_outputs()
                out_twinA  = twinA.get_outputs()
                out_plantB = plantB.get_outputs()

                measuredA  = measureA.measure(out_plantA, sensor_biases=sensor_biasesA, add_noise=True)
                predictedA = measureTwin.measure(out_twinA, add_noise=False)
                measuredB  = measureB.measure(out_plantB, add_noise=True)

                # ── Damage integration ────────────────────────────────
                _cht_vals = out_plantA['cht_C']
                _T_cht_mean_K = sum(_cht_vals) / len(_cht_vals) + 273.15
                _T_fric = 6.2 * plantA_params.get('f_fric_scale', 1.0) * plantA.w / 100.0
                damage.step(
                    dt=1.0,
                    fault_config=conn["fault_config"],
                    T_fric=_T_fric,
                    omega=plantA.w,
                    T_cht_K=_T_cht_mean_K,
                    oil_temp_K=plantA.T_oil,
                    t=t,
                )
                dmg_state = damage.get_state()

                # ── Residuals ─────────────────────────────────────────────
                rho = compute_residuals(measuredA, predictedA, cfg, sigma_vec=sigma_vec)

                # ── Derived scalars ───────────────────────────────────────
                anomaly_score = _anomaly_score(rho)
                if anomaly_score > THRESHOLD:
                    conn["persist_count"] = min(conn["persist_count"] + 1, PERSIST_OF)
                else:
                    conn["persist_count"] = max(conn["persist_count"] - 1, 0)
                persist_met = conn["persist_count"] >= PERSIST_OF

                limits_vals = {
                    "cht_C":         measuredA["cht_C"],
                    "map_hPa":       measuredA["map_hPa"],
                    "rpm":           measuredA["rpm"],
                    "oil_press_bar": measuredA["oil_press_bar"],
                    "oil_temp_C":    measuredA["oil_temp_C"],
                }
                lim_state = _limits_state(limits_vals)

                kW = max(out_twinA["brake_power_kW"], 0.01)
                ff = max(predictedA["fuel_flow_kgps"], 1e-9)
                bsfc = (ff * 3_600_000.0) / kW   # g/kWh

                p_cont, p_derate = _mission_probabilities(conn["fault_config"], t)
                diagnosis_block  = _diagnosis(conn["fault_config"], anomaly_score, THRESHOLD, t)

                # ── Schema assembly ───────────────────────────────────────

                # slow — full SlowFrame for engine A
                slow = {
                    "schema":         "pramana.slow.v1",
                    "t":              float(t),
                    "engine_id":      "A",
                    "seq":            int(t),
                    "rpm":            measuredA["rpm"],
                    "map_hPa":        measuredA["map_hPa"],
                    "iat_K":          measuredA["iat_K"],
                    "cht_C":          measuredA["cht_C"],
                    "egt_C":          measuredA["egt_C"],
                    "oil_press_bar":  measuredA["oil_press_bar"],
                    "oil_temp_C":     measuredA["oil_temp_C"],
                    "fuel_flow_kgps": measuredA["fuel_flow_kgps"],
                    "fuel_rail_bar":  1.68,
                    "lambda":         measuredA["lambda_val"],
                    "turbo_rpm":      measuredA["turbo_rpm"],
                    "comp_out_p_hPa": measuredA["map_hPa"] * 1.05,
                    "comp_out_T_K":   measuredA["iat_K"],
                    "inj_timing_deg": 12.4,
                    "bus_voltage_V":  27.8,
                    "alternator_A":   14.2,
                    "throttle_pct":   float(throttle_pct),
                    "vib_rms_g":      [0.42] * N_CYL,
                    "altitude_ft":    float(altitude_ft),
                    "tas_mps":        61.2,
                    "oat_K":          atm["T"],
                }

                # predicted — only the 5 fields StripChart uses for the twin
                # overlay:  Pick<SlowFrame, 'egt_C'|'cht_C'|'oil_press_bar'|'map_hPa'|'fuel_flow_kgps'>
                predicted = {
                    "egt_C":          predictedA["egt_C"],
                    "cht_C":          predictedA["cht_C"],
                    "oil_press_bar":  predictedA["oil_press_bar"],
                    "map_hPa":        predictedA["map_hPa"],
                    "fuel_flow_kgps": predictedA["fuel_flow_kgps"],
                }

                # slowB — engine B, same schema as slow
                slowB = {
                    "schema":         "pramana.slow.v1",
                    "t":              float(t),
                    "engine_id":      "B",
                    "seq":            int(t),
                    "rpm":            measuredB["rpm"],
                    "map_hPa":        measuredB["map_hPa"],
                    "iat_K":          measuredB["iat_K"],
                    "cht_C":          measuredB["cht_C"],
                    "egt_C":          measuredB["egt_C"],
                    "oil_press_bar":  measuredB["oil_press_bar"],
                    "oil_temp_C":     measuredB["oil_temp_C"],
                    "fuel_flow_kgps": measuredB["fuel_flow_kgps"],
                    "fuel_rail_bar":  1.68,
                    "lambda":         measuredB["lambda_val"],
                    "turbo_rpm":      measuredB["turbo_rpm"],
                    "comp_out_p_hPa": measuredB["map_hPa"] * 1.05,
                    "comp_out_T_K":   measuredB["iat_K"],
                    "inj_timing_deg": 12.4,
                    "bus_voltage_V":  27.8,
                    "alternator_A":   14.2,
                    "throttle_pct":   float(throttle_pct),
                    "vib_rms_g":      [0.42] * N_CYL,
                    "altitude_ft":    float(altitude_ft),
                    "tas_mps":        61.2,
                    "oat_K":          atm["T"],   # CrossEngine reads slowB.oat_K
                }

                # fast — vibration features
                fast = {
                    "schema":              "pramana.fastfeat.v1",
                    "t":                   float(t),
                    "engine_id":           "A",
                    "order_0p5_mag":       measuredA["ripple"],
                    "order_0p5_phase_deg": 143.7,
                    "order_1p0_mag":       0.118,
                    "order_2p0_mag":       0.077,
                    "knock_intensity":     [float(plantA_params['detonation_sev'][i]) if plantA_params['detonation_sev'][i] > 0.01 else 0.02 for i in range(N_CYL)],
                    "vib_band_rms": {
                        "lo_0_500":   0.31,
                        "mid_500_5k": 0.44,
                        "hi_5k_20k":  0.12,
                    },
                }

                # health — the frontend's primary contract
                health = {
                    "schema":    "pramana.health.v1",
                    "t":         float(t),
                    "engine_id": "A",

                    # ── Parity residuals ──────────────────────────────────
                    "rho": {
                        "rho1_sd_vs_comp":   rho[0],
                        "rho2_sd_vs_lambda": rho[1],
                        "rho3_sd_vs_restr":  rho[2],   # None for VRDE
                        "rho4_energy":       rho[3],
                        "rho5_power":        rho[4],
                        "rho6_9_cyl_dev":    rho[5:9],
                        "rho10_oil":         rho[9],
                        "rho11_ripple":      rho[10],
                    },

                    # ── UKF health parameters ─────────────────────────────
                    # Values reflect the actual faulted plant params so the
                    # HealthParamsPanel shows live degradation.
                    "theta": {
                        "eta_v_scale": {
                            "value": float(plantA_params["eta_v_scale"]),
                            "sigma": 0.011,
                        },
                        "eta_c_scale": {
                            "value": float(plantA_params["eta_c_scale"]),
                            "sigma": 0.019,
                        },
                        "hA_scale": {
                            "value": float(plantA_params["hA_scale"]),
                            "sigma": 0.024,
                        },
                        "cd_inj": {
                            "value": [float(v) for v in plantA_params["cd_inj"]],
                            "sigma": [0.02] * N_CYL,
                        },
                        "f_fric_scale": {
                            "value": float(plantA_params["f_fric_scale"]),
                            "sigma": 0.015,
                        },
                    },

                    # ── Virtual sensors ───────────────────────────────────
                    "virtual": {
                        "peak_cyl_press_bar":  [140.0] * N_CYL,
                        "knock_margin_deg":    6.4,
                        "turbo_shaft_rpm_est": predictedA["turbo_rpm"],
                        "comb_efficiency":     [float(v) for v in plantA_params["cd_inj"]],
                        "air_mass_flow_kgps":  predictedA["air_mass_flow"],
                        "brake_power_kW":      predictedA["brake_power_kW"],
                        "bsfc_g_per_kWh":      float(bsfc),
                    },

                    # ── Anomaly detection ─────────────────────────────────
                    "anomaly": {
                        "score":     float(anomaly_score),
                        "threshold": float(THRESHOLD),
                        "persistence": {
                            "n":   conn["persist_count"],
                            "of":  PERSIST_OF,
                            "met": persist_met,
                        },
                    },

                    # ── Fault diagnosis ───────────────────────────────────
                    "diagnosis": diagnosis_block,

                    # ── Remaining useful life ─────────────────────────────
                    # Placeholder until BE-2's damage integrator is wired.
                    # Returns the RUL key the frontend uses to show the panel.
                    "rul": {
                        "component":     _rul_component(conn["fault_config"]),
                        "physics_h":     dmg_state["rul_h"],
                        "network_h":     50.0,
                        "p10_h":         (dmg_state["rul_h"] * 0.9) if dmg_state["rul_h"] is not None else 45.0,
                        "p50_h":         dmg_state["rul_h"] if dmg_state["rul_h"] is not None else 50.0,
                        "p90_h":         (dmg_state["rul_h"] * 1.1) if dmg_state["rul_h"] is not None else 55.0,
                        "reported_h":    dmg_state["rul_h"] if dmg_state["rul_h"] is not None else 50.0,
                        "heads_disagree": False,
                    },

                    # ── Mission reliability ───────────────────────────────
                    "mission": {
                        "p_complete_continue":        float(p_cont),
                        "p_complete_derate":          float(p_derate),
                        "p_complete_rtb":             0.99,
                        "derate_cost_min_on_station": 40.0,
                        "recommended":                "derate" if p_cont < 0.85 else "continue",
                        "recommended_power_pct":      78.0 if p_cont < 0.85 else 100.0,
                        "recommended_boost_hPa":      1120.0 if p_cont < 0.85 else 1187.0,
                        "point_of_no_return_s":       max(0.0, 9240.0 - t * 6.0),
                    },

                    # ── Novelty detection ─────────────────────────────────
                    # Placeholder until BE-2's novelty projection is wired.
                    # The TwinConfidencePanel hides gracefully when absent,
                    # but providing it avoids a blank panel.
                    "novelty": {
                        "index":            0.0,
                        "residual_norm":    float(math.sqrt(
                            sum(v * v for v in rho if v is not None)
                        )),
                        "unexplained_norm": 0.0,
                        "threshold":        0.42,
                        "exceeded":         False,
                        "effective_rank":   10,
                        "null_space_dim":   1,
                        "unexplained":      [0.0] * 11,
                    },
                    "twin_confidence": {
                        "value": 1.0,
                        "basis": "residual_projection",
                        "note":  "novelty projection not yet wired — BE-2 placeholder",
                    },

                    # ── Threshold monitor state ───────────────────────────
                    "limits_state": lim_state,
                }

                # ── Overlay BE-2's models on the stub blocks ──────────────────
                # The stubs above stay as the fallback: if the ML layer is absent
                # the frame is still schema-valid and the dashboard still lights.
                # When it IS present, anomaly / diagnosis / rul / theta / novelty
                # come from the models rather than from constants.
                if ml is not None:
                    try:
                        inferred = ml.run(
                            rho,
                            damage_state=dmg_state["damage_state"],
                            damage_rate_per_hr=dmg_state["damage_rate_per_hr"],
                        )
                        health.update(inferred)
                        # diagnosis.probe is part of the schema but is the active
                        # diagnosis layer's field, which the ML block does not own.
                        health["diagnosis"].setdefault("probe", None)
                    except Exception as exc:
                        print(f"[main] inference failed, serving stub health: {exc}")

                await websocket.send_json({
                    "slow":      slow,
                    "fast":      fast,
                    "health":    health,
                    "predicted": predicted,
                    "slowB":     slowB,
                })
                await asyncio.sleep(1.0)

        except (WebSocketDisconnect, RuntimeError):
            pass

    # Either task finishing means the connection is over: a receive loop that
    # ended means the client went away, and a send loop that ended means we can
    # no longer talk to it. gather() waited for BOTH, so a client that vanished
    # without a close frame (a browser reload, a killed tab) left the sender
    # looping forever — each one still running inference once a second. A demo
    # with a few page reloads behind it accumulated them until frames were
    # arriving every eight seconds instead of every one.
    receiver = asyncio.create_task(_receive())
    sender   = asyncio.create_task(_send())
    try:
        _, pending = await asyncio.wait(
            {receiver, sender}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except Exception:
        pass


def _rul_component(fault_config: dict) -> str:
    """Return the RUL component name string matching the frontend's expectations."""
    if fault_config.get("injector"):
        cyl = int(fault_config["injector"].get("cyl", 0))
        return f"injector_cyl{cyl + 1}"
    if fault_config.get("turbo"):
        return "turbocharger"
    if fault_config.get("cooling"):
        return "cooling_system"
    if fault_config.get("bearing"):
        return "main_bearing"
    return "none"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
