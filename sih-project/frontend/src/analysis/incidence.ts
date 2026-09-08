/**
 * The fault incidence matrix, as shipped in docs/spec/residual-spec.md §3.
 *
 * Each row is a fault mode's SIGNATURE: how that fault excites each of the
 * eleven parity residuals. Component faults above the rule, instrumentation
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

/** Column order: rho1 rho2 rho3 rho4 rho5 rho6 rho7 rho8 rho9 rho10 rho11 */
export const INCIDENCE: Partial<Record<FaultId, number[]>> = {
  //                    ρ1  ρ2  ρ3  ρ4  ρ5  ρ6  ρ7  ρ8  ρ9 ρ10 ρ11
  ring_wear:           [ 1,  1,  1,  1,  1,  0,  0,  0,  0, -1,  0],
  turbo_degradation:   [-2,  0,  0,  1,  0,  0,  0,  0,  0,  0,  0],
  injector_fouling:    [ 0, -2,  0,  1,  0,  2,  2,  2,  2,  0,  2],
  fuel_filter_clog:    [ 0, -2,  0,  1, -1,  0,  0,  0,  0,  0,  0],
  ignition_misfire:    [ 0,  0,  0,  2, -1, -2, -2, -2, -2,  0,  2],
  cooling_fouling:     [ 0,  0,  0,  1,  0,  1,  1,  1,  1,  1,  0],
  oil_pump_wear:       [ 0,  0,  0,  0,  1,  0,  0,  0,  0, -2,  0],
  bearing_wear:        [ 0,  0,  0,  1,  2,  0,  0,  0,  0, -1,  0],
  detonation:          [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  0],
  map_sensor_drift:    [ 2,  1,  1,  1,  0,  0,  0,  0,  0,  0,  0],
  egt_sensor_drift:    [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  0],
  cht_sensor_drift:    [ 0,  0,  0,  0,  0,  2,  2,  2,  2,  0,  0],
  lambda_sensor_drift: [ 0,  2,  0,  0,  0,  0,  0,  0,  0,  0,  0],
};

export const FAULT_ORDER = Object.keys(INCIDENCE) as FaultId[];

/** Signature glyph for display. */
export const SIGN = (v: number) =>
  v === 2 ? '⇑' : v === 1 ? '↑' : v === -1 ? '↓' : v === -2 ? '⇓' : '·';
