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
  // Sized to hit 180 hp; DRDO publishes no displacement/bore/stroke. Mirrors
  // `geometry: provenance: assumed` in config/engine_vrde_180.yaml.
  geometryProvenance: 'assumed',

  cylinders: 4,
  displacement_m3: 2.0e-3,
  bore_m: 0.086,
  stroke_m: 0.086,
  gearRatio: 1.0,

  ratedPower_kW: 134.2,         // derived: 180 hp x 0.7457, not separately published
  ratedPower_hp: 180,           // published
  criticalAltitude_ft: 11000,   // published — "180 hp constant up to 11,000 ft"
  cruiseRpm: 3580,
  maxRpm: 3800,

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
    { key: 'rpm',  label: 'RPM',          unit: 'rpm',  limit: 3800, provenance: 'assumed' },
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

  cruiseAltitude_ft: 18000,
  cruiseTas_mps: 61.2,
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
    intakeRestriction: true,
  },
  pathNote:
    'Throttled, so the compressible-orifice path works. 4 available paths give 3 ' +
    'independent air-path residuals; ρ₃ is live and the isolability table differs.',

  cruiseAltitude_ft: 15000,
  cruiseTas_mps: 54.0,
};

export const ENGINES: EngineProfile[] = [VRDE_180, ROTAX_914];

export function engineById(id: string): EngineProfile {
  return ENGINES.find((e) => e.id === id) ?? VRDE_180;
}
