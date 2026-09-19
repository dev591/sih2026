/**
 * The per-frame channel between telemetry and geometry.
 *
 * The old scene called setState inside useFrame, which re-rendered the whole
 * engine tree 60 times a second. Here Scene writes the latest numbers into one
 * mutable object and each part reads it inside its own useFrame, mutating its
 * materials and transforms directly. React only re-renders when something
 * structural changes (engine profile, view mode).
 */

import { createContext, useContext } from 'react';
import * as THREE from 'three';
import { SENSOR_FAULTS, type Diagnosis, type FaultId } from '../../types/telemetry';

/** A physical place on the engine a diagnosis can point at. */
export type PartId =
  | `cyl:${number}`
  | `injector:${number}`
  | `chamber:${number}`
  | `piston:${number}`
  | `egt:${number}`
  | `cht:${number}`
  | 'oil' | 'turbo' | 'cooling' | 'fuel' | 'bearings'
  | 'sensor:map' | 'sensor:lambda';

export interface Highlight {
  /** 0..1, eased */
  p: number;
  sensor: boolean;
}

export interface Live {
  /** accumulated, display-scaled crank angle in radians */
  crank: number;
  rpm: number;
  running: boolean;
  turboRpm: number;
  effScale: number;
  cht: number[];
  egt: number[];
  chtRampFrom: number;
  nominalEgt: number;
  fuelFlow: number;
  map_hPa: number;
  coolant: number | null;
  /** eased fault probability per cylinder */
  cylFault: number[];
  cylSensor: boolean[];
  anomaly: number;
  /** eased, keyed by PartId; parts not present are dark */
  parts: Map<string, Highlight>;
  partTargets: Map<string, Highlight>;
  /** view modes, eased toward their targets */
  cut: number;
  cutTarget: number;
  flow: number;
  flowTarget: number;
  explode: number;
  explodeTarget: number;
  hovered: number | null;
  selected: number | null;
  /** clip plane for the cutaway; keeps z <= constant */
  plane: THREE.Plane;
  time: number;
}

export function createLive(): Live {
  return {
    crank: 0, rpm: 0, running: false, turboRpm: 0, effScale: 1,
    cht: [0, 0, 0, 0], egt: [0, 0, 0, 0], chtRampFrom: 150, nominalEgt: 700,
    fuelFlow: 0, map_hPa: 1000, coolant: null,
    cylFault: [0, 0, 0, 0], cylSensor: [false, false, false, false], anomaly: 0,
    parts: new Map(), partTargets: new Map(),
    cut: 0, cutTarget: 0, flow: 0, flowTarget: 0, explode: 0, explodeTarget: 0,
    hovered: null, selected: null,
    plane: new THREE.Plane(new THREE.Vector3(0, 0, -1), 100),
    time: 0,
  };
}

export const LiveContext = createContext<Live | null>(null);

export function useLive(): Live {
  const live = useContext(LiveContext);
  if (!live) throw new Error('useLive outside LiveContext');
  return live;
}

/**
 * Where on the engine each fault lives. This is what lets a viewer who has
 * never seen a residual still see WHERE the problem is: an injector fault
 * lights the injector, a MAP-sensor drift lights the sensor on the plenum.
 */
const FAULT_PARTS: Record<FaultId, (cyl: number | null) => PartId[]> = {
  healthy: () => [],
  unknown: () => [],
  injector_fouling: (c) => (c == null ? ['fuel'] : [`injector:${c}`, `cyl:${c}`]),
  ignition_misfire: (c) => (c == null ? [] : [`chamber:${c}`, `cyl:${c}`]),
  detonation: (c) => (c == null ? [] : [`chamber:${c}`, `cyl:${c}`]),
  ring_wear: (c) => (c == null ? [] : [`piston:${c}`, `cyl:${c}`]),
  oil_pump_wear: () => ['oil'],
  turbo_degradation: () => ['turbo'],
  cooling_fouling: () => ['cooling'],
  fuel_filter_clog: () => ['fuel'],
  bearing_wear: () => ['bearings'],
  map_sensor_drift: () => ['sensor:map'],
  egt_sensor_drift: (c) => (c == null ? [] : [`egt:${c}`]),
  cht_sensor_drift: (c) => (c == null ? [] : [`cht:${c}`]),
  lambda_sensor_drift: () => ['sensor:lambda'],
};

/** Rebuild the highlight targets from the current diagnosis. */
export function setPartTargets(live: Live, diagnosis: Diagnosis, running: boolean) {
  live.partTargets.clear();
  if (!running) return;
  for (const h of diagnosis.top) {
    if (h.fault === 'healthy' || h.p < 0.05) continue;
    // Per hypothesis, not the diagnosis-wide flag: a sensor drift and an
    // engine fault can be live at once, and each must keep its own colour.
    const sensor = SENSOR_FAULTS.includes(h.fault);
    for (const id of FAULT_PARTS[h.fault](h.cylinder ?? null)) {
      const prev = live.partTargets.get(id);
      if (!prev || prev.p < h.p) live.partTargets.set(id, { p: h.p, sensor });
    }
  }
}

/** Frame-rate independent easing of every highlight toward its target. */
export function easeParts(live: Live, dt: number, lambda = 1.4) {
  const k = 1 - Math.exp(-lambda * dt);
  for (const [id, t] of live.partTargets) {
    const cur = live.parts.get(id);
    if (cur) {
      cur.p += (t.p - cur.p) * k;
      cur.sensor = t.sensor;
    } else {
      live.parts.set(id, { p: t.p * k, sensor: t.sensor });
    }
  }
  for (const [id, cur] of live.parts) {
    if (live.partTargets.has(id)) continue;
    cur.p += (0 - cur.p) * k;
    if (cur.p < 0.002) live.parts.delete(id);
  }
}

export function partGlow(live: Live, id: string): Highlight | undefined {
  return live.parts.get(id);
}
