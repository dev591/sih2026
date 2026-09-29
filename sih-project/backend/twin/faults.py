"""
Fault injector for the PRAMANA engine twin.

Translates a FaultConfig dict (received over the WebSocket from the
frontend's FaultConsole) into per-step MVEM parameter mutations and
sensor bias offsets.

Message contract — reused verbatim from frontend/src/mock/missionGenerator.ts
so the Fault Console needs zero frontend changes and SIMULATED/LIVE stay
behaviourally identical:

    FaultSpec  { startT: number, cyl?: number, rate: number }
    FaultConfig {
        injector?:    FaultSpec   rate = fraction of C_d lost per minute
        turbo?:       FaultSpec   rate = fraction of eta_c lost per minute
        cooling?:     FaultSpec   rate = fraction of RADIATOR effectiveness lost per minute
        coolantPump?: FaultSpec   rate = fraction of coolant flow lost per minute
        bearing?:     FaultSpec   rate = friction fraction gained per minute
        chtSensor?:   FaultSpec   rate = degC per minute of bias
        egtSensor?:   FaultSpec   rate = degC per minute of bias
        warmAirMass?: boolean     common-mode OAT offset, hits BOTH engines
    }

Ramp formula (matches frontend's elapsedMin exactly):
    severity = rate * max(0, (t - startT) / 60)

Clamps match missionGenerator.ts values so LIVE and SIMULATED degrade
in the same shape.

Sensor faults (chtSensor, egtSensor) go through sensor_biases ONLY —
they never touch plantA_params, preserving the engine/sensor asymmetry
that is already correct in measurement.py.
"""

from __future__ import annotations
import copy
from typing import Any


# ISA offset added when warmAirMass is active [K]
WARM_AIR_MASS_OFFSET_K = 15.0

# Per-fault clamps — matched to frontend/src/mock/missionGenerator.ts
_CLAMPS: dict[str, tuple[float, float]] = {
    "injector": (0.4,  1.0),   # cd_inj lower bound
    "turbo":    (0.55, 1.0),   # eta_c_scale lower bound
    "cooling":  (0.5,  1.0),   # rad_eff_scale lower bound (radiator fouling)
    "coolantPump": (0.4, 1.0), # cool_pump_scale lower bound
    "bearing":  (1.0,  2.2),   # f_fric_scale upper bound
    "ringWear": (0.6,  1.0),   # eta_v_scale lower bound
    "oilLeak":  (0.2,  1.0),   # oil_pump_scale lower bound
    "fuelFilter": (0.3, 1.0),  # fuel_rail_scale lower bound
    "unmodelled": (0.6, 1.0),  # prop_cp_scale lower bound (blade damage)
}


def apply_fault_config(
    t: float,
    fault_config: dict[str, Any],
    nominal_params: dict,
    sensor_biases: dict,
    isa_offset_K: float,
    altitude_ft: float,
    liquid_cooled: bool = True,
) -> tuple[dict, dict, float]:
    """
    Apply the current fault_config at mission time t.

    Parameters
    ----------
    t               : mission time [s] — seconds since connection start
    fault_config    : dict received from the frontend FaultConsole
    nominal_params  : healthy MVEM parameter dict (never mutated in-place)
    sensor_biases   : healthy sensor bias dict (never mutated in-place)
    isa_offset_K    : current ISA temperature offset [K]
    altitude_ft     : current altitude [ft] for pressure-dependent faults
    liquid_cooled   : whether the profile carries a coolant loop; decides
                      whether `cooling` means radiator fouling or (on the
                      air-cooled Rotax profile) the old conductance scale

    Returns
    -------
    (plantA_params, sensor_biases_out, isa_offset_K_out)
    """
    params = copy.deepcopy(nominal_params)
    biases = copy.deepcopy(sensor_biases)
    isa_out = isa_offset_K

    for fault_name, spec in fault_config.items():
        if spec is None:
            continue

        # warmAirMass is a boolean flag, not a FaultSpec
        if fault_name == "warmAirMass":
            if spec:
                isa_out += WARM_AIR_MASS_OFFSET_K
            continue

        # All other faults are FaultSpec dicts
        start_t     = float(spec.get("startT", 0.0))
        rate        = float(spec.get("rate",   0.0))
        cyl         = int(spec.get("cyl", 0))

        elapsed_min = max(0.0, (t - start_t) / 60.0)
        sev         = rate * elapsed_min

        if fault_name == "injector":
            lo, _ = _CLAMPS["injector"]
            params["cd_inj"][cyl] = max(lo, 1.0 - sev)

        elif fault_name == "turbo":
            lo, _ = _CLAMPS["turbo"]
            params["eta_c_scale"] = max(lo, 1.0 - sev)

        elif fault_name == "cooling":
            # A blocked or fouled radiator core, which is the common real
            # cooling failure. It acts on the RADIATOR, so coolant temperature
            # rises and drags head temperature with it — where the old
            # `hA_scale` version could only move head temperature, with the
            # coolant pinned at a constant. On a profile with no coolant loop
            # (Rotax) it still falls back to hA_scale, so that engine's
            # behaviour is unchanged.
            lo, _ = _CLAMPS["cooling"]
            if liquid_cooled:
                params["rad_eff_scale"] = max(lo, 1.0 - sev)
            else:
                params["hA_scale"] = max(lo, 1.0 - sev)

        elif fault_name == "coolantPump":
            lo, _ = _CLAMPS["coolantPump"]
            params["cool_pump_scale"] = max(lo, 1.0 - sev)

        elif fault_name == "bearing":
            _, hi = _CLAMPS["bearing"]
            params["f_fric_scale"] = min(hi, 1.0 + sev)

        elif fault_name == "ringWear":
            lo, _ = _CLAMPS["ringWear"]
            params["eta_v_scale"] = max(lo, 1.0 - sev)

        elif fault_name == "oilLeak":
            lo, _ = _CLAMPS["oilLeak"]
            params["oil_pump_scale"] = max(lo, 1.0 - sev)

        elif fault_name == "fuelFilter":
            lo, _ = _CLAMPS["fuelFilter"]
            alt_factor = 1.0 + max(0.0, (altitude_ft - 8000.0) / 20000.0)
            params["fuel_rail_scale"] = max(lo, 1.0 - sev * alt_factor)

        elif fault_name == "misfire":
            params["misfire_prob"][cyl] = min(sev, 1.0)

        elif fault_name == "detonation":
            params["detonation_sev"][cyl] = min(sev, 1.0)

        elif fault_name == "chtSensor":
            # Sensor bias only — engine state is unaffected
            biases["cht_C"][cyl] = biases["cht_C"].get(cyl, 0.0) + sev \
                if isinstance(biases["cht_C"], dict) \
                else biases["cht_C"][cyl] + sev

        elif fault_name == "egtSensor":
            biases["egt_C"][cyl] = biases["egt_C"].get(cyl, 0.0) + sev \
                if isinstance(biases["egt_C"], dict) \
                else biases["egt_C"][cyl] + sev

        elif fault_name == "mapSensor":
            biases["map_hPa"] = biases.get("map_hPa", 0.0) + sev

        elif fault_name == "lambdaSensor":
            biases["lambda"] = biases.get("lambda", 0.0) + sev

        elif fault_name == "unmodelled":
            # The novelty test needs a REAL physical fault the classifier was
            # never trained on. This key used to change nothing at all, so a
            # judge picking "fault outside the library" on the live backend
            # watched a healthy engine. Now: propeller blade damage, rate =
            # fraction of power coefficient lost per minute.
            lo, _ = _CLAMPS["unmodelled"]
            params["prop_cp_scale"] = max(lo, 1.0 - sev)

    return params, biases, isa_out


def fresh_sensor_biases(n_cyl: int) -> dict:
    """Return a zeroed sensor-bias dict for n_cyl cylinders."""
    return {
        "cht_C":    [0.0] * n_cyl,
        "egt_C":    [0.0] * n_cyl,
        "map_hPa":  0.0,
        "lambda":   0.0,
    }
