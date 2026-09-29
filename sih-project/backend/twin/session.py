"""
One engine session: the per-tick physics → measurement → residual chain.

This is the ONLY place that chain is assembled. The live WebSocket server
(main.py) and the ML dataset generator (ml/data/mvem_dataset.py) both step an
EngineSession, so the models are trained on exactly the residuals the live
system serves — the previous training data came from a separate hand-written
generator that no live frame ever resembled.

Time is SIMULATED time: one step() is one second of engine physics, and the
fault ramps, altitude profile and damage all read that clock. The server used
to read wall-clock time while stepping the physics a fixed 1 s, so whenever
the event loop fell behind (several connections, a slow laptop) faults ramped
faster than the engine they were injected into was simulated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from parity.residuals import compute_extra_residuals, compute_residuals
from twin.atmosphere import isa
from twin.faults import apply_fault_config, fresh_sensor_biases
from twin.measurement import MeasurementModel
from twin.mvem import MVEM

# ── Scenario profile (moved verbatim from main.py) ───────────────────────────
# SIMULATION PACING, not airframe performance claims: there is no airframe
# model, so altitude, throttle and TAS are scenario inputs.
ALT_RAMP_S = 20.0
CLIMB_TAS_MPS, CRUISE_TAS_MPS = 48.0, 61.2
CLIMB_S, TAS_BLEND_S = 480.0, 30.0
ALT_LO_FT, ALT_HI_FT = 5000.0, 11000.0
CRUISE_THROTTLE_PCT = 72.0


def _smooth(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


def scenario_altitude_ft(t: float, alt_cmd: dict | None) -> float:
    """Commanded altitude if the GCS asked for one, otherwise climb 5,000 →
    11,000 ft (DRDO's published VRDE critical altitude) over 8 min, then cruise."""
    if alt_cmd:
        u = min(1.0, max(0.0, (t - alt_cmd["startT"]) / ALT_RAMP_S))
        return alt_cmd["from_ft"] + (alt_cmd["to_ft"] - alt_cmd["from_ft"]) * _smooth(u)
    if t <= 0.0:
        return ALT_LO_FT
    if t >= CLIMB_S:
        return ALT_HI_FT
    return ALT_LO_FT + (ALT_HI_FT - ALT_LO_FT) * (t / CLIMB_S)


def scenario_tas_mps(t: float, alt_cmd: dict | None) -> float:
    if alt_cmd:
        return CRUISE_TAS_MPS
    u = min(1.0, max(0.0, (t - (CLIMB_S - TAS_BLEND_S)) / TAS_BLEND_S))
    return CLIMB_TAS_MPS + (CRUISE_TAS_MPS - CLIMB_TAS_MPS) * _smooth(u)


def nominal_params(n_cyl: int) -> dict:
    return {
        "cd_inj": [1.0] * n_cyl, "eta_v_scale": 1.0, "eta_c_scale": 1.0,
        "hA_scale": 1.0, "f_fric_scale": 1.0, "oil_pump_scale": 1.0,
        "fuel_rail_scale": 1.0, "misfire_prob": [0.0] * n_cyl,
        "detonation_sev": [0.0] * n_cyl, "rad_eff_scale": 1.0, "cool_pump_scale": 1.0,
    }


@dataclass
class Tick:
    t: float
    altitude_ft: float
    throttle_pct: float
    tas_mps: float
    plant_params: dict
    out_plantA: dict
    out_twinA: dict
    measuredA: dict
    predictedA: dict
    measuredB: dict
    rho: list
    rho_ext: list


@dataclass
class EngineSession:
    cfg: dict
    sigma_vec: list
    sigma_ext: list | None = None
    plant_seed: int = 42
    twin_seed: int = 43
    measure_seed: int = 42
    measure_seed_b: int = 100
    # Scenario hooks — the dataset generator varies these; the server uses the
    # defaults above.
    altitude_fn: Callable[[float, dict | None], float] = scenario_altitude_ft
    tas_fn: Callable[[float, dict | None], float] = scenario_tas_mps
    throttle_fn: Callable[[float], float] = lambda _t: CRUISE_THROTTLE_PCT
    isa_base_K: float = 0.0
    with_engine_b: bool = True
    fault_config: dict = field(default_factory=dict)
    alt_cmd: dict | None = None
    t: float = 0.0

    def __post_init__(self) -> None:
        self.n_cyl = self.cfg["geometry"]["cylinders"]
        self.liquid = self.cfg["mvem"].get("cooling", {}).get("type") == "liquid"
        self.plantA = MVEM(self.cfg, seed=self.plant_seed)
        self.twinA = MVEM(self.cfg, seed=self.twin_seed)
        self.plantB = MVEM(self.cfg, seed=self.plant_seed + 2) if self.with_engine_b else None
        self.measureA = MeasurementModel(seed=self.measure_seed)
        self.measureB = MeasurementModel(seed=self.measure_seed_b)
        self.measureTwin = MeasurementModel(seed=999, is_twin=True)

    def command_altitude(self, to_ft: float) -> None:
        self.alt_cmd = {"startT": self.t,
                        "from_ft": self.altitude_fn(self.t, self.alt_cmd),
                        "to_ft": float(to_ft)}

    def step(self) -> Tick:
        t = self.t
        alt = self.altitude_fn(t, self.alt_cmd)
        thr = self.throttle_fn(t)
        tas = self.tas_fn(t, self.alt_cmd)
        nominal = nominal_params(self.n_cyl)
        # The ISA offset is recomputed from its base every tick. It used to be
        # fed back into itself, so warmAirMass added +15 K PER SECOND.
        params, biases, isa_k = apply_fault_config(
            t, self.fault_config, nominal, fresh_sensor_biases(self.n_cyl),
            self.isa_base_K, alt, liquid_cooled=self.liquid)
        atm = isa(alt, isa_offset_K=isa_k)

        self.plantA.step(1.0, params, atm, thr, tas)
        self.twinA.step(1.0, nominal, atm, thr, tas)
        oA, oT = self.plantA.get_outputs(), self.twinA.get_outputs()
        mA = self.measureA.measure(oA, sensor_biases=biases, add_noise=True)
        pA = self.measureTwin.measure(oT, add_noise=False)
        mB = None
        if self.plantB is not None:
            self.plantB.step(1.0, nominal, atm, thr, tas)
            mB = self.measureB.measure(self.plantB.get_outputs(), add_noise=True)
        rho = compute_residuals(mA, pA, self.cfg, sigma_vec=self.sigma_vec)
        rho_ext = compute_extra_residuals(mA, pA, self.cfg, sigma_ext=self.sigma_ext)

        self.t += 1.0
        return Tick(t, alt, thr, tas, params, oA, oT, mA, pA, mB, rho, rho_ext)
