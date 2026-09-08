/**
 * Shared material vocabulary for the engine.
 *
 * A real engine is made of four or five distinct materials, and reading them
 * apart at a glance is most of what makes a render look like hardware rather
 * than a diagram. Cast aluminium is pale, rough and non-reflective. Machined
 * steel is dark, tight and mirror-ish. Exhaust steel is heat-discoloured. Cast
 * iron is nearly black.
 *
 * Everything is defined once here so parts cannot drift apart.
 */

import * as THREE from 'three';

export const MAT = {
  /** Sand-cast aluminium — crankcase, heads, rocker covers. Matte, slightly warm. */
  castAlu: {
    color: '#8d9299',
    metalness: 0.58,
    roughness: 0.62,
  },
  /** Darker cast — sump, gearbox housing. Reads as a separate casting. */
  castAluDark: {
    color: '#6b7079',
    metalness: 0.55,
    roughness: 0.68,
  },
  /** Machined / polished steel — crankshaft, fasteners, compressor wheel. */
  steel: {
    color: '#aeb6c2',
    metalness: 0.95,
    roughness: 0.19,
  },
  /** Dark machined steel — barrels, hardware. */
  steelDark: {
    color: '#5c636e',
    metalness: 0.88,
    roughness: 0.34,
  },
  /** Cast iron — turbine housing. Nearly black, coarse. */
  castIron: {
    color: '#2f333a',
    metalness: 0.72,
    roughness: 0.78,
  },
  /** Inconel / heat-cycled stainless — exhaust runners before any glow. */
  exhaust: {
    color: '#6f7078',
    metalness: 0.82,
    roughness: 0.41,
  },
  /** Anodised black — intake plenum, brackets. */
  anodised: {
    color: '#23272e',
    metalness: 0.62,
    roughness: 0.48,
  },
  /** Rubber / composite — hoses, mounts. */
  rubber: {
    color: '#191c21',
    metalness: 0.05,
    roughness: 0.92,
  },
} as const;

/**
 * Steel → amber → red thermal ramp.
 *
 * Anchored so a HEALTHY engine reads as bare metal. Normal cruise CHT on this
 * engine is ~128-140 degC against a 200 degC limit, so the ramp stays cold
 * until 150 and only saturates as the real limit is approached. If a healthy
 * engine glows, the colour carries no information when a real fault arrives —
 * which is the entire reason it is here.
 */
export function thermalRamp(cht: number, lo = 150, hi = 205): THREE.Color {
  const x = THREE.MathUtils.clamp((cht - lo) / (hi - lo), 0, 1);
  const cold = new THREE.Color(MAT.castAlu.color);
  const amber = new THREE.Color('#c9761f');
  const red = new THREE.Color('#a83226');
  return x < 0.5
    ? cold.clone().lerp(amber, x * 2)
    : amber.clone().lerp(red, (x - 0.5) * 2);
}

/**
 * Fault glow colour.
 *
 * AMBER for a component fault, CYAN for an instrumentation fault. Two colours
 * because they are two different KINDS of problem, and telling them apart is
 * the entire differentiator of this system. A judge should be able to see
 * which kind it is from across the room without reading a word.
 */
export function faultColour(p: number, isSensor: boolean): THREE.Color {
  return new THREE.Color('#000000').lerp(
    new THREE.Color(isSensor ? '#22d3ee' : '#f59e0b'),
    THREE.MathUtils.clamp(p, 0, 1)
  );
}

/** Heat glow on the exhaust runners, anchored just above healthy cruise EGT
 *  (~818 degC) so the manifold sits dark until a cylinder actually departs
 *  from its siblings. A permanently red manifold tells you nothing. */
export function exhaustHeat(egt: number): number {
  return THREE.MathUtils.clamp((egt - 838) / 125, 0, 1);
}

export const N_FINS = 9;
export const CYL_SPACING = 1.42;
export const CYL_X = (i: number, n: number) => (i - (n - 1) / 2) * CYL_SPACING;
