# PRAMANA — Frozen Telemetry Schema

**Status: FROZEN as of Day 0. Changes require agreement from BE-1, BE-2 and frontend together.**

This is the contract that lets three developers build simultaneously without
blocking each other. BE-1 produces these messages; BE-2 consumes the residual
vector; the frontend consumes the health frame. Nobody waits.

If you need a field that isn't here, add it — but announce it, and never
rename or retype an existing field mid-build. A silent rename on Day 3 costs
the team more than the feature was worth.

---

## The two-lane split, and why it exists

This is not an architectural preference. It is bandwidth arithmetic, and
stating it that way makes the edge/cloud split sound inevitable rather than
fashionable.

Knock and bearing signatures live at 5–20 kHz. A MALE UAV's downlink is a few
tens of kilobits per second over satcom or line-of-sight, **shared with video
and command traffic**. Raw vibration cannot go down that pipe, in any
architecture, ever. So the fast signal processing happens on the airframe, and
only extracted features and health state travel down.

| Lane | Rate | Lives where | Down the link? |
|---|---|---|---|
| **Fast** | 5–20 kHz | On-board only | No — extracted features only |
| **Slow** | 10–50 Hz | Bus → ground | Yes |
| **Health** | 1 Hz | Computed at edge | Yes — a few hundred bytes/s |

---

## Lane 1 — SLOW (10–50 Hz, downlinked)

The raw engine channel set. This is PS component **B** (health monitoring)
verbatim — every channel DRDO names is present, including the two most teams
skip.

```jsonc
{
  "schema": "pramana.slow.v1",
  "t": 1725782400.000,          // float, seconds since mission start (NOT wall clock)
  "engine_id": "A",             // "A" | "B" — twin-engine platform. See note below.
  "seq": 18422,                 // uint32, monotonic, wraps. Gap = dropped frame.

  "rpm": 3580.4,                // crankshaft speed [rpm]
  "map_hPa": 1187.2,            // intake manifold absolute pressure
  "iat_K": 318.6,               // intake air temperature (post-intercooler)
  "cht_C": [128.1, 129.4, 131.8, 127.9],   // per cylinder, ALWAYS length = n_cylinders
  "egt_C": [718.2, 720.6, 719.1, 717.4],   // per cylinder, same ordering, 1-indexed in UI
  "oil_press_bar": 3.42,
  "oil_temp_C": 96.3,
  "fuel_flow_kgps": 0.00612,    // delivered fuel mass flow
  "fuel_rail_bar": 1.68,
  "lambda": 1.42,               // wideband UEGO. Path 3 depends on this.
  "turbo_rpm": 118400.0,        // turbocharger shaft speed
  "comp_out_p_hPa": 1240.0,     // compressor delivery pressure (before intercooler)
  "comp_out_T_K": 372.4,
  "inj_timing_deg": 12.4,       // injection timing advance  <-- PS component B, named
  "bus_voltage_V": 27.8,        // battery/alternator health  <-- PS component B, named
  "alternator_A": 14.2,         //                            <-- PS component B, named
  "throttle_pct": 72.0,
  "vib_rms_g": [0.42, 0.44, 0.61, 0.43],   // per-cylinder-region RMS, FEATURE not raw

  "altitude_ft": 18000.0,       // from the flight sim / mission profile
  "tas_mps": 61.2,              // true airspeed — needed for propeller advance ratio J
  "oat_K": 251.6                // outside air temperature
}
```

**`inj_timing_deg`, `bus_voltage_V` and `alternator_A` are three channels and
five minutes of work, and they prove the team read the actual problem
statement text.** Do not drop them.

**On `engine_id`:** the platform is twin-engined and that is a free reference
channel, not a cosmetic detail. Two nominally identical engines, same fuel,
same air, same profile, for eighteen hours — the best controlled experiment in
aviation. Every stream carries its engine tag from Day 0 so the cross-engine
differential `Δ = y_A − y_B` is available later without a schema change.
Common-mode variation (ambient, fuel batch, density) cancels exactly; what
survives is degradation or instrumentation. **Both engines drifting together
is the environment; one engine drifting alone is that engine.**

---

## Lane 2 — FAST (5–20 kHz, on-board only, NEVER downlinked)

Never serialised to JSON in production — this is a shared-memory ring buffer on
the airframe. Represented here only so the simulator and the edge process agree
on shape.

```jsonc
{
  "schema": "pramana.fast.v1",
  "t0": 1725782400.000,
  "fs_hz": 20000.0,
  "crank_angle_deg": [ /* float32 */ ],   // instantaneous crank position
  "omega_rad_s":     [ /* float32 */ ],   // angular velocity — the 0.5-order source
  "vib_xyz_g":       [ /* float32 x3 */ ],
  "knock_v":         [ /* float32 */ ]
}
```

**What crosses the boundary** — features only, computed at the edge:

```jsonc
{
  "schema": "pramana.fastfeat.v1",
  "t": 1725782400.000,
  "engine_id": "A",
  "order_0p5_mag": 0.031,       // THE misfire discriminant. In a four-stroke each
                                // cylinder fires once per 720 deg of crank rotation,
                                // so a single-cylinder defect appears at 0.5 ENGINE
                                // ORDER in the angular-velocity spectrum. Zero for a
                                // balanced engine. Recovered from the crank sensor
                                // that is already fitted — no new hardware.
  "order_0p5_phase_deg": 143.7, // phase localises WHICH cylinder
  "order_1p0_mag": 0.118,
  "order_2p0_mag": 0.077,
  "knock_intensity": [0.02, 0.02, 0.09, 0.02],
  "vib_band_rms": {"lo_0_500": 0.31, "mid_500_5k": 0.44, "hi_5k_20k": 0.12}
}
```

---

## Lane 3 — HEALTH (1 Hz, downlinked)

**This is the frontend's primary contract.** If BE-1 and BE-2 are behind, the
frontend mocks *this* message and builds the entire dashboard against it.

```jsonc
{
  "schema": "pramana.health.v1",
  "t": 1725782400.000,
  "engine_id": "A",

  // ---- PARITY RESIDUAL VECTOR (see residual-spec.md) ----
  // Nominally ZERO. Operating-point invariant BY CONSTRUCTION.
  // Normalised by the healthy-data sigma, so units are "sigmas".
  "rho": {
    "rho1_sd_vs_comp":   0.12,   // speed-density  vs compressor map
    "rho2_sd_vs_lambda": 3.81,   // speed-density  vs fuel/lambda
    "rho3_sd_vs_restr":  null,   // null when the path is unavailable on this engine
    "rho4_energy":       1.44,   // first-law closure
    "rho5_power":       -0.22,   // propeller-as-dynamometer power closure
    "rho6_9_cyl_dev":  [0.10, 3.94, 0.08, -0.12],  // per-cyl thermal deviation
    "rho10_oil":        -0.06,
    "rho11_ripple":      2.71    // 0.5-order magnitude
  },

  // ---- ESTIMATED HEALTH PARAMETERS (UKF) ----
  // These ARE the health indicators, in physical units, each with a covariance.
  // "compressor efficiency scale 0.87 +/- 0.02, down from 0.94" is a statement a
  // propulsion engineer can accept or dispute. "Health score 0.71" is not.
  "theta": {
    "eta_v_scale":   {"value": 0.981, "sigma": 0.011},
    "eta_c_scale":   {"value": 0.994, "sigma": 0.019},
    "hA_scale":      {"value": 1.002, "sigma": 0.024},
    "cd_inj":        {"value": [1.00, 0.91, 1.00, 0.99],
                      "sigma": [0.02, 0.03, 0.02, 0.02]},
    "f_fric_scale":  {"value": 1.008, "sigma": 0.015}
  },

  // ---- VIRTUAL SENSORS (analytical redundancy — PS asks for this explicitly) ----
  "virtual": {
    "peak_cyl_press_bar": [141.2, 138.8, 142.0, 140.6],
    "knock_margin_deg": 6.4,
    "turbo_shaft_rpm_est": 118210.0,
    "comb_efficiency": [0.982, 0.961, 0.981, 0.980],
    "air_mass_flow_kgps": 0.0891,
    "brake_power_kW": 88.4,
    "bsfc_g_per_kWh": 249.1        // degraded, not the book figure — drives PNR
  },

  // ---- ANOMALY / DIAGNOSIS ----
  "anomaly": {
    "score": 0.84,                 // LSTM-AE reconstruction error, normalised
    "threshold": 0.31,             // 99.5th pct of healthy held-out. NEVER hand-picked.
    "persistence": {"n": 4, "of": 5, "met": true}   // N-of-M rule suppresses transients
  },
  "diagnosis": {
    "top": [
      {"fault": "injector_fouling", "cylinder": 2, "p": 0.91,
       "source": "classifier+matrix"},   // network AND signature matrix agree
      {"fault": "egt_sensor_drift", "cylinder": 2, "p": 0.06, "source": "matrix"}
    ],
    "is_sensor_fault": false,      // THE differentiator. Drives the demo's 3:00 beat.
    "ambiguous": false,            // true => active diagnosis is armed
    "probe": null                  // populated while an active probe is running
  },

  // ---- REMAINING USEFUL LIFE — two heads, always both ----
  "rul": {
    "component": "injector_cyl2",
    "physics_h": 4.1,              // damage-rate integration forward
    "network_h": 3.7,              // quantile-regression p50
    "p10_h": 2.4,
    "p50_h": 3.7,
    "p90_h": 4.1,
    "reported_h": 3.7,             // the CONSERVATIVE of the two heads
    "heads_disagree": false        // disagreement beyond the interval is itself a warning
  },

  // ---- MISSION DECISION — PS component "reliability ENHANCEMENT" ----
  "mission": {
    "p_complete_continue": 0.58,
    "p_complete_derate": 0.94,
    "p_complete_rtb": 0.99,
    "derate_cost_min_on_station": 40.0,   // reliability advice that ignores mission
                                          // value is ignored advice
    "recommended": "derate",
    "recommended_power_pct": 78.0,
    "recommended_boost_hPa": 1120.0,      // directly consumable by the FMS
    "point_of_no_return_s": 9240.0        // from remaining fuel and DEGRADED bsfc
  },

  "limits_state": "green"          // green | caution | exceeded — what a THRESHOLD
                                   // system would be showing right now. Keep it on
                                   // screen: the contrast is the whole argument.
}
```

---

## Transport

```
MVEM  →  SocketCAN vcan0  →  CAN→MQTT bridge  →  MQTT→FastAPI  →  WebSocket  →  React
       (CANaerospace-ish)                                          (JSON above)
```

CAN framing, because that is what an engine controller actually emits — the PS
names CAN explicitly, not MQTT. CANaerospace (Stock Flight Systems 1998,
adopted by NASA as the AGATE databus standard 2001, identifiers 300–1799
reserved) and DroneCAN/Cyphal (what ArduPilot and PX4 run, 1 Mbit/s) are the
two relevant conventions.

In development the bus is a Linux virtual CAN interface, so **the ground
software consumes byte-identical frames whether the source is the simulator or
a real engine.** That is the entire deployment argument, and it is why the
boundary is worth designing deliberately.

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set up vcan0
```

Keep `candump vcan0` scrolling in a corner of the demo screen. It does more
for credibility than any slide, because it says *this speaks the bus the ECU
speaks.*

**Storage:** Parquet + DuckDB. Ten minutes of setup, queries fly, and mission
replay becomes a range scan. TimescaleDB belongs on the deployment-roadmap
slide, not in the hackathon repo — you get the credit without the setup cost.

---

## Field conventions, so nobody has to ask

- **Time** is `t`, float seconds since mission start. Not wall clock, not ISO strings.
- **Units are in the field name.** `_C`, `_K`, `_bar`, `_hPa`, `_kgps`, `_h`, `_ft`, `_mps`. No bare numbers.
- **Temperatures:** `_C` for anything shown to a human, `_K` for anything entering the physics. Both may exist; never silently mix them.
- **Per-cylinder arrays** are always length `n_cylinders`, always the same ordering, always **0-indexed in code and 1-indexed in the UI**. Cylinder 2 in the demo script is `[1]` in the array.
- **Unavailable channels are `null`**, never `0`, never `-999`. A missing parity path is `null` and the isolability table is recomputed for the available set.
- **Nothing is pre-rounded** for display. The frontend formats; the backend transmits full precision.
