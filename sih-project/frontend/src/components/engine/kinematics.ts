/**
 * Cylinder frames and slider-crank kinematics, derived from the profile.
 *
 * Scene unit: 1 unit = 100 mm, so both engines render at their true relative
 * size and every piston travels its real stroke.
 *
 * Crank angle theta advances once per revolution; a four-stroke cycle is two
 * revolutions. Cylinder i fires (compression TDC) when its cycle phase
 * c_i = (theta - phi_i) mod 4pi equals 0, with phi_i spaced 720/N degrees in
 * firing order. Its crank pin must then point along its own bore axis, which
 * fixes the pin's angular position on the crank: psi_i - phi_i. For an inline
 * four with firing order 1-3-4-2 that yields pins at 0/180/180/0 degrees, the
 * real flat-plane crank; for a boxer 1-4-2-3 it yields the real flat-four
 * crank. (The previous model phased cylinders 90 degrees apart, which no
 * four-cylinder four-stroke has.)
 */

import type { EngineProfile } from '../../config/engines';

export const MM = 0.01;

export interface CylFrame {
  index: number;
  /** position along the crank axis */
  x: number;
  /** bore axis angle from +Y, rotating about +X: axis = (0, cos psi, sin psi) */
  psi: number;
  /** cycle offset in radians (0..4pi) */
  phi: number;
}

export interface Geometry {
  bore: number;
  r: number;
  rod: number;
  compH: number;
  pistonH: number;
  /** crank centre to deck face, along the bore axis */
  deck: number;
  pitch: number;
  frames: CylFrame[];
}

export function engineGeometry(p: EngineProfile): Geometry {
  const a = p.architecture;
  const bore = p.bore_m * 1000 * MM;
  const r = (p.stroke_m * 1000 * MM) / 2;
  const rod = a.rodLength_m * 1000 * MM;
  const compH = bore * 0.47;
  const pistonH = bore * 0.78;
  const pitch = a.boreSpacing_m * 1000 * MM;
  const n = p.cylinders;
  const fireSlot = (cyl: number) => a.firingOrder.indexOf(cyl + 1);
  const phiOf = (i: number) => (fireSlot(i) * 4 * Math.PI) / n;

  let frames: CylFrame[];
  if (a.layout === 'inline') {
    frames = Array.from({ length: n }, (_, i) => ({
      index: i,
      x: (i - (n - 1) / 2) * pitch,
      psi: 0,
      phi: phiOf(i),
    }));
  } else {
    // Opposed banks, staggered along the crank by a throw's width. Odd
    // cylinders on the +Z bank, even on -Z.
    const stagger = pitch * 0.16;
    frames = Array.from({ length: n }, (_, i) => {
      const pair = Math.floor(i / 2);
      const right = i % 2 === 0;
      return {
        index: i,
        x: (pair - (n / 2 - 1) / 2) * pitch + (right ? -stagger : stagger),
        psi: right ? Math.PI / 2 : -Math.PI / 2,
        phi: phiOf(i),
      };
    });
  }
  return { bore, r, rod, compH, pistonH, deck: r + rod + compH, pitch, frames };
}

const TAU = Math.PI * 2;
export const mod = (a: number, m: number) => ((a % m) + m) % m;

/** Pin angle on the crank for cylinder f (radians, about +X, from +Y). */
export function pinOffset(f: CylFrame): number {
  return f.psi - f.phi;
}

export interface SliderState {
  /** piston-pin distance from crank centre along the bore axis */
  s: number;
  /** rod angle about +X from +Y (world) */
  beta: number;
  /** pin position in the YZ plane */
  pinY: number;
  pinZ: number;
  /** position in the 4-stroke cycle, 0..4pi, 0 = firing TDC */
  cycle: number;
}

export function slider(g: Geometry, f: CylFrame, theta: number): SliderState {
  const alpha = theta + pinOffset(f);          // pin angle, world, from +Y
  const rel = alpha - f.psi;                     // pin angle relative to bore axis
  const sinR = Math.sin(rel);
  const k = Math.sqrt(Math.max(g.rod * g.rod - g.r * g.r * sinR * sinR, 1e-9));
  const s = g.r * Math.cos(rel) + k;
  return {
    s,
    beta: f.psi + Math.atan2(-g.r * sinR, k),
    pinY: g.r * Math.cos(alpha),
    pinZ: g.r * Math.sin(alpha),
    cycle: mod(theta - f.phi, 2 * TAU),
  };
}

/** 0..1 flash strength: combustion peaks just after firing TDC. */
export function combustion(cycle: number): number {
  const deg = (cycle * 180) / Math.PI;
  if (deg > 60 && deg < 700) return 0;
  const x = deg >= 700 ? deg - 720 : deg;       // -20..60 around TDC
  if (x < -6) return 0;
  return Math.exp(-((x - 10) * (x - 10)) / 260);
}

/** Valve lift 0..1. Intake opens near 360 (overlap) and closes past BDC. */
export function valveLift(cycle: number, intake: boolean): number {
  const deg = (cycle * 180) / Math.PI;
  const [open, close] = intake ? [350, 590] : [130, 370];
  const span = mod(close - open, 720);
  const d = mod(deg - open, 720);
  if (d > span) return 0;
  return Math.sin((Math.PI * d) / span) ** 1.4;
}
