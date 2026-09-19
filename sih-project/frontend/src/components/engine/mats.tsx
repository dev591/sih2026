/**
 * The material library: one instance of each finish, shared by every part.
 * Exterior finishes are clipped by the cutaway plane; internals never are, so
 * moving parts stay whole in the section view.
 *
 * A real engine is five or six visibly different finishes, and reading them
 * apart is most of what separates hardware from a diagram.
 */

import { createContext, useContext, useMemo, type ReactNode } from 'react';
import * as THREE from 'three';
import { useLive } from './live';
import { brushed, castGrain } from './textures';

type Phys = THREE.MeshPhysicalMaterial;

export interface Mats {
  paint: Phys;
  castAlu: Phys;
  machined: Phys;
  steel: Phys;
  steelDark: Phys;
  castIron: Phys;
  exhaust: Phys;
  rubber: Phys;
  silicone: Phys;
  anoRed: Phys;
  anoBlue: Phys;
  goldFoil: Phys;
  black: Phys;
  core: Phys;
  oil: Phys;
  int: { steel: Phys; steelDark: Phys; piston: Phys; ring: Phys; gear: Phys; bearing: Phys };
}

const Ctx = createContext<Mats | null>(null);

export function MatsProvider({ children }: { children: ReactNode }) {
  const live = useLive();
  const mats = useMemo<Mats>(() => {
    const clippingPlanes = [live.plane];
    const grain = castGrain();
    const brush = brushed();
    const ext = (o: THREE.MeshPhysicalMaterialParameters) => new THREE.MeshPhysicalMaterial({ clippingPlanes, ...o });
    const int = (o: THREE.MeshPhysicalMaterialParameters) => new THREE.MeshPhysicalMaterial(o);
    return {
      paint: ext({
        color: '#56606c', metalness: 0.35, roughness: 0.55,
        roughnessMap: grain, bumpMap: grain, bumpScale: 0.3,
        clearcoat: 0.4, clearcoatRoughness: 0.45,
      }),
      castAlu: ext({
        color: '#a7adb5', metalness: 0.85, roughness: 0.56,
        roughnessMap: grain, bumpMap: grain, bumpScale: 0.45,
      }),
      machined: ext({ color: '#d3d8de', metalness: 0.9, roughness: 0.34, roughnessMap: brush, anisotropy: 0.6 }),
      steel: ext({ color: '#dadee4', metalness: 1, roughness: 0.16 }),
      steelDark: ext({ color: '#5e656f', metalness: 0.9, roughness: 0.34 }),
      castIron: ext({
        color: '#40444b', metalness: 0.78, roughness: 0.72,
        roughnessMap: grain, bumpMap: grain, bumpScale: 0.55,
      }),
      exhaust: ext({ color: '#ffffff', vertexColors: true, metalness: 0.92, roughness: 0.32, side: THREE.DoubleSide }),
      rubber: ext({ color: '#16191d', metalness: 0, roughness: 0.75, clearcoat: 0.15, side: THREE.DoubleSide }),
      silicone: ext({ color: '#2158a8', metalness: 0, roughness: 0.52, clearcoat: 0.3, side: THREE.DoubleSide }),
      anoRed: ext({ color: '#c62d20', metalness: 0.75, roughness: 0.3, clearcoat: 0.6 }),
      anoBlue: ext({ color: '#2263d4', metalness: 0.75, roughness: 0.3, clearcoat: 0.6 }),
      goldFoil: ext({ color: '#e2b24c', metalness: 1, roughness: 0.3, roughnessMap: brush, bumpMap: grain, bumpScale: 0.7 }),
      black: ext({ color: '#131519', metalness: 0.2, roughness: 0.5 }),
      core: ext({ color: '#80878f', metalness: 0.9, roughness: 0.45, roughnessMap: brush }),
      oil: ext({ color: '#9a6614', metalness: 0, roughness: 0.06, transparent: true, opacity: 0.88, clearcoat: 1 }),
      int: {
        steel: int({ color: '#dfe3e9', metalness: 1, roughness: 0.2 }),
        steelDark: int({ color: '#6b727c', metalness: 0.95, roughness: 0.3 }),
        piston: int({ color: '#c6cbd2', metalness: 0.95, roughness: 0.34, roughnessMap: brush }),
        ring: int({ color: '#2b2f35', metalness: 0.9, roughness: 0.3 }),
        gear: int({ color: '#bcc1c9', metalness: 1, roughness: 0.25, roughnessMap: brush }),
        bearing: int({ color: '#c99c44', metalness: 1, roughness: 0.3 }),
      },
    };
  }, [live.plane]);
  return <Ctx.Provider value={mats}>{children}</Ctx.Provider>;
}

/**
 * Material.clone() deep-copies clippingPlanes, so a plain clone stops
 * following the cutaway plane. Every per-part copy of an exterior finish must
 * share the live plane array instead.
 */
export function cloneClipped<T extends THREE.Material>(src: T): T {
  const c = src.clone() as T;
  c.clippingPlanes = src.clippingPlanes;
  return c;
}

export function useMats(): Mats {
  const m = useContext(Ctx);
  if (!m) throw new Error('useMats outside MatsProvider');
  return m;
}

/**
 * Oxide temper colours of hot-worked steel, 0 = bare, 1 = fully blued: the
 * straw, bronze, purple, blue sequence every real exhaust header shows,
 * strongest at the port and fading downstream.
 */
const TEMPER: [number, THREE.Color][] = [
  [0, new THREE.Color('#9ea3ab')], [0.25, new THREE.Color('#b8a47c')], [0.45, new THREE.Color('#9d6b3d')],
  [0.65, new THREE.Color('#5e3b64')], [0.85, new THREE.Color('#304b88')], [1, new THREE.Color('#4069a6')],
];

export function temperColour(t: number, out = new THREE.Color()): THREE.Color {
  for (let i = 1; i < TEMPER.length; i++) {
    if (t <= TEMPER[i][0]) {
      const [a0, c0] = TEMPER[i - 1];
      const [a1, c1] = TEMPER[i];
      return out.copy(c0).lerp(c1, (t - a0) / (a1 - a0));
    }
  }
  return out.copy(TEMPER[TEMPER.length - 1][1]);
}

/** Vertex-colour a tube with the temper gradient (u = 0 at the port). */
export function paintTemper(geo: THREE.TubeGeometry, from = 0.95, to = 0.1): THREE.TubeGeometry {
  const { tubularSegments, radialSegments } = geo.parameters;
  const colors = new Float32Array(geo.attributes.position.count * 3);
  const c = new THREE.Color();
  for (let i = 0; i <= tubularSegments; i++) {
    temperColour(from + (to - from) * (i / tubularSegments), c);
    for (let j = 0; j <= radialSegments; j++) {
      const k = (i * (radialSegments + 1) + j) * 3;
      colors[k] = c.r; colors[k + 1] = c.g; colors[k + 2] = c.b;
    }
  }
  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  return geo;
}

const _amber = new THREE.Color('#c9761f');
const _red = new THREE.Color('#a83226');
/** Bare metal until hot, then amber, then red. */
export function thermal(base: THREE.Color, t: number, out: THREE.Color): THREE.Color {
  const x = THREE.MathUtils.clamp(t, 0, 1);
  return x < 0.5 ? out.copy(base).lerp(_amber, x * 2) : out.copy(_amber).lerp(_red, (x - 0.5) * 2);
}

export const GLOW_FAULT = new THREE.Color('#ff7a1a');
export const GLOW_SENSOR = new THREE.Color('#22d3ee');

/** Apply a part highlight to a material's emissive. Returns the strength. */
export function applyGlow(mat: THREE.MeshPhysicalMaterial | THREE.MeshStandardMaterial, p: number, sensor: boolean, time: number, peak = 2.4): number {
  const pulse = 0.82 + 0.18 * Math.sin(time * 4.2);
  const k = p * p * pulse;
  mat.emissive.copy(sensor ? GLOW_SENSOR : GLOW_FAULT);
  mat.emissiveIntensity = k * peak;
  return k;
}
