"""
Damage integrator for PRAMANA engine twin.

Ground-truth damage accumulation D ∈ [0,1] and exact RUL computation,
following the construction in docs/team/BE-1-FINAL-DIRECTIVE-2026-09-10.md §3.

Two physically motivated damage laws:

  1. Adhesive/abrasive wear (Archard) — for friction/clearance-driven faults
     (bearing, ring wear). dD/dt proportional to T_fric × ω / H_material.

  2. Thermally activated ageing (Arrhenius) — for heat-driven faults (cooling
     fouling, detonation, injector coking). dD/dt proportional to
     exp(-E_a / (R × T_cht)).

Cumulative damage via Miner's rule: D = Σ(n_i / N_i).
Failure at D = 1.0.

Ground-truth RUL (exact, no model needed — this is the label BE-2 trains on):
  RUL_h = (1 - D) / max(dD_dt, ε) / 3600

Calibration basis:
  Constants chosen so a "typical" demo-rate fault (rate ≈ 0.04–0.06/min) reaches
  D = 1 in roughly 30–90 minutes of simulated time — dramatically compressed
  versus real engine wear (tens of thousands of hours) but plausible enough for
  a live demo. There is no public wear-rate data for the VRDE 180, and we do
  not claim these are physical material constants — they produce the correct
  shape (monotonic D, monotonically decreasing RUL) at the correct time scale
  for the demo, which is all that is needed for ground-truth label generation.
"""

from __future__ import annotations
import math
from typing import Any

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------
R_GAS = 8.314          # Universal gas constant [J/(mol·K)]

# ---------------------------------------------------------------------------
# Calibrated damage constants
#
# k_archard and k_arrhenius are scaled so that under typical demo conditions
# (T_fric ≈ 6–15 N·m, ω ≈ 260 rad/s, T_cht ≈ 450–520 K) the damage rate
# produces D = 1 in O(30–90 min) of simulated time.
# ---------------------------------------------------------------------------
K_ARCHARD     = 2.5e-7     # Archard wear coefficient [1/(N·m·rad)]
H_MATERIAL    = 1.0        # Normalised hardness (absorbed into K_ARCHARD)
K_ARRHENIUS   = 8.0        # Pre-exponential factor [1/s]. NOTE: at typical
                            # T_cht (450-520K) this alone drives D -> 1 in
                            # 35-160 min. Was 8.0e4 — that reached D=1 in
                            # ~1s regardless of fault severity (verified via
                            # sigma_generator's fault-run output showing
                            # D=0.74 already on row 0), which would have
                            # collapsed the RUL ground-truth label for every
                            # injector/cooling/detonation/turbo/oilLeak/
                            # fuelFilter run handed to BE-2 for M3 training.
E_ACTIVATION  = 42000.0    # Activation energy [J/mol] — typical for polymer/
                            # carbon deposit ageing, deliberately low to compress
                            # the time scale for the demo.


class DamageIntegrator:
    """
    Per-component damage accumulator.

    Each active fault type gets its own D state. The integrator is driven
    each physics tick from the MVEM's actual state variables — never from
    injected noise, so every sample gets an exact RUL label with no
    censoring (the same construction NASA used for C-MAPSS).
    """

    def __init__(self) -> None:
        # Damage state per component, keyed by fault name.
        # Values are D ∈ [0, 1].
        self._D: dict[str, float] = {}
        # Last computed damage rate per component [1/s]
        self._dD_dt: dict[str, float] = {}

    def reset(self) -> None:
        """Clear all accumulated damage (e.g. on a 'reset' message)."""
        self._D.clear()
        self._dD_dt.clear()

    def step(
        self,
        dt: float,
        fault_config: dict[str, Any],
        T_fric: float,
        omega: float,
        T_cht_K: float,
        oil_temp_K: float,
        t: float = 0.0,
    ) -> None:
        """
        Advance damage by dt seconds.

        Parameters
        ----------
        dt           : time step [s]
        fault_config : current FaultConfig from the WebSocket
        T_fric       : friction torque [N·m] — from MVEM
        omega        : crankshaft angular velocity [rad/s] — from MVEM
        T_cht_K      : representative cylinder head temperature [K] — mean of T_cht
        oil_temp_K   : oil temperature [K] — from MVEM
        t            : mission time [s], for ramping misfire's skip probability
                       the same way twin/faults.py does
        """
        for fault_name, spec in fault_config.items():
            if spec is None or fault_name in ("warmAirMass", "unmodelled"):
                continue
            if isinstance(spec, bool):
                continue

            # Only accumulate damage after the fault has started
            start_t_fault = float(spec.get("startT", 0.0))
            # We don't have mission time here, but the fault_config is only
            # non-empty when the fault is active (apply_fault_config checks
            # elapsed time). So if the key is present, it's running.

            dD_dt = 0.0

            if fault_name in ("bearing", "ringWear"):
                # Archard: wear proportional to friction power
                dD_dt = K_ARCHARD * abs(T_fric) * abs(omega) / H_MATERIAL

            elif fault_name in ("injector", "cooling", "detonation"):
                # Arrhenius: thermally activated ageing
                # Use T_cht for injector/cooling/detonation
                T_eff = max(T_cht_K, 300.0)
                dD_dt = K_ARRHENIUS * math.exp(-E_ACTIVATION / (R_GAS * T_eff))

            elif fault_name == "turbo":
                # Turbo degradation: combination of thermal and mechanical
                # Use a blended rate — thermal from exhaust + mechanical from shaft
                T_eff = max(T_cht_K, 300.0)
                dD_dt_thermal = K_ARRHENIUS * 0.5 * math.exp(-E_ACTIVATION / (R_GAS * T_eff))
                dD_dt_mech = K_ARCHARD * 0.3 * abs(T_fric) * abs(omega) / H_MATERIAL
                dD_dt = dD_dt_thermal + dD_dt_mech

            elif fault_name == "oilLeak":
                # Oil system wear: driven by oil temperature (thermal ageing of seals)
                T_eff = max(oil_temp_K, 300.0)
                dD_dt = K_ARRHENIUS * 0.6 * math.exp(-E_ACTIVATION / (R_GAS * T_eff))

            elif fault_name == "fuelFilter":
                # Fuel filter clogging: slow thermal/chemical process
                T_eff = max(T_cht_K, 300.0)
                dD_dt = K_ARRHENIUS * 0.4 * math.exp(-E_ACTIVATION / (R_GAS * T_eff))

            elif fault_name == "misfire":
                # Misfire damage: each missed event contributes thermal shock.
                # Rate proportional to the *current* skip probability (ramped
                # the same way twin/faults.py ramps it into mvem.py), not the
                # raw config rate — otherwise this label overstates damage
                # before the fault has fully ramped in.
                rate = float(spec.get("rate", 0.0))
                elapsed_min = max(0.0, (t - start_t_fault) / 60.0)
                skip_prob = min(rate * elapsed_min, 1.0)
                T_eff = max(T_cht_K, 300.0)
                dD_dt = K_ARRHENIUS * skip_prob * math.exp(-E_ACTIVATION / (R_GAS * T_eff))

            elif fault_name in ("chtSensor", "egtSensor", "mapSensor", "lambdaSensor"):
                # Sensor faults do NOT accumulate physical damage — they are
                # instrumentation failures, not engine degradation.
                continue

            # Integrate (Miner's rule)
            D_prev = self._D.get(fault_name, 0.0)
            D_new = min(D_prev + dD_dt * dt, 1.0)
            self._D[fault_name] = D_new
            self._dD_dt[fault_name] = dD_dt

    @property
    def total_D(self) -> float:
        """Worst-case damage across all components."""
        if not self._D:
            return 0.0
        return max(self._D.values())

    @property
    def total_dD_dt(self) -> float:
        """Damage rate of the most-damaged component [1/s]."""
        if not self._D:
            return 0.0
        worst = max(self._D, key=self._D.get)  # type: ignore[arg-type]
        return self._dD_dt.get(worst, 0.0)

    @property
    def rul_h(self) -> float | None:
        """
        Ground-truth remaining useful life [hours].

        Returns None if no damage is accumulating (healthy engine — the
        correct behaviour, matching docs/qa/known-issues.md).
        """
        dD = self.total_dD_dt
        if dD < 1e-15:
            return None
        D = self.total_D
        if D >= 1.0:
            return 0.0
        return (1.0 - D) / dD / 3600.0

    def get_state(self) -> dict:
        """
        Return the full damage state for wiring into the health frame.

        Returns a dict with:
          - damage_state: float (D, worst-case)
          - damage_rate_per_hr: float (dD/dt × 3600)
          - rul_h: float | None (ground-truth RUL in hours)
          - per_component: dict[str, {D, dD_dt, rul_h}]
        """
        per_comp = {}
        for name, D in self._D.items():
            dD = self._dD_dt.get(name, 0.0)
            comp_rul = None
            if dD > 1e-15 and D < 1.0:
                comp_rul = (1.0 - D) / dD / 3600.0
            per_comp[name] = {
                "D": D,
                "dD_dt": dD,
                "rul_h": comp_rul,
            }

        return {
            "damage_state": self.total_D,
            "damage_rate_per_hr": self.total_dD_dt * 3600.0,
            "rul_h": self.rul_h,
            "per_component": per_comp,
        }
