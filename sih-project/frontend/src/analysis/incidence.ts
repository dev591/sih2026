/**
 * The fault incidence matrix, as shipped in docs/spec/residual-spec.md §3.
 *
 * Each row is a fault mode's SIGNATURE: how that fault excites each of the
 * thirteen parity residuals. Component faults above the rule, instrumentation
 * faults below it.
 *
 *    2 = strong positive   1 = weak positive
 *   -2 = strong negative  -1 = weak negative    0 = unexcited
 *
 * This is derived from the physics, not asserted from experience: because each
 * parity relation is built from a known subset of sensors and a known piece of
 * physics, the effect of any fault on any relation follows.
 *
 * Single source of truth — the explain drawer and the novelty detector both
 * read it from here.
 */

import type { FaultId } from '../types/telemetry';

/** Column order: rho1 .. rho13. rho12 = coolant closure, rho13 = head-temp
 *  closure; both added 2026-09-17. Mirrors ml/incidence.py — change together. */
export const INCIDENCE: Partial<Record<FaultId, number[]>> = {
  //                    ρ1  ρ2  ρ3  ρ4  ρ5  ρ6  ρ7  ρ8  ρ9 ρ10 ρ11 ρ12 ρ13
  // ρ₁₂/ρ₁₃ entries are MEASURED through the real MVEM at sea-level take-off,
  // not asserted: |Δ| < 1σ → 0, 1–3σ → ±1, > 3σ → ±2. Mirrors ml/incidence.py,
  // which carries the full measurement note — change the two together.
  ring_wear:           [ 1,  1,  1,  1,  1,  0,  0,  0,  0, -1,  0,  0, -1],
  turbo_degradation:   [-2,  0,  0,  1,  0,  0,  0,  0,  0,  0,  0,  0,  0],
  // A starved cylinder drags the cross-cylinder MEAN down, so ρ₁₃ goes
  // negative for injector fouling, rail loss and misfire alike.
  injector_fouling:    [ 0, -2,  0,  1,  0,  2,  2,  2,  2,  0,  2, -1, -2],
  fuel_filter_clog:    [ 0, -2,  0,  1, -1,  0,  0,  0,  0,  0,  0, -1, -2],
  ignition_misfire:    [ 0,  0,  0,  2, -1, -2, -2, -2, -2,  0,  2, -1, -2],
  // A blocked radiator lifts BOTH thermal closures. Before ρ₁₂/ρ₁₃ existed the
  // only channel that moved was ρ₁₀ (oil) — so a cooling failure read as an oil
  // failure, which is what these two columns exist to fix.
  cooling_fouling:     [ 0,  0,  0,  1,  0,  1,  1,  1,  1,  1,  0,  2,  2],
  // Degraded coolant pump: heads run hot, coolant loop stays in its band.
  // Measured: CHT +59 °C for a coolant change of ~0.
  coolant_pump_degradation:
                       [ 0,  0,  0,  1,  0,  0,  0,  0,  0,  0,  0, -1,  2],
  oil_pump_wear:       [ 0,  0,  0,  0,  1,  0,  0,  0,  0, -2,  0,  0,  0],
  bearing_wear:        [ 0,  0,  0,  1,  2,  0,  0,  0,  0, -1,  0,  0,  1],
  // rho11 (+2): detonation rings the structure and shows in the 0.5-order
  // crank ripple; a drifting thermocouple does not. Without this entry the
  // detonation and egt_sensor_drift rows were identical, so the matrix could
  // not tell a component fault from an instrumentation one. Mirrored in
  // ml/incidence.py and docs/spec/residual-spec.md - change all three together.
  detonation:          [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  2,  0,  2],
  map_sensor_drift:    [ 2,  1,  1,  1,  0,  0,  0,  0,  0,  0,  0,  0,  0],
  egt_sensor_drift:    [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  0,  0,  0],
  // A drifting CHT probe biases the MEAN as well as the deviations, so it lifts
  // ρ₁₃ — but leaves the coolant loop untouched, which is how it separates from
  // a real cooling fault.
  cht_sensor_drift:    [ 0,  0,  0,  0,  0,  2,  2,  2,  2,  0,  0,  0,  2],
  lambda_sensor_drift: [ 0,  2,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0],
};
export const FAULT_ORDER = Object.keys(INCIDENCE) as FaultId[];

/** Signature glyph for display. */
export const SIGN = (v: number) =>
  v === 2 ? '⇑' : v === 1 ? '↑' : v === -1 ? '↓' : v === -2 ? '⇓' : '·';
