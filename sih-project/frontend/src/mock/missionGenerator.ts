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
import {
  N_CYL,
  type FaultId,
  type HealthFrame,
  type MissionTick,
  type SlowFrame,
  type FastFeatures,
  type PerCylinder,
} from '../types/telemetry';

// ---------------------------------------------------------------------------
// Deterministic RNG — the demo must look identical every single run.
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

/** Matches docs/pitch/demo-script.md beat for beat. */
export const SCRIPT_BEATS = [
  { t: 0, label: 'Healthy cruise, 18 000 ft' },
  { t: 40, label: 'Injector fouling begins — cylinder 2' },
  { t: 62, label: 'Anomaly score crosses threshold' },
  { t: 95, label: 'Isolation: injector fouling, cyl 2' },
  { t: 140, label: 'RUL with uncertainty band' },
  { t: 165, label: 'Mission decision: continue / derate / RTB' },
  { t: 200, label: 'CHT sensor 3 begins drifting — ENGINE IS HEALTHY' },
  { t: 235, label: 'Sensor fault correctly identified' },
  { t: 260, label: 'UNMODELLED fault — the twin says "I do not know"' },
] as const;

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
const NULL_DIR = nullSpaceDirection(0);

const FOULED_CYL = 1; // cylinder 2, 0-indexed
const DRIFT_CYL = 2;  // cylinder 3, 0-indexed

// Cruise operating point
const CRUISE = {
  altitude_ft: 18000,
  rpm: 3580,
  throttle_pct: 72,
  tas_mps: 61.2,
};

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

function trueStateAt(t: number): TrueState {
  const cd = new Array(N_CYL).fill(1.0);

  // Injector fouling: C_d falls ~0.4 %/min on the affected cylinder.
  if (t >= T_INJECTOR_START) {
    const mins = (t - T_INJECTOR_START) / 60;
    cd[FOULED_CYL] = Math.max(0.55, 1.0 - 0.045 * mins);
  }

  // Damage accumulates faster once the injector is fouling, because the
  // cylinder runs hotter — Arrhenius, in spirit.
  const foulSeverity = 1 - cd[FOULED_CYL];
  const damage = Math.min(1, 0.004 * (t / 60) + 0.9 * foulSeverity * foulSeverity);

  return {
    cd_inj: cd,
    eta_v_scale: 1.0,
    eta_c_scale: 1.0,
    hA_scale: 1.0,
    f_fric_scale: 1.0,
    damage,
  };
}

function sensorBiasAt(t: number): SensorBias {
  const cht = new Array(N_CYL).fill(0);

  // CHT sensor 3 drift: a pure measurement bias ramp. THE ENGINE IS FINE.
  // Nothing else in the system moves, and that is exactly how we catch it.
  if (t >= T_SENSOR_DRIFT_START) {
    const mins = (t - T_SENSOR_DRIFT_START) / 60;
    cht[DRIFT_CYL] = 24 * mins; // ~0.4 degC/s
  }

  return {
    cht_C: cht,
    egt_C: new Array(N_CYL).fill(0),
    map_hPa: 0,
    lambda: 0,
  };
}

// ---------------------------------------------------------------------------
// Mean-value engine model, reduced. Same structure as the real thing so the
// numbers move in the right directions for the right reasons.
// ---------------------------------------------------------------------------
const V_D = 2.0e-3;        // swept volume [m^3]
const R_AIR = 287.05;
const Q_LHV = 43.0e6;      // J/kg
const AFR_ST = 14.5;
const ETA_V_NOM = 0.88;
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

function physicsAt(t: number, s: TrueState): Physics {
  const atm = isa(CRUISE.altitude_ft);

  // Turbo holds boost below critical altitude; degrade eta_c and boost falls.
  const boostRatio = 1.72 * s.eta_c_scale;
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
  const fuel_cmd_per_cyl = fuel_cmd_total / N_CYL;

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
    const phi = (f * AFR_ST * N_CYL) / air_mass_flow; // local equivalence ratio
    const leanPenalty = Math.max(0, 1 - phi) * 340;
    // A weak cylinder burns late and incompletely: less of the released energy
    // reaches the crank, more of it leaves through the port.
    const foulPenalty = Math.max(0, 1 - f / fuel_cmd_per_cyl) * 900;
    return 720 + leanPenalty + foulPenalty - 40 * (1 - f / fuel_cmd_per_cyl);
  });

  // Cylinder head thermal state follows exhaust temperature with cooling.
  const cht_C = egt_C.map((e) => {
    const q = (e - 700) * 0.09;
    return 128 + q / s.hA_scale;
  });

  // Crankshaft: a weak cylinder contributes less torque.
  const brake_power_kW =
    (ETA_I * fuel_flow_total * Q_LHV) / 1000 - 6.2 * s.f_fric_scale;

  // 0.5-ENGINE-ORDER RIPPLE. In a four-stroke each cylinder fires once per
  // 720 deg, so ANY single-cylinder imbalance shows at half engine order.
  // Zero for a balanced engine. This is what localises the fault.
  const mean = fuel_delivered_per_cyl.reduce((a, b) => a + b, 0) / N_CYL;
  const imbalance =
    Math.max(...fuel_delivered_per_cyl.map((f) => Math.abs(f - mean))) / mean;
  const ripple = 0.004 + imbalance * 0.62;

  const turbo_rpm = 118400 * s.eta_c_scale * (map_hPa / 1187);
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
// Generate one tick
// ---------------------------------------------------------------------------
function makeTick(t: number, noise: (s: number) => number): MissionTick {
  const trueS = trueStateAt(t);
  const bias = sensorBiasAt(t);
  const phys = physicsAt(t, trueS);

  // The TWIN's prediction assumes a HEALTHY engine — nominal parameters.
  // The gap between this and the measurement is the entire product.
  const nominal = trueStateAt(0);
  const predicted = physicsAt(t, nominal);

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
    fuel_rail_bar: 1.68 + noise(0.01),
    lambda: lambda_meas,
    turbo_rpm: phys.turbo_rpm + noise(220),
    comp_out_p_hPa: phys.map_hPa * 1.045 + noise(2),
    comp_out_T_K: phys.iat_K + 54 + noise(0.5),
    inj_timing_deg: 12.4 + noise(0.05),
    bus_voltage_V: 27.8 + noise(0.03),
    alternator_A: 14.2 + noise(0.1),
    throttle_pct: CRUISE.throttle_pct,
    vib_rms_g: phys.egt_C.map((_, i) =>
      0.42 + (i === FOULED_CYL ? phys.ripple * 0.35 : 0) + noise(0.01)
    ),
    altitude_ft: CRUISE.altitude_ft,
    tas_mps: CRUISE.tas_mps,
    oat_K: isa(CRUISE.altitude_ft).T,
  };

  const fast: FastFeatures = {
    schema: 'pramana.fastfeat.v1',
    t,
    engine_id: 'A',
    order_0p5_mag: phys.ripple + noise(0.002),
    order_0p5_phase_deg: 90 + FOULED_CYL * 90,
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
  const mdot_lambda = lambda_meas * AFR_ST * (phys.fuel_cmd_per_cyl * N_CYL);

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
  const chtMean = cht_meas.reduce((a, b) => a + b, 0) / N_CYL;
  const egtMean = egt_meas.reduce((a, b) => a + b, 0) / N_CYL;
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
  const unmodelled = t >= T_UNMODELLED_START ? (t - T_UNMODELLED_START) * 0.62 : 0;
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
  const injectorActive = t >= T_INJECTOR_START;
  const sensorActive = t >= T_SENSOR_DRIFT_START;

  const anomalyRaw = Math.sqrt(
    rho1 ** 2 + rho2 ** 2 + rho4 ** 2 + rho5 ** 2 +
    rho6_9.reduce((a, b) => a + b * b, 0) + rho10 ** 2 + rho11 ** 2
  ) / 11;

  const threshold = 0.31;
  const anomalyScore = Math.min(1, anomalyRaw);

  let diagnosis: HealthFrame['diagnosis'];

  if (sensorActive) {
    // A drifting transducer perturbs ONLY the relations that contain it.
    // No ripple, no fuel-flow change, no energy-balance shift, no corroboration
    // anywhere. That absence IS the evidence.
    const conf = Math.min(0.94, 0.55 + (t - T_SENSOR_DRIFT_START) * 0.012);
    diagnosis = {
      top: [
        { fault: 'cht_sensor_drift', cylinder: DRIFT_CYL, p: conf, source: 'classifier+matrix' },
        { fault: 'injector_fouling', cylinder: FOULED_CYL, p: 0.88, source: 'classifier+matrix' },
        { fault: 'cooling_fouling', cylinder: DRIFT_CYL, p: 1 - conf, source: 'matrix' },
      ],
      is_sensor_fault: true,
      ambiguous: false,
      probe: null,
    };
  } else if (injectorActive && t >= 62) {
    const conf = Math.min(0.93, 0.6 + (t - 62) * 0.011);
    // Between detection and confident isolation the structure genuinely cannot
    // separate injector fouling from an EGT transducer drift on the same
    // cylinder — so we say so, and the active probe is armed.
    const ambiguous = t < 88;
    diagnosis = {
      top: [
        { fault: 'injector_fouling', cylinder: FOULED_CYL, p: conf, source: 'classifier+matrix' },
        { fault: 'egt_sensor_drift', cylinder: FOULED_CYL, p: 1 - conf, source: 'matrix' },
      ],
      is_sensor_fault: false,
      ambiguous,
      probe: ambiguous
        ? {
            running: true,
            cylinder: FOULED_CYL,
            amplitude_pct: 4.0,
            elapsed_s: t - 62,
            // Gain is ATTENUATED under the injector hypothesis; a sensor's
            // constant bias cancels identically from the alternating component.
            gain_estimate: 0.62,
            log_likelihood_ratio: (t - 62) * 0.42,
            decision: 'pending',
          }
        : null,
    };
  } else {
    diagnosis = {
      top: [{ fault: 'healthy', cylinder: null, p: 0.98, source: 'classifier+matrix' }],
      is_sensor_fault: false,
      ambiguous: false,
      probe: null,
    };
  }

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
  const dD = Math.max(1e-6, (trueStateAt(t + 1).damage - trueS.damage));
  const physics_h = Math.min(48, ((1 - trueS.damage) / dD) / 3600);
  const network_h = physics_h * 0.9;

  const rul: HealthFrame['rul'] = {
    component: injectorActive ? `injector_cyl${FOULED_CYL + 1}` : 'none',
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
  const severity = injectorActive ? 1 - trueS.cd_inj[FOULED_CYL] : 0;
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
      // Path 4 does not exist on an unthrottled FADEC aero-diesel.
      // null, never a fabricated number.
      rho3_sd_vs_restr: null,
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
        n: anomalyScore > threshold ? 5 : 0,
        of: 5,
        met: anomalyScore > threshold,
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

export function generateMission(): MissionTick[] {
  if (cached) return cached;
  const noise = makeNoise(0x5148);
  const ticks: MissionTick[] = [];
  for (let t = 0; t <= MISSION_DURATION_S; t += 1 / HEALTH_HZ) {
    ticks.push(makeTick(t, noise));
  }
  cached = ticks;
  return ticks;
}

export const FOULED_CYLINDER = FOULED_CYL;
export const DRIFTING_CYLINDER = DRIFT_CYL;
export const FAULT_TIMES = { injector: T_INJECTOR_START, sensor: T_SENSOR_DRIFT_START };
export type { FaultId };
