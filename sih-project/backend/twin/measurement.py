"""
MEASUREMENT LAYER — transducers, not `true_value + noise`.

What this replaces
------------------
The previous model was 64 lines of `true + bias + gauss(0, sigma)`: memoryless
(it held only an RNG, so it was structurally incapable of lag), instantaneous,
and identical for every channel. A reviewing mechanical engineer pointed out
that the TYPE of sensor changes both the governing equation and the response
time, which a single Gaussian cannot express. He was right.

What a transducer actually does, and what is modelled here
----------------------------------------------------------
1. FIRST-ORDER LAG. Each channel has its own tau from config/sensors.yaml.
   Lags that are PHYSICAL rather than instrumental (the head's thermal mass for
   CHT, manifold filling for MAP, oil bulk for oil temperature) already live in
   mvem.py and are deliberately NOT repeated here. EGT is the exception: it is
   algebraic in the plant, so it carries a gas-path transport lag as well as a
   probe lag.

2. TOLERANCE IS NOT NOISE. A Class 1 thermocouple's +/-1.5 degC is
   overwhelmingly a FIXED per-probe calibration offset, not a value redrawn
   every second. Each probe therefore draws its offset ONCE, at construction,
   and holds it. This is why a real engine shows a persistent
   cylinder-to-cylinder EGT/CHT spread — which a resampled Gaussian cannot
   reproduce, and which is the genuinely hard part of per-cylinder diagnosis.
   These offsets belong to the PLANT ONLY: the twin cannot know them, and that
   asymmetry is what makes them realistic.

3. THE TRANSDUCTION CHAIN. For the channels where it changes the number, the
   real chain is walked: temperature -> EMF -> cold-junction error -> ADC
   quantisation -> numerical inversion -> indicated temperature. That produces
   non-linear quantisation and a genuine CJC error that no additive model gives
   you. For the linear piezoresistive channels, a bridge plus a linear ADC is
   arithmetically just "quantise in engineering units", so that is what is
   done — see `docs/plan` on where realism actually changes the number.

4. VALIDITY. A cold wideband UEGO does not read lean, it reads NOTHING. Such
   channels return None until the heater reaches its window. The residual
   generator already handles None channels (parity path 4 does this), so that
   path is reused rather than inventing a second convention.

Backward compatibility
----------------------
`measure()` keeps its signature and output keys. Each lag filter INITIALISES on
its first sample, so a call site that constructs a model and calls measure()
once (several tests do) gets the unlagged value exactly as before. That is also
physically right: a sensor that has been sitting at a condition reads it.
"""

from __future__ import annotations

import math
import random

from .profiles import load_engine_profile

# Loaded once. This is a generic YAML-from-config/ loader despite the name.
SENSOR_SPEC: dict = load_engine_profile("sensors.yaml")
_ACQ: dict = SENSOR_SPEC["acquisition"]
_CH: dict = SENSOR_SPEC["channels"]

_ADC_LEVELS = 2 ** int(_ACQ["adc_bits"])


# ---------------------------------------------------------------------------
# ITS-90 thermocouple reference functions
# ---------------------------------------------------------------------------
# Verbatim from its90.nist.gov/downloadFiles/type_{k,j}.tab.txt, re-fetched and
# verified 2026-09-12: reproduces the published tables to +/-0.0005 mV over the
# full range, which is the tables' own rounding.
#
# TRANSCRIPTION TRAP, because it already cost us a wrong answer: NIST prints
# these as `0.971511471520E-22`, which is 9.715e-23, NOT 9.715e-22. Shifting
# the last two coefficients by 10x gave a polynomial that was correct at 0 and
# 100 degC but read 23.85 mV instead of 20.644 mV at 500 degC — about +75 degC
# of error, invisible at exactly the points you would spot-check first.
_TYPE_K_C = (
    -1.76004136860e-2, 3.89212049750e-2, 1.85587700320e-5, -9.94575928740e-8,
    3.18409457190e-10, -5.60728448890e-13, 5.60750590590e-16,
    -3.20207200030e-19, 9.71511471520e-23, -1.21047212750e-26,
)
# Type K alone carries a Gaussian term — a real ~+0.12 mV bump centred at
# 127 degC. Dropping it is a ~3 degC error through the whole cruise range.
_TYPE_K_A = (0.1185976, -1.183432e-4, 126.9686)

_TYPE_J_C = (
    0.0, 5.03811878150e-2, 3.04758369300e-5, -8.56810657200e-8,
    1.32281952950e-10, -1.70529583370e-13, 2.09480906970e-16,
    -1.25383953360e-19, 1.56317256970e-23,
)

_TC_RANGE = {"type_k": (0.0, 1372.0), "type_j": (0.0, 760.0)}


def tc_emf_mV(temp_C: float, kind: str) -> float:
    """Seebeck EMF for a thermocouple junction at `temp_C`, in mV."""
    if kind == "type_k":
        e = sum(c * temp_C ** i for i, c in enumerate(_TYPE_K_C))
        a0, a1, a2 = _TYPE_K_A
        return e + a0 * math.exp(a1 * (temp_C - a2) ** 2)
    e = sum(c * temp_C ** i for i, c in enumerate(_TYPE_J_C))
    return e


def tc_demf_dT(temp_C: float, kind: str) -> float:
    """Seebeck coefficient dE/dT in mV/degC — the derivative of `tc_emf_mV`."""
    if kind == "type_k":
        d = sum(i * c * temp_C ** (i - 1) for i, c in enumerate(_TYPE_K_C) if i > 0)
        a0, a1, a2 = _TYPE_K_A
        return d + a0 * math.exp(a1 * (temp_C - a2) ** 2) * 2.0 * a1 * (temp_C - a2)
    return sum(i * c * temp_C ** (i - 1) for i, c in enumerate(_TYPE_J_C) if i > 0)


def tc_temp_C(emf_mV: float, kind: str, guess_C: float | None = None) -> float:
    """
    Invert the reference function numerically.

    NIST publishes separate inverse polynomials, but they could not be verified
    from source, whereas the forward set above IS verified against the printed
    tables. The function is monotonic over the range, so inverting the verified
    forward function is exact to it and removes any dependency on unverified
    coefficients.

    Newton, seeded with the true temperature where the caller has it — which is
    always, since we just quantised that value's EMF. That converges in two or
    three steps instead of the ~50 a bisection needs. Bisection is kept as the
    fallback for a cold call with no guess, and as the escape if Newton wanders
    outside the range.
    """
    lo, hi = _TC_RANGE[kind]
    lo -= 50.0

    if guess_C is not None:
        t = min(max(guess_C, lo), hi)
        for _ in range(6):
            slope = tc_demf_dT(t, kind)
            if slope <= 1e-9:
                break
            step = (tc_emf_mV(t, kind) - emf_mV) / slope
            t -= step
            if not (lo <= t <= hi):
                break
            if abs(step) < 1e-6:
                return t
        else:
            return t
        if lo <= t <= hi:
            return t

    for _ in range(48):
        mid = 0.5 * (lo + hi)
        if tc_emf_mV(mid, kind) < emf_mV:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def ntc_resistance_ohm(temp_C: float, r25: float = 2500.0, beta: float = 3700.0) -> float:
    """Beta-parameter NTC curve. `provenance: assumed` — see sensors.yaml."""
    t_k = temp_C + 273.15
    return r25 * math.exp(beta * (1.0 / t_k - 1.0 / 298.15))


def ntc_temp_C(resistance_ohm: float, r25: float = 2500.0, beta: float = 3700.0) -> float:
    """Inverse of `ntc_resistance_ohm`."""
    resistance_ohm = max(resistance_ohm, 1e-6)
    inv = 1.0 / 298.15 + math.log(resistance_ohm / r25) / beta
    return 1.0 / inv - 273.15


# Bosch LSU 4.9 pumping current against lambda, from the datasheet table.
# Strongly non-linear beyond lambda ~2 — which is exactly where a diesel runs,
# so interpolating this properly matters rather than being a nicety.
_LSU49_IP_LAMBDA = (
    (-2.000, 0.650), (-1.602, 0.700), (-1.243, 0.750), (-0.927, 0.800),
    (-0.800, 0.822), (-0.652, 0.850), (-0.405, 0.900), (-0.183, 0.950),
    (-0.106, 0.970), (-0.040, 0.990), (0.000, 1.003), (0.015, 1.010),
    (0.097, 1.050), (0.193, 1.100), (0.250, 1.132), (0.329, 1.179),
    (0.671, 1.429), (0.938, 1.701), (1.150, 1.990), (1.385, 2.434),
    (1.700, 3.413), (2.000, 5.391), (2.150, 7.506), (2.250, 10.119),
)


def _interp(x: float, table, xi: int, yi: int) -> float:
    """Monotonic piecewise-linear lookup over `table`, clamped at both ends."""
    if x <= table[0][xi]:
        return table[0][yi]
    if x >= table[-1][xi]:
        return table[-1][yi]
    for a, b in zip(table, table[1:]):
        if a[xi] <= x <= b[xi]:
            span = b[xi] - a[xi]
            if span <= 0:
                return a[yi]
            f = (x - a[xi]) / span
            return a[yi] + f * (b[yi] - a[yi])
    return table[-1][yi]


def lambda_to_ip_mA(lam: float) -> float:
    return _interp(lam, _LSU49_IP_LAMBDA, 1, 0)


def ip_mA_to_lambda(ip: float) -> float:
    return _interp(ip, _LSU49_IP_LAMBDA, 0, 1)


_EMF_BOUNDS: dict[str, tuple[float, float]] = {}


def _emf_bounds(name: str, kind: str) -> tuple[float, float]:
    """Cached EMF span for a channel's temperature range — constant per channel."""
    got = _EMF_BOUNDS.get(name)
    if got is None:
        lo_C, hi_C = _CH[name].get("range", _TC_RANGE[kind])
        got = (tc_emf_mV(lo_C, kind), tc_emf_mV(hi_C, kind))
        _EMF_BOUNDS[name] = got
    return got


def quantise(value: float, lo: float, hi: float, levels: int = _ADC_LEVELS) -> float:
    """Round `value` onto an ADC grid spanning [lo, hi]."""
    if hi <= lo:
        return value
    span = hi - lo
    step = span / (levels - 1)
    clamped = min(max(value, lo), hi)
    return lo + round((clamped - lo) / step) * step


class MeasurementModel:
    """
    One instrumentation package. Hold one instance per engine and call it once
    per frame — the lag filters are stateful, so a fresh instance per call
    throws the sensor dynamics away (and silently reverts to the old
    pass-through behaviour).

    is_twin=True suppresses the per-probe tolerance offsets. The twin models the
    DETERMINISTIC part of the sensor (lag, quantisation) because it must, or
    every transient produces a residual purely from lag mismatch — but it cannot
    know each individual probe's calibration error.
    """

    def __init__(self, seed: int = 42, is_twin: bool = False, n_cyl: int = 4,
                 cold_start: bool = False):
        self.rng = random.Random(seed)
        self.is_twin = is_twin
        self.n_cyl = n_cyl
        # The plant starts AT CRUISE (see INITIAL_ENGINE_STATE in the frontend
        # store and the scenario profile in main.py) — nothing here models a
        # cold start, so the UEGO heater is already at its operating point and
        # lambda is valid from the first frame. cold_start=True exercises the
        # light-off path, during which lambda correctly reads None; only turn it
        # on alongside an actual start sequence, and expect None-handling
        # downstream when you do.
        self._cold_start = cold_start

        # Lag state. None means "not yet initialised" — the first sample seeds
        # the filter, so one-shot call sites behave exactly as before.
        self._lag: dict[str, float | list[float] | None] = {}

        # Cold-junction (terminal block) temperature, which lags ambient badly
        # and is therefore a real slow-drift error source after a cowl
        # temperature step. Seeded on first use.
        self._t_cj: float | None = None

        # Heater/light-off accumulator for the UEGO. Pre-lit unless a cold
        # start was asked for, per the note in __init__.
        self._lambda_warm_s = 0.0 if cold_start else 1e9

        # Per-probe fixed calibration offsets, drawn ONCE. Same seed gives the
        # same offsets, so seeded comparisons across runs stay valid.
        self._offset: dict[str, float | list[float]] = {}
        if not is_twin:
            for name, spec in _CH.items():
                tol = spec.get("tolerance_degC")
                if tol is None:
                    # For pressures, use the ZERO-OFFSET fraction, not the total
                    # accuracy band. `accuracy_frac_fs` combines offset, span,
                    # linearity and temperature terms, and only the offset part
                    # is a fixed per-unit error the twin cannot calibrate out.
                    # Using the whole band inflated sigma(rho10) ~390% and
                    # desensitised oil-circuit detection for no physical reason.
                    frac = spec.get("offset_frac_fs", spec.get("accuracy_frac_fs"))
                    if frac is None:
                        continue
                    rng_hi = spec.get("range", [0.0, 1.0])[1]
                    tol = frac * rng_hi
                if name in ("cht_C", "egt_C"):
                    self._offset[name] = [
                        self.rng.uniform(-tol, tol) for _ in range(n_cyl)
                    ]
                else:
                    self._offset[name] = self.rng.uniform(-tol, tol)

    # -- helpers ------------------------------------------------------------
    def noise(self, sigma: float) -> float:
        """Kept for backward compatibility; callers used this directly."""
        return self.rng.gauss(0, sigma)

    def _n(self, name: str, add_noise: bool) -> float:
        if not add_noise:
            return 0.0
        return self.rng.gauss(0, _CH[name].get("noise_sigma", 0.0))

    def _apply_lag(self, name: str, value: float, dt: float, tau_key: str = "tau_s") -> float:
        spec = _CH[name]
        tau = float(spec.get(tau_key, 0.0) or 0.0)
        extra = float(spec.get("tau_gas_transport_s", 0.0) or 0.0)
        tau += extra
        prev = self._lag.get(name)
        if prev is None or tau <= 0.0 or dt <= 0.0:
            self._lag[name] = value
            return value
        alpha = 1.0 - math.exp(-dt / tau)
        out = prev + alpha * (value - prev)
        self._lag[name] = out
        return out

    def _apply_lag_vec(self, name: str, values: list[float], dt: float) -> list[float]:
        spec = _CH[name]
        tau = float(spec.get("tau_s", 0.0) or 0.0) + float(
            spec.get("tau_gas_transport_s", 0.0) or 0.0
        )
        prev = self._lag.get(name)
        if prev is None or tau <= 0.0 or dt <= 0.0:
            self._lag[name] = list(values)
            return list(values)
        alpha = 1.0 - math.exp(-dt / tau)
        out = [p + alpha * (v - p) for p, v in zip(prev, values)]  # type: ignore[arg-type]
        self._lag[name] = out
        return out

    def _thermocouple(self, name: str, true_C: float, kind: str, idx: int | None,
                      bias: float, add_noise: bool) -> float:
        """
        Walk the real chain: junction EMF, cold-junction compensation error,
        ADC quantisation of the VOLTAGE, then numerical inversion.

        Quantising the voltage rather than the temperature is the point: the
        Seebeck coefficient is not constant, so a uniform voltage grid is a
        NON-uniform temperature grid. No additive model reproduces that.
        """
        spec = _CH[name]
        off = self._offset.get(name, 0.0)
        if isinstance(off, list):
            off = off[idx] if idx is not None and idx < len(off) else 0.0

        e_hot = tc_emf_mV(true_C, kind)
        e_cj_true = tc_emf_mV(self._t_cj if self._t_cj is not None else 25.0, kind)

        cj = spec.get("cold_junction", {})
        if cj.get("enabled"):
            # The instrument believes its terminals are at the LAGGED
            # temperature, so it adds back the wrong EMF. That residual error
            # is the physical effect, and it decays with the block's tau.
            e_meas = e_hot - e_cj_true
            e_reported = e_meas + e_cj_true
        else:
            e_reported = e_hot

        e_lo, e_hi = _emf_bounds(name, kind)
        e_reported = quantise(e_reported, e_lo, e_hi)

        # Seed the inversion with the true value: we are inverting the EMF of a
        # temperature we already know, so Newton lands in two or three steps.
        indicated = tc_temp_C(e_reported, kind, guess_C=true_C)
        return indicated + off + bias + self._n(name, add_noise)

    # -- the measurement ----------------------------------------------------
    def measure(self, phys: dict, sensor_biases: dict | None = None,
                add_noise: bool = True, dt: float = 1.0) -> dict:
        """
        Map true plant state to what the instrumentation reports.

        `dt` is the interval since the previous call, and drives the lag
        filters. It defaults to 1.0 to match the frame rate; a call site that
        measures once gets the unlagged value regardless.
        """
        if sensor_biases is None:
            sensor_biases = {}

        n_cyl = len(phys["cht_C"])
        cht_bias = sensor_biases.get("cht_C", [0.0] * n_cyl)
        egt_bias = sensor_biases.get("egt_C", [0.0] * n_cyl)

        # Cold junction tracks intake air as a proxy for cowl/terminal ambient.
        t_amb_C = phys["iat_K"] - 273.15
        if self._t_cj is None:
            self._t_cj = t_amb_C
        else:
            tau_cj = float(_CH["egt_C"]["cold_junction"].get("tau_s", 60.0))
            self._t_cj += (1.0 - math.exp(-dt / tau_cj)) * (t_amb_C - self._t_cj)

        # ---- temperatures: full transduction chain ----
        egt = [
            self._thermocouple("egt_C", v, "type_k", i, egt_bias[i], add_noise)
            for i, v in enumerate(phys["egt_C"])
        ]
        cht = [
            self._thermocouple("cht_C", v, "type_j", i, cht_bias[i], add_noise)
            for i, v in enumerate(phys["cht_C"])
        ]
        egt = self._apply_lag_vec("egt_C", egt, dt)
        cht = self._apply_lag_vec("cht_C", cht, dt)

        # ---- NTC channels: divider, so resolution compresses when hot ----
        iat_C_true = phys["iat_K"] - 273.15
        r_iat = ntc_resistance_ohm(iat_C_true)
        iat_C = ntc_temp_C(quantise(r_iat, 50.0, 40000.0))
        iat_K = self._apply_lag("iat_K", iat_C + 273.15, dt) \
            + float(self._offset.get("iat_K", 0.0)) + self._n("iat_K", add_noise)

        r_oil = ntc_resistance_ohm(phys["oil_temp_C"])
        oil_t = ntc_temp_C(quantise(r_oil, 50.0, 40000.0))
        oil_t = self._apply_lag("oil_temp_C", oil_t, dt) \
            + float(self._offset.get("oil_temp_C", 0.0)) + self._n("oil_temp_C", add_noise)

        # ---- piezoresistive: linear bridge + linear ADC == quantise here ----
        map_lo, map_hi = _CH["map_hPa"]["range"]
        map_v = quantise(phys["map_hPa"], map_lo, map_hi)
        map_v = self._apply_lag("map_hPa", map_v, dt) \
            + float(self._offset.get("map_hPa", 0.0)) \
            + sensor_biases.get("map_hPa", 0.0) + self._n("map_hPa", add_noise)

        # Compressor inlet conditions — Path 2's own transducers, genuinely
        # separate from the MAP sensor. That separateness is what makes rho1 a
        # parity relation rather than a restatement of Path 1.
        pa_lo, pa_hi = _CH["p_amb_hPa"]["range"]
        p_amb = quantise(phys["p_amb_hPa"], pa_lo, pa_hi)
        p_amb = self._apply_lag("p_amb_hPa", p_amb, dt) \
            + float(self._offset.get("p_amb_hPa", 0.0)) + self._n("p_amb_hPa", add_noise)

        oat_C_true = phys["oat_K"] - 273.15
        oat = self._apply_lag("oat_K", oat_C_true + 273.15, dt) \
            + float(self._offset.get("oat_K", 0.0)) + self._n("oat_K", add_noise)

        op_lo, op_hi = _CH["oil_press_bar"]["range"]
        oil_p = quantise(phys["oil_press_bar"], op_lo, op_hi)
        oil_p = self._apply_lag("oil_press_bar", oil_p, dt) \
            + float(self._offset.get("oil_press_bar", 0.0)) + self._n("oil_press_bar", add_noise)

        # ---- rpm: discrete, tooth-triggered, and dead below arming speed ----
        rpm_spec = _CH["rpm"]
        updates = float(rpm_spec["wheel"]["updates_per_rev"])
        # Torsional ripple is REAL shaft-speed variation, not measurement noise,
        # so it is added BEFORE the pickup quantises — otherwise the reported
        # value is knocked off the tooth grid and the discreteness, which is the
        # whole observable point of a variable-reluctance sensor, disappears.
        rpm_true = phys["rpm"] + self._n("rpm", add_noise)
        rpm_out = round(rpm_true * updates / 60.0) * 60.0 / updates if rpm_true > 0 else 0.0

        turbo = phys["turbo_rpm"] + self._n("turbo_rpm", add_noise)

        # ---- lambda: UEGO through its own non-linear characteristic ----
        lam_spec = _CH["lambda_val"]
        self._lambda_warm_s += dt
        heater = lam_spec.get("heater", {})
        lit = self._lambda_warm_s >= float(heater.get("light_off_s", 0.0))
        lam_true = phys["lambda_val"] + sensor_biases.get("lambda", 0.0)
        if lit or not heater.get("invalid_until_lit", False):
            ip = lambda_to_ip_mA(lam_true)
            ip_q = quantise(ip, -2.25, 2.25)
            lam_out = ip_mA_to_lambda(ip_q)
            lam_out = self._apply_lag("lambda_val", lam_out, dt) + self._n("lambda_val", add_noise)
        else:
            # A cold UEGO reads nothing at all. None, not a wrong number.
            lam_out = None

        # ---- fuel flow: a pulse counter measures VOLUME, not mass ----
        ff_spec = _CH["fuel_flow_kgps"]
        rho_fuel = float(ff_spec["fuel_density_kg_per_l"]) * 1000.0  # kg/m3
        vol_m3ps = phys["fuel_flow_total"] / rho_fuel
        gate = float(ff_spec.get("gate_time_s", 1.0))
        k_per_m3 = float(ff_spec["k_factor_pulses_per_usgal"]) / 3.785411784e-3
        pulses = vol_m3ps * gate * k_per_m3
        vol_q = (round(pulses) / (gate * k_per_m3)) if k_per_m3 > 0 else vol_m3ps
        ff = vol_q * rho_fuel
        ff = self._apply_lag("fuel_flow_kgps", ff, dt) + self._n("fuel_flow_kgps", add_noise)

        return {
            "rpm": float(rpm_out),
            "map_hPa": float(map_v),
            "iat_K": float(iat_K),
            "cht_C": [float(v) for v in cht],
            "egt_C": [float(v) for v in egt],
            "oil_press_bar": float(oil_p),
            "oil_temp_C": float(oil_t),
            "fuel_flow_kgps": float(ff),
            "lambda_val": (float(lam_out) if lam_out is not None else None),
            "turbo_rpm": float(turbo),
            "p_amb_hPa": float(p_amb),
            "oat_K": float(oat),
            "ripple": float(phys["ripple"] + self.noise(0.002) if add_noise else phys["ripple"]),

            # air_mass_flow and brake_power_kW are NOT directly-sensed channels:
            # residual-spec.md describes air_mass_flow as an ESTIMATE derived
            # from noisy MAP/IAT/N (Path 1), and brake power has no transducer
            # at all. Giving them an INDEPENDENT noise draw is what makes rho1
            # and rho5 look independent when they are not — see the plan's
            # Phase 3, which replaces this with propagated noise. Left as-is
            # here so this change is only about the sensor layer.
            "air_mass_flow": float(
                phys["air_mass_flow"]
                + (self.noise(max(phys["air_mass_flow"] * 0.015, 3e-4)) if add_noise else 0.0)
            ),
            "fuel_cmd_per_cyl": float(phys["fuel_cmd_per_cyl"]),
            "brake_power_kW": float(
                phys["brake_power_kW"]
                + (self.noise(max(phys["brake_power_kW"] * 0.01, 0.05)) if add_noise else 0.0)
            ),
        }
