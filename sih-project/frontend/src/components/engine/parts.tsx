/**
 * Engine parts, composed from named primitives.
 *
 * NO DOWNLOADED GLB, deliberately. A Sketchfab model arrives as a few hundred
 * unnamed meshes and you lose hours working out which one is cylinder two; it
 * is also ~40 MB and it drags the frame rate. Here every mesh is a variable we
 * named, so binding live health state to material colour and emissive is three
 * lines, and it looks deliberate rather than like a stock asset dropped into a
 * student project.
 *
 * The parts vocabulary follows a real turbocharged aero-diesel: finned barrels,
 * heads with rocker covers, pushrod tubes, an intake plenum feeding runners, an
 * exhaust log merging into a turbine housing, a reduction gearbox and prop
 * flange at the nose.
 */

import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html, RoundedBox } from '@react-three/drei';
import * as THREE from 'three';
import { MAT, N_FINS, CYL_X, thermalRamp, faultColour, exhaustHeat } from './materials';

// ---------------------------------------------------------------------------
// Small reusable hardware
// ---------------------------------------------------------------------------

/** Hex-ish fastener. Cheap, and a scattering of them is most of what makes a
 *  casting read as a real part rather than an extruded shape. */
function Bolt({ position, r = 0.036, h = 0.05 }: { position: [number, number, number]; r?: number; h?: number }) {
  return (
    <mesh position={position}>
      <cylinderGeometry args={[r, r, h, 6]} />
      <meshStandardMaterial {...MAT.steelDark} />
    </mesh>
  );
}

/** A ring of bolts around a circular joint face. */
function BoltCircle({ y, radius, count, z = 0 }: { y: number; radius: number; count: number; z?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => {
        const a = (i / count) * Math.PI * 2;
        return (
          <Bolt key={i} position={[Math.cos(a) * radius, y, z + Math.sin(a) * radius]} />
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// CYLINDER ASSEMBLY — barrel, cooling fins, head, rocker cover, piston
// ---------------------------------------------------------------------------
export interface CylinderProps {
  index: number;
  count: number;
  cht: number;
  egt: number;
  anomaly: number;
  faultProb: number;
  isSensorFault: boolean;
  selected: boolean;
  hovered: boolean;
  onSelect: () => void;
  onHover: (h: boolean) => void;
  crankAngle: number;
}

export function CylinderAssembly({
  index, count, cht, anomaly, faultProb, isSensorFault,
  selected, hovered, onSelect, onHover, crankAngle,
}: CylinderProps) {
  const pistonRef = useRef<THREE.Mesh>(null);
  const rodRef = useRef<THREE.Mesh>(null);

  // Four-stroke firing order 1-3-4-2 — each cylinder offset in the cycle.
  const phase = useMemo(
    () => [0, Math.PI, Math.PI * 1.5, Math.PI * 0.5][index % 4],
    [index]
  );

  useFrame(() => {
    const theta = crankAngle + phase;
    const stroke = 0.30;
    const y = 0.30 + stroke * Math.cos(theta);
    if (pistonRef.current) pistonRef.current.position.y = y;
    if (rodRef.current) {
      rodRef.current.position.y = y - 0.30;
      rodRef.current.rotation.z = Math.sin(theta) * 0.16;
    }
  });

  const body = useMemo(() => thermalRamp(cht), [cht]);
  const glow = useMemo(() => faultColour(faultProb, isSensorFault), [faultProb, isSensorFault]);
  const glowI = 0.06 + 0.94 * anomaly;

  // Fins taper toward the head, as they do on a real air-cooled barrel —
  // the bottom of the barrel is hotter and gets more area.
  const fins = useMemo(
    () =>
      Array.from({ length: N_FINS }, (_, k) => {
        const f = k / (N_FINS - 1);
        return { y: 0.10 + f * 0.72, r: 0.475 - f * 0.075, t: 0.026 - f * 0.005 };
      }),
    []
  );

  const ring = selected ? '#38bdf8' : hovered ? '#7c8899' : null;

  return (
    <group
      position={[CYL_X(index, count), 0, 0]}
      onClick={(e) => { e.stopPropagation(); onSelect(); }}
      onPointerOver={(e) => { e.stopPropagation(); onHover(true); }}
      onPointerOut={() => onHover(false)}
    >
      {/* ---- barrel ---- */}
      <mesh position={[0, 0.46, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[0.335, 0.365, 0.94, 40]} />
        <meshStandardMaterial
          color={body} emissive={glow} emissiveIntensity={glowI}
          metalness={MAT.steelDark.metalness} roughness={MAT.steelDark.roughness}
        />
      </mesh>

      {/* ---- cooling fins ---- */}
      {fins.map((f, k) => (
        <mesh key={k} position={[0, f.y, 0]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <torusGeometry args={[f.r, f.t, 6, 44]} />
          <meshStandardMaterial
            color={body} emissive={glow} emissiveIntensity={glowI * 0.75}
            metalness={0.62} roughness={0.58}
          />
        </mesh>
      ))}

      {/* ---- cylinder head ---- */}
      <mesh position={[0, 1.02, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[0.40, 0.375, 0.24, 40]} />
        <meshStandardMaterial
          color={body} emissive={glow} emissiveIntensity={glowI}
          metalness={MAT.castAlu.metalness} roughness={MAT.castAlu.roughness}
        />
      </mesh>

      {/* ---- rocker cover: the squared-off casting on top ---- */}
      <RoundedBox args={[0.56, 0.20, 0.46]} radius={0.05} smoothness={4} position={[0, 1.23, 0]} castShadow>
        <meshStandardMaterial
          color={body} emissive={glow} emissiveIntensity={glowI * 0.85}
          metalness={MAT.castAluDark.metalness} roughness={MAT.castAluDark.roughness}
        />
      </RoundedBox>
      <Bolt position={[0.21, 1.34, 0.16]} />
      <Bolt position={[-0.21, 1.34, 0.16]} />
      <Bolt position={[0.21, 1.34, -0.16]} />
      <Bolt position={[-0.21, 1.34, -0.16]} />

      {/* ---- head bolts around the joint face ---- */}
      <BoltCircle y={1.145} radius={0.335} count={8} />

      {/* ---- injector / glow-plug boss ---- */}
      <mesh position={[0.17, 1.30, -0.02]} rotation={[0, 0, -0.34]} castShadow>
        <cylinderGeometry args={[0.052, 0.062, 0.30, 14]} />
        <meshStandardMaterial {...MAT.steel} />
      </mesh>
      <mesh position={[0.205, 1.44, -0.02]} rotation={[0, 0, -0.34]}>
        <cylinderGeometry args={[0.028, 0.028, 0.10, 10]} />
        <meshStandardMaterial {...MAT.anodised} />
      </mesh>

      {/* ---- pushrod tubes: two slim tubes down the back of the barrel ---- */}
      {[-0.17, 0.17].map((x, i) => (
        <mesh key={i} position={[x, 0.55, -0.31]} castShadow>
          <cylinderGeometry args={[0.036, 0.036, 0.92, 10]} />
          <meshStandardMaterial {...MAT.steelDark} />
        </mesh>
      ))}

      {/* ---- piston + connecting rod, animating at scaled RPM ---- */}
      <mesh ref={pistonRef} position={[0, 0.30, 0]}>
        <cylinderGeometry args={[0.30, 0.30, 0.26, 28]} />
        <meshStandardMaterial {...MAT.steel} />
      </mesh>
      <mesh ref={rodRef} position={[0, 0.0, 0]}>
        <boxGeometry args={[0.085, 0.42, 0.06]} />
        <meshStandardMaterial {...MAT.steelDark} />
      </mesh>

      {/* ---- selection ring ---- */}
      {ring && (
        <mesh position={[0, 0.04, 0]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.55, 0.016, 8, 56]} />
          <meshBasicMaterial color={ring} />
        </mesh>
      )}
    </group>
  );
}

/** Floating cylinder tag. Kept as a separate component so it renders after all
 *  geometry and never fights the meshes for depth. */
export function CylinderTag({
  index, count, cht, faultProb, isSensorFault,
}: { index: number; count: number; cht: number; faultProb: number; isSensorFault: boolean }) {
  const active = faultProb > 0.4;
  const accent = isSensorFault ? '#22d3ee' : '#fbbf24';
  return (
    <Html position={[CYL_X(index, count), 2.12, 0]} center distanceFactor={4.4}>
      <div
        style={{
          fontFamily: 'ui-monospace, Menlo, monospace',
          fontSize: 11,
          fontVariantNumeric: 'tabular-nums',
          letterSpacing: '0.04em',
          color: active ? accent : '#94a3b8',
          background: 'rgba(8,11,16,0.86)',
          padding: '2px 7px',
          borderRadius: 2,
          border: `1px solid ${active ? accent : '#2b3646'}`,
          boxShadow: active ? `0 0 12px ${accent}55` : 'none',
          whiteSpace: 'nowrap',
          pointerEvents: 'none',
        }}
      >
        {index + 1} · {cht.toFixed(0)}°C
      </div>
    </Html>
  );
}

// ---------------------------------------------------------------------------
// CRANKCASE — main case, sump, mounting lugs
// ---------------------------------------------------------------------------
export function Crankcase({ count }: { count: number }) {
  const len = count * 1.42 + 0.62;
  return (
    <group>
      {/* upper case */}
      <RoundedBox args={[len, 0.66, 1.06]} radius={0.13} smoothness={5} position={[0, -0.36, 0]} castShadow receiveShadow>
        <meshStandardMaterial {...MAT.castAlu} />
      </RoundedBox>

      {/* case split line — a thin darker band reads as two castings bolted together */}
      <mesh position={[0, -0.30, 0]}>
        <boxGeometry args={[len + 0.008, 0.030, 1.075]} />
        <meshStandardMaterial {...MAT.castAluDark} />
      </mesh>

      {/* sump, tapered */}
      <RoundedBox args={[len * 0.72, 0.34, 0.86]} radius={0.10} smoothness={4} position={[0, -0.80, 0]} castShadow>
        <meshStandardMaterial {...MAT.castAluDark} />
      </RoundedBox>
      <mesh position={[0, -0.985, 0]}>
        <cylinderGeometry args={[0.05, 0.05, 0.07, 6]} />
        <meshStandardMaterial {...MAT.steelDark} />
      </mesh>

      {/* case bolts along the split line */}
      {Array.from({ length: count * 2 + 3 }, (_, i) => {
        const x = -len / 2 + 0.22 + i * ((len - 0.44) / (count * 2 + 2));
        return (
          <group key={i}>
            <Bolt position={[x, -0.30, 0.545]} r={0.032} h={0.045} />
            <Bolt position={[x, -0.30, -0.545]} r={0.032} h={0.045} />
          </group>
        );
      })}

      {/* engine mounting lugs */}
      {[-1, 1].map((s) => (
        <group key={s}>
          <mesh position={[s * (len / 2 - 0.18), -0.52, 0.60]} castShadow>
            <boxGeometry args={[0.22, 0.16, 0.16]} />
            <meshStandardMaterial {...MAT.castAluDark} />
          </mesh>
          <mesh position={[s * (len / 2 - 0.18), -0.52, -0.60]} castShadow>
            <boxGeometry args={[0.22, 0.16, 0.16]} />
            <meshStandardMaterial {...MAT.castAluDark} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

/** Crankshaft with webs and counterweights, rotating at scaled RPM. */
export function Crankshaft({ crankAngle, count }: { crankAngle: number; count: number }) {
  const ref = useRef<THREE.Group>(null);
  const len = count * 1.42 + 0.9;
  useFrame(() => { if (ref.current) ref.current.rotation.x = crankAngle; });
  return (
    <group ref={ref} position={[0, -0.36, 0]}>
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.088, 0.088, len, 24]} />
        <meshStandardMaterial {...MAT.steel} />
      </mesh>
      {Array.from({ length: count }, (_, i) => (
        <group key={i} position={[CYL_X(i, count), 0, 0]}>
          {/* crank pin */}
          <mesh position={[0, 0.17, 0]} rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[0.072, 0.072, 0.24, 18]} />
            <meshStandardMaterial {...MAT.steel} />
          </mesh>
          {/* counterweight */}
          <mesh position={[0, -0.13, 0]} rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[0.20, 0.20, 0.10, 20, 1, false, 0, Math.PI]} />
            <meshStandardMaterial {...MAT.steelDark} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

// ---------------------------------------------------------------------------
// INTAKE — plenum across the vee, runners down to each head
// ---------------------------------------------------------------------------
export function IntakeSystem({ count }: { count: number }) {
  const len = count * 1.42 - 0.3;

  const runners = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => {
        const x = CYL_X(i, count);
        const curve = new THREE.CatmullRomCurve3([
          new THREE.Vector3(x, 1.56, -0.38),
          new THREE.Vector3(x, 1.44, -0.36),
          new THREE.Vector3(x, 1.30, -0.30),
          new THREE.Vector3(x, 1.20, -0.19),
        ]);
        return new THREE.TubeGeometry(curve, 22, 0.085, 14, false);
      }),
    [count]
  );

  return (
    <group>
      {/* plenum */}
      <RoundedBox args={[len, 0.30, 0.34]} radius={0.13} smoothness={5} position={[0, 1.60, -0.40]} castShadow receiveShadow>
        <meshStandardMaterial color="#3b424c" metalness={0.72} roughness={0.44} />
      </RoundedBox>
      {/* plenum end caps, so it terminates like a part rather than a slab */}
      {[-1, 1].map((sgn) => (
        <mesh key={sgn} position={[sgn * len / 2, 1.60, -0.40]} rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.152, 0.152, 0.06, 22]} />
          <meshStandardMaterial color="#2b3138" metalness={0.68} roughness={0.5} />
        </mesh>
      ))}
      {runners.map((g, i) => (
        <mesh key={i} geometry={g} castShadow>
          <meshStandardMaterial {...MAT.anodised} />
        </mesh>
      ))}
      {/* charge pipe from the intercooler into the plenum */}
      <mesh position={[-len / 2 - 0.32, 1.60, -0.40]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.12, 0.12, 0.62, 20]} />
        <meshStandardMaterial {...MAT.rubber} />
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------------------
// EXHAUST — one runner per cylinder into a collector, then the turbine
// ---------------------------------------------------------------------------
export function ExhaustSystem({ egt, count }: { egt: number[]; count: number }) {
  const collector = useMemo(
    () => new THREE.Vector3(count * 1.42 * 0.5 + 0.30, 0.18, 0.66),
    [count]
  );

  const tubes = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => {
        const x = CYL_X(i, count);
        const curve = new THREE.CatmullRomCurve3([
          new THREE.Vector3(x, 1.06, 0.30),
          new THREE.Vector3(x + 0.06, 0.92, 0.55),
          new THREE.Vector3(x + 0.22, 0.62, 0.70),
          new THREE.Vector3((x + collector.x) / 2 + 0.12, 0.36, 0.74),
          collector,
        ]);
        return new THREE.TubeGeometry(curve, 52, 0.082, 16, false);
      }),
    [count, collector]
  );

  return (
    <group>
      {tubes.map((g, i) => {
        const heat = exhaustHeat(egt[i] ?? 0);
        return (
          <mesh key={i} geometry={g} castShadow>
            <meshStandardMaterial
              color={new THREE.Color(MAT.exhaust.color).lerp(new THREE.Color('#8f2f22'), heat)}
              emissive={new THREE.Color('#e0451f')}
              emissiveIntensity={heat * 1.35}
              metalness={MAT.exhaust.metalness}
              roughness={MAT.exhaust.roughness}
            />
          </mesh>
        );
      })}
      {/* collector */}
      <mesh position={[collector.x, collector.y, collector.z]} castShadow>
        <sphereGeometry args={[0.15, 20, 16]} />
        <meshStandardMaterial {...MAT.castIron} />
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------------------
// TURBOCHARGER — turbine housing, centre section, compressor volute, wastegate
// ---------------------------------------------------------------------------
export function Turbocharger({
  rpm, effScale, count,
}: { rpm: number; effScale: number; count: number }) {
  const wheel = useRef<THREE.Group>(null);
  const x = count * 1.42 * 0.5 + 0.78;

  useFrame((_, dt) => {
    if (wheel.current) wheel.current.rotation.x += dt * (rpm / 118400) * 26;
  });

  // Compressor degradation discolours the housing — invisible at sea level,
  // mission-limiting above critical altitude.
  const degraded = THREE.MathUtils.clamp((1 - effScale) * 6, 0, 1);
  const housing = new THREE.Color(MAT.castIron.color).lerp(new THREE.Color('#8a5a24'), degraded);

  return (
    <group position={[x, 0.14, 0.62]}>
      {/* turbine housing — the hot side, a fat volute */}
      <group>
        <mesh rotation={[0, 0, Math.PI / 2]} castShadow>
          <torusGeometry args={[0.27, 0.155, 18, 34]} />
          <meshStandardMaterial color={housing} metalness={MAT.castIron.metalness} roughness={MAT.castIron.roughness} />
        </mesh>
        <mesh position={[-0.10, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.19, 0.19, 0.16, 26]} />
          <meshStandardMaterial color={housing} metalness={0.7} roughness={0.75} />
        </mesh>
      </group>

      {/* centre / bearing housing, with oil feed */}
      <mesh position={[0.24, 0, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.135, 0.155, 0.30, 24]} />
        <meshStandardMaterial {...MAT.steelDark} />
      </mesh>
      {[0, 1, 2].map((i) => (
        <mesh key={i} position={[0.18 + i * 0.06, 0, 0]} rotation={[0, 0, Math.PI / 2]}>
          <torusGeometry args={[0.152, 0.014, 8, 24]} />
          <meshStandardMaterial {...MAT.steel} />
        </mesh>
      ))}
      <mesh position={[0.24, 0.20, 0]} castShadow>
        <cylinderGeometry args={[0.035, 0.035, 0.22, 12]} />
        <meshStandardMaterial {...MAT.steel} />
      </mesh>

      {/* compressor volute — the cold side */}
      <group position={[0.52, 0, 0]}>
        <mesh rotation={[0, 0, Math.PI / 2]} castShadow>
          <torusGeometry args={[0.29, 0.145, 18, 34]} />
          <meshStandardMaterial {...MAT.castAlu} />
        </mesh>
        <mesh position={[0.11, 0, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.175, 0.20, 0.22, 28]} />
          <meshStandardMaterial {...MAT.castAlu} />
        </mesh>
        {/* compressor wheel, visible down the inlet */}
        <group ref={wheel} position={[0.16, 0, 0]}>
          {Array.from({ length: 8 }, (_, i) => (
            <mesh key={i} rotation={[(i / 8) * Math.PI * 2, 0, Math.PI / 2]} position={[0, 0, 0]}>
              <boxGeometry args={[0.02, 0.13, 0.10]} />
              <meshStandardMaterial {...MAT.steel} />
            </mesh>
          ))}
          <mesh rotation={[0, 0, -Math.PI / 2]}>
            <coneGeometry args={[0.055, 0.14, 16]} />
            <meshStandardMaterial {...MAT.steel} />
          </mesh>
        </group>
      </group>

      {/* wastegate actuator can */}
      <mesh position={[0.30, 0.30, 0.16]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.085, 0.085, 0.17, 18]} />
        <meshStandardMaterial {...MAT.steelDark} />
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------------------
// REDUCTION GEARBOX + PROP FLANGE
// ---------------------------------------------------------------------------
export function GearboxNose({ count, crankAngle }: { count: number; crankAngle: number }) {
  const flange = useRef<THREE.Group>(null);
  const x = -(count * 1.42 * 0.5 + 0.52);

  useFrame(() => {
    // Geared: propeller turns slower than the crank. Getting this ratio wrong
    // is what silently breaks the rho5 power-closure residual.
    if (flange.current) flange.current.rotation.x = crankAngle / 2.43;
  });

  return (
    <group position={[x, -0.30, 0]}>
      <mesh rotation={[0, 0, Math.PI / 2]} castShadow receiveShadow>
        <cylinderGeometry args={[0.42, 0.36, 0.44, 32]} />
        <meshStandardMaterial {...MAT.castAlu} />
      </mesh>
      <BoltCircle y={0} radius={0.33} count={8} />
      <mesh position={[-0.30, 0, 0]} rotation={[0, 0, Math.PI / 2]} castShadow>
        <cylinderGeometry args={[0.20, 0.24, 0.20, 26]} />
        <meshStandardMaterial {...MAT.castAluDark} />
      </mesh>
      <group ref={flange} position={[-0.44, 0, 0]}>
        <mesh rotation={[0, 0, Math.PI / 2]} castShadow>
          <cylinderGeometry args={[0.26, 0.26, 0.09, 28]} />
          <meshStandardMaterial {...MAT.steel} />
        </mesh>
        {Array.from({ length: 6 }, (_, i) => {
          const a = (i / 6) * Math.PI * 2;
          return (
            <mesh key={i} position={[-0.06, Math.cos(a) * 0.185, Math.sin(a) * 0.185]} rotation={[0, 0, Math.PI / 2]}>
              <cylinderGeometry args={[0.026, 0.026, 0.09, 8]} />
              <meshStandardMaterial {...MAT.steelDark} />
            </mesh>
          );
        })}
      </group>
    </group>
  );
}

/** Oil cooler block hung off the back of the case — small, but it fills the
 *  silhouette and gives the oil-pressure residual something to point at. */
export function OilCooler({ count }: { count: number }) {
  const x = -(count * 1.42 * 0.5) + 0.35;
  return (
    <group position={[x, -0.52, -0.72]}>
      <RoundedBox args={[0.62, 0.44, 0.12]} radius={0.03} smoothness={3} castShadow>
        <meshStandardMaterial {...MAT.castAluDark} />
      </RoundedBox>
      {Array.from({ length: 9 }, (_, i) => (
        <mesh key={i} position={[-0.26 + i * 0.065, 0, 0.07]}>
          <boxGeometry args={[0.022, 0.36, 0.02]} />
          <meshStandardMaterial {...MAT.steelDark} />
        </mesh>
      ))}
    </group>
  );
}
