/**
 * Crankshaft, rods, pistons and combustion. Shared by every layout: each
 * cylinder is a frame (position on the crank, bore axis angle, firing phase)
 * from kinematics.ts, so an inline four and a boxer are the same code.
 *
 * Only visible in the cutaway, which is the point of the cutaway: this is the
 * machine the twin is modelling, moving at (slowed) true kinematics.
 */

import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useLive } from './live';
import { useMats, GLOW_FAULT, GLOW_SENSOR } from './mats';
import { combustion, pinOffset, slider, type CylFrame, type Geometry } from './kinematics';
import { cylX } from './geom';

function Piston({ g, f }: { g: Geometry; f: CylFrame }) {
  const live = useLive();
  const m = useMats();
  const piston = useRef<THREE.Group>(null);
  const rod = useRef<THREE.Group>(null);
  const flame = useRef<THREE.Mesh>(null);
  const flameMat = useMemo(
    () => new THREE.MeshBasicMaterial({ color: '#ffb35c', transparent: true, opacity: 0, toneMapped: false, depthWrite: false, blending: THREE.AdditiveBlending }),
    [],
  );
  const ringMat = useMemo(() => m.int.ring.clone(), [m]);
  const pr = g.bore / 2 - 0.006;
  const geo = useMemo(() => ({
    wristPin: cylX(0.07, g.bore * 0.72, 20),
    bigEnd: cylX(0.13, 0.13, 28),
    smallEnd: cylX(0.085, 0.1, 22),
  }), [g.bore]);

  useFrame(() => {
    const st = slider(g, f, live.crank);
    const ax = Math.sin(f.psi), ay = Math.cos(f.psi);
    if (piston.current) piston.current.position.set(f.x, ay * st.s, ax * st.s);
    if (rod.current) {
      rod.current.position.set(f.x, st.pinY, st.pinZ);
      rod.current.rotation.x = st.beta;
    }
    // Flame: brightest just after firing TDC, gone by 60 degrees.
    const chamber = live.parts.get(`chamber:${f.index}`);
    const fire = live.running ? combustion(st.cycle) : 0;
    flameMat.opacity = Math.min(1, fire * (0.55 + 0.45 * live.cut));
    if (chamber) flameMat.color.set('#ffb35c').lerp(chamber.sensor ? GLOW_SENSOR : GLOW_FAULT, chamber.p);
    if (flame.current) {
      const top = g.deck - 0.02;
      flame.current.position.set(f.x, ay * top, ax * top);
      const k = 0.35 + fire * 0.65;
      flame.current.scale.set(pr * k, 0.05 + fire * 0.06, pr * k);
      flame.current.rotation.x = f.psi;
    }
    const ring = live.parts.get(`piston:${f.index}`);
    ringMat.emissive.copy(GLOW_FAULT);
    ringMat.emissiveIntensity = ring ? ring.p * 2.5 : 0;
  });

  return (
    <>
      {/* piston: body, three ring lands, and the wrist pin boss */}
      <group ref={piston}>
        <group rotation={[f.psi, 0, 0]}>
          <mesh position={[0, g.compH - g.pistonH / 2, 0]} material={m.int.piston} castShadow>
            <cylinderGeometry args={[pr, pr * 0.985, g.pistonH, 40]} />
          </mesh>
          {[0.06, 0.1, 0.14].map((d) => (
            <mesh key={d} position={[0, g.compH - d * g.bore * 1.2, 0]} rotation={[Math.PI / 2, 0, 0]} material={ringMat}>
              <torusGeometry args={[pr + 0.001, 0.008, 6, 40]} />
            </mesh>
          ))}
          <mesh geometry={geo.wristPin} material={m.int.steel} />
        </group>
      </group>

      {/* connecting rod: big end on the pin, I-beam up to the wrist pin */}
      <group ref={rod}>
        <mesh position={[0, g.rod / 2, 0]} material={m.int.steelDark} castShadow>
          <boxGeometry args={[0.06, g.rod - 0.12, 0.12]} />
        </mesh>
        <mesh geometry={geo.bigEnd} material={m.int.steelDark} />
        <mesh position={[0, g.rod, 0]} geometry={geo.smallEnd} material={m.int.steelDark} />
      </group>

      <mesh ref={flame} material={flameMat}>
        <sphereGeometry args={[1, 24, 12]} />
      </mesh>
    </>
  );
}

function Crankshaft({ g, frames, length }: { g: Geometry; frames: CylFrame[]; length: number }) {
  const live = useLive();
  const m = useMats();
  const ref = useRef<THREE.Group>(null);
  const bearingMat = useMemo(() => m.int.bearing.clone(), [m]);
  useFrame(() => {
    if (ref.current) ref.current.rotation.x = live.crank;
    const b = live.parts.get('bearings');
    bearingMat.emissive.copy(GLOW_FAULT);
    bearingMat.emissiveIntensity = b ? b.p * 2.4 * (0.8 + 0.2 * Math.sin(live.time * 4.2)) : 0;
  });
  const journal = useMemo(() => cylX(0.13, length, 32), [length]);
  const pin = useMemo(() => cylX(0.11, 0.2, 28), []);
  const shell = useMemo(() => cylX(0.155, 0.07, 32), []);
  // Half-disc counterweight: theta 0..pi, then turned so its axis is X and
  // the half points at -Y, opposite the pin.
  const counterweight = useMemo(() => {
    const cw = new THREE.CylinderGeometry(g.r * 0.95 + 0.12, g.r * 0.95 + 0.12, 0.07, 28, 1, false, 0, Math.PI);
    cw.rotateZ(-Math.PI / 2);
    return cw;
  }, [g.r]);
  const web = 0.07;
  // Main bearings sit between throws (inline) or pairs (boxer).
  const mains = useMemo(() => {
    const xs = [...new Set(frames.map((f) => f.x))].sort((a, b) => a - b);
    const out = [xs[0] - g.pitch / 2];
    for (let i = 0; i < xs.length - 1; i++) out.push((xs[i] + xs[i + 1]) / 2);
    out.push(xs[xs.length - 1] + g.pitch / 2);
    return out;
  }, [frames, g.pitch]);

  return (
    <>
      <group ref={ref}>
        <mesh geometry={journal} material={m.int.steel} castShadow />
        {frames.map((f) => (
          <group key={f.index} position={[f.x, 0, 0]} rotation={[pinOffset(f), 0, 0]}>
            <mesh position={[0, g.r, 0]} geometry={pin} material={m.int.steel} />
            {[-1, 1].map((sgn) => (
              <group key={sgn} position={[sgn * (0.1 + web / 2), 0, 0]}>
                {/* web up to the pin */}
                <mesh position={[0, g.r / 2, 0]} material={m.int.steelDark} castShadow>
                  <boxGeometry args={[web, g.r + 0.26, 0.3]} />
                </mesh>
                {/* counterweight opposite the pin */}
                <mesh geometry={counterweight} material={m.int.steelDark} />
              </group>
            ))}
          </group>
        ))}
      </group>
      {mains.map((x) => (
        <mesh key={x} position={[x, 0, 0]} geometry={shell} material={bearingMat} />
      ))}
    </>
  );
}

export function Internals({ g, crankLength }: { g: Geometry; crankLength: number }) {
  return (
    <group>
      <Crankshaft g={g} frames={g.frames} length={crankLength} />
      {g.frames.map((f) => <Piston key={f.index} g={g} f={f} />)}
    </group>
  );
}
