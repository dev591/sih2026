/**
 * Sub-tick interpolation and temporal easing.
 *
 * The store advances `index` fractionally every animation frame, and the
 * mission carries per-tick Gaussian sensor noise — but every consumer used to
 * throw that away with `Math.round(index)`, so a 1 Hz mission rendered as a
 * 1 Hz slideshow. Interpolating between the two neighbouring ticks is what
 * turns the same data into instrumentation that breathes.
 *
 * This is a STRUCTURAL walk, not a field-by-field lerp. The telemetry schema is
 * ~80 fields across a dozen nested interfaces and is owned by BE-1; a hand
 * written field list would silently rot the first time a channel is added.
 */

import type { MissionTick, Diagnosis } from '../types/telemetry';

/**
 * Fields that are counts or ranks rather than measurements. Interpolating them
 * produces "3.4 of 5" or "RANK F 10.3/11", which reads as a rendering bug.
 * Matched at any depth.
 */
const DISCRETE_KEYS = new Set(['seq', 'n', 'of', 'effective_rank', 'null_space_dim']);

/** Linear interpolation between two numbers. */
export function lerp(a: number, b: number, u: number): number {
  return a + (b - a) * u;
}

/**
 * Frame-rate independent exponential approach — the standard "damp" from
 * Game Programming Gems 4, also available as THREE.MathUtils.damp.
 * `lambda` is roughly "how many e-foldings per second"; higher is snappier.
 */
export function damp(current: number, target: number, lambda: number, dt: number): number {
  return lerp(current, target, 1 - Math.exp(-lambda * dt));
}

/** Hermite smoothstep, for fading things in instead of popping them in. */
export function smoothstep(edge0: number, edge1: number, x: number): number {
  if (edge1 === edge0) return x < edge0 ? 0 : 1;
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/**
 * Two hypothesis lists are interpolable only if they describe the SAME
 * hypotheses in the same order. When the classifier changes its mind we take
 * the nearer frame wholesale — blending `p` across two different faults would
 * invent a confidence for a hypothesis neither frame actually reported.
 */
function sameHypotheses(a: Diagnosis, b: Diagnosis): boolean {
  if (a.top.length !== b.top.length) return false;
  return a.top.every(
    (h, i) => h.fault === b.top[i].fault && h.cylinder === b.top[i].cylinder
  );
}

function lerpValue(a: unknown, b: unknown, u: number, key?: string): unknown {
  // Non-numeric: strings, booleans, null (an unavailable parity path stays
  // unavailable), undefined. Snap to whichever frame is nearer so a label never
  // garbles halfway between two states.
  if (typeof a === 'number' && typeof b === 'number') {
    if (key !== undefined && DISCRETE_KEYS.has(key)) return u < 0.5 ? a : b;
    return lerp(a, b, u);
  }

  if (Array.isArray(a) && Array.isArray(b)) {
    // A length change means the shape changed, not the value — don't blend it.
    if (a.length !== b.length) return u < 0.5 ? a : b;
    return a.map((v, i) => lerpValue(v, b[i], u));
  }

  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(a as object)) {
      out[k] = lerpValue(
        (a as Record<string, unknown>)[k],
        (b as Record<string, unknown>)[k],
        u,
        k
      );
    }
    return out;
  }

  return u < 0.5 ? a : b;
}

/**
 * Interpolate a whole tick. `u` is the fraction between `a` and `b`, in [0,1].
 *
 * Numbers sweep, labels snap to the nearer frame, and `diagnosis` is blended
 * only while it is describing the same hypotheses.
 */
export function lerpTick(a: MissionTick, b: MissionTick, u: number): MissionTick {
  if (a === b || u <= 0) return a;
  if (u >= 1) return b;

  const out = lerpValue(a, b, u) as MissionTick;

  if (!sameHypotheses(a.health.diagnosis, b.health.diagnosis)) {
    out.health.diagnosis = u < 0.5 ? a.health.diagnosis : b.health.diagnosis;
  }

  return out;
}
