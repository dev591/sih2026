/**
 * PRAMANA — the interactive 3D engine twin.
 *
 * Composed entirely from named primitives (see engine/parts.tsx). Crank,
 * pistons, compressor wheel and prop flange all animate at scaled RPM; orbit,
 * zoom, and click a cylinder to open the explain drawer for it.
 *
 * Lighting is a deliberate three-point studio rig rather than drei's
 * <Environment preset ...>, because presets fetch an HDR from a CDN at runtime
 * and a demo laptop cannot be assumed to have network. Everything here renders
 * offline.
 */

import { useEffect, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, ContactShadows } from '@react-three/drei';
import { EffectComposer, Bloom, Vignette, SMAA } from '@react-three/postprocessing';
import * as THREE from 'three';
import { useMission, useCurrentTick } from '../state/missionStore';
import { N_CYL } from '../types/telemetry';
import {
  CylinderAssembly, CylinderTag, Crankcase, Crankshaft,
  IntakeSystem, ExhaustSystem, Turbocharger, GearboxNose, OilCooler,
} from './engine/parts';

// ---------------------------------------------------------------------------
// Lighting rig — warm key, cool fill, hard rim. The cool fill is what keeps
// aluminium reading as metal rather than as grey plastic.
// ---------------------------------------------------------------------------
function Rig() {
  const key = useRef<THREE.DirectionalLight>(null);
  useEffect(() => {
    if (!key.current) return;
    key.current.shadow.mapSize.set(2048, 2048);
    key.current.shadow.camera.near = 1;
    key.current.shadow.camera.far = 26;
    key.current.shadow.bias = -0.0006;
  }, []);
  return (
    <>
      <hemisphereLight args={['#4a5568', '#0a0d13', 0.6]} />
      <directionalLight ref={key} position={[5.5, 7.5, 4.5]} intensity={2.0} color="#fff4e6" castShadow />
      <directionalLight position={[-6, 3.5, -3.5]} intensity={0.8} color="#5eb3f5" />
      <directionalLight position={[0, 1.5, -7]} intensity={0.55} color="#93c5fd" />
      <pointLight position={[0, 3.2, 4.5]} intensity={26} distance={15} decay={2} color="#dbeafe" />
      <spotLight position={[0, 6.5, 0]} angle={0.75} penumbra={0.9} intensity={20} distance={16} decay={2} color="#e2e8f0" />
    </>
  );
}

function Scene() {
  const tick = useCurrentTick();
  const selected = useMission((s) => s.selectedCylinder);
  const selectCylinder = useMission((s) => s.selectCylinder);
  const [hovered, setHovered] = useState<number | null>(null);

  const crank = useRef(0);
  const [crankAngle, setCrankAngle] = useState(0);
  const { gl } = useThree();

  const rpm = tick.slow.rpm;
  useFrame((_, dt) => {
    // Heavily scaled — 3580 rpm at real speed is an unreadable strobe.
    crank.current += Math.min(dt, 0.05) * (rpm / 60) * 0.15 * Math.PI * 2;
    setCrankAngle(crank.current);
  });

  useEffect(() => {
    gl.domElement.style.cursor = hovered !== null ? 'pointer' : 'grab';
  }, [hovered, gl]);

  const { diagnosis, anomaly } = tick.health;

  const cylFault = Array.from({ length: N_CYL }, (_, i) => {
    const hit = diagnosis.top.find((h) => h.cylinder === i && h.fault !== 'healthy');
    return hit ? hit.p : 0;
  });
  const cylSensor = Array.from({ length: N_CYL }, (_, i) => {
    const hit = diagnosis.top.find((h) => h.cylinder === i && h.fault !== 'healthy');
    return hit ? hit.fault.includes('sensor') : false;
  });

  return (
    <>
      <Rig />

      <group position={[0, -0.1, 0]}>
        <Crankcase count={N_CYL} />
        <Crankshaft crankAngle={crankAngle} count={N_CYL} />

        {Array.from({ length: N_CYL }, (_, i) => (
          <CylinderAssembly
            key={i}
            index={i}
            count={N_CYL}
            cht={tick.slow.cht_C[i]}
            egt={tick.slow.egt_C[i]}
            anomaly={cylFault[i] > 0.3 ? anomaly.score : anomaly.score * 0.08}
            faultProb={cylFault[i]}
            isSensorFault={cylSensor[i]}
            selected={selected === i}
            hovered={hovered === i}
            onSelect={() => selectCylinder(selected === i ? null : i)}
            onHover={(h) => setHovered(h ? i : null)}
            crankAngle={crankAngle}
          />
        ))}

        <IntakeSystem count={N_CYL} />
        <ExhaustSystem egt={tick.slow.egt_C} count={N_CYL} />
        <Turbocharger
          rpm={tick.slow.turbo_rpm}
          effScale={tick.health.theta.eta_c_scale.value}
          count={N_CYL}
        />
        <GearboxNose count={N_CYL} crankAngle={crankAngle} />
        <OilCooler count={N_CYL} />

        {Array.from({ length: N_CYL }, (_, i) => (
          <CylinderTag
            key={i}
            index={i}
            count={N_CYL}
            cht={tick.slow.cht_C[i]}
            faultProb={cylFault[i]}
            isSensorFault={cylSensor[i]}
          />
        ))}
      </group>

      {/* soft grounding shadow — cheaper and better looking than a shadow plane */}
      <ContactShadows
        position={[0, -1.30, 0]}
        opacity={0.6}
        scale={17}
        blur={2.6}
        far={4}
        resolution={1024}
        color="#000000"
      />
      <gridHelper args={[26, 26, '#1a2432', '#111823']} position={[0, -1.31, 0]} />

      <OrbitControls
        enablePan={false}
        enableDamping
        dampingFactor={0.06}
        minDistance={3.6}
        maxDistance={15}
        minPolarAngle={0.15}
        maxPolarAngle={Math.PI / 2.08}
        target={[0.16, 0.28, 0]}
      />
    </>
  );
}

export function Engine3D() {
  return (
    <div className="engine3d">
      <Canvas
        shadows
        camera={{ position: [1.7, 2.15, 6.5], fov: 37 }}
        dpr={[1, 2]}
        gl={{ antialias: false, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.05 }}
      >
        <color attach="background" args={['#070a0f']} />
        <fog attach="fog" args={['#070a0f', 13, 28]} />
        <Scene />
        {/* Bloom is what makes a faulted cylinder read as GLOWING rather than
            merely tinted — the difference between "that one is orange" and
            "that one is wrong" at a glance from across a room. */}
        <EffectComposer multisampling={0}>
          <Bloom intensity={0.75} luminanceThreshold={0.6} luminanceSmoothing={0.3} mipmapBlur />
          <Vignette eskil={false} offset={0.25} darkness={0.7} />
          <SMAA />
        </EffectComposer>
      </Canvas>
      <div className="engine3d-hint">DRAG TO ORBIT · SCROLL TO ZOOM · CLICK A CYLINDER</div>
    </div>
  );
}
