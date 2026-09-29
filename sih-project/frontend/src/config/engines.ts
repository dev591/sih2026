/**
 * ENGINE PROFILES — the single source of engine-specific truth for the UI.
 *
 * "A new engine is a config change, not a rewrite" is one of the five claims
 * this project makes that other teams cannot. It therefore has to be TRUE IN
 * THE CODE, not merely true in the architecture diagram. Nothing outside this
 * file may hardcode a cylinder count, a displacement, a limit or a critical
 * altitude.
 *
 * These objects mirror `sih-project/config/engine_*.yaml`, which is what BE-1's
 * backend actually loads. When the live socket is running the profile arrives
 * with the telemetry and this file becomes a fallback for offline/demo use;
 * until then it is the definition. Keep the two in step — the YAML is
 * authoritative for anything a slide quotes, because it carries the provenance
 * markers.
 *
 * PROVENANCE is preserved deliberately: `published` figures are citable,
 * `assumed` ones must be labelled as such if they ever reach a slide.
 */

export type Provenance = 'published' | 'derived' | 'assumed';

/**
 * What the machine physically IS. Drives the 3D model, so switching profile
 * builds a different engine rather than recolouring one: the old model drew
 * finned air-cooled barrels for both, which is wrong in cooling for the VRDE
 * and wrong in layout for the Rotax.
 */
export interface EngineArchitecture {
  layout: 'inline' | 'boxer';
  cooling: 'liquid' | 'liquid_heads_air_barrels';
  fuelSystem: 'common_rail' | 'carburettor';
  valvetrain: 'dohc_4v' | 'ohv_2v';
  /** 1-indexed cylinder numbers in firing order. */
  firingOrder: number[];
  /** Render-only: not published for either engine, and never used by physics. */
  rodLength_m: number;
  boreSpacing_m: number;
  provenance: Provenance;
}

export interface LimitDef {
  key: string;
  label: string;
  unit: string;
  /** the redline a conventional threshold monitor would alarm on */
  limit: number;
  /** true when LOW is bad (oil pressure), rather than high */
  inverted?: boolean;
  decimals?: number;
  provenance: Provenance;
}

export interface EngineProfile {
  id: string;
  name: string;
  short: string;
  developer: string;
  cycle: 'diesel' | 'spark_ignition';
  fuel: string;
  /** Covers engine identity and the RATINGS block only — not geometry. */
  provenance: Provenance;
  /**
   * Geometry has its own marker because the two profiles genuinely differ:
   * the Rotax figures are a published spec, the VRDE ones are sized to hit
   * 180 hp because DRDO has never published displacement/bore/stroke. The
   * YAML has carried this distinction per-block all along
   * (`config/engine_vrde_180.yaml` → `geometry: provenance: assumed`); this
   * field is what keeps the two in step, per the note at the top of the file.
   */
  geometryProvenance: Provenance;

  architecture: EngineArchitecture;

  cylinders: number;
  displacement_m3: number;
  bore_m: number;
  stroke_m: number;
  gearRatio: number;

  ratedPower_kW: number;
  ratedPower_hp: number;
  criticalAltitude_ft: number;
  cruiseRpm: number;
  maxRpm: number;

  Q_LHV: number;
  AFR_stoich: number;
  etaV_nominal: number;
  etaC_nominal: number;
  boostRatio: number;

  /** healthy-cruise anchors, used by the display ramps */
  nominalCht_C: number;
  nominalEgt_C: number;
  chtRampFrom_C: number;

  limits: LimitDef[];

  /** Which air-mass-flow paths this installation can actually produce.
   *  With n paths you get n-1 independent residuals, and rho3 is null when
   *  Path 4 does not exist. We report null; we never fabricate it. */
  parityPaths: {
    speedDensity: boolean;
    compressorMap: boolean;
    fuelLambda: boolean;
    intakeRestriction: boolean;
  };

  /** Why Path 4 is or is not available on this engine, shown in the UI. */
  pathNote: string;

  cruiseAltitude_ft: number;
  cruiseTas_mps: number;
  /** Healthy-cruise blade angle; absent on a fixed-pitch propeller. */
  cruiseBladeAngle_deg?: number;
  /** Healthy-cruise gearbox oil temperature; absent without a gearbox oil node. */
  nominalGearboxOil_C?: number;
  /** Healthy-cruise coolant temperature; absent on an air-cooled profile. */
  nominalCoolant_C?: number;
}

// ---------------------------------------------------------------------------
// PRIMARY — DRDO's own engine. Twinning their engine, in front of them, using
// their published altitude-trial numbers, is the cheapest credibility available.
// ---------------------------------------------------------------------------
export const VRDE_180: EngineProfile = {
  id: 'vrde_180',
  name: 'VRDE 180 hp Aero-Diesel',
  short: 'VRDE 180',
  developer: 'Vehicles Research and Development Establishment, DRDO',
  cycle: 'diesel',
  fuel: 'Jet A-1',
  provenance: 'published',
  // 2179 cc is the mHawk 2.2 base block — a community claim, not DRDO data;
  // bore/stroke are assumed. Mirrors `geometry: provenance: unverified_community`
  // in config/engine_vrde_180.yaml. See docs/research/VRDE-PUBLIC-DOSSIER.md.
  geometryProvenance: 'assumed',

  // Liquid cooling is the VRDE's own spec (the backend models its coolant
  // loop, thermostat, radiator and intercooler). Inline-4, common rail and
  // DOHC 16-valve follow the mHawk base block — the same community claim as
  // the displacement, hence 'assumed'.
  architecture: {
    layout: 'inline',
    cooling: 'liquid',
    fuelSystem: 'common_rail',
    valvetrain: 'dohc_4v',
    firingOrder: [1, 3, 4, 2],
    rodLength_m: 0.152,
    boreSpacing_m: 0.096,
    provenance: 'assumed',
  },

  cylinders: 4,
  displacement_m3: 2.179e-3,
  bore_m: 0.085,
  stroke_m: 0.096,
  gearRatio: 1.69,              // AE330-class reduction gearbox (comparable engine)

  ratedPower_kW: 134.2,         // derived: 180 hp x 0.7457, not separately published
  ratedPower_hp: 180,           // published
  criticalAltitude_ft: 11000,   // published — "180 hp constant up to 11,000 ft"
  cruiseRpm: 3588,              // governor schedule at 72 % (2123 prop rpm) x 1.69
  maxRpm: 3880,                 // AE330-class take-off crank speed (comparable engine)

  Q_LHV: 43.0e6,
  AFR_stoich: 14.5,
  etaV_nominal: 0.88,
  etaC_nominal: 0.72,
  boostRatio: 1.72,

  nominalCht_C: 128,
  nominalEgt_C: 720,
  chtRampFrom_C: 150,

  limits: [
    { key: 'cht',  label: 'CHT max',      unit: '°C',   limit: 200,  provenance: 'assumed' },
    { key: 'oilp', label: 'Oil pressure', unit: 'bar',  limit: 2.0,  inverted: true, decimals: 2, provenance: 'assumed' },
    { key: 'oilt', label: 'Oil temp',     unit: '°C',   limit: 130,  provenance: 'assumed' },
    { key: 'map',  label: 'MAP',          unit: 'hPa',  limit: 1900, provenance: 'assumed' },
    // Crank speed and gearbox oil: EASA TCDS E.200 values for the AE330 this
    // engine replaces — a comparable engine, not VRDE data (the YAML marks
    // them published_comparable_engine; this union has no such tier, so they
    // stay labelled assumed on screen).
    { key: 'rpm',  label: 'RPM (max continuous)', unit: 'rpm', limit: 3720, provenance: 'assumed' },
    { key: 'gbox', label: 'Gearbox oil',  unit: '°C',   limit: 120,  provenance: 'assumed' },
    { key: 'coolant', label: 'Coolant',   unit: '°C',   limit: 105,  provenance: 'assumed' },
    // NOTE: no EGT row. EGT is a TREND parameter on this class, not a limit
    // parameter — which is precisely the information a twin exists to exploit.
  ],

  parityPaths: {
    speedDensity: true,
    compressorMap: true,
    fuelLambda: true,
    intakeRestriction: false,
  },
  pathNote:
    'Unthrottled FADEC aero-diesel — no metering restriction, so Path 4 does not exist. ' +
    '3 available paths give 2 independent air-path residuals; ρ₃ is null.',

  // Reference altitude moved 2026-09-19 from 18,000 ft (an unsourced demo
  // point — see backend/gates_check.py's module docstring) to 11,000 ft,
  // DRDO's own published VRDE critical altitude. Values below re-measured
  // from backend verify.py at the new point, not hand-adjusted.
  cruiseAltitude_ft: 11000,
  cruiseTas_mps: 61.2,
  cruiseBladeAngle_deg: 25.5,   // backend verify.py, 11,000 ft / 72 % steady state
  nominalGearboxOil_C: 96.5,    // same run
  nominalCoolant_C: 86.2,       // backend, 11,000 ft / 72 %, thermostat ~41 % open
};

// ---------------------------------------------------------------------------
// FALLBACK / TRANSFER TARGET — every limit citable from EASA TCDS E.122.
// Different cycle, different fuel, geared, and THROTTLED — so Path 4 exists
// here and the isolability analysis legitimately differs.
// ---------------------------------------------------------------------------
export const ROTAX_914: EngineProfile = {
  id: 'rotax_914',
  name: 'Rotax 914 F/UL',
  short: 'Rotax 914',
  developer: 'BRP-Rotax',
  cycle: 'spark_ignition',
  fuel: 'AVGAS 100LL',
  provenance: 'published',
  geometryProvenance: 'published',   // real Rotax 914 spec, unlike the VRDE

  // Horizontally opposed, liquid-cooled heads on air-cooled finned barrels,
  // twin carburettors, pushrod OHV: the published Rotax 91x configuration.
  architecture: {
    layout: 'boxer',
    cooling: 'liquid_heads_air_barrels',
    fuelSystem: 'carburettor',
    valvetrain: 'ohv_2v',
    firingOrder: [1, 4, 2, 3],
    rodLength_m: 0.1025,
    boreSpacing_m: 0.108,
    provenance: 'published',
  },

  cylinders: 4,
  displacement_m3: 1.2114e-3,
  bore_m: 0.0793,
  stroke_m: 0.0613,
  gearRatio: 2.4295,            // GEARED — n_prop = N_crank / 2.4295

  ratedPower_kW: 73.5,
  ratedPower_hp: 98.6,
  criticalAltitude_ft: 16000,   // assumed
  cruiseRpm: 5100,
  maxRpm: 5800,

  Q_LHV: 43.5e6,
  AFR_stoich: 14.7,
  etaV_nominal: 0.85,
  etaC_nominal: 0.70,
  boostRatio: 1.42,

  nominalCht_C: 108,
  nominalEgt_C: 780,
  chtRampFrom_C: 118,

  limits: [
    // ALL PUBLISHED, verbatim from EASA TCDS E.122.
    { key: 'cht',  label: 'CHT max',      unit: '°C',   limit: 135,  provenance: 'published' },
    { key: 'oilp', label: 'Oil pressure', unit: 'bar',  limit: 2.0,  inverted: true, decimals: 2, provenance: 'published' },
    { key: 'oilt', label: 'Oil temp',     unit: '°C',   limit: 130,  provenance: 'published' },
    { key: 'map',  label: 'MAP',          unit: 'hPa',  limit: 1200, provenance: 'published' },
    { key: 'rpm',  label: 'RPM',          unit: 'rpm',  limit: 5800, provenance: 'published' },
  ],

  parityPaths: {
    speedDensity: true,
    compressorMap: true,
    fuelLambda: true,
    intakeRestriction: false,
  },
  pathNote:
    'The engine is throttled, but this installation lacks throttle-area and upstream-airbox ' +
    'sensors. The compressible-orifice path remains unavailable, so ρ₃ is null rather than fabricated.',

  cruiseAltitude_ft: 15000,
  cruiseTas_mps: 54.0,
};

export const ENGINES: EngineProfile[] = [VRDE_180, ROTAX_914];

export function engineById(id: string): EngineProfile {
  return ENGINES.find((e) => e.id === id) ?? VRDE_180;
}
