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

import { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Environment, Lightformer } from '@react-three/drei';
import { EffectComposer, Bloom, SMAA } from '@react-three/postprocessing';
import * as THREE from 'three';
import { useMission, useCurrentTick } from '../state/missionStore';
import { N_CYL } from '../types/telemetry';
import {
  CylinderAssembly, CylinderTag, Crankcase, Crankshaft,
  IntakeSystem, ExhaustSystem, Turbocharger, GearboxNose, OilCooler,
} from './engine/parts';
import { SCENE } from '../theme';

/** Where the camera looks. Shared by OrbitControls and FitCamera so the two
 *  cannot disagree about what "centred" means. */
const TARGET: [number, number, number] = [0.16, 0.28, 0];

/** Half-extents of the engine in scene units: four cylinders on 1.42 spacing
 *  plus the turbo and the prop flange make it a long, fairly flat object. */
const HALF_W = 3.7;
const HALF_H = 1.9;

/**
 * Frame the engine to the viewport it actually has.
 *
 * The camera distance used to be a single hardcoded number, which worked only
 * because there was a single viewport: the 432px expert panel. The simple view's
 * full-bleed stage is nearly twice as tall and much wider, and one distance
 * cannot serve both — tuned for the stage, the engine overflowed the panel and
 * cropped to an unreadable close-up; tuned for the panel, it sat marooned in the
 * middle of the stage.
 *
 * So solve for it instead. Take the distance needed to fit the object's width
 * given the horizontal FOV and the distance needed to fit its height given the
 * vertical one, and use whichever is larger. This also handles window resizes
 * and a projector's aspect ratio for free, which the hardcoded value never did.
 */
function FitCamera() {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const width = useThree((s) => s.size.width);
  const height = useThree((s) => s.size.height);

  useEffect(() => {
    // A canvas can report a degenerate size on its first layout pass, and an
    // aspect of 0 sends the horizontal FOV to 0, the division below to
    // Infinity, and the camera somewhere from which nothing is visible. Wait
    // for a real size instead — the effect re-runs as soon as there is one.
    if (width < 1 || height < 1) return;

    const vFov = THREE.MathUtils.degToRad(camera.fov);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * (width / height));
    const dist = THREE.MathUtils.clamp(
      Math.max(HALF_H / Math.tan(vFov / 2), HALF_W / Math.tan(hFov / 2)) * 1.16,
      4,
      20
    );

    // Move along the CURRENT view direction, so this reframes without throwing
    // away an orbit the operator has already set up.
    const target = new THREE.Vector3(...TARGET);
    const dir = camera.position.clone().sub(target).normalize();
    camera.position.copy(target).addScaledVector(dir, dist);
    camera.updateProjectionMatrix();
  }, [camera, width, height]);

  return null;
}

/**
 * Ground shadow.
 *
 * A hand-rolled gradient plane rather than drei's <ContactShadows>, which on
 * this scene rendered its shadow-catcher as a visibly lighter quadrilateral
 * with hard edges — invisible against the old black backdrop, obvious against
 * a white one. This is fully transparent where there is no shadow, so there is
 * no plane to see, and it costs one 256px canvas instead of a per-frame
 * shadow-map render.
 *
 * It is what sits the engine in the room rather than floating it on the page,
 * which matters far more on a light ground than it did on a dark one.
 */
function GroundShadow() {
  const texture = useMemo(() => {
    const size = 256;
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext('2d')!;
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    g.addColorStop(0, 'rgba(24,36,52,0.40)');
    g.addColorStop(0.4, 'rgba(24,36,52,0.20)');
    g.addColorStop(0.75, 'rgba(24,36,52,0.05)');
    g.addColorStop(1, 'rgba(24,36,52,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, size, size);
    const t = new THREE.CanvasTexture(canvas);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }, []);

  return (
    <mesh position={[0.1, -1.29, 0]} rotation={[-Math.PI / 2, 0, 0]} renderOrder={-1}>
      {/* stretched to the engine's footprint — four cylinders is a long, narrow
          object, and a circular shadow under it reads as a spotlight */}
      <planeGeometry args={[10, 4.4]} />
      <meshBasicMaterial map={texture} transparent depthWrite={false} toneMapped={false} />
    </mesh>
  );
}

// ---------------------------------------------------------------------------
// Lighting rig — warm key, cool fill, hard rim. The cool fill is what keeps
// aluminium reading as metal rather than as grey plastic.
//
// Retuned for a light room. The old point and spot lights ran at intensity 26
// and 20 because they were lifting an object out of near-black; against a white
// backdrop the same values blow the highlights out and the engine goes chalky,
// so the ambient work moves to the hemisphere light and the punctual lights are
// cut to what they are actually for — a specular hit on the machined faces.
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
      {/* The studio environment. This is the single most important thing in the
          light theme: these materials run at metalness 0.58-0.95, and a metal
          renders as a reflection of its surroundings, so with no environment map
          it renders BLACK. Against the old black backdrop that was invisible;
          against white the engine became a silhouette.

          Built from Lightformers rather than <Environment preset="...">, for the
          same reason the rig was hand-built in the first place: presets fetch an
          HDR from a CDN at runtime and a demo laptop cannot be assumed to have
          network. This is generated on the GPU at startup and works offline. */}
      <Environment resolution={256} frames={1}>
        <color attach="background" args={['#dfe6ef']} />
        {/* overhead softbox — the main thing the machined faces reflect */}
        <Lightformer intensity={2.2} form="rect" scale={[12, 12, 1]} position={[0, 8, 0]} rotation={[-Math.PI / 2, 0, 0]} color="#ffffff" />
        {/* side fills, so the barrels have something to catch on both flanks */}
        <Lightformer intensity={1.1} form="rect" scale={[10, 6, 1]} position={[-8, 2, 3]} rotation={[0, Math.PI / 2, 0]} color="#eef4ff" />
        <Lightformer intensity={0.9} form="rect" scale={[10, 6, 1]} position={[8, 2, -3]} rotation={[0, -Math.PI / 2, 0]} color="#fff6ea" />
        {/* darker floor, so the underside does not glow and the engine keeps a
            bottom edge against the page */}
        <Lightformer intensity={0.35} form="rect" scale={[12, 12, 1]} position={[0, -5, 0]} rotation={[Math.PI / 2, 0, 0]} color="#9aa6b4" />
      </Environment>

      <hemisphereLight args={[SCENE.hemiSky, SCENE.hemiGround, 0.55]} />
      <directionalLight ref={key} position={[5.5, 7.5, 4.5]} intensity={1.5} color={SCENE.key} castShadow />
      <directionalLight position={[-6, 3.5, -3.5]} intensity={0.4} color={SCENE.fill} />
      <directionalLight position={[0, 1.5, -7]} intensity={0.3} color={SCENE.rim} />
      <pointLight position={[0, 3.2, 4.5]} intensity={5} distance={15} decay={2} color={SCENE.rim} />
    </>
  );
}

function Scene() {
  const tick = useCurrentTick();
  const engine = useMission((s) => s.engine);
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
            chtRampFrom={engine.chtRampFrom_C}
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
        <ExhaustSystem
          egt={tick.slow.egt_C}
          count={N_CYL}
          nominalEgt={engine.nominalEgt_C}
          chtRampFrom={engine.chtRampFrom_C}
        />
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

      {/* Soft grounding shadow. On a light floor this does most of the work of
          sitting the engine in the room rather than floating it on the page —
          on black the object read as grounded simply by being lighter than its
          surroundings. No gridHelper: on a white studio floor a grid reads as
          drawing-paper texture and fights the object for attention. */}
      <FitCamera />
      <GroundShadow />

      <OrbitControls
        enablePan={false}
        enableDamping
        dampingFactor={0.06}
        minDistance={3.6}
        maxDistance={22}
        minPolarAngle={0.15}
        maxPolarAngle={Math.PI / 2.08}
        target={TARGET}
      />
    </>
  );
}

export function Engine3D() {
  return (
    <div className="engine3d">
      {/* Position here only sets the viewing ANGLE — FitCamera overrides the
          distance from the actual viewport. */}
      <Canvas
        shadows
        camera={{ position: [1.7, 2.15, 6.5], fov: 37 }}
        dpr={[1, 2]}
        gl={{ antialias: false, toneMapping: THREE.NeutralToneMapping, toneMappingExposure: 1.0 }}
      >
        <color attach="background" args={[SCENE.bg]} />
        <fog attach="fog" args={[SCENE.bg, SCENE.fogNear, SCENE.fogFar]} />
        <Scene />
        {/* Bloom is what makes a faulted cylinder read as GLOWING rather than
            merely tinted — the difference between "that one is orange" and
            "that one is wrong" at a glance from across a room.

            The threshold is the load-bearing number of the light theme. On the
            old black ground almost nothing exceeded 0.6, so the faulted cylinder
            was the only thing that bloomed. On white nearly every pixel exceeds
            0.6, so the same setting would bloom the entire frame into mush. It
            now sits just above the backdrop's luminance and the fault's emissive
            is pushed past it (SCENE.faultEmissive), restoring the original
            property: the fault is the only thing in frame bright enough to glow.

            No Vignette — edge darkening reads as dirt on a light page. */}
        <EffectComposer multisampling={0}>
          <Bloom
            intensity={SCENE.bloomIntensity}
            luminanceThreshold={SCENE.bloomThreshold}
            luminanceSmoothing={0.25}
            mipmapBlur
          />
          <SMAA />
        </EffectComposer>
      </Canvas>
      <div className="engine3d-hint">DRAG TO ORBIT · SCROLL TO ZOOM · CLICK A CYLINDER</div>
    </div>
  );
}
