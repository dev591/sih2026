/**
 * PRAMANA — mock mission generator.
 *
 * STANDS IN FOR BE-1's LAYER 0 until the real MVEM publishes over the socket.
 * When it does, this file is deleted and `useMission` subscribes instead.
 * Nothing else in the frontend changes — that is the point of the
 * pramana.health.v1 contract.
 *
 * Two rules it follows, because they are the rules that make the real thing
 * credible and the demo coherent:
 *
 *   1. FAULTS ARE PARAMETER PERTURBATIONS, NOT SPIKES PASTED ON A SIGNAL.
 *      We degrade an injector discharge coefficient and let the exhaust
 *      temperature, the fuel flow, the lambda reading and the crank ripple
 *      move on their own. Nobody hand-authors what a fault "looks like".
 *
 *   2. A SENSOR FAULT PERTURBS THE MEASUREMENT ONLY, NEVER THE ENGINE.
 *      That asymmetry is the entire sensor-vs-engine discriminator, and it
 *      has to be true in the generator or the demo is a lie.
 *
 * The whole mission is generated up front and cached. Never generate during
 * a demo.
 */

import { computeNovelty, nullSpaceDirection } from '../analysis/novelty';
import { VRDE_180, type EngineProfile } from '../config/engines';
import {
  N_CYL,
  type FaultId,
  type HealthFrame,
  type MissionTick,
  type SlowFrame,
  type FastFeatures,
  type PerCylinder,
  type FaultHypothesis,
} from '../types/telemetry';

// ---------------------------------------------------------------------------
// Seeded RNG. Deterministic FOR A GIVEN SEED — same seed always reproduces
// the same run bit-for-bit, which is what makes the mid-playback continuity
// guarantees below possible to state precisely. It is NOT one fixed seed:
// see `freshSeed()` and the `seed` field in state/missionStore.ts for where a
// genuinely new run draws a new one, and why a scripted-looking demo that
// always detects at the same instant regardless of noise was the bug this
// replaced, not the intended behaviour.
// ---------------------------------------------------------------------------
function mulberry32(seed: number) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Box-Muller, so sensor noise is Gaussian rather than uniform. */
function makeNoise(seed: number) {
  const rnd = mulberry32(seed);
  return (sigma: number) => {
    const u1 = Math.max(rnd(), 1e-9);
    const u2 = rnd();
    return sigma * Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  };
}

// ---------------------------------------------------------------------------
// Atmosphere — ISA troposphere. Three lines, and it unlocks every
// "environmental condition simulation" the problem statement asks for.
// ---------------------------------------------------------------------------
const FT_TO_M = 0.3048;

export function isa(altitudeFt: number, isaOffsetK = 0) {
  const h = altitudeFt * FT_TO_M;
  const T = 288.15 - 0.0065 * h + isaOffsetK;
  const p = 101325 * Math.pow(1 - 2.25577e-5 * h, 5.2559);
  const rho = p / (287.05 * T);
  return { T, p, rho };
}

// ---------------------------------------------------------------------------
// Mission definition
// ---------------------------------------------------------------------------
export const MISSION_DURATION_S = 300;
export const HEALTH_HZ = 1;


const T_INJECTOR_START = 40;
const T_SENSOR_DRIFT_START = 200;

/**
 * An exhaust valve leak on cylinder 4 — REAL physics, DELIBERATELY LEFT OUT of
 * the ten-fault library, so the novelty detector has something honest to catch.
 *
 * We inject it along a direction in the null space of the fault signature
 * matrix, which is exactly what "a fault the library cannot express" means.
 * BE-1's version should model the leak physically (blow-down loss through a
 * leaking seat) rather than adding a residual vector — but it must still be
 * kept OUT of F, or the detector has nothing to detect.
 */
const T_UNMODELLED_START = 258;

/**
 * COMMON-MODE DISTURBANCE — the whole point of the cross-engine channel.
 *
 * A warm air mass: ambient temperature rises, and it rises for BOTH engines
 * because they are on the same aeroplane flying through the same air. Every
 * absolute channel moves. The DIFFERENTIAL does not, because whatever is shared
 * cancels exactly in the subtraction.
 *
 * Without something like this in the data, a cross-engine panel has nothing to
 * prove. With it, the argument is visible in three seconds:
 *   both engines drifting together is the environment.
 *   one engine drifting alone is that engine.
 */
const T_WARM_AIR_START = 120;
function commonModeOffsetK(t: number, cfg: FaultConfig = SCRIPTED): number {
  if (!cfg.warmAirMass) return 0;
  if (t < T_WARM_AIR_START) return 0;
  if (t < T_WARM_AIR_START + 35) return ((t - T_WARM_AIR_START) / 35) * 22;   // ramp into warmer air
  return 22;                                    // and stay there
}
const NULL_DIR = nullSpaceDirection(0);

const FOULED_CYL = 1; // cylinder 2, 0-indexed
const DRIFT_CYL = 2;  // cylinder 3, 0-indexed

// ---------------------------------------------------------------------------
// FAULT CONFIGURATION
//
// The scripted mission and the interactive sandbox are the SAME physics with
// different configuration. That matters: whatever a judge injects live goes
// through exactly the code path the rehearsed demo uses, so there is no
// "demo mode" that behaves differently from the thing being demonstrated.
// ---------------------------------------------------------------------------
export interface FaultSpec {
  /** seconds into the run at which this fault begins */
  startT: number;
  /** 0-indexed cylinder, where the fault is per-cylinder */
  cyl?: number;
  /** severity rate, units depend on the fault */
  rate: number;
}

export interface FaultConfig {
  /** COMPONENT faults — these change the engine */
  injector?: FaultSpec;      // rate = fraction of C_d lost per minute
  turbo?: FaultSpec;         // rate = fraction of eta_c lost per minute
  cooling?: FaultSpec;       // rate = fraction of hA lost per minute
  bearing?: FaultSpec;       // rate = friction fraction gained per minute
  ringWear?: FaultSpec;      // rate = fraction of eta_v lost per minute
  oilLeak?: FaultSpec;       // rate = fraction of pump flow lost per minute
  misfire?: FaultSpec;       // rate = probability of a skipped firing event, 0-1
  detonation?: FaultSpec;    // rate = knock severity 0-1
  fuelFilter?: FaultSpec;    // rate = fraction of fuel rail capacity lost per minute
  /** INSTRUMENTATION faults — these change only what the sensor REPORTS */
  chtSensor?: FaultSpec;     // rate = degC per minute of bias
  egtSensor?: FaultSpec;     // rate = degC per minute of bias
  mapSensor?: FaultSpec;     // rate = hPa per minute of bias
  lambdaSensor?: FaultSpec;  // rate = lambda units per minute of bias
  /** deliberately outside the fault library, for the novelty detector */
  unmodelled?: FaultSpec;    // rate = sigma per second along the null space
  /** common-mode: affects BOTH engines, so it must cancel in the differential */
  warmAirMass?: boolean;
}

/** The rehearsed four-minute mission. */
export const SCRIPTED: FaultConfig = {
  injector: { startT: 40, cyl: 1, rate: 0.045 },
  chtSensor: { startT: 200, cyl: 2, rate: 24 },
  unmodelled: { startT: T_UNMODELLED_START, rate: 0.62 },
  warmAirMass: true,
};

/**
 * A commanded change of cruise altitude, ramped from the moment it was asked
 * for — the same shape as a fault spec, and for the same reason: the operator
 * watches the transition instead of being cut to the far side of it.
 */
export interface AltitudePlan {
  startT: number;
  from_ft: number;
  to_ft: number;
}

/**
 * Seconds for a commanded altitude change to complete.
 *
 * SIMULATION PACING, not a climb-rate claim — a real airframe in this class
 * would take minutes, and nothing here is derived from its performance. It is
 * set so the transition is watchable inside a demo slot.
 */
export const ALT_RAMP_S = 20;

/**
 * UNMODELLED CHANNELS — must stay byte-identical to `UNMODELLED` in
 * backend/main.py, or LIVE and SIMULATED disagree on the same field.
 *
 * PS component B names battery, alternator and injection timing, so the fields
 * exist. Nothing models them at either end. They previously carried synthetic
 * jitter here (`27.8 + noise(0.03)`) while the backend sent a bare constant —
 * so the same channel wobbled in SIMULATED and froze in LIVE.
 *
 * The jitter is gone rather than copied to the backend: a number that moves
 * without a cause asserts that something is being measured. Same reason parity
 * path 4 returns null instead of reusing another path's estimate.
 *
 * `fuel_rail_bar` was 1.68 at both ends — three orders of magnitude low for a
 * common rail (250-2000+ bar). Now a plausible constant, still not a measurement.
 */
const UNMODELLED = {
  fuel_rail_bar: 900.0,
  inj_timing_deg: 12.4,
  bus_voltage_V: 27.8,
  alternator_A: 14.2,
} as const;

/** Commanded altitude at t, smoothstepped so neither end of the ramp corners. */
export function altitudeAt(
  t: number, eng: EngineProfile, plan?: AltitudePlan | null
): number {
  if (!plan) return eng.cruiseAltitude_ft;
  if (t <= plan.startT) return plan.from_ft;
  const u = Math.min(1, (t - plan.startT) / ALT_RAMP_S);
  return plan.from_ft + (plan.to_ft - plan.from_ft) * (u * u * (3 - 2 * u));
}

// Cruise operating point, derived from the active engine profile. Nothing
// engine-specific may be hardcoded below this line.
const cruiseOf = (e: EngineProfile, t = 0, alt?: AltitudePlan | null) => ({
  altitude_ft: altitudeAt(t, e, alt),
  rpm: e.cruiseRpm,
  throttle_pct: 72,
  tas_mps: e.cruiseTas_mps,
});

// ---------------------------------------------------------------------------
// The engine's TRUE internal state — what is physically happening.
// A sensor fault never touches this.
// ---------------------------------------------------------------------------
interface TrueState {
  cd_inj: PerCylinder;   // injector discharge coefficient, per cylinder
  eta_v_scale: number;   // volumetric efficiency scale — ring wear
  eta_c_scale: number;   // compressor efficiency scale — turbo degradation
  hA_scale: number;      // cooling effectiveness scale
  f_fric_scale: number;  // friction scale — bearing wear
  damage: number;        // D in [0,1]; RUL = (1-D)/(dD/dt)
}

/** Bias added to the MEASUREMENT only. The engine does not know about this. */
interface SensorBias {
  cht_C: PerCylinder;
  egt_C: PerCylinder;
  map_hPa: number;
  lambda: number;
}

const elapsedMin = (t: number, f?: FaultSpec) =>
  f && t >= f.startT ? (t - f.startT) / 60 : 0;

// ---------------------------------------------------------------------------
// DETECTION STATE — carried sequentially through generateFrom's loop.
//
// Earlier versions of this file named a hypothesis and grew its confidence
// purely from ELAPSED TIME since the fault's scripted onset: `t >= startT +
// 22` revealed it, `0.45 + elapsed*k` was its confidence. That is scheduling,
// not detection — two runs with different noise would report the identical
// isolation instant to the second, which is exactly the tell that separates
// a scripted animation from a computed twin.
//
// Detection here is a real sequential test: each fault's diagnostic residual
// (already computed above with real noise in it) accumulates evidence only
// when it exceeds a noise floor, CUSUM-style. A hypothesis is REVEALED once
// its accumulator crosses a bar, and its confidence IS that accumulator
// (rescaled) — not a clock. Run the same fault twice with different noise
// seeds and the reveal instant and the confidence trajectory both differ,
// because they are now genuinely a function of the residual path realised.
// ---------------------------------------------------------------------------
interface DetectorState {
  /** rolling window of `anomalyScore > threshold` outcomes, for n-of-m persistence */
  anomalyWindow: boolean[];
  evidence: {
    injector: number; turbo: number; cooling: number; bearing: number;
    chtSensor: number; egtSensor: number;
  };
  /** accumulated DISTINGUISHING evidence (rho11) since the injector hypothesis
   *  was revealed — what resolves the injector-vs-EGT-sensor ambiguity */
  ambiguity: number;
}

function createDetector(): DetectorState {
  return {
    anomalyWindow: [],
    evidence: { injector: 0, turbo: 0, cooling: 0, bearing: 0, chtSensor: 0, egtSensor: 0 },
    ambiguity: 0,
  };
}

/** Sigma units below which a residual is noise, not signal — nothing accrues here. */
const EVIDENCE_FLOOR = 0.55;
/** Accumulated-evidence bar a hypothesis must clear to be named at all. */
const REVEAL_BAR = 9.0;
/** Accumulated-evidence bar that resolves the injector/EGT-sensor ambiguity. */
const RESOLVE_BAR = 7.0;
/** Confidence per unit of accumulated evidence past REVEAL_BAR. */
const CONF_SCALE = 0.018;

/** One CUSUM-style accumulator step: grows only while the signal clears the
 *  noise floor, never goes negative, so a fault that stops progressing stops
 *  accruing confidence rather than freezing it artificially high. */
function accrue(prev: number, signal: number): number {
  return Math.max(0, prev + (Math.abs(signal) - EVIDENCE_FLOOR));
}

function evidenceConfidence(evidence: number, cap: number): number {
  return Math.min(cap, 0.45 + Math.max(0, evidence - REVEAL_BAR) * CONF_SCALE);
}

function trueStateAt(t: number, cfg: FaultConfig = SCRIPTED): TrueState {
  const cd = new Array(N_CYL).fill(1.0);

  // Injector fouling: C_d falls on the affected cylinder.
  const inj = cfg.injector;
  if (inj) {
    const i = inj.cyl ?? FOULED_CYL;
    cd[i] = Math.max(0.4, 1.0 - inj.rate * elapsedMin(t, inj));
  }

  const etaC = Math.max(0.55, 1.0 - (cfg.turbo?.rate ?? 0) * elapsedMin(t, cfg.turbo));
  const hA = Math.max(0.5, 1.0 - (cfg.cooling?.rate ?? 0) * elapsedMin(t, cfg.cooling));
  const fFric = Math.min(2.2, 1.0 + (cfg.bearing?.rate ?? 0) * elapsedMin(t, cfg.bearing));

  // Damage accumulates faster once a component is degrading, because the
  // engine runs hotter and works harder — Arrhenius and Archard, in spirit.
  const worstCd = Math.min(...cd);
  const sev = Math.max(1 - worstCd, 1 - etaC, 1 - hA, fFric - 1);
  const damage = Math.min(1, 0.004 * (t / 60) + 0.9 * sev * sev);

  return {
    cd_inj: cd,
    eta_v_scale: 1.0,
    eta_c_scale: etaC,
    hA_scale: hA,
    f_fric_scale: fFric,
    damage,
  };
}

function sensorBiasAt(t: number, cfg: FaultConfig = SCRIPTED): SensorBias {
  const cht = new Array(N_CYL).fill(0);
  const egt = new Array(N_CYL).fill(0);

  // A pure MEASUREMENT bias ramp. THE ENGINE IS FINE. Nothing else in the
  // system moves, and that absence of corroboration is exactly how we catch it.
  if (cfg.chtSensor) {
    const i = cfg.chtSensor.cyl ?? DRIFT_CYL;
    cht[i] = cfg.chtSensor.rate * elapsedMin(t, cfg.chtSensor);
  }
  if (cfg.egtSensor) {
    const i = cfg.egtSensor.cyl ?? DRIFT_CYL;
    egt[i] = cfg.egtSensor.rate * elapsedMin(t, cfg.egtSensor);
  }

  return { cht_C: cht, egt_C: egt, map_hPa: 0, lambda: 0 };
}

// ---------------------------------------------------------------------------
// Mean-value engine model, reduced. Same structure as the real thing so the
// numbers move in the right directions for the right reasons.
// ---------------------------------------------------------------------------
const R_AIR = 287.05;      // gas constant for air — not engine-specific
const ETA_I = 0.40;        // indicated efficiency

interface Physics {
  map_hPa: number;
  iat_K: number;
  air_mass_flow: number;
  fuel_cmd_per_cyl: number;
  fuel_delivered_per_cyl: PerCylinder;
  fuel_flow_total: number;
  lambda: number;
  egt_C: PerCylinder;
  cht_C: PerCylinder;
  brake_power_kW: number;
  turbo_rpm: number;
  oil_press_bar: number;
  oil_temp_C: number;
  ripple: number;
}

function physicsAt(
  t: number, s: TrueState, cfg: FaultConfig = SCRIPTED, eng: EngineProfile = VRDE_180,
  alt?: AltitudePlan | null
): Physics {
  const CRUISE = cruiseOf(eng, t, alt);
  const { displacement_m3: V_D, Q_LHV, AFR_stoich: AFR_ST, etaV_nominal: ETA_V_NOM } = eng;
  const N_CYL_E = eng.cylinders;
  const atm = isa(CRUISE.altitude_ft, commonModeOffsetK(t, cfg));

  // Turbo holds boost below critical altitude; degrade eta_c and boost falls.
  const boostRatio = eng.boostRatio * s.eta_c_scale;
  const map_Pa = atm.p * boostRatio;
  const map_hPa = map_Pa / 100;

  // Charge air temperature after compression and intercooling.
  const iat_K = atm.T + 46 * s.eta_c_scale + 8;

  // Speed-density: the flow the cylinders actually ingest.
  const eta_v = ETA_V_NOM * s.eta_v_scale;
  const air_mass_flow =
    (eta_v * map_Pa * V_D * CRUISE.rpm) / (R_AIR * iat_K * 120);

  // FADEC commands a fuel quantity per cylinder to hold the power setting.
  const fuel_cmd_total = air_mass_flow / (AFR_ST * 1.42);
  const fuel_cmd_per_cyl = fuel_cmd_total / N_CYL_E;

  // A FOULED INJECTOR DELIVERS LESS THAN COMMANDED. Everything downstream
  // follows from this single line — we never write "EGT goes up".
  const fuel_delivered_per_cyl = s.cd_inj.map((cd) => cd * fuel_cmd_per_cyl);
  const fuel_flow_total = fuel_delivered_per_cyl.reduce((a, b) => a + b, 0);

  // Less fuel for the same air => the mixture leans => measured lambda RISES.
  const lambda = air_mass_flow / (AFR_ST * fuel_flow_total);

  // Per-cylinder exhaust temperature. A lean, incomplete burn puts more of
  // the energy out of the exhaust port instead of into the crankshaft, so a
  // fouled cylinder runs HOTTER even though it burns less fuel.
  const egt_C = fuel_delivered_per_cyl.map((f) => {
    const phi = (f * AFR_ST * N_CYL_E) / air_mass_flow; // local equivalence ratio
    const leanPenalty = Math.max(0, 1 - phi) * 340;
    // A weak cylinder burns late and incompletely: less of the released energy
    // reaches the crank, more of it leaves through the port.
    const foulPenalty = Math.max(0, 1 - f / fuel_cmd_per_cyl) * 900;
    return eng.nominalEgt_C + leanPenalty + foulPenalty - 40 * (1 - f / fuel_cmd_per_cyl);
  });

  // Cylinder head thermal state follows exhaust temperature with cooling.
  const cht_C = egt_C.map((e) => {
    const q = (e - (eng.nominalEgt_C - 20)) * 0.09;
    return eng.nominalCht_C + q / s.hA_scale;
  });

  // Crankshaft: a weak cylinder contributes less torque.
  const brake_power_kW =
    (ETA_I * fuel_flow_total * Q_LHV) / 1000 - 6.2 * s.f_fric_scale;

  // 0.5-ENGINE-ORDER RIPPLE. In a four-stroke each cylinder fires once per
  // 720 deg, so ANY single-cylinder imbalance shows at half engine order.
  // Zero for a balanced engine. This is what localises the fault.
  const mean = fuel_delivered_per_cyl.reduce((a, b) => a + b, 0) / N_CYL_E;
  const imbalance =
    Math.max(...fuel_delivered_per_cyl.map((f) => Math.abs(f - mean))) / mean;
  const ripple = 0.004 + imbalance * 0.62;

  const turbo_rpm = 118400 * s.eta_c_scale * (map_hPa / (atm.p / 100 * eng.boostRatio));
  const oil_press_bar = 3.42 / s.f_fric_scale;
  const oil_temp_C = 96.3 + (s.f_fric_scale - 1) * 40;

  return {
    map_hPa,
    iat_K,
    air_mass_flow,
    fuel_cmd_per_cyl,
    fuel_delivered_per_cyl,
    fuel_flow_total,
    lambda,
    egt_C,
    cht_C,
    brake_power_kW,
    turbo_rpm,
    oil_press_bar,
    oil_temp_C,
    ripple,
  };
}

// ---------------------------------------------------------------------------
// ENGINE B — the free reference channel.
//
// A MALE UAV of this class is twin-engined: two nominally identical engines,
// built to the same specification, drawing from the same tanks, flying the same
// profile through the same air, for eighteen hours. That is the best controlled
// experiment available in aviation and it costs nothing to use.
//
// B stays healthy for the whole mission, so every difference that survives the
// subtraction belongs to A.
// ---------------------------------------------------------------------------
function makeEngineB(
  t: number, noise: (s: number) => number, cfg: FaultConfig = SCRIPTED, eng: EngineProfile = VRDE_180,
  alt?: AltitudePlan | null
): SlowFrame {
  const CRUISE = cruiseOf(eng, t, alt);
  const healthy = trueStateAt(0, cfg);
  const phys = physicsAt(t, healthy, cfg, eng, alt);
  return {
    schema: 'pramana.slow.v1',
    t,
    engine_id: 'B',
    seq: Math.round(t * 10),
    rpm: CRUISE.rpm + noise(3),
    map_hPa: phys.map_hPa + noise(2.0),
    iat_K: phys.iat_K + noise(0.4),
    // Small fixed build-to-build biases, because two real engines are never
    // numerically identical. This is why the differential needs a baseline
    // rather than being assumed to sit at exactly zero.
    cht_C: phys.cht_C.map((v, i) => v + [0.6, -0.4, 0.3, -0.5][i] + noise(0.6)),
    egt_C: phys.egt_C.map((v, i) => v + [2.1, -1.4, 0.8, -1.5][i] + noise(2.2)),
    oil_press_bar: phys.oil_press_bar + 0.03 + noise(0.02),
    oil_temp_C: phys.oil_temp_C - 0.8 + noise(0.3),
    fuel_flow_kgps: phys.fuel_flow_total + noise(2e-6),
    lambda: phys.lambda + noise(0.006),
    turbo_rpm: phys.turbo_rpm + noise(220),
    comp_out_p_hPa: phys.map_hPa * 1.045 + noise(2),
    comp_out_T_K: phys.iat_K + 54 + noise(0.5),
    ...UNMODELLED,
    throttle_pct: CRUISE.throttle_pct,
    vib_rms_g: phys.egt_C.map(() => 0.42 + noise(0.01)),
    altitude_ft: CRUISE.altitude_ft,
    tas_mps: CRUISE.tas_mps,
    oat_K: isa(CRUISE.altitude_ft, commonModeOffsetK(t, cfg)).T,
    p_amb_hPa: isa(CRUISE.altitude_ft).p / 100,
    prop_rpm: CRUISE.rpm / eng.gearRatio + noise(1),
    blade_angle_deg: eng.cruiseBladeAngle_deg == null ? null : eng.cruiseBladeAngle_deg + noise(0.05),
    gearbox_oil_C: eng.nominalGearboxOil_C == null ? null : eng.nominalGearboxOil_C + noise(0.2),
    coolant_temp_C: eng.nominalCoolant_C == null ? null : eng.nominalCoolant_C + noise(0.15),
  };
}

// ---------------------------------------------------------------------------
// Generate one tick
// ---------------------------------------------------------------------------
function makeTick(
  t: number, noise: (s: number) => number, det: DetectorState, cfg: FaultConfig = SCRIPTED,
  eng: EngineProfile = VRDE_180, alt?: AltitudePlan | null
): MissionTick {
  const CRUISE = cruiseOf(eng, t, alt);
  const AFR_ST = eng.AFR_stoich;
  const N_CYL_E = eng.cylinders;
  const trueS = trueStateAt(t, cfg);
  const bias = sensorBiasAt(t, cfg);
  const phys = physicsAt(t, trueS, cfg, eng, alt);

  // The TWIN's prediction assumes a HEALTHY engine — nominal parameters. It
  // gets the SAME commanded altitude, which is the whole point: the operating
  // point is shared, so it cancels in the residual and only the health
  // difference survives.
  const nominal = trueStateAt(0, cfg);
  const predicted = physicsAt(t, nominal, cfg, eng, alt);

  // ---- measured values = physics + sensor bias + noise ----
  const egt_meas = phys.egt_C.map((v, i) => v + bias.egt_C[i] + noise(2.2));
  const cht_meas = phys.cht_C.map((v, i) => v + bias.cht_C[i] + noise(0.6));
  const map_meas = phys.map_hPa + bias.map_hPa + noise(2.0);
  const lambda_meas = phys.lambda + bias.lambda + noise(0.006);

  const slow: SlowFrame = {
    schema: 'pramana.slow.v1',
    t,
    engine_id: 'A',
    seq: Math.round(t * 10),
    rpm: CRUISE.rpm + noise(3),
    map_hPa: map_meas,
    iat_K: phys.iat_K + noise(0.4),
    cht_C: cht_meas,
    egt_C: egt_meas,
    oil_press_bar: phys.oil_press_bar + noise(0.02),
    oil_temp_C: phys.oil_temp_C + noise(0.3),
    fuel_flow_kgps: phys.fuel_flow_total + noise(2e-6),
    lambda: lambda_meas,
    turbo_rpm: phys.turbo_rpm + noise(220),
    comp_out_p_hPa: phys.map_hPa * 1.045 + noise(2),
    comp_out_T_K: phys.iat_K + 54 + noise(0.5),
    ...UNMODELLED,
    throttle_pct: CRUISE.throttle_pct,
    vib_rms_g: phys.egt_C.map((_, i) =>
      0.42 + (i === (cfg.injector?.cyl ?? FOULED_CYL) ? phys.ripple * 0.35 : 0) + noise(0.01)
    ),
    altitude_ft: CRUISE.altitude_ft,
    tas_mps: CRUISE.tas_mps,
    oat_K: isa(CRUISE.altitude_ft, commonModeOffsetK(t, cfg)).T,
    p_amb_hPa: isa(CRUISE.altitude_ft).p / 100,
    prop_rpm: CRUISE.rpm / eng.gearRatio + noise(1),
    blade_angle_deg: eng.cruiseBladeAngle_deg == null ? null : eng.cruiseBladeAngle_deg + noise(0.05),
    gearbox_oil_C: eng.nominalGearboxOil_C == null ? null : eng.nominalGearboxOil_C + noise(0.2),
    coolant_temp_C: eng.nominalCoolant_C == null ? null : eng.nominalCoolant_C + noise(0.15),
  };

  const fast: FastFeatures = {
    schema: 'pramana.fastfeat.v1',
    t,
    engine_id: 'A',
    order_0p5_mag: phys.ripple + noise(0.002),
    order_0p5_phase_deg: 90 + (cfg.injector?.cyl ?? FOULED_CYL) * 90,
    order_1p0_mag: 0.118 + noise(0.004),
    order_2p0_mag: 0.077 + noise(0.003),
    knock_intensity: new Array(N_CYL).fill(0).map(() => 0.02 + noise(0.002)),
    vib_band_rms: {
      lo_0_500: 0.31 + noise(0.01),
      mid_500_5k: 0.44 + noise(0.01),
      hi_5k_20k: 0.12 + noise(0.005),
    },
  };

  // ------------------------------------------------------------------
  // PARITY RESIDUALS — measured minus twin-predicted, normalised to sigmas.
  // Nominally zero, and INVARIANT TO OPERATING POINT by construction.
  // ------------------------------------------------------------------

  // Air path. Path 1 (speed-density) uses MAP; Path 3 (fuel/lambda) uses the
  // COMMANDED fuel. When an injector under-delivers, the mixture leans, the
  // measured lambda rises, and Path 3 over-reads relative to Path 1.
  const mdot_sd = phys.air_mass_flow;
  const mdot_comp = predicted.air_mass_flow * (1 + noise(0.001));
  const mdot_lambda = lambda_meas * AFR_ST * (phys.fuel_cmd_per_cyl * N_CYL_E);

  const rho1 = ((mdot_sd - mdot_comp) / predicted.air_mass_flow) * 340 + noise(0.25);
  const rho2 = ((mdot_sd - mdot_lambda) / predicted.air_mass_flow) * 340 + noise(0.25);

  // Energy closure: sensitive to almost every fault, so a good DETECTOR and a
  // poor ISOLATOR on its own.
  const energyGap =
    (phys.fuel_flow_total - predicted.fuel_flow_total) / predicted.fuel_flow_total;
  const chtGap =
    (cht_meas.reduce((a, b) => a + b, 0) - predicted.cht_C.reduce((a, b) => a + b, 0)) /
    N_CYL;
  const rho4 = -energyGap * 190 + chtGap * 0.42 + noise(0.3);

  // Power closure via the propeller. Friction faults live here.
  const rho5 =
    ((phys.brake_power_kW - predicted.brake_power_kW) / predicted.brake_power_kW) * 45 +
    noise(0.28);

  // Per-cylinder thermal deviation from the CONDITIONAL MEAN across cylinders.
  // Conditional, not absolute — that is what makes it operating-point invariant,
  // and it is why a drifting sensor shows up here with nothing to corroborate it.
  const chtMean = cht_meas.reduce((a, b) => a + b, 0) / N_CYL_E;
  const egtMean = egt_meas.reduce((a, b) => a + b, 0) / N_CYL_E;
  const rho6_9 = cht_meas.map((c, i) => {
    const dCht = (c - chtMean) / 0.9;
    const dEgt = (egt_meas[i] - egtMean) / 6.0;
    return 0.45 * dCht + 0.55 * dEgt + noise(0.2);
  });

  const rho10 =
    ((phys.oil_press_bar - predicted.oil_press_bar) / predicted.oil_press_bar) * 40 +
    noise(0.25);

  let rho11 = (phys.ripple - 0.004) / 0.0125 + noise(0.22);

  // ---- unmodelled fault: excitation the library provably cannot explain ----
  const unmodelled = cfg.unmodelled && t >= cfg.unmodelled.startT
    ? (t - cfg.unmodelled.startT) * cfg.unmodelled.rate
    : 0;
  const rhoVec = [rho1, rho2, null, rho4, rho5, ...rho6_9, rho10, rho11];
  if (unmodelled > 0) {
    for (let i = 0; i < rhoVec.length; i++) {
      if (rhoVec[i] !== null) rhoVec[i] = (rhoVec[i] as number) + NULL_DIR[i] * unmodelled;
    }
    rho11 = rhoVec[10] as number;
  }
  const nov = computeNovelty(rhoVec);
  const novThreshold = 0.42;

  // SIGNIFICANCE WEIGHTING — nu alone is not interpretable.
  //
  // At rest the residual is pure noise, and isotropic noise puts sqrt(3/11)
  // ~= 0.52 of itself in the null space BY CONSTRUCTION. So a healthy engine
  // shows a high nu with nothing wrong, and an unweighted confidence channel
  // would sit at ~50% permanently — worse than useless, because it would be
  // ignored exactly when it mattered.
  //
  // nu only carries information once there is a residual worth explaining.
  // Below the noise floor there is nothing to explain, so confidence is full.
  const RHO_NOISE_FLOOR = 2.0;   // sigma units; calibrate on healthy data
  const RHO_FULL_WEIGHT = 4.5;
  const significance = Math.min(
    1,
    Math.max(0, (nov.residualNorm - RHO_NOISE_FLOOR) / (RHO_FULL_WEIGHT - RHO_NOISE_FLOOR))
  );
  const novExceeded = nov.index > novThreshold && significance > 0.5;

  // ------------------------------------------------------------------
  // Diagnosis
  // ------------------------------------------------------------------
  const anomalyRaw = Math.sqrt(
    rho1 ** 2 + rho2 ** 2 + rho4 ** 2 + rho5 ** 2 +
    rho6_9.reduce((a, b) => a + b * b, 0) + rho10 ** 2 + rho11 ** 2
  ) / 11;

  const threshold = 0.31;
  const anomalyScore = Math.min(1, anomalyRaw);

  // n-of-m persistence on the generic anomaly flag — a real rolling window,
  // not "instantaneously true this frame therefore report 5-of-5 forever".
  det.anomalyWindow.push(anomalyScore > threshold);
  if (det.anomalyWindow.length > 5) det.anomalyWindow.shift();
  const persistN = det.anomalyWindow.filter(Boolean).length;

  // ------------------------------------------------------------------
  // Build the hypothesis list from whatever is ACTUALLY configured, so the
  // interactive sandbox and the rehearsed script share one code path. There is
  // no "demo mode" that behaves differently from the thing being demonstrated.
  //
  // Each fault's diagnostic residual accumulates evidence in `det` (CUSUM-
  // style, see the comment by DetectorState above); a hypothesis is named only
  // once its OWN accumulator clears REVEAL_BAR, and its confidence tracks that
  // accumulator, not the clock. Two runs with different noise reveal at
  // different instants and grow at different rates — this is a measured
  // outcome, not a scheduled one.
  // ------------------------------------------------------------------
  const hyps: FaultHypothesis[] = [];
  let sensorLed = false;
  let anyAmbiguous = false;
  let probeCyl: number | null = null;

  // INSTRUMENTATION faults first when present: a transducer perturbs only the
  // relations that contain it, so once detected it is the cleanest call we make.
  // Evidence channel: rho6_9[cyl] carries both the CHT and EGT bias terms.
  if (cfg.chtSensor && t >= cfg.chtSensor.startT) {
    const cyl = cfg.chtSensor.cyl ?? DRIFT_CYL;
    det.evidence.chtSensor = accrue(det.evidence.chtSensor, rho6_9[cyl]);
    if (det.evidence.chtSensor > REVEAL_BAR && persistN >= 4) {
      hyps.push({
        fault: 'cht_sensor_drift', cylinder: cyl,
        p: evidenceConfidence(det.evidence.chtSensor, 0.94), source: 'classifier+matrix',
      });
      sensorLed = true;
    }
  }
  if (cfg.egtSensor && t >= cfg.egtSensor.startT) {
    const cyl = cfg.egtSensor.cyl ?? DRIFT_CYL;
    det.evidence.egtSensor = accrue(det.evidence.egtSensor, rho6_9[cyl]);
    if (det.evidence.egtSensor > REVEAL_BAR && persistN >= 4) {
      hyps.push({
        fault: 'egt_sensor_drift', cylinder: cyl,
        p: evidenceConfidence(det.evidence.egtSensor, 0.94), source: 'classifier+matrix',
      });
      sensorLed = true;
    }
  }

  // COMPONENT faults. Evidence channel: rho2 (fuel/lambda path — an injector
  // that under-delivers leans the mixture, which this path is built to catch).
  if (cfg.injector && t >= cfg.injector.startT) {
    const cyl = cfg.injector.cyl ?? FOULED_CYL;
    det.evidence.injector = accrue(det.evidence.injector, rho2);
    const revealed = det.evidence.injector > REVEAL_BAR && persistN >= 4;
    if (revealed) {
      hyps.push({
        fault: 'injector_fouling', cylinder: cyl,
        p: evidenceConfidence(det.evidence.injector, 0.93), source: 'classifier+matrix',
      });
      // Early on, injector fouling and an EGT transducer drift on the SAME
      // cylinder are not structurally separable — they differ only through
      // rho11 (crank ripple: a real mechanical irregularity a measurement
      // bias cannot produce). Ambiguous until THAT evidence, specifically,
      // has accumulated enough to resolve it — not after a fixed duration.
      det.ambiguity = accrue(det.ambiguity, rho11);
      const ambiguous = det.ambiguity < RESOLVE_BAR;
      if (ambiguous) {
        hyps.push({ fault: 'egt_sensor_drift', cylinder: cyl, p: 0.22, source: 'matrix' });
        anyAmbiguous = true;
        probeCyl = cyl;
      }
    }
  }
  if (cfg.turbo && t >= cfg.turbo.startT) {
    det.evidence.turbo = accrue(det.evidence.turbo, rho1);
    if (det.evidence.turbo > REVEAL_BAR && persistN >= 4) {
      hyps.push({ fault: 'turbo_degradation', cylinder: null, p: evidenceConfidence(det.evidence.turbo, 0.94), source: 'classifier+matrix' });
    }
  }
  if (cfg.cooling && t >= cfg.cooling.startT) {
    det.evidence.cooling = accrue(det.evidence.cooling, rho4);
    if (det.evidence.cooling > REVEAL_BAR && persistN >= 4) {
      hyps.push({ fault: 'cooling_fouling', cylinder: cfg.cooling.cyl ?? null, p: evidenceConfidence(det.evidence.cooling, 0.94), source: 'classifier+matrix' });
    }
  }
  if (cfg.bearing && t >= cfg.bearing.startT) {
    det.evidence.bearing = accrue(det.evidence.bearing, rho10);
    if (det.evidence.bearing > REVEAL_BAR && persistN >= 4) {
      hyps.push({ fault: 'bearing_wear', cylinder: null, p: evidenceConfidence(det.evidence.bearing, 0.94), source: 'classifier+matrix' });
    }
  }

  hyps.sort((a, b) => b.p - a.p);

  // `let`, not `const`: the novelty block below reassigns this when the
  // residual cannot be explained by the fault library.
  let diagnosis: HealthFrame['diagnosis'] = hyps.length
    ? {
        top: hyps.slice(0, 4),
        // Sensor-led only when the LEADING hypothesis is instrumentation.
        // With both an engine fault and a sensor fault present, whichever is
        // more confident leads — and the panel still lists the other.
        is_sensor_fault: sensorLed && hyps[0].fault.includes('sensor'),
        ambiguous: anyAmbiguous,
        probe:
          anyAmbiguous && probeCyl !== null
            ? {
                running: true,
                cylinder: probeCyl,
                amplitude_pct: 4.0,
                elapsed_s: t - (cfg.injector?.startT ?? t),
                // Gain is ATTENUATED under the injector hypothesis; a sensor's
                // constant bias cancels identically from the alternating part.
                gain_estimate: 0.62,
                // The real accumulated rho11 evidence, not a clock — this is
                // exactly what RESOLVE_BAR above is compared against.
                log_likelihood_ratio: det.ambiguity,
                decision: 'pending',
              }
            : null,
      }
    : {
        top: [{ fault: 'healthy', cylinder: null, p: 0.98, source: 'classifier+matrix' }],
        is_sensor_fault: false,
        ambiguous: false,
        probe: null,
      };

  // A high novelty index does not erase what we already identified — it says
  // there is something ELSE as well. Report both: the known fault keeps its
  // confidence, and the unexplained component is surfaced above it rather than
  // silently replacing it.
  if (novExceeded) {
    diagnosis = {
      top: [
        { fault: 'unknown', cylinder: null, p: nov.index, source: 'matrix' },
        ...diagnosis.top.filter((h) => h.fault !== 'healthy'),
      ],
      is_sensor_fault: false,
      ambiguous: true,
      probe: null,
    };
  }

  // ------------------------------------------------------------------
  // RUL — two heads, always both, advise on the conservative one.
  // ------------------------------------------------------------------
  const dD = Math.max(1e-6, (trueStateAt(t + 1, cfg).damage - trueS.damage));
  const physics_h = Math.min(48, ((1 - trueS.damage) / dD) / 3600);
  const network_h = physics_h * 0.9;

  // Gated on the fault being ACTIVE (t >= startT), not merely CONFIGURED —
  // `cfg.injector` exists in the scenario object for the whole mission, so
  // checking only its presence named a specific degrading component (and
  // started its countdown) from t=0, minutes before the fault the scenario
  // itself says begins at 40s. During that window nothing is actually
  // degrading and the panel must say so.
  const rul: HealthFrame['rul'] = {
    component: cfg.injector && t >= cfg.injector.startT
      ? `injector_cyl${(cfg.injector.cyl ?? FOULED_CYL) + 1}`
      : cfg.turbo && t >= cfg.turbo.startT ? 'turbocharger'
      : cfg.cooling && t >= cfg.cooling.startT ? 'cooling_system'
      : cfg.bearing && t >= cfg.bearing.startT ? 'main_bearing'
      : 'none',
    physics_h,
    network_h,
    p10_h: network_h * 0.65,
    p50_h: network_h,
    p90_h: physics_h * 1.05,
    reported_h: Math.min(physics_h, network_h),
    heads_disagree: Math.abs(physics_h - network_h) > physics_h * 0.4,
  };

  // ------------------------------------------------------------------
  // Mission decision. PS asks for reliability ENHANCEMENT, not monitoring.
  // ------------------------------------------------------------------
  const severity = Math.max(
    1 - Math.min(...trueS.cd_inj),
    1 - trueS.eta_c_scale,
    1 - trueS.hA_scale,
    trueS.f_fric_scale - 1,
    0
  );
  const p_continue = Math.max(0.35, 0.99 - severity * 1.5);
  const p_derate = Math.max(0.7, 0.995 - severity * 0.35);

  const mission: HealthFrame['mission'] = {
    p_complete_continue: p_continue,
    p_complete_derate: p_derate,
    p_complete_rtb: 0.99,
    derate_cost_min_on_station: 40,
    recommended: p_continue < 0.85 ? 'derate' : 'continue',
    recommended_power_pct: p_continue < 0.85 ? 78 : 100,
    recommended_boost_hPa: p_continue < 0.85 ? 1120 : 1187,
    point_of_no_return_s: 9240 - t * 6,
  };

  const health: HealthFrame = {
    schema: 'pramana.health.v1',
    t,
    engine_id: 'A',
    rho: {
      rho1_sd_vs_comp: rhoVec[0] as number,
      rho2_sd_vs_lambda: rhoVec[1] as number,
      // Path 4 exists only where there is a metering restriction. On an
      // unthrottled FADEC aero-diesel there is none, so we report NULL — never
      // a fabricated number. On a throttled engine the path is live and the
      // framework gains a third independent air-path residual.
      rho3_sd_vs_restr: eng.parityPaths.intakeRestriction
        ? ((mdot_sd - predicted.air_mass_flow * (1 + noise(0.0015))) /
            predicted.air_mass_flow) * 340 + noise(0.3)
        : null,
      rho4_energy: rhoVec[3] as number,
      rho5_power: rhoVec[4] as number,
      rho6_9_cyl_dev: rhoVec.slice(5, 9) as number[],
      rho10_oil: rhoVec[9] as number,
      rho11_ripple: rhoVec[10] as number,
    },
    novelty: {
      index: nov.index,
      residual_norm: nov.residualNorm,
      unexplained_norm: nov.unexplainedNorm,
      threshold: novThreshold,
      exceeded: novExceeded,
      effective_rank: nov.effectiveRank,
      null_space_dim: nov.nullSpaceDim,
      unexplained: nov.unexplained,
    },
    twin_confidence: {
      value: Math.max(0, Math.min(1, 1 - nov.index * significance)),
      basis: 'residual_projection',
      note: novExceeded
        ? 'excitation pattern is not in the fault library'
        : 'residual is well explained by known fault directions',
    },
    theta: {
      eta_v_scale: { value: 1.0 + noise(0.004), sigma: 0.011 },
      eta_c_scale: { value: 1.0 + noise(0.006), sigma: 0.019 },
      hA_scale: { value: 1.0 + noise(0.008), sigma: 0.024 },
      cd_inj: {
        // The UKF recovers the TRUE injector coefficient — and this is the
        // health report: a physical quantity with a covariance, not a score.
        value: trueS.cd_inj.map((c) => c + noise(0.006)),
        sigma: [0.02, 0.03, 0.02, 0.02],
      },
      f_fric_scale: { value: 1.0 + noise(0.005), sigma: 0.015 },
    },
    virtual: {
      peak_cyl_press_bar: phys.egt_C.map((e) => 141 - (e - 720) * 0.06 + noise(0.4)),
      knock_margin_deg: 6.4 + noise(0.1),
      turbo_shaft_rpm_est: phys.turbo_rpm,
      comb_efficiency: trueS.cd_inj.map((c) => 0.982 * c + 0.018 * c * c),
      air_mass_flow_kgps: phys.air_mass_flow,
      brake_power_kW: phys.brake_power_kW,
      bsfc_g_per_kWh: (phys.fuel_flow_total * 3.6e6) / phys.brake_power_kW,
    },
    anomaly: {
      score: anomalyScore,
      threshold,
      persistence: {
        // A genuine rolling count of the last 5 frames, not "instantaneously
        // true this frame, therefore claim 5-of-5 forever" — met only once
        // the window itself has actually accumulated 4 of its 5 slots.
        n: persistN,
        of: 5,
        met: persistN >= 4,
      },
    },
    diagnosis,
    rul,
    mission,
    // What a THRESHOLD system would be showing. Stays green through the
    // entire injector event — which is the argument, in one field.
    limits_state:
      Math.max(...cht_meas) > 200 ? 'exceeded'
        : Math.max(...cht_meas) > 180 ? 'caution'
        : 'green',
  };

  return {
    slow,
    slowB: makeEngineB(t, noise, cfg, eng, alt),
    fast,
    health,
    predicted: {
      egt_C: predicted.egt_C,
      cht_C: predicted.cht_C,
      oil_press_bar: predicted.oil_press_bar,
      map_hPa: predicted.map_hPa,
      fuel_flow_kgps: predicted.fuel_flow_total,
    },
  };
}

// ---------------------------------------------------------------------------
// Generate the whole mission once. NEVER generate during a demo.
// ---------------------------------------------------------------------------
let cached: MissionTick[] | null = null;

/**
 * Generate a mission from an ARBITRARY fault configuration.
 *
 * This is what the interactive console calls. It runs the same physics, the
 * same residual generator and the same diagnosis code as the rehearsed script —
 * only the configuration differs. Whatever a judge injects therefore goes
 * through exactly the path being demonstrated, which is the point: there is no
 * separate "sandbox mode" whose behaviour could diverge from the real thing.
 */
export function generateFrom(
  cfg: FaultConfig, eng: EngineProfile = VRDE_180, seed = 0x5148,
  alt?: AltitudePlan | null
): MissionTick[] {
  const noise = makeNoise(seed);
  const det = createDetector();
  const ticks: MissionTick[] = [];
  for (let t = 0; t <= MISSION_DURATION_S; t += 1 / HEALTH_HZ) {
    ticks.push(makeTick(t, noise, det, cfg, eng, alt));
  }
  return ticks;
}

/**
 * A fresh seed for a genuinely new run — NOT the reused-seed case (mid-
 * mission altitude change, fault injection continuing forward from the
 * frame on screen), where the existing seed must be kept so the already-
 * displayed past reproduces exactly. See callers in state/missionStore.ts.
 */
export function freshSeed(): number {
  return ((Date.now() ^ Math.floor(Math.random() * 0x7fffffff)) & 0x7fffffff) || 1;
}

export function generateMission(): MissionTick[] {
  if (cached) return cached;
  cached = generateFrom(SCRIPTED, VRDE_180, freshSeed());
  return cached;
}

/**
 * The demo beat bar, computed from the ACTUAL generated mission rather than
 * a hand-picked list of seconds.
 *
 * Two earlier problems this fixes at once:
 *
 *   1. Detection instants ("anomaly score crosses threshold", "isolation:
 *      injector fouling") were hardcoded seconds that the diagnosis logic
 *      didn't even check against — they were true by construction of the
 *      elapsed-time gates that used to drive hypothesis reveal, decoupled
 *      from the anomalyScore/threshold pair the label claims to describe.
 *      Now the diagnosis is genuinely evidence-driven (see DetectorState
 *      above), so its timing can only be discovered by scanning the frames
 *      it actually produced — which is what this does.
 *   2. Because detection now depends on the realised noise path, the exact
 *      instant varies run to run. A label frozen at "1:35" would silently
 *      go stale — sometimes pointing at the right beat, sometimes not.
 *      Reading the time off the mission that is actually on screen means
 *      the beat bar is never wrong about the run it is describing.
 *
 * SCENARIO events (a fault's own onset, the environmental warm-air-mass
 * ramp) are NOT scanned for — they are authored facts about what happens,
 * pulled from the same constants that drive the physics, never duplicated
 * as a second literal that could drift out of sync with it.
 */
export interface ScriptBeat { t: number; label: string }

export function computeScriptBeats(ticks: MissionTick[]): ScriptBeat[] {
  const find = (pred: (tick: MissionTick) => boolean, after = 0): number | null => {
    const hit = ticks.find((tk) => tk.slow.t >= after && pred(tk));
    return hit ? hit.slow.t : null;
  };

  const beats: { t: number; label: string }[] = [{ t: 0, label: 'Healthy cruise' }];
  const push = (t: number | null, label: string) => { if (t !== null) beats.push({ t, label }); };

  // Scenario-authored — WHEN a fault begins is a scripted fact, not a result.
  push(T_INJECTOR_START, `Injector fouling begins — cylinder ${FOULED_CYL + 1}`);
  push(T_WARM_AIR_START, 'Warm air mass — BOTH engines rise, differential does not');
  push(T_SENSOR_DRIFT_START, `CHT sensor ${DRIFT_CYL + 1} begins drifting — ENGINE IS HEALTHY`);

  // Emergent — WHEN the twin actually calls it, read off the frames it
  // produced. Each is searched only after its fault can possibly have begun,
  // so an unrelated coincidental crossing earlier in the mission can't be
  // mistaken for this event.
  const anomalyT = find((tk) => tk.health.anomaly.persistence.met, T_INJECTOR_START);
  push(anomalyT, 'Anomaly score crosses threshold');

  const isolationT = find(
    (tk) => tk.health.diagnosis.top[0]?.fault === 'injector_fouling',
    T_INJECTOR_START
  );
  push(isolationT, `Isolation: injector fouling, cyl ${FOULED_CYL + 1}`);
  // The RUL panel's number is only about a NAMED component, so its "worth
  // opening the panel" instant is the same instant isolation happened.
  push(isolationT, 'RUL with uncertainty band');

  const decisionT = find((tk) => tk.health.mission.recommended !== 'continue', T_INJECTOR_START);
  push(decisionT, 'Mission decision: continue / derate / RTB');

  const sensorT = find(
    (tk) => tk.health.diagnosis.top[0]?.fault === 'cht_sensor_drift' && tk.health.diagnosis.is_sensor_fault,
    T_SENSOR_DRIFT_START
  );
  push(sensorT, 'Sensor fault correctly identified');

  const unmodelledT = find((tk) => tk.health.diagnosis.top[0]?.fault === 'unknown', T_UNMODELLED_START);
  push(unmodelledT, 'UNMODELLED fault — the twin says "I do not know"');

  // Strictly chronological: the "which beat is active" logic compares
  // against the NEXT entry, so an out-of-order array silently highlights the
  // wrong chip. Two emergent events landing in the same second is possible
  // (rare) and sorts stably either way.
  return beats.sort((a, b) => a.t - b.t);
}

export const FOULED_CYLINDER = FOULED_CYL;
export const DRIFTING_CYLINDER = DRIFT_CYL;
export const FAULT_TIMES = { injector: T_INJECTOR_START, sensor: T_SENSOR_DRIFT_START };
export type { FaultId };
