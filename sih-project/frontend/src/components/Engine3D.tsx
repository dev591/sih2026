/**
 * PRAMANA — the interactive 3D engine.
 *
 * BUILT FROM PRIMITIVES, DELIBERATELY. No downloaded GLB.
 *
 * A model off Sketchfab arrives as a few hundred unnamed meshes, and you lose
 * hours in the console working out which one is cylinder two. It is also ~40 MB
 * and it drags the frame rate. Composed this way, EVERY MESH IS A VARIABLE WE
 * NAMED, binding health state to appearance is three lines, and it looks
 * deliberate rather than like a stock asset dropped into a student project.
 *
 * Crank and pistons animate at scaled RPM. That single touch is what makes
 * people say "twin" instead of "diagram".
 */

import { useMemo, useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls, Html, RoundedBox } from '@react-three/drei';
import * as THREE from 'three';
import { useMission, useCurrentTick, useMissionTime } from '../state/missionStore';
import { N_CYL } from '../types/telemetry';

// ---------------------------------------------------------------------------
// Health → appearance. The whole point of naming our own meshes.
// ---------------------------------------------------------------------------

/**
 * Steel → amber → red thermal ramp.
 *
 * Anchored so a HEALTHY engine reads as bare metal. Normal cruise CHT on this
 * engine is ~128-140 degC against a 200 degC limit, so the ramp stays cold
 * until 150 and only saturates as the real limit is approached. If a healthy
 * engine glows on screen, the colour carries no information when a real fault
 * arrives — which is the whole reason it is here.
 */
function thermalRamp(cht: number, lo = 150, hi = 205): THREE.Color {
  const x = THREE.MathUtils.clamp((cht - lo) / (hi - lo), 0, 1);
  const steel = new THREE.Color('#8a94a3');
  const amber = new THREE.Color('#d98324');
  const red = new THREE.Color('#c0392b');
  return x < 0.5
    ? steel.clone().lerp(amber, x * 2)
    : amber.clone().lerp(red, (x - 0.5) * 2);
}

/** Amber for a component fault, cyan for an instrumentation fault.
 *  Two different colours because they are two different KINDS of problem —
 *  and telling them apart is the entire differentiator. */
function faultColour(p: number, isSensor: boolean): THREE.Color {
  const base = new THREE.Color('#000000');
  const target = new THREE.Color(isSensor ? '#22d3ee' : '#f59e0b');
  return base.lerp(target, THREE.MathUtils.clamp(p, 0, 1));
}

const CYL_SPACING = 1.35;
const CYL_X = (i: number) => (i - (N_CYL - 1) / 2) * CYL_SPACING;

// ---------------------------------------------------------------------------
// One cylinder: barrel + stacked cooling fins + head + piston + con-rod
// ---------------------------------------------------------------------------
interface CylinderProps {
  index: number;
  cht: number;
  anomaly: number;
  faultProb: number;
  isSensorFault: boolean;
  selected: boolean;
  onSelect: () => void;
  crankAngle: number;
}

function Cylinder({
  index, cht, anomaly, faultProb, isSensorFault, selected, onSelect, crankAngle,
}: CylinderProps) {
  const pistonRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);

  // Four-stroke firing order 1-3-4-2, each cylinder offset by 180 deg of crank.
  const phase = [0, Math.PI, Math.PI * 1.5, Math.PI * 0.5][index];

  useFrame(() => {
    if (!pistonRef.current) return;
    // Simple slider-crank: good enough to read as motion, cheap enough to be free.
    const theta = crankAngle + phase;
    const stroke = 0.42;
    pistonRef.current.position.y = 0.35 + stroke * Math.cos(theta) * 0.5;
  });

  const bodyColour = useMemo(() => thermalRamp(cht), [cht]);
  const emissive = useMemo(
    () => faultColour(faultProb, isSensorFault),
    [faultProb, isSensorFault]
  );

  const fins = useMemo(() => Array.from({ length: 7 }, (_, k) => 0.02 + k * 0.115), []);

  return (
    <group
      position={[CYL_X(index), 0, 0]}
      onClick={(e) => { e.stopPropagation(); onSelect(); }}
      onPointerOver={(e) => { e.stopPropagation(); setHovered(true); }}
      onPointerOut={() => setHovered(false)}
    >
      {/* barrel */}
      <mesh position={[0, 0.45, 0]} castShadow>
        <cylinderGeometry args={[0.34, 0.36, 0.92, 32]} />
        <meshStandardMaterial
          color={bodyColour}
          emissive={emissive}
          emissiveIntensity={0.15 + 0.85 * anomaly}
          metalness={0.65}
          roughness={0.42}
        />
      </mesh>

      {/* stacked cooling fins — torus rings, the detail that reads as "engine" */}
      {fins.map((y, k) => (
        <mesh key={k} position={[0, 0.06 + y * 0.8, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.4, 0.028, 8, 40]} />
          <meshStandardMaterial
            color={bodyColour}
            emissive={emissive}
            emissiveIntensity={0.1 + 0.6 * anomaly}
            metalness={0.7}
            roughness={0.5}
          />
        </mesh>
      ))}

      {/* head */}
      <mesh position={[0, 1.0, 0]}>
        <cylinderGeometry args={[0.38, 0.34, 0.26, 32]} />
        <meshStandardMaterial
          color={bodyColour}
          emissive={emissive}
          emissiveIntensity={0.2 + 0.8 * anomaly}
          metalness={0.6}
          roughness={0.38}
        />
      </mesh>

      {/* spark/injector boss */}
      <mesh position={[0.22, 1.16, 0]} rotation={[0, 0, -0.4]}>
        <cylinderGeometry args={[0.055, 0.055, 0.22, 12]} />
        <meshStandardMaterial color="#2f3540" metalness={0.9} roughness={0.3} />
      </mesh>

      {/* piston — animates at scaled RPM */}
      <mesh ref={pistonRef} position={[0, 0.35, 0]}>
        <cylinderGeometry args={[0.3, 0.3, 0.24, 24]} />
        <meshStandardMaterial color="#b9c0cb" metalness={0.95} roughness={0.18} />
      </mesh>

      {/* selection / hover ring */}
      {(selected || hovered) && (
        <mesh position={[0, 0.02, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.5, 0.018, 8, 48]} />
          <meshBasicMaterial color={selected ? '#38bdf8' : '#64748b'} />
        </mesh>
      )}

      {/* cylinder number, always facing the camera */}
      <Html position={[0, 1.52, 0]} center distanceFactor={4.2}>
        <div
          style={{
            fontFamily: 'ui-monospace, Menlo, monospace',
            fontSize: 11,
            fontVariantNumeric: 'tabular-nums',
            color: faultProb > 0.4 ? (isSensorFault ? '#22d3ee' : '#fbbf24') : '#94a3b8',
            background: 'rgba(9,12,17,0.82)',
            padding: '2px 6px',
            borderRadius: 3,
            border: `1px solid ${faultProb > 0.4 ? (isSensorFault ? '#22d3ee' : '#fbbf24') : '#334155'}`,
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
          }}
        >
          {index + 1} · {cht.toFixed(0)}°C
        </div>
      </Html>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Crankcase, crankshaft, turbo, exhaust
// ---------------------------------------------------------------------------
function Crankcase() {
  return (
    <RoundedBox args={[N_CYL * CYL_SPACING + 0.5, 0.62, 1.0]} radius={0.12} smoothness={4} position={[0, -0.34, 0]}>
      <meshStandardMaterial color="#3a424f" metalness={0.7} roughness={0.45} />
    </RoundedBox>
  );
}

function Crankshaft({ crankAngle }: { crankAngle: number }) {
  const ref = useRef<THREE.Group>(null);
  useFrame(() => {
    if (ref.current) ref.current.rotation.x = crankAngle;
  });
  return (
    <group ref={ref} position={[0, -0.34, 0]}>
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.09, 0.09, N_CYL * CYL_SPACING + 0.8, 20]} />
        <meshStandardMaterial color="#8e97a5" metalness={0.95} roughness={0.22} />
      </mesh>
      {Array.from({ length: N_CYL }, (_, i) => (
        <mesh key={i} position={[CYL_X(i), 0.16, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.07, 0.07, 0.2, 16]} />
          <meshStandardMaterial color="#a8b0bd" metalness={0.9} roughness={0.28} />
        </mesh>
      ))}
    </group>
  );
}

/** Torus + cone, spinning at scaled turbo speed. Compressor degradation is
 *  invisible at sea level and mission-limiting above critical altitude —
 *  worth having on screen so the story has somewhere to point. */
function Turbocharger({ rpm, health }: { rpm: number; health: number }) {
  const ref = useRef<THREE.Group>(null);
  useFrame((_, dt) => {
    if (ref.current) ref.current.rotation.x += dt * (rpm / 118400) * 22;
  });
  const colour = health > 0.9 ? '#5b6675' : '#a8722c';
  return (
    <group position={[N_CYL * CYL_SPACING * 0.5 + 0.72, 0.05, 0.62]}>
      <group ref={ref}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <torusGeometry args={[0.3, 0.14, 14, 32]} />
          <meshStandardMaterial color={colour} metalness={0.85} roughness={0.3} />
        </mesh>
        <mesh rotation={[0, 0, -Math.PI / 2]} position={[0.2, 0, 0]}>
          <coneGeometry args={[0.2, 0.3, 18]} />
          <meshStandardMaterial color="#c2c8d2" metalness={0.95} roughness={0.2} />
        </mesh>
      </group>
      <Html position={[0, 0.58, 0]} center distanceFactor={4.2}>
        <div style={{
          fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 10,
          color: '#64748b', whiteSpace: 'nowrap', pointerEvents: 'none',
        }}>
          TURBO {(rpm / 1000).toFixed(0)}k
        </div>
      </Html>
    </group>
  );
}

/** Exhaust runners as tube geometry along a curve — one per cylinder,
 *  merging into a collector that feeds the turbine. */
function ExhaustManifold({ egt }: { egt: number[] }) {
  const tubes = useMemo(() => {
    return Array.from({ length: N_CYL }, (_, i) => {
      const start = new THREE.Vector3(CYL_X(i), 1.02, 0.3);
      const mid = new THREE.Vector3(CYL_X(i) + 0.25, 0.75, 0.75);
      const end = new THREE.Vector3(N_CYL * CYL_SPACING * 0.5 + 0.72, 0.05, 0.62);
      const curve = new THREE.CatmullRomCurve3([
        start,
        mid,
        new THREE.Vector3((CYL_X(i) + end.x) / 2, 0.35, 0.8),
        end,
      ]);
      return new THREE.TubeGeometry(curve, 40, 0.075, 12, false);
    });
  }, []);

  return (
    <>
      {tubes.map((geo, i) => {
        // Runner glows with its own cylinder's exhaust temperature — so the
        // fouled cylinder is visible in the pipework, not just the barrel.
        // Anchored just above the healthy cruise value (~818 degC) so the
        // manifold sits dark until a cylinder actually departs from its
        // siblings. A permanently red manifold tells you nothing.
        const heat = THREE.MathUtils.clamp((egt[i] - 835) / 130, 0, 1);
        return (
          <mesh key={i} geometry={geo}>
            <meshStandardMaterial
              color={new THREE.Color('#4b5563').lerp(new THREE.Color('#dc2626'), heat)}
              emissive={new THREE.Color('#dc2626')}
              emissiveIntensity={heat * 0.55}
              metalness={0.75}
              roughness={0.45}
            />
          </mesh>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// Scene
// ---------------------------------------------------------------------------
function EngineScene() {
  const tick = useCurrentTick();
  const t = useMissionTime();
  const selected = useMission((s) => s.selectedCylinder);
  const selectCylinder = useMission((s) => s.selectCylinder);
  const crankRef = useRef(0);
  const [crankAngle, setCrankAngle] = useState(0);

  const rpm = tick.slow.rpm;
  useFrame((_, dt) => {
    // Scaled down heavily — 3580 rpm at real speed is an unreadable blur.
    crankRef.current += dt * (rpm / 60) * 0.16 * Math.PI * 2;
    setCrankAngle(crankRef.current);
  });

  const { diagnosis, anomaly } = tick.health;

  // Per-cylinder fault probability, from the top diagnosis hypothesis.
  const cylFault = Array.from({ length: N_CYL }, (_, i) => {
    const hit = diagnosis.top.find((h) => h.cylinder === i && h.fault !== 'healthy');
    return hit ? hit.p : 0;
  });
  const cylSensorFault = Array.from({ length: N_CYL }, (_, i) => {
    const hit = diagnosis.top.find((h) => h.cylinder === i && h.fault !== 'healthy');
    return hit ? hit.fault.includes('sensor') : false;
  });

  return (
    <>
      <ambientLight intensity={0.55} />
      <directionalLight position={[5, 8, 5]} intensity={1.15} castShadow />
      <directionalLight position={[-6, 3, -4]} intensity={0.35} color="#7dd3fc" />
      <pointLight position={[0, 2.5, 3]} intensity={0.5} />

      <group position={[0, -0.2, 0]}>
        <Crankcase />
        <Crankshaft crankAngle={crankAngle} />
        {Array.from({ length: N_CYL }, (_, i) => (
          <Cylinder
            key={i}
            index={i}
            cht={tick.slow.cht_C[i]}
            anomaly={cylFault[i] > 0.3 ? anomaly.score : anomaly.score * 0.12}
            faultProb={cylFault[i]}
            isSensorFault={cylSensorFault[i]}
            selected={selected === i}
            onSelect={() => selectCylinder(selected === i ? null : i)}
            crankAngle={crankAngle}
          />
        ))}
        <ExhaustManifold egt={tick.slow.egt_C} />
        <Turbocharger
          rpm={tick.slow.turbo_rpm}
          health={tick.health.theta.eta_c_scale.value}
        />
      </group>

      {/* ground plane — grounds the object without stealing attention */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.95, 0]} receiveShadow>
        <planeGeometry args={[24, 24]} />
        <meshStandardMaterial color="#0b0f16" metalness={0.1} roughness={0.95} />
      </mesh>
      <gridHelper args={[24, 24, '#1e293b', '#141b26']} position={[0, -0.94, 0]} />

      <OrbitControls
        enablePan={false}
        minDistance={3.4}
        maxDistance={13}
        maxPolarAngle={Math.PI / 2.05}
        target={[0, 0.2, 0]}
      />
      {/* t is read so the scene re-renders on scrub even while paused */}
      <group visible={false}><mesh name={`t-${t.toFixed(0)}`} /></group>
    </>
  );
}

export function Engine3D() {
  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <Canvas
        shadows
        camera={{ position: [0.6, 2.1, 5.9], fov: 40 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
      >
        <color attach="background" args={['#080b11']} />
        <fog attach="fog" args={['#080b11', 11, 22]} />
        <EngineScene />
      </Canvas>
      <div
        style={{
          position: 'absolute', bottom: 8, left: 12,
          fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 10,
          color: '#475569', pointerEvents: 'none', letterSpacing: '0.04em',
        }}
      >
        DRAG TO ORBIT · SCROLL TO ZOOM · CLICK A CYLINDER
      </div>
    </div>
  );
}
