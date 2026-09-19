/**
 * The Rotax 914 as what it is: a horizontally opposed four with air-cooled
 * finned barrels, individually liquid-cooled heads with dual spark plugs,
 * twin carburettors under an airbox pressurised by a rear-mounted turbo, and
 * a reduction gearbox to the propeller.
 *
 * Same frame/kinematics code as the inline engine: switching profile swaps
 * the architecture, not a colour.
 */

import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three-stdlib';
import { useLive } from './live';
import { useMats, applyGlow, cloneClipped, paintTemper, thermal, GLOW_FAULT } from './mats';
import { Solid } from './section';
import { Internals } from './Internals';
import { Turbo } from './Turbo';
import { Gearbox } from './Inline4';
import { circle, extrudeAlongX, roundRect, side, tube } from './geom';
import type { CylFrame, Geometry } from './kinematics';
import type { EngineProfile } from '../../config/engines';

const CASE_HALF = 0.62;
const CASE_LEN = 2.5;
const HEAD_H = 0.36;

export function boxerTagAnchor(g: Geometry, f: CylFrame): [number, number, number] {
  return [f.x, 0.95, Math.sin(f.psi) * (g.deck + HEAD_H / 2)];
}
export const BOXER_FLOOR = -1.35;

function Crankcase() {
  const m = useMats();
  const geo = useMemo(() => {
    const outer = roundRect(...side(CASE_HALF, -0.52), ...side(-CASE_HALF, 0.56), 0.16);
    const inner = roundRect(...side(CASE_HALF - 0.08, -0.44), ...side(-(CASE_HALF - 0.08), 0.48), 0.1, true);
    outer.holes.push(inner);
    const plate = roundRect(...side(CASE_HALF, -0.52), ...side(-CASE_HALF, 0.56), 0.16);
    plate.holes.push(circle(...side(0, 0), 0.15));
    return {
      shell: extrudeAlongX([outer], -CASE_LEN / 2, CASE_LEN),
      plate: extrudeAlongX([plate], -0.05, 0.1),
    };
  }, []);
  return (
    <group>
      <Solid geometry={geo.shell} material={m.castAlu} />
      <Solid geometry={geo.plate} material={m.castAlu} position={[-CASE_LEN / 2 + 0.05, 0, 0]} />
      <Solid geometry={geo.plate} material={m.castAlu} position={[CASE_LEN / 2 - 0.05, 0, 0]} />
      {/* case split line along the top */}
      <mesh position={[0, 0.565, 0]} material={m.steelDark}>
        <boxGeometry args={[CASE_LEN, 0.015, 0.02]} />
      </mesh>
    </group>
  );
}

/** Finned barrel turned on a lathe: every fin is real geometry. */
function barrelGeometry(g: Geometry): THREE.LatheGeometry {
  const ri = g.bore / 2, rb = ri + 0.06, rf = ri + 0.23;
  const h0 = CASE_HALF, h1 = g.deck;
  const pts: THREE.Vector2[] = [new THREE.Vector2(ri, h0), new THREE.Vector2(ri, h1), new THREE.Vector2(rb, h1)];
  const fins = Math.floor((h1 - h0 - 0.08) / 0.07);
  for (let k = 0; k < fins; k++) {
    const y = h1 - 0.05 - k * 0.07;
    const taper = 1 - (k / fins) * 0.25;
    pts.push(new THREE.Vector2(rb, y), new THREE.Vector2(rb + (rf - rb) * taper, y), new THREE.Vector2(rb + (rf - rb) * taper, y - 0.022), new THREE.Vector2(rb, y - 0.022));
  }
  pts.push(new THREE.Vector2(rb + 0.04, h0 + 0.03), new THREE.Vector2(rb + 0.04, h0), new THREE.Vector2(ri, h0));
  return new THREE.LatheGeometry(pts, 56);
}

function Cylinder({ g, f, onSelect, onHover }: { g: Geometry; f: CylFrame; onSelect: (i: number) => void; onHover: (i: number | null) => void }) {
  const live = useLive();
  const m = useMats();
  const head = useMemo(() => cloneClipped(m.castAlu), [m]);
  const base = useMemo(() => m.castAlu.color.clone(), [m]);
  const barrelGeo = useMemo(() => barrelGeometry(g), [g]);
  const headGeo = useMemo(() => new RoundedBoxGeometry(0.78, HEAD_H, 0.8, 4, 0.07), []);
  const edge = useRef<THREE.LineSegments>(null);
  const edgeGeo = useMemo(() => new THREE.EdgesGeometry(new THREE.BoxGeometry(1.06, g.deck + HEAD_H - CASE_HALF + 0.08, 1.06)), [g.deck]);
  const edgeMat = useMemo(() => new THREE.LineBasicMaterial({ color: '#38bdf8', transparent: true, toneMapped: false }), []);
  const plug = useMemo(() => new THREE.MeshPhysicalMaterial({ color: '#f1efe8', roughness: 0.25, clearcoat: 0.8, clippingPlanes: [live.plane] }), [live.plane]);

  useFrame(() => {
    thermal(base, (live.cht[f.index] - live.chtRampFrom) / 55, head.color);
    const h = live.parts.get(`cyl:${f.index}`) ?? live.parts.get(`cht:${f.index}`);
    if (h) applyGlow(head, h.p, h.sensor, live.time);
    else head.emissiveIntensity = 0;
    const sel = live.selected === f.index ? 1 : live.hovered === f.index ? 0.5 : 0;
    edgeMat.opacity = sel;
    if (edge.current) edge.current.visible = sel > 0;
  });

  const headMid = g.deck + HEAD_H / 2;
  return (
    <group position={[f.x, 0, 0]} rotation={[f.psi, 0, 0]}>
      <Solid geometry={barrelGeo} material={m.castAlu} />
      <Solid geometry={headGeo} material={head} position={[0, headMid, 0]} />
      {/* rocker cover */}
      <mesh position={[0, headMid, 0.42]} material={m.black}>
        <boxGeometry args={[0.7, 0.3, 0.08]} />
      </mesh>
      {/* dual ignition: two spark plugs per head, one each side */}
      {[-1, 1].map((s) => (
        <group key={s} position={[s * 0.42, headMid - 0.04, 0]} rotation={[0, 0, (s * Math.PI) / 2]}>
          <mesh position={[0, 0.08, 0]} material={plug}><cylinderGeometry args={[0.035, 0.035, 0.14, 16]} /></mesh>
          <mesh position={[0, 0.0, 0]} material={m.steel}><cylinderGeometry args={[0.05, 0.05, 0.05, 6]} /></mesh>
        </group>
      ))}
      <lineSegments ref={edge} geometry={edgeGeo} material={edgeMat} position={[0, (CASE_HALF + g.deck + HEAD_H) / 2, 0]} visible={false} />
      <mesh
        position={[0, (CASE_HALF + g.deck + HEAD_H) / 2, 0]}
        onClick={(e) => { e.stopPropagation(); onSelect(f.index); }}
        onPointerOver={(e) => { e.stopPropagation(); onHover(f.index); }}
        onPointerOut={() => onHover(null)}
      >
        <boxGeometry args={[0.95, g.deck + HEAD_H - CASE_HALF + 0.1, 1.3]} />
        <meshBasicMaterial colorWrite={false} depthWrite={false} />
      </mesh>
    </group>
  );
}

function Induction({ g }: { g: Geometry }) {
  const live = useLive();
  const m = useMats();
  const carb = useMemo(() => cloneClipped(m.castAlu), [m]);
  const mapS = useMemo(() => cloneClipped(m.black), [m]);
  const runners = useMemo(() => g.frames.map((f) => {
    const s = Math.sign(Math.sin(f.psi));
    const pairX = f.x < 0 ? -0.54 : 0.54;
    return tube([[pairX, 0.82, s * 0.25], [pairX + (f.x - pairX) * 0.5, 0.78, s * 0.9], [f.x, 0.55, s * (g.deck - 0.05)], [f.x, 0.3, s * (g.deck + 0.2)]], 0.055, 36, 12);
  }), [g]);
  useFrame(() => {
    const h = live.parts.get('fuel');
    if (h) applyGlow(carb, h.p, h.sensor, live.time); else carb.emissiveIntensity = 0;
    const s = live.parts.get('sensor:map');
    if (s) applyGlow(mapS, s.p, s.sensor, live.time, 3.5); else mapS.emissiveIntensity = 0;
  });
  return (
    <group>
      {/* pressurised airbox */}
      <mesh position={[0, 1.16, 0]} material={m.black} castShadow>
        <boxGeometry args={[1.7, 0.3, 0.62]} />
      </mesh>
      <mesh position={[-0.6, 1.36, 0.12]} material={mapS}><cylinderGeometry args={[0.05, 0.05, 0.1, 16]} /></mesh>
      {/* twin carburettors, one per pair of cylinders */}
      {[-0.54, 0.54].map((x) => (
        <group key={x} position={[x, 0.86, 0]}>
          <mesh rotation={[Math.PI / 2, 0, 0]} material={carb} castShadow><cylinderGeometry args={[0.13, 0.13, 0.5, 28]} /></mesh>
          <mesh position={[0, 0.16, 0]} material={carb}><boxGeometry args={[0.2, 0.14, 0.2]} /></mesh>
          <mesh position={[0, 0.16, 0]} material={m.rubber}><cylinderGeometry args={[0.1, 0.1, 0.18, 20]} /></mesh>
        </group>
      ))}
      {runners.map((r, i) => <mesh key={i} geometry={r} material={m.castAlu} castShadow />)}
    </group>
  );
}

function ExhaustAndTurbo({ g }: { g: Geometry }) {
  const live = useLive();
  const m = useMats();
  const pipes = useMemo(() => g.frames.map((f) => {
    const s = Math.sign(Math.sin(f.psi));
    return paintTemper(tube([[f.x, -0.25, s * (g.deck + 0.1)], [f.x, -0.62, s * (g.deck - 0.1)], [f.x * 0.6 - 0.6, -0.86, s * 0.7], [-1.62, -0.66, 0.25]], 0.055, 44, 12));
  }), [g]);
  const lambdaMat = useMemo(() => cloneClipped(m.steel), [m]);
  const geo = useMemo(() => ({
    down: paintTemper(tube([[-1.45, -0.85, 0], [-1.32, -1.08, 0.2], [-1.45, -1.28, 0.55], [-1.8, -1.3, 0.9]], 0.08, 40, 14), 0.45, 0.05),
    charge: tube([[-2.2, -0.55, 0.26], [-2.35, 0.2, 0.3], [-1.6, 1.12, 0.22], [-0.85, 1.16, 0.2]], 0.075, 60, 14),
  }), []);
  useFrame(() => {
    const h = live.parts.get('sensor:lambda');
    if (h) applyGlow(lambdaMat, h.p, h.sensor, live.time, 3.5); else lambdaMat.emissiveIntensity = 0;
  });
  return (
    <group>
      {pipes.map((p, i) => <mesh key={i} geometry={p} material={m.exhaust} castShadow />)}
      <Turbo position={[-1.9, -0.85, 0]} rotation={[0, Math.PI, 0]} />
      <mesh geometry={geo.down} material={m.exhaust} />
      <mesh geometry={geo.charge} material={m.silicone} />
      <group position={[-1.38, -1.12, 0.36]} rotation={[Math.PI / 2, 0, 0]}>
        <mesh material={lambdaMat}><cylinderGeometry args={[0.025, 0.025, 0.18, 10]} /></mesh>
      </group>
    </group>
  );
}

function Services() {
  const live = useLive();
  const m = useMats();
  const tank = useMemo(() => cloneClipped(m.castAlu), [m]);
  const rad = useMemo(() => cloneClipped(m.core), [m]);
  useFrame(() => {
    const o = live.parts.get('oil');
    if (o) applyGlow(tank, o.p, o.sensor, live.time); else tank.emissiveIntensity = 0;
    const c = live.parts.get('cooling');
    if (c) applyGlow(rad, c.p, c.sensor, live.time); else rad.emissiveIntensity = 0;
  });
  return (
    <group>
      {/* dry-sump oil tank */}
      <group position={[-1.55, 0.6, -0.95]}>
        <mesh material={tank} castShadow><cylinderGeometry args={[0.2, 0.2, 0.62, 32]} /></mesh>
        <mesh position={[0, 0.34, 0]} material={m.anoRed}><cylinderGeometry args={[0.07, 0.07, 0.06, 20]} /></mesh>
      </group>
      {/* coolant radiator for the heads */}
      <group position={[0.1, -0.95, 0]}>
        <mesh material={m.castAlu}><boxGeometry args={[1.5, 0.08, 0.7]} /></mesh>
        {Array.from({ length: 22 }, (_, i) => (
          <mesh key={i} position={[-0.7 + i * 0.066, -0.06, 0]} material={rad}>
            <boxGeometry args={[0.012, 0.06, 0.64]} />
          </mesh>
        ))}
      </group>
      {/* expansion tank */}
      <mesh position={[0.95, 1.1, -0.2]} material={m.castAlu}><cylinderGeometry args={[0.1, 0.1, 0.25, 24]} /></mesh>
    </group>
  );
}

function Halo({ f, g }: { f: CylFrame; g: Geometry }) {
  const live = useLive();
  const mat = useMemo(() => new THREE.MeshBasicMaterial({ color: GLOW_FAULT, transparent: true, opacity: 0, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending }), []);
  useFrame(() => {
    const h = live.parts.get(`cyl:${f.index}`);
    mat.opacity = h ? Math.min(0.55, h.p * 0.6) : 0;
    if (h) mat.color.set(h.sensor ? '#22d3ee' : '#ff7a1a');
  });
  return (
    <mesh position={[f.x, BOXER_FLOOR + 0.01, Math.sin(f.psi) * g.deck * 0.8]} rotation={[-Math.PI / 2, 0, 0]} material={mat}>
      <circleGeometry args={[0.65, 48]} />
    </mesh>
  );
}

export function Boxer4({ g, profile, onSelect, onHover }: {
  g: Geometry; profile: EngineProfile; onSelect: (i: number) => void; onHover: (i: number | null) => void;
}) {
  return (
    <group>
      <Crankcase />
      {g.frames.map((f) => <Cylinder key={f.index} g={g} f={f} onSelect={onSelect} onHover={onHover} />)}
      <Induction g={g} />
      <ExhaustAndTurbo g={g} />
      <Services />
      <Gearbox x0={CASE_LEN / 2} ratio={profile.gearRatio} rA={0.17} R={0.5} len={0.55} />
      <Internals g={g} crankLength={CASE_LEN + 0.2} />
      {g.frames.map((f) => <Halo key={f.index} f={f} g={g} />)}
    </group>
  );
}
