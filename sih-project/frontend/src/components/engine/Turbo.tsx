/**
 * Turbocharger. Shaft along +X: hot turbine side at -X, cold compressor side
 * at +X with its inlet open so the wheel is visible spinning.
 *
 * Both housings are real scrolls (volutes): the passage spirals once round
 * the wheel and grows toward its outlet. The old model used a torus, which
 * no turbo has.
 */

import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useLive } from './live';
import { useMats, applyGlow, cloneClipped } from './mats';
import { cylX, volute } from './geom';

const TURN = Math.PI * 1.85;

/** Scroll plus a straight stub continuing its end, turned so the stub points +Y. */
function Scroll({ rMean, t0, t1, stub, material }: { rMean: number; t0: number; t1: number; stub: number; material: THREE.Material }) {
  const geo = useMemo(() => volute(rMean, t0, t1, TURN), [rMean, t0, t1]);
  const r = rMean + (t1 - t0) * 0.9;
  const tangentAngle = TURN + Math.PI / 2;
  // Stub centre: the scroll's end, pushed half a stub along its tangent.
  const cy = Math.cos(TURN) * r + Math.cos(tangentAngle) * stub / 2;
  const cz = Math.sin(TURN) * r + Math.sin(tangentAngle) * stub / 2;
  return (
    <group rotation={[-tangentAngle, 0, 0]}>
      <mesh geometry={geo} material={material} castShadow />
      <mesh position={[0, cy, cz]} rotation={[tangentAngle, 0, 0]} material={material} castShadow>
        <cylinderGeometry args={[t1, t1, stub, 20, 1, false]} />
      </mesh>
    </group>
  );
}

export function Turbo({ position, rotation }: { position: [number, number, number]; rotation?: [number, number, number] }) {
  const live = useLive();
  const m = useMats();
  const wheel = useRef<THREE.Group>(null);
  const comp = useMemo(() => cloneClipped(m.castAlu), [m]);
  const hot = useMemo(() => cloneClipped(m.castIron), [m]);
  const baseAlu = useMemo(() => m.castAlu.color.clone(), [m]);
  const scorched = useMemo(() => new THREE.Color('#8a5a24'), []);

  const blade = useMemo(() => {
    // A swept blade: thin plate twisted along its span.
    const g = new THREE.BoxGeometry(0.1, 0.1, 0.008, 6, 6, 1);
    const p = g.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), y = p.getY(i);
      const twist = (x + 0.05) * 5.5;
      const z = p.getZ(i);
      p.setZ(i, z * Math.cos(twist) - y * 0.15 * Math.sin(twist));
      p.setY(i, y + 0.05);
    }
    g.computeVertexNormals();
    return g;
  }, []);

  const geo = useMemo(() => ({
    turbineHub: cylX(0.17, 0.2, 32),
    turbineFlange: cylX(0.15, 0.06, 32),
    chra: cylX(0.11, 0.26, 28),
    chraRib: cylX(0.118, 0.012, 28),
    compHub: cylX(0.16, 0.18, 32),
    wgCan: cylX(0.075, 0.13, 24),
    wgRod: cylX(0.01, 0.2, 8),
  }), []);

  useFrame((_, dt) => {
    if (wheel.current) wheel.current.rotation.x += Math.min(dt, 0.05) * 2 * Math.PI * 1.6 * (live.turboRpm / 150000);
    // Compressor fouling/erosion discolours the cold housing.
    const degraded = THREE.MathUtils.clamp((1 - live.effScale) * 6, 0, 1);
    comp.color.copy(baseAlu).lerp(scorched, degraded);
    const h = live.parts.get('turbo');
    if (h) applyGlow(comp, h.p, h.sensor, live.time);
    else comp.emissiveIntensity = 0;
    // Turbine housing runs visibly hot in proportion to mean EGT.
    const egt = live.egt.reduce((a, b) => a + b, 0) / Math.max(live.egt.length, 1);
    const heat = THREE.MathUtils.clamp((egt - 500) / 450, 0, 1);
    hot.emissive.set('#ff4a14');
    hot.emissiveIntensity = heat * heat * 0.9;
  });

  return (
    <group position={position} rotation={rotation}>
      {/* turbine: scroll, hub, axial outlet flange */}
      <group position={[-0.3, 0, 0]}>
        <Scroll rMean={0.2} t0={0.07} t1={0.125} stub={0.24} material={hot} />
        <mesh geometry={geo.turbineHub} material={hot} castShadow />
        <mesh position={[-0.16, 0, 0]} geometry={geo.turbineFlange} material={m.steelDark} />
      </group>

      {/* gold heat shield between the hot side and everything else */}
      <mesh position={[-0.3, 0.04, 0]} rotation={[0, 0, Math.PI / 2]} material={m.goldFoil}>
        <cylinderGeometry args={[0.36, 0.36, 0.3, 36, 1, true, Math.PI * 0.15, Math.PI * 0.85]} />
      </mesh>

      {/* centre housing: bearings, oil feed, coolant jacket lines */}
      <mesh geometry={geo.chra} material={m.steelDark} castShadow />
      {[-0.06, 0, 0.06].map((x) => (
        <mesh key={x} position={[x, 0, 0]} geometry={geo.chraRib} material={m.machined} />
      ))}
      <mesh position={[0, 0.2, 0]} material={m.steel}>
        <cylinderGeometry args={[0.022, 0.022, 0.2, 10]} />
      </mesh>
      <mesh position={[0, 0.3, 0]} material={m.anoRed}>
        <cylinderGeometry args={[0.035, 0.035, 0.05, 12]} />
      </mesh>

      {/* compressor: scroll, inlet bellmouth, spinning wheel */}
      <group position={[0.3, 0, 0]}>
        <Scroll rMean={0.21} t0={0.06} t1={0.115} stub={0.28} material={comp} />
        <mesh geometry={geo.compHub} material={comp} castShadow />
        <mesh position={[0.2, 0, 0]} material={m.machined} rotation={[0, 0, -Math.PI / 2]}>
          <cylinderGeometry args={[0.15, 0.13, 0.22, 36, 1, true]} />
        </mesh>
        <mesh position={[0.31, 0, 0]} rotation={[0, Math.PI / 2, 0]} material={m.machined}>
          <torusGeometry args={[0.15, 0.018, 10, 40]} />
        </mesh>
        <group ref={wheel} position={[0.14, 0, 0]}>
          {Array.from({ length: 12 }, (_, i) => (
            <group key={i} rotation={[(i / 12) * Math.PI * 2, 0, 0]}>
              <mesh geometry={blade} material={m.int.steel} scale={i % 2 ? [0.8, 0.8, 1] : 1} />
            </group>
          ))}
          <mesh rotation={[0, 0, -Math.PI / 2]} material={m.int.steel}>
            <coneGeometry args={[0.05, 0.12, 20]} />
          </mesh>
        </group>
      </group>

      {/* wastegate actuator can and rod */}
      <group position={[-0.05, -0.1, 0.3]}>
        <mesh geometry={geo.wgCan} material={m.steelDark} castShadow />
        <mesh position={[-0.16, 0, 0]} geometry={geo.wgRod} material={m.steel} />
      </group>
    </group>
  );
}
