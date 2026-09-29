/**
 * Engine catalog — what the landing screen shows, straight from the backend's /engines.
 *
 * The backend is the source of truth for which engines exist and what state each is in. The UI's own
 * EngineProfile (config/engines.ts) is richer than a YAML card, so a card is mapped onto one:
 *   - the two built-in engines use their hand-written profiles exactly;
 *   - a generated engine starts from the built-in profile of the same cycle and overrides only what the card
 *     actually knows (identity, geometry, ratings, CHT limit). Everything else stays the base engine's and is
 *     therefore labelled 'assumed'.
 */
import { ROTAX_914, VRDE_180, type EngineProfile } from './engines';

export const API =
  (import.meta.env.VITE_PRAMANA_API as string | undefined) ?? `http://${window.location.hostname}:8000`;

export interface EngineCard {
  id: string;
  name: string;
  developer: string | null;
  cycle: 'diesel' | 'spark_ignition';
  aspiration: string;
  cylinders: number;
  displacement_L: number;
  bore_m: number | null;
  stroke_m: number | null;
  rated_kW: number | null;
  rated_hp: number | null;
  rated_rpm: number | null;
  max_rpm: number | null;
  critical_altitude_ft: number | null;
  cht_limit_C: number | null;
  generated: boolean;
  base_profile: string | null;
  runnable: boolean;
  ml_ready: boolean;
  has_noise_levels?: boolean;
  profile_errors?: string[];
  assumed_fields?: number;
  cylinders_supported?: boolean;
  unsupported_reason?: string | null;
  error?: string;
}

export interface Catalog { default: string; engines: EngineCard[] }

export async function fetchCatalog(): Promise<Catalog> {
  const r = await fetch(`${API}/engines`);
  if (!r.ok) throw new Error(`backend answered ${r.status}`);
  return r.json();
}

/** Offline fallback: the two engines the UI knows without a backend. Both are simulation-only then. */
export function builtinCards(): Catalog {
  const mk = (p: EngineProfile): EngineCard => ({
    id: p.id, name: p.name, developer: p.developer, cycle: p.cycle, aspiration: 'turbocharged',
    cylinders: p.cylinders, displacement_L: p.displacement_m3 * 1000, bore_m: p.bore_m, stroke_m: p.stroke_m,
    rated_kW: p.ratedPower_kW, rated_hp: p.ratedPower_hp, rated_rpm: p.maxRpm, max_rpm: p.maxRpm,
    critical_altitude_ft: p.criticalAltitude_ft, cht_limit_C: p.limits.find((l) => l.key === 'cht')?.limit ?? null,
    generated: false, base_profile: null, runnable: false, ml_ready: false,
  });
  return { default: VRDE_180.id, engines: [mk(VRDE_180), mk(ROTAX_914)] };
}

/** Conventional in-line firing orders; anything else falls back to numerical order. */
const INLINE_FIRING: Record<number, number[]> = {
  2: [1, 2], 3: [1, 2, 3], 4: [1, 3, 4, 2], 5: [1, 2, 4, 5, 3], 6: [1, 5, 3, 6, 2, 4], 7: [1, 2, 4, 6, 7, 5, 3],
  8: [1, 6, 2, 5, 8, 3, 7, 4],
};

export function profileFromCard(c: EngineCard): EngineProfile {
  if (c.id === VRDE_180.id) return VRDE_180;
  if (c.id === ROTAX_914.id) return ROTAX_914;
  const base = c.cycle === 'diesel' ? VRDE_180 : ROTAX_914;
  const maxRpm = c.max_rpm ?? c.rated_rpm ?? base.maxRpm;
  const kW = c.rated_kW ?? base.ratedPower_kW;
  return {
    ...base,
    id: c.id,
    name: c.name,
    short: c.id,
    developer: c.developer ?? 'User-specified engine',
    provenance: 'assumed',
    geometryProvenance: 'assumed',
    // The base engine's 3D architecture only fits ITS cylinder count; anything else is drawn in-line.
    architecture: {
      ...base.architecture,
      layout: c.cylinders === base.cylinders ? base.architecture.layout : 'inline',
      firingOrder: c.cylinders === base.cylinders ? base.architecture.firingOrder
        : (INLINE_FIRING[c.cylinders] ?? Array.from({ length: c.cylinders }, (_, i) => i + 1)),
      provenance: 'assumed',
    },
    cylinders: c.cylinders,
    displacement_m3: c.displacement_L / 1000,
    bore_m: c.bore_m ?? base.bore_m,
    stroke_m: c.stroke_m ?? base.stroke_m,
    ratedPower_kW: kW,
    ratedPower_hp: c.rated_hp ?? Math.round((kW / 0.7457) * 10) / 10,
    criticalAltitude_ft: c.critical_altitude_ft ?? base.criticalAltitude_ft,
    maxRpm,
    cruiseRpm: Math.round((base.cruiseRpm / base.maxRpm) * maxRpm),
    limits: base.limits.map((l) =>
      l.key === 'cht' && c.cht_limit_C != null ? { ...l, limit: c.cht_limit_C, provenance: 'assumed' as const } : l),
    cruiseAltitude_ft: Math.min(base.cruiseAltitude_ft, c.critical_altitude_ft ?? base.cruiseAltitude_ft),
  };
}

/** Why a card cannot drive the live engine model, in words. */
export function simOnlyReason(c: EngineCard): string {
  if (c.cylinders_supported === false && c.unsupported_reason) return c.unsupported_reason;
  if (!c.runnable && c.cylinders !== 4) return `${c.cylinders}-cylinder engines need the live twin (the built-in simulation is 4-cylinder only)`;
  if (c.error) return `profile could not be read: ${c.error}`;
  if (c.profile_errors && c.profile_errors.length)
    return `profile incomplete — ${c.profile_errors.length} required field(s) missing (${c.profile_errors
      .map((e) => e.replace(/^MISSING\s+/, '').split(/\s{2,}/)[0]).join(', ')})`;
  if (c.has_noise_levels === false) return 'healthy noise levels not generated yet';
  return 'live engine model not available';
}
