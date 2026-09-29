/**
 * PRAMANA — the interactive 3D engine twin.
 *
 * The model is built from the ACTIVE PROFILE's architecture (engine/*), so
 * the VRDE renders as the liquid-cooled inline four it is and the Rotax as a
 * boxer. Every diagnosed fault lights the physical part it lives in, amber for
 * a component and cyan for an instrument, and a cutaway shows the moving
 * internals with true section faces.
 *
 * Lighting is built from Lightformers rather than an HDR file, so the demo
 * renders identically with no network.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { CameraControls, Environment, Html, Lightformer, PerformanceMonitor } from '@react-three/drei';
import { EffectComposer, Bloom, N8AO, SMAA, ToneMapping, Vignette } from '@react-three/postprocessing';
import { ToneMappingMode } from 'postprocessing';
import * as THREE from 'three';
import { useMission, useCurrentTick, useEngineDisplay, coldBlend } from '../state/missionStore';
import { C, SCENE } from '../theme';
import { LiveContext, createLive, easeParts, setPartTargets, useLive, type Live } from './engine/live';
import { SectionProvider } from './engine/section';
import { MatsProvider } from './engine/mats';
import { engineGeometry, type Geometry } from './engine/kinematics';
import { Inline4, inlineTagAnchor, INLINE_FLOOR } from './engine/Inline4';
import { Boxer4, boxerTagAnchor, BOXER_FLOOR } from './engine/Boxer4';
import { floorPool } from './engine/textures';
import type { EngineProfile } from '../config/engines';

/** Crank display speed as a fraction of real: 3,600 rpm is an unreadable strobe. */
const DISPLAY_SPEED = 0.045;
/** Where the section plane rests when the cutaway is off, and where it cuts. */
const CUT_OFF = 6, CUT_ON = -0.2;
/** Default viewing direction from the model centre. */
const HOME_DIR = new THREE.Vector3(0.3, 0.36, 1).normalize();

type View = 'exterior' | 'cutaway';

// ---------------------------------------------------------------------------
// Telemetry -> live, once per React render; animation clocks once per frame.
// ---------------------------------------------------------------------------
function Sync({ live }: { live: Live }) {
  const tick = useCurrentTick();
  const engine = useMission((s) => s.engine);
  const selected = useMission((s) => s.selectedCylinder);
  const display = useEngineDisplay();

  live.rpm = display.rpm;
  live.running = display.running;
  live.turboRpm = display.running ? tick.slow.turbo_rpm : tick.slow.turbo_rpm * display.startProgress;
  live.effScale = tick.health.theta?.eta_c_scale.value ?? 1;
  live.cht = tick.slow.cht_C.map((c) => coldBlend(c, display.chtScale));
  live.egt = tick.slow.egt_C;
  live.chtRampFrom = engine.chtRampFrom_C;
  live.nominalEgt = engine.nominalEgt_C;
  live.fuelFlow = tick.slow.fuel_flow_kgps;
  live.map_hPa = tick.slow.map_hPa;
  live.anomaly = tick.health.anomaly.score ?? 0;
  live.selected = selected;
  setPartTargets(live, tick.health.diagnosis, display.running);
  // A CHT sensor the twin says is lying must not paint its cylinder hot: show
  // the twin's own estimate for that head instead of the bad reading.
  live.cht = live.cht.map((c, i) =>
    live.partTargets.has(`cht:${i}`) ? coldBlend(tick.predicted.cht_C[i], display.chtScale) : c);

  useFrame((_, dtRaw) => {
    // Easing must finish in real time even at a few frames per second (a
    // slow laptop, a software renderer), so it gets the true frame time; only
    // the crank is capped, so a stalled tab does not jump it half a turn.
    const dt = Math.min(dtRaw, 0.5);
    live.time += dt;
    live.crank += Math.min(dt, 0.05) * (live.rpm / 60) * Math.PI * 2 * DISPLAY_SPEED;
    easeParts(live, dt);
    // Cutaway: ease, then sweep the plane through the engine.
    live.cut += (live.cutTarget - live.cut) * (1 - Math.exp(-2.6 * dt));
    const u = live.cut * live.cut * (3 - 2 * live.cut);
    live.plane.constant = CUT_OFF + (CUT_ON - CUT_OFF) * u;
  });
  return null;
}

// ---------------------------------------------------------------------------
// Studio: a dark room lit by long softboxes. Metals render as reflections of
// their surroundings, so the strip lights are what draw the highlights along
// every tube and casting edge.
// ---------------------------------------------------------------------------
function Studio({ floorY }: { floorY: number }) {
  const key = useRef<THREE.DirectionalLight>(null);
  useEffect(() => {
    const l = key.current;
    if (!l) return;
    l.shadow.mapSize.set(2048, 2048);
    Object.assign(l.shadow.camera, { left: -7, right: 7, top: 7, bottom: -7, near: 1, far: 30 });
    l.shadow.bias = -0.0004;
    l.shadow.normalBias = 0.02;
    l.shadow.camera.updateProjectionMatrix();
  }, []);
  const pool = useMemo(() => floorPool(), []);
  return (
    <>
      <Environment resolution={512} frames={1}>
        <color attach="background" args={['#0d1016']} />
        {/* overhead softbox */}
        <Lightformer form="rect" intensity={3.2} scale={[16, 6, 1]} position={[0, 9, 1]} rotation={[-Math.PI / 2, 0, 0]} color="#ffffff" />
        {/* the big one behind the camera: what every camera-facing metal face reflects */}
        <Lightformer form="rect" intensity={1.35} scale={[18, 7, 1]} position={[2, 3, 11]} rotation={[0, Math.PI, 0]} color="#e9f0f8" />
        {/* strip lights: long highlights down every tube and casting edge */}
        <Lightformer form="rect" intensity={5} scale={[0.7, 14, 1]} position={[-8, 3, 3]} rotation={[0, Math.PI / 2.6, 0]} color="#dbe8ff" />
        <Lightformer form="rect" intensity={4.5} scale={[0.7, 14, 1]} position={[8, 3, 3]} rotation={[0, -Math.PI / 2.6, 0]} color="#fff1dc" />
        {/* cool rim from behind, to separate the silhouette from the room */}
        <Lightformer form="rect" intensity={2.4} scale={[14, 1.6, 1]} position={[0, 2, -9]} color="#8fb0ff" />
        <Lightformer form="ring" intensity={3} scale={3} position={[6, 7, 6]} color="#ffffff" />
      </Environment>
      <hemisphereLight args={['#b9c7de', '#0c0f14', 0.35]} />
      <directionalLight ref={key} position={[4, 10, 6]} intensity={1.6} color="#fff4e6" castShadow />
      <directionalLight position={[-6, 3, -5]} intensity={0.6} color="#8fb0ff" />
      <mesh position={[0, floorY - 0.002, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[26, 26]} />
        <meshBasicMaterial map={pool} transparent depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh position={[0, floorY, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[26, 26]} />
        <shadowMaterial transparent opacity={0.55} />
      </mesh>
    </>
  );
}

// ---------------------------------------------------------------------------
// Cylinder tags: the per-cylinder CHT is the most important number on this
// view, so it is HTML (crisp at any zoom), not geometry.
// ---------------------------------------------------------------------------
function Tags({ g, profile }: { g: Geometry; profile: EngineProfile }) {
  const tick = useCurrentTick();
  const display = useEngineDisplay();
  const live = useLive();
  const [, force] = useState(0);
  // Faults ease in over about a second; re-read the eased values at 10 Hz
  // rather than re-rendering the tags every frame.
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), 100);
    return () => clearInterval(id);
  }, []);
  const anchor = profile.architecture.layout === 'inline' ? inlineTagAnchor : boxerTagAnchor;
  return (
    <>
      {g.frames.map((f) => {
        const h = live.parts.get(`cyl:${f.index}`) ?? live.parts.get(`cht:${f.index}`);
        const active = !!h && h.p > 0.4;
        const accent = h?.sensor ? C.sensor : C.warn;
        const cht = coldBlend(tick.slow.cht_C[f.index], display.chtScale);
        return (
          <Html key={f.index} position={anchor(g, f)} center zIndexRange={[30, 0]}>
            <div className={`cyl-tag${active ? ' cyl-tag-on' : ''}`} style={active ? { borderColor: accent, color: accent } : undefined}>
              <span className="cyl-tag-n">{f.index + 1}</span>
              <span className="cyl-tag-v">{cht.toFixed(0)}°C</span>
            </div>
          </Html>
        );
      })}
    </>
  );
}

function Model({ profile, g, onHover }: { profile: EngineProfile; g: Geometry; onHover: (i: number | null) => void }) {
  const selectCylinder = useMission((s) => s.selectCylinder);
  const selected = useMission((s) => s.selectedCylinder);
  const live = useLive();
  const onSelect = (i: number) => selectCylinder(selected === i ? null : i);
  const hover = (i: number | null) => { live.hovered = i; onHover(i); };
  return profile.architecture.layout === 'inline'
    ? <Inline4 g={g} profile={profile} onSelect={onSelect} onHover={hover} />
    : <Boxer4 g={g} profile={profile} onSelect={onSelect} onHover={hover} />;
}

/**
 * Frames whatever engine is loaded: measures the model's bounds and fits the
 * camera to them, so a new profile never needs a hand-tuned distance.
 * After a while idle, a slow sway keeps the hero shot alive without ever
 * turning the faulted side away from the audience.
 */
function Rig({ target, resetNonce, profileId }: { target: React.RefObject<THREE.Group | null>; resetNonce: number; profileId: string }) {
  const controls = useRef<CameraControls>(null);
  const size = useThree((s) => s.size);
  const idle = useRef(0);
  const home = useRef(0);

  useEffect(() => {
    const c = controls.current, obj = target.current;
    if (!c || !obj || size.width < 1) return;
    const box = new THREE.Box3().setFromObject(obj);
    const center = box.getCenter(new THREE.Vector3());
    const radius = box.getBoundingSphere(new THREE.Sphere()).radius;
    const cam = c.camera as THREE.PerspectiveCamera;
    const vFov = THREE.MathUtils.degToRad(cam.fov);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * (size.width / size.height));
    const dist = (radius * 0.8) / Math.sin(Math.min(vFov, hFov) / 2);
    const eye = center.clone().addScaledVector(HOME_DIR, dist);
    c.minDistance = radius * 0.8;
    c.maxDistance = dist * 2.2;
    void c.setLookAt(eye.x, eye.y, eye.z, center.x, center.y, center.z, resetNonce > 0);
    home.current = Math.atan2(HOME_DIR.x, HOME_DIR.z);
    idle.current = 0;
  }, [target, resetNonce, profileId, size.width, size.height]);

  useFrame((_, dt) => {
    const c = controls.current;
    if (!c) return;
    idle.current += dt;
    if (idle.current > 12) {
      const t = idle.current - 12;
      c.azimuthAngle = home.current + Math.sin(t * 0.18) * 0.22 * Math.min(1, t / 4);
    }
  });

  return (
    <CameraControls
      ref={controls}
      makeDefault
      smoothTime={0.55}
      draggingSmoothTime={0.12}
      minPolarAngle={0.2}
      maxPolarAngle={Math.PI / 2.05}
      truckSpeed={0}
      onStart={() => { idle.current = -1e9; }}
      onEnd={() => { idle.current = 0; }}
    />
  );
}

function Effects({ high }: { high: boolean }) {
  return (
    <EffectComposer multisampling={0} stencilBuffer>
      {high ? <N8AO halfRes aoRadius={0.35} distanceFalloff={0.6} intensity={2.4} quality="medium" /> : <></>}
      <Bloom intensity={SCENE.bloomIntensity} luminanceThreshold={SCENE.bloomThreshold} luminanceSmoothing={0.2} mipmapBlur />
      <ToneMapping mode={ToneMappingMode.AGX} />
      <Vignette offset={0.28} darkness={0.55} />
      <SMAA />
    </EffectComposer>
  );
}

export function Engine3D() {
  const profile = useMission((s) => s.engine);
  const g = useMemo(() => engineGeometry(profile), [profile]);
  const live = useMemo(() => createLive(), []);
  // Dev-only handle for inspecting eased state from the console or a headless driver.
  if (import.meta.env.DEV) (window as unknown as { __live?: Live }).__live = live;
  const model = useRef<THREE.Group>(null);
  const [view, setView] = useState<View>(() =>
    new URLSearchParams(window.location.search).get('view') === 'cutaway' ? 'cutaway' : 'exterior');
  const [resetNonce, setResetNonce] = useState(0);
  const [hovered, setHovered] = useState<number | null>(null);
  const [high, setHigh] = useState(true);
  const floorY = profile.architecture.layout === 'inline' ? INLINE_FLOOR(g) : BOXER_FLOOR;

  live.cutTarget = view === 'cutaway' ? 1 : 0;

  return (
    <div className={`engine3d${hovered !== null ? ' engine3d-pointer' : ''}`}>
      <Canvas
        shadows
        dpr={high ? [1, 1.75] : 1}
        camera={{ fov: 30, near: 0.1, far: 120, position: [6, 6, 14] }}
        gl={{ antialias: false, stencil: true, toneMapping: THREE.NoToneMapping, powerPreference: 'high-performance' }}
      >
        <LiveContext.Provider value={live}>
          <PerformanceMonitor onDecline={() => setHigh(false)} flipflops={2} />
          <color attach="background" args={[SCENE.bg]} />
          <fog attach="fog" args={[SCENE.bg, SCENE.fogNear, SCENE.fogFar]} />
          <Sync live={live} />
          <Studio floorY={floorY} />
          <SectionProvider>
            <MatsProvider>
              <group ref={model} key={profile.id}>
                <Model profile={profile} g={g} onHover={setHovered} />
              </group>
              <Tags g={g} profile={profile} />
            </MatsProvider>
          </SectionProvider>
          <Rig target={model} resetNonce={resetNonce} profileId={profile.id} />
          <Effects high={high} />
        </LiveContext.Provider>
      </Canvas>

      <div className="engine3d-tools" role="group" aria-label="Engine view">
        <button className={view === 'exterior' ? 'on' : ''} onClick={() => setView('exterior')}>Exterior</button>
        <button className={view === 'cutaway' ? 'on' : ''} onClick={() => setView('cutaway')}>Cutaway</button>
        <span className="engine3d-tools-sep" />
        <button onClick={() => setResetNonce((n) => n + 1)} title="Restore the framed view">Reset view</button>
      </div>
      <div className="engine3d-legend">
        <span><i className="dot dot-fault" />Engine fault</span>
        <span><i className="dot dot-sensor" />Sensor fault</span>
        <span className="engine3d-legend-arch">{archLabel(profile)}</span>
      </div>
      <div className="engine3d-hint">Drag to orbit · scroll to zoom · click a cylinder</div>
    </div>
  );
}

function archLabel(p: EngineProfile): string {
  const a = p.architecture;
  const layout = a.layout === 'inline' ? `Inline-${p.cylinders}` : `Flat-${p.cylinders} boxer`;
  const cooling = a.cooling === 'liquid' ? 'liquid-cooled' : 'liquid heads, air-cooled barrels';
  const fuel = a.fuelSystem === 'common_rail' ? 'common-rail diesel' : 'carburetted';
  return `${layout} · ${cooling} · ${fuel} · turbocharged`;
}
