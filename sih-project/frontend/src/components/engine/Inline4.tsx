/**
 * The VRDE 180 as what it is: a liquid-cooled inline four, common-rail,
 * DOHC 16-valve, turbocharged with an integrated charge-air cooler, driving
 * the propeller through a reduction gearbox.
 *
 * Orientation: crank along X, cylinder 1 at -X (the timing/accessory end,
 * the usual convention, and it reads 1-2-3-4 left to right from the default
 * camera), gearbox and propeller flange at +X, exhaust and turbo on +Z facing
 * the camera, intake and charge-air cooler on -Z.
 */

import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useLive } from './live';
import { useMats, applyGlow, cloneClipped, paintTemper, thermal, GLOW_FAULT } from './mats';
import { Solid } from './section';
import { Internals } from './Internals';
import { Turbo } from './Turbo';
import { circle, cylX, extrudeAlongX, extrudeUp, gearShape, polygon, roundRect, side, tube, up, type P2 } from './geom';
import { valveLift, type CylFrame, type Geometry } from './kinematics';
import { nameplate } from './textures';
import type { EngineProfile } from '../../config/engines';

interface Dims {
  L: number; xL: number; xR: number;
  barrelBottom: number; deck: number; headTop: number; coverTop: number;
  skirtBottom: number; sumpBottom: number;
  halfW: number; headHalfW: number;
}

export function inlineDims(g: Geometry): Dims {
  const L = g.frames.length * g.pitch + 0.46;
  return {
    L, xL: -L / 2, xR: L / 2,
    barrelBottom: 0.9, deck: g.deck, headTop: g.deck + 0.9, coverTop: g.deck + 1.55,
    skirtBottom: -0.62, sumpBottom: -1.45,
    halfW: g.bore / 2 + 0.355, headHalfW: g.bore / 2 + 0.425,
  };
}

/** Where each cylinder's floating tag sits. */
export function inlineTagAnchor(g: Geometry, f: CylFrame): [number, number, number] {
  return [f.x, inlineDims(g).coverTop + 0.75, 0];
}

// ---------------------------------------------------------------------------
// Block: barrel section with true bores and jacket passages, two crankcase
// skirts, end walls and main-bearing bulkheads.
// ---------------------------------------------------------------------------
function Block({ g, d }: { g: Geometry; d: Dims }) {
  const m = useMats();
  const geo = useMemo(() => {
    const x0 = g.frames[0].x, x1 = g.frames[g.frames.length - 1].x;
    const jIn = g.bore / 2 + 0.075, jOut = g.bore / 2 + 0.195;
    const outline: P2[] = [up(d.xL, -d.halfW), up(d.xR, -d.halfW), up(d.xR, d.halfW), up(d.xL, d.halfW)];
    const holes: THREE.Path[] = g.frames.map((f) => circle(...up(f.x, 0), g.bore / 2));
    for (const s of [-1, 1]) {
      const za = s * jIn, zb = s * jOut;
      const p = new THREE.Path();
      const pts = [up(x0 - 0.3, za), up(x1 + 0.3, za), up(x1 + 0.3, zb), up(x0 - 0.3, zb)];
      p.moveTo(...pts[0]); pts.slice(1).forEach((q) => p.lineTo(...q)); p.closePath();
      holes.push(p);
    }
    const barrel = extrudeUp([polygon(outline, holes)], d.barrelBottom, d.deck - d.barrelBottom);

    const skirt = (s: number): THREE.Shape => polygon([
      side(s * d.halfW, d.barrelBottom), side(s * 0.45, d.barrelBottom), side(s * 0.62, 0.6),
      side(s * 0.8, 0.1), side(s * 0.8, d.skirtBottom), side(s * 1.04, d.skirtBottom),
      side(s * 1.04, d.skirtBottom + 0.07), side(s * 0.96, d.skirtBottom + 0.14), side(s * 0.96, 0.3),
    ]);
    const skirts = extrudeAlongX([skirt(1), skirt(-1)], d.xL, d.L);

    const inner: P2[] = [
      side(-0.8, d.skirtBottom), side(0.8, d.skirtBottom), side(0.8, 0.1), side(0.62, 0.6),
      side(0.45, d.barrelBottom), side(-0.45, d.barrelBottom), side(-0.62, 0.6), side(-0.8, 0.1),
    ];
    const bulkShape = () => polygon(inner, [circle(...side(0, 0), 0.17)]);
    const bulkhead = extrudeAlongX([bulkShape()], -0.04, 0.08);
    const endWall = extrudeAlongX([bulkShape()], -0.06, 0.12);
    const mids = g.frames.slice(0, -1).map((f, i) => (f.x + g.frames[i + 1].x) / 2);
    return { barrel, skirts, bulkhead, endWall, mids };
  }, [g, d]);

  return (
    <group>
      <Solid geometry={geo.barrel} material={m.paint} />
      <Solid geometry={geo.skirts} material={m.paint} />
      <Solid geometry={geo.endWall} material={m.paint} position={[d.xL + 0.06, 0, 0]} />
      <Solid geometry={geo.endWall} material={m.paint} position={[d.xR - 0.06, 0, 0]} />
      {geo.mids.map((x) => <Solid key={x} geometry={geo.bulkhead} material={m.paint} position={[x, 0, 0]} />)}
      {/* stiffening ribs down both flanks at every bulkhead: flat slabs are
          what make a casting read as CAD */}
      {[d.xL + 0.12, ...geo.mids, d.xR - 0.12].flatMap((x) => [-1, 1].map((s) => (
        <mesh key={`${x}${s}`} position={[x, (d.barrelBottom + d.deck) / 2 - 0.02, s * (d.halfW + 0.02)]} material={m.paint} castShadow>
          <boxGeometry args={[0.07, d.deck - d.barrelBottom - 0.1, 0.05]} />
        </mesh>
      )))}
      {/* head gasket line */}
      <mesh position={[0, d.deck, 0]} material={m.steelDark}>
        <boxGeometry args={[d.L + 0.01, 0.022, d.headHalfW * 2 + 0.012]} />
      </mesh>
      {/* engine mounts with rubber isolators */}
      {[-1, 1].flatMap((sx) => [-1, 1].map((sz) => (
        <group key={`${sx}${sz}`} position={[sx * (d.L / 2 - 0.55), 0.05, sz * 1.02]}>
          <mesh material={m.paint} castShadow><boxGeometry args={[0.3, 0.22, 0.16]} /></mesh>
          <mesh position={[0, 0, sz * 0.14]} rotation={[Math.PI / 2, 0, 0]} material={m.rubber}>
            <cylinderGeometry args={[0.1, 0.1, 0.12, 20]} />
          </mesh>
        </group>
      )))}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Head: one casting per cylinder so each can carry its own CHT colour and
// fault glow (CHT is literally measured in the head). Valve and injector
// bores are real holes, so the valves are visible in the cut.
// ---------------------------------------------------------------------------
function HeadSlice({ g, d, f, last }: { g: Geometry; d: Dims; f: CylFrame; last: boolean }) {
  const live = useLive();
  const m = useMats();
  const mat = useMemo(() => cloneClipped(m.castAlu), [m]);
  const base = useMemo(() => m.castAlu.color.clone(), [m]);
  const edge = useRef<THREE.LineSegments>(null);
  const geo = useMemo(() => {
    const x0 = f.index === 0 ? d.xL : f.x - g.pitch / 2;
    const x1 = last ? d.xR : f.x + g.pitch / 2;
    const w = d.headHalfW;
    const holes = [
      ...[-0.17, 0.17].flatMap((dx) => [-0.2, 0.2].map((z) => circle(...up(f.x + dx, z), 0.115))),
      circle(...up(f.x, 0), 0.06),
    ];
    return extrudeUp([polygon([up(x0, -w), up(x1, -w), up(x1, w), up(x0, w)], holes)], d.deck, d.headTop - d.deck);
  }, [g, d, f, last]);
  const edges = useMemo(
    () => new THREE.EdgesGeometry(new THREE.BoxGeometry(g.pitch - 0.03, d.coverTop - d.deck + 0.05, d.headHalfW * 2 + 0.06)),
    [g.pitch, d],
  );
  const edgeMat = useMemo(() => new THREE.LineBasicMaterial({ color: '#38bdf8', transparent: true, opacity: 0, toneMapped: false }), []);
  const probe = useMemo(() => cloneClipped(m.steel), [m]);

  useFrame(() => {
    const t = (live.cht[f.index] - live.chtRampFrom) / 55;
    thermal(base, t, mat.color);
    const h = live.parts.get(`cyl:${f.index}`);
    if (h) applyGlow(mat, h.p, h.sensor, live.time);
    else mat.emissiveIntensity = 0;
    const s = live.parts.get(`cht:${f.index}`);
    if (s) applyGlow(probe, s.p, s.sensor, live.time, 4);
    else probe.emissiveIntensity = 0;
    const sel = live.selected === f.index ? 1 : live.hovered === f.index ? 0.5 : 0;
    edgeMat.opacity = sel;
    if (edge.current) edge.current.visible = sel > 0;
  });

  return (
    <group>
      <Solid geometry={geo} material={mat} />
      {/* exhaust and intake port flanges */}
      <mesh position={[f.x, d.deck + 0.45, d.headHalfW + 0.04]} material={mat} castShadow>
        <boxGeometry args={[0.5, 0.32, 0.08]} />
      </mesh>
      <mesh position={[f.x, d.deck + 0.45, -d.headHalfW - 0.04]} material={mat} castShadow>
        <boxGeometry args={[0.5, 0.3, 0.08]} />
      </mesh>
      {/* CHT thermocouple boss: the sensor a CHT-drift diagnosis points at */}
      <group position={[f.x - 0.33, d.deck + 0.72, d.headHalfW + 0.02]} rotation={[Math.PI / 2, 0, 0]}>
        <mesh position={[0, 0.05, 0]} material={probe}><cylinderGeometry args={[0.035, 0.035, 0.12, 12]} /></mesh>
        <mesh position={[0, 0.0, 0]} material={m.steelDark}><cylinderGeometry args={[0.05, 0.05, 0.04, 6]} /></mesh>
        <mesh position={[0, 0.13, 0]} material={m.black}><cylinderGeometry args={[0.028, 0.028, 0.06, 10]} /></mesh>
      </group>
      {/* head bolts on the flange either side of the cam cover */}
      {[-0.28, 0.28].flatMap((dx) => [-1, 1].map((s) => (
        <mesh key={`${dx}${s}`} position={[f.x + dx, d.headTop + 0.02, s * (d.headHalfW - 0.035)]} material={m.steelDark}>
          <cylinderGeometry args={[0.028, 0.028, 0.04, 6]} />
        </mesh>
      )))}
      <lineSegments ref={edge} geometry={edges} material={edgeMat} position={[f.x, (d.deck + d.coverTop) / 2, 0]} visible={false} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Valvetrain: two camshafts turning at half crank speed, four valves per
// cylinder lifting on the real four-stroke timing. Exhaust-side parts use the
// clipped material, so the cut removes them with the front half, as a real
// sectioned engine would.
// ---------------------------------------------------------------------------
function Valves({ d, f }: { d: Dims; f: CylFrame }) {
  const live = useLive();
  const m = useMats();
  const refs = useRef<(THREE.Group | null)[]>([]);
  const valves = useMemo(() => [-0.17, 0.17].flatMap((dx) => [-0.2, 0.2].map((z) => ({ dx, z, intake: z < 0 }))), []);
  useFrame(() => {
    const cycle = ((live.crank - f.phi) % (4 * Math.PI) + 4 * Math.PI) % (4 * Math.PI);
    valves.forEach((v, i) => {
      const r = refs.current[i];
      if (r) r.position.y = -valveLift(cycle, v.intake) * 0.09;
    });
  });
  const stemLen = d.headTop - d.deck + 0.12;
  return (
    <>
      {valves.map((v, i) => {
        const mat = v.intake ? m.int.steel : m.steel;
        return (
          <group key={i} ref={(el) => { refs.current[i] = el; }} position={[f.x + v.dx, 0, v.z]}>
            <mesh position={[0, d.deck + 0.015, 0]} material={mat}>
              <cylinderGeometry args={[0.1, 0.07, 0.03, 24]} />
            </mesh>
            <mesh position={[0, d.deck + stemLen / 2, 0]} material={mat}>
              <cylinderGeometry args={[0.018, 0.018, stemLen, 10]} />
            </mesh>
            <mesh position={[0, d.headTop + 0.1, 0]} material={v.intake ? m.int.steelDark : m.steelDark}>
              <cylinderGeometry args={[0.055, 0.055, 0.1, 14]} />
            </mesh>
          </group>
        );
      })}
    </>
  );
}

function Camshaft({ g, d, z }: { g: Geometry; d: Dims; z: number }) {
  const live = useLive();
  const m = useMats();
  const ref = useRef<THREE.Group>(null);
  const intake = z < 0;
  // Each lobe points down onto its valve at the middle of that valve's event.
  const lobes = useMemo(() => g.frames.flatMap((f) => [-0.17, 0.17].map((dx) => {
    const midDeg = intake ? 470 : 250;
    return { x: f.x + dx, offset: Math.PI - (f.phi + (midDeg * Math.PI) / 180) / 2 };
  })), [g, intake]);
  const shaft = useMemo(() => cylX(0.045, d.L - 0.3, 20), [d.L]);
  const lobeGeo = useMemo(() => cylX(0.085, 0.07, 24), []);
  useFrame(() => { if (ref.current) ref.current.rotation.x = live.crank / 2; });
  const mat = intake ? m.int.steel : m.steel;
  return (
    <group ref={ref} position={[0, d.headTop + 0.28, z]}>
      <mesh geometry={shaft} material={mat} />
      {lobes.map((l, i) => (
        <group key={i} position={[l.x, 0, 0]} rotation={[l.offset, 0, 0]}>
          <mesh position={[0, 0.03, 0]} geometry={lobeGeo} material={mat} />
        </group>
      ))}
    </group>
  );
}

function CamCover({ d, profile }: { d: Dims; profile: EngineProfile }) {
  const m = useMats();
  const geo = useMemo(() => {
    const y0 = d.headTop, y1 = d.coverTop, w = d.halfW + 0.02;
    const shell = polygon([
      side(-w, y0), side(w, y0), side(w, y1 - 0.2), side(w - 0.18, y1), side(-(w - 0.18), y1), side(-w, y1 - 0.2),
    ], []);
    const inner = polygon([
      side(-(w - 0.08), y0 - 0.01), side(w - 0.08, y0 - 0.01), side(w - 0.08, y1 - 0.23),
      side(w - 0.22, y1 - 0.08), side(-(w - 0.22), y1 - 0.08), side(-(w - 0.08), y1 - 0.23),
    ]);
    shell.holes.push(inner);
    const body = extrudeAlongX([shell], d.xL + 0.06, d.L - 0.12);
    const cap = extrudeAlongX([polygon([
      side(-w, y0), side(w, y0), side(w, y1 - 0.2), side(w - 0.18, y1), side(-(w - 0.18), y1), side(-w, y1 - 0.2),
    ])], -0.05, 0.1);
    return { body, cap };
  }, [d]);
  const plate = useMemo(() => nameplate(profile.short.toUpperCase(), 'PRAMANA DIGITAL TWIN'), [profile.short]);
  return (
    <group>
      <Solid geometry={geo.body} material={m.machined} />
      <Solid geometry={geo.cap} material={m.machined} position={[d.xL + 0.06, 0, 0]} />
      <Solid geometry={geo.cap} material={m.machined} position={[d.xR - 0.06, 0, 0]} />
      {/* stiffening ribs along the top */}
      {[-0.3, 0.3].map((z) => (
        <mesh key={z} position={[0, d.coverTop + 0.015, z]} material={m.machined}>
          <boxGeometry args={[d.L - 0.4, 0.03, 0.04]} />
        </mesh>
      ))}
      {/* identification plate on the camera-facing flank */}
      <mesh position={[-0.2, d.headTop + 0.23, d.halfW + 0.03]}>
        <planeGeometry args={[1.2, 0.3]} />
        <meshStandardMaterial map={plate} metalness={0.6} roughness={0.4} clippingPlanes={m.machined.clippingPlanes} />
      </mesh>
      {/* oil filler cap */}
      <mesh position={[d.xL + 0.45, d.coverTop + 0.05, -0.3]} material={m.anoRed}>
        <cylinderGeometry args={[0.1, 0.1, 0.1, 28]} />
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Common-rail fuel system: rail, four HP lines, injectors through the cover.
// ---------------------------------------------------------------------------
function Injector({ d, f }: { d: Dims; f: CylFrame }) {
  const live = useLive();
  const m = useMats();
  const body = useMemo(() => m.int.steel.clone(), [m]);
  useFrame(() => {
    const h = live.parts.get(`injector:${f.index}`);
    if (h) applyGlow(body, h.p, h.sensor, live.time, 3);
    else body.emissiveIntensity = 0;
  });
  const top = d.coverTop + 0.28;
  return (
    <group position={[f.x, 0, 0]}>
      <mesh position={[0, (d.deck + d.headTop) / 2, 0]} material={body}>
        <cylinderGeometry args={[0.03, 0.02, d.headTop - d.deck, 12]} />
      </mesh>
      <mesh position={[0, (d.headTop + top) / 2, 0]} material={body}>
        <cylinderGeometry args={[0.07, 0.06, top - d.headTop, 20]} />
      </mesh>
      <mesh position={[0, top + 0.07, 0]} material={m.int.steelDark}>
        <boxGeometry args={[0.14, 0.12, 0.1]} />
      </mesh>
      <mesh position={[0, top + 0.07, 0.07]} material={m.black}>
        <boxGeometry args={[0.08, 0.08, 0.08]} />
      </mesh>
    </group>
  );
}

function FuelSystem({ g, d }: { g: Geometry; d: Dims }) {
  const live = useLive();
  const m = useMats();
  const rail = useMemo(() => cloneClipped(m.machined), [m]);
  const railY = d.coverTop + 0.18, railZ = -0.56;
  const x0 = g.frames[0].x - 0.4, x1 = g.frames[g.frames.length - 1].x + 0.4;
  const geo = useMemo(() => ({
    rail: cylX(0.06, x1 - x0, 24),
    lines: g.frames.map((f) => tube([[f.x, railY, railZ + 0.05], [f.x, railY + 0.08, -0.32], [f.x, railY + 0.04, -0.12], [f.x, railY + 0.02, -0.06]], 0.016, 24, 8)),
    feed: tube([[d.xL - 0.2, 2.1, -1.0], [d.xL - 0.3, 3.0, -0.9], [d.xL - 0.1, railY + 0.1, -0.7], [x0, railY, railZ]], 0.018, 40, 8),
    pump: cylX(0.12, 0.3, 24),
  }), [g, d, railY, x0, x1]);
  useFrame(() => {
    const h = live.parts.get('fuel');
    if (h) applyGlow(rail, h.p, h.sensor, live.time);
    else rail.emissiveIntensity = 0;
  });
  return (
    <group>
      <mesh geometry={geo.rail} material={rail} position={[(x0 + x1) / 2, railY, railZ]} castShadow />
      <mesh position={[x1 + 0.05, railY, railZ]} material={m.black} geometry={useMemo(() => cylX(0.045, 0.12, 16), [])} />
      <mesh position={[x0 - 0.04, railY, railZ]} material={m.anoRed} geometry={useMemo(() => cylX(0.05, 0.08, 16), [])} />
      {geo.lines.map((l, i) => <mesh key={i} geometry={l} material={m.steel} />)}
      <mesh geometry={geo.feed} material={m.steel} />
      <mesh geometry={geo.pump} material={rail} position={[d.xL - 0.05, 2.0, -1.0]} castShadow />
      {g.frames.map((f) => <Injector key={f.index} d={d} f={f} />)}
      {/* fuel filter canister on the intake flank */}
      <group position={[-0.9, 1.35, -1.12]}>
        <mesh material={rail}><cylinderGeometry args={[0.14, 0.14, 0.42, 28]} /></mesh>
        <mesh position={[0, 0.05, 0]} material={m.anoBlue}><cylinderGeometry args={[0.145, 0.145, 0.1, 28]} /></mesh>
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Intake: plenum with an integrated liquid-cooled charge-air cooler on top,
// short runners into the head. No throttle body: an unthrottled diesel.
// ---------------------------------------------------------------------------
function Intake({ g, d }: { g: Geometry; d: Dims }) {
  const live = useLive();
  const m = useMats();
  const core = useMemo(() => cloneClipped(m.core), [m]);
  const mapSensor = useMemo(() => cloneClipped(m.black), [m]);
  const x0 = g.frames[0].x - 0.45, x1 = g.frames[g.frames.length - 1].x + 0.45;
  const yc = d.deck + 0.45;
  const plenum = useMemo(() => extrudeAlongX([roundRect(...side(-1.0, yc - 0.3), ...side(-1.46, yc + 0.3), 0.12)], x0, x1 - x0), [x0, x1, yc]);
  useFrame(() => {
    const h = live.parts.get('cooling');
    if (h) applyGlow(core, h.p, h.sensor, live.time);
    else core.emissiveIntensity = 0;
    const s = live.parts.get('sensor:map');
    if (s) applyGlow(mapSensor, s.p, s.sensor, live.time, 3.5);
    else mapSensor.emissiveIntensity = 0;
  });
  const coolerX0 = x0 + 0.25, coolerX1 = x1 - 0.25;
  const fins = Math.round((coolerX1 - coolerX0) / 0.06);
  return (
    <group>
      <mesh geometry={plenum} material={m.castAlu} castShadow receiveShadow />
      {g.frames.map((f) => (
        <mesh key={f.index} position={[f.x, yc, -d.headHalfW - 0.1]} material={m.castAlu} castShadow>
          <boxGeometry args={[0.36, 0.26, 0.2]} />
        </mesh>
      ))}
      {/* charge-air cooler core: frame and fin pack */}
      <group position={[0, yc + 0.5, -1.23]}>
        <mesh material={m.castAlu} castShadow><boxGeometry args={[coolerX1 - coolerX0 + 0.12, 0.44, 0.42]} /></mesh>
        {Array.from({ length: fins }, (_, i) => (
          <mesh key={i} position={[coolerX0 + i * 0.06 + 0.03, 0.02, 0.215]} material={core}>
            <boxGeometry args={[0.012, 0.36, 0.02]} />
          </mesh>
        ))}
        {/* coolant in/out */}
        {[-0.5, 0.5].map((x) => (
          <mesh key={x} position={[x, 0.26, -0.1]} material={m.anoBlue}>
            <cylinderGeometry args={[0.04, 0.04, 0.12, 16]} />
          </mesh>
        ))}
      </group>
      {/* MAP sensor: parity path 1 (speed-density) starts here */}
      <group position={[x0 + 0.3, yc + 0.3, -1.1]}>
        <mesh material={mapSensor}><cylinderGeometry args={[0.05, 0.05, 0.12, 16]} /></mesh>
        <mesh position={[0, -0.05, 0]} material={m.anoBlue}><cylinderGeometry args={[0.055, 0.055, 0.03, 16]} /></mesh>
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Exhaust: four heat-blued headers into a log, the turbo, the downpipe with
// its lambda sensor. Header glow follows each cylinder's own EGT.
// ---------------------------------------------------------------------------
const TURBO_AT: [number, number, number] = [1.2, 1.5, 1.4];

function Header({ d, f }: { d: Dims; f: CylFrame }) {
  const live = useLive();
  const m = useMats();
  const mat = useMemo(() => cloneClipped(m.exhaust), [m]);
  const probe = useMemo(() => cloneClipped(m.steel), [m]);
  const yc = d.deck + 0.45;
  const geo = useMemo(() => paintTemper(tube([
    [f.x, yc, d.headHalfW + 0.06], [f.x, yc - 0.08, d.headHalfW + 0.25], [f.x + 0.06, yc - 0.45, 1.12], [f.x + 0.16, 2.08, 1.15],
  ], 0.075, 36, 14)), [f, d, yc]);
  useFrame(() => {
    const heat = THREE.MathUtils.clamp((live.egt[f.index] - (live.nominalEgt + 60)) / 160, 0, 1);
    mat.emissive.set('#ff4a14');
    mat.emissiveIntensity = heat * heat * 1.6;
    const h = live.parts.get(`egt:${f.index}`);
    if (h) applyGlow(probe, h.p, h.sensor, live.time, 3.5);
    else probe.emissiveIntensity = 0;
  });
  return (
    <group>
      <mesh geometry={geo} material={mat} castShadow />
      {/* EGT probe: the per-cylinder thermocouple */}
      <mesh position={[f.x, yc - 0.12, d.headHalfW + 0.33]} rotation={[Math.PI / 2, 0, 0]} material={probe}>
        <cylinderGeometry args={[0.014, 0.014, 0.22, 8]} />
      </mesh>
    </group>
  );
}

function Exhaust({ g, d }: { g: Geometry; d: Dims }) {
  const live = useLive();
  const m = useMats();
  const lambda = useMemo(() => cloneClipped(m.steel), [m]);
  const [tx, ty, tz] = TURBO_AT;
  const geo = useMemo(() => ({
    log: paintTemper(tube([[g.frames[0].x - 0.05, 2.08, 1.15], [0, 2.08, 1.15], [tx - 0.3, 2.02, 1.15], [tx - 0.3, 1.8, 1.15]], 0.1, 50, 16), 0.7, 0.4),
    down: paintTemper(tube([[tx - 0.5, ty, tz], [tx - 0.75, ty - 0.1, tz + 0.05], [tx - 0.9, ty - 0.8, tz + 0.1], [tx - 0.95, -1.3, tz + 0.1]], 0.11, 48, 16), 0.45, 0.05),
    charge: tube([[tx + 0.3, ty + 0.3, tz - 0.26], [tx + 0.45, 2.7, 1.25], [tx + 0.8, 4.3, 0.6], [tx + 0.85, 4.35, -0.8], [tx + 0.4, 3.5, -1.2], [g.frames[g.frames.length - 1].x + 0.3, d.deck + 0.95, -1.23]], 0.1, 90, 18),
  }), [g, d, tx, ty, tz]);
  useFrame(() => {
    const h = live.parts.get('sensor:lambda');
    if (h) applyGlow(lambda, h.p, h.sensor, live.time, 3.5);
    else lambda.emissiveIntensity = 0;
  });
  return (
    <group>
      {g.frames.map((f) => <Header key={f.index} d={d} f={f} />)}
      <mesh geometry={geo.log} material={m.exhaust} castShadow />
      <mesh geometry={geo.down} material={m.exhaust} castShadow />
      <Turbo position={TURBO_AT} />
      {/* blue silicone charge pipe arcing over the engine to the cooler, with red anodised clamps */}
      <mesh geometry={geo.charge} material={m.silicone} castShadow />
      {[0.08, 0.93].map((u) => {
        const p = geo.charge.parameters.path.getPointAt(u);
        const t = geo.charge.parameters.path.getTangentAt(u);
        const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), t);
        return (
          <mesh key={u} position={p} quaternion={q} material={m.anoRed}>
            <cylinderGeometry args={[0.115, 0.115, 0.05, 24]} />
          </mesh>
        );
      })}
      {/* intake filter on the compressor inlet */}
      <group position={[tx + 0.75, ty, tz]} rotation={[0, 0, -Math.PI / 2]}>
        <mesh material={m.black}><cylinderGeometry args={[0.2, 0.15, 0.3, 32]} /></mesh>
        <mesh position={[0, -0.16, 0]} material={m.anoRed}><cylinderGeometry args={[0.155, 0.155, 0.04, 32]} /></mesh>
      </group>
      {/* wideband lambda sensor in the downpipe: parity path 3 */}
      <group position={[tx - 0.87, 0.9, tz + 0.2]} rotation={[Math.PI / 2, 0, 0]}>
        <mesh material={lambda}><cylinderGeometry args={[0.03, 0.03, 0.2, 10]} /></mesh>
        <mesh position={[0, 0.06, 0]} material={m.steelDark}><cylinderGeometry args={[0.05, 0.05, 0.05, 6]} /></mesh>
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------
// Sump, oil filter, timing/accessory drive, coolant hoses.
// ---------------------------------------------------------------------------
function Bottom({ d }: { d: Dims }) {
  const live = useLive();
  const m = useMats();
  const filter = useMemo(() => cloneClipped(m.anoBlue), [m]);
  const geo = useMemo(() => {
    const y0 = d.skirtBottom, y1 = d.sumpBottom;
    const u = polygon([
      side(-1.0, y0), side(-0.92, y1 + 0.2), side(-0.8, y1), side(0.8, y1), side(0.92, y1 + 0.2), side(1.0, y0),
      side(0.92, y0), side(0.85, y1 + 0.22), side(0.75, y1 + 0.08), side(-0.75, y1 + 0.08), side(-0.85, y1 + 0.22), side(-0.92, y0),
    ]);
    return {
      sump: extrudeAlongX([u], d.xL + 0.08, d.L - 0.16),
      end: new THREE.BoxGeometry(0.06, y0 - y1, 1.9),
    };
  }, [d]);
  useFrame(() => {
    const h = live.parts.get('oil');
    if (h) applyGlow(filter, h.p, h.sensor, live.time);
    else filter.emissiveIntensity = 0;
  });
  return (
    <group>
      <Solid geometry={geo.sump} material={m.castAlu} />
      <Solid geometry={geo.end} material={m.castAlu} position={[d.xL + 0.11, (d.skirtBottom + d.sumpBottom) / 2, 0]} />
      <Solid geometry={geo.end} material={m.castAlu} position={[d.xR - 0.11, (d.skirtBottom + d.sumpBottom) / 2, 0]} />
      {/* oil, visible only once the sump is cut */}
      <mesh position={[0, d.sumpBottom + 0.3, 0]} rotation={[-Math.PI / 2, 0, 0]} material={m.oil}>
        <planeGeometry args={[d.L - 0.4, 1.66]} />
      </mesh>
      <mesh position={[0.4, d.sumpBottom - 0.03, 0.3]} material={m.steelDark}>
        <cylinderGeometry args={[0.05, 0.05, 0.06, 6]} />
      </mesh>
      <group position={[0.3, 0.1, -1.1]}>
        <mesh material={filter} castShadow><cylinderGeometry args={[0.16, 0.16, 0.42, 32]} /></mesh>
        <mesh position={[0, -0.23, 0]} material={m.steelDark}><cylinderGeometry args={[0.12, 0.12, 0.05, 6]} /></mesh>
      </group>
    </group>
  );
}

function FrontEnd({ d }: { d: Dims }) {
  const live = useLive();
  const m = useMats();
  const spin = useRef<THREE.Group[]>([]);
  const xf = d.xL - 0.16;
  const pulleys: { y: number; z: number; r: number; ratio: number }[] = [
    { y: 0, z: 0, r: 0.34, ratio: 1 },
    { y: 1.25, z: 0.42, r: 0.19, ratio: 1.8 },
    { y: 0.95, z: -1.02, r: 0.15, ratio: 2.3 },
    { y: 2.05, z: -0.35, r: 0.2, ratio: 0.5 },
  ];
  const geo = useMemo(() => {
    const cover = extrudeAlongX([roundRect(...side(0.72, -0.5), ...side(-0.72, d.headTop + 0.3), 0.2)], d.xL - 0.1, 0.1);
    // belt: a loop around the outside of the pulleys
    const pts: [number, number][] = [];
    const hull: { y: number; z: number; r: number }[] = [pulleys[0], pulleys[2], pulleys[3], pulleys[1]];
    hull.forEach((p, i) => {
      const prev = hull[(i + hull.length - 1) % hull.length], next = hull[(i + 1) % hull.length];
      const a0 = Math.atan2(p.y - prev.y, p.z - prev.z) + Math.PI / 2;
      const a1 = Math.atan2(next.y - p.y, next.z - p.z) + Math.PI / 2;
      let da = a1 - a0;
      while (da <= 0) da += Math.PI * 2;
      for (let k = 0; k <= 6; k++) {
        const a = a0 + (da * k) / 6;
        pts.push([p.z + Math.cos(a) * (p.r + 0.02), p.y + Math.sin(a) * (p.r + 0.02)]);
      }
    });
    const curve = new THREE.CatmullRomCurve3(pts.map(([z, y]) => new THREE.Vector3(xf - 0.02, y, z)), true);
    const belt = new THREE.TubeGeometry(curve, 160, 0.025, 6, true);
    return { cover, belt, pulley: cylX(1, 0.08, 40), damper: cylX(1, 0.05, 40) };
  }, [d, xf]);
  useFrame(() => {
    spin.current.forEach((g, i) => { if (g) g.rotation.x = live.crank * pulleys[i].ratio; });
  });
  return (
    <group>
      <Solid geometry={geo.cover} material={m.castAlu} />
      {pulleys.map((p, i) => (
        <group key={i} ref={(el) => { if (el) spin.current[i] = el; }} position={[xf, p.y, p.z]}>
          <mesh geometry={geo.pulley} scale={[1, p.r, p.r]} material={m.machined} castShadow />
          <mesh geometry={geo.damper} scale={[1.2, p.r * 0.45, p.r * 0.45]} position={[-0.05, 0, 0]} material={m.steelDark} />
          {[0, 1, 2, 3, 4].map((k) => (
            <mesh key={k} position={[-0.045, Math.cos((k / 5) * Math.PI * 2) * p.r * 0.62, Math.sin((k / 5) * Math.PI * 2) * p.r * 0.62]} material={m.steelDark}>
              <boxGeometry args={[0.02, p.r * 0.14, p.r * 0.14]} />
            </mesh>
          ))}
        </group>
      ))}
      <mesh geometry={geo.belt} material={m.rubber} />
      {/* alternator body behind its pulley */}
      <mesh position={[d.xL + 0.18, 0.95, -1.02]} geometry={useMemo(() => cylX(0.26, 0.5, 32), [])} material={m.castAlu} castShadow />
      {/* thermostat housing and the coolant hoses */}
      <mesh position={[d.xL + 0.05, d.headTop - 0.1, 0.55]} material={m.castAlu} castShadow>
        <boxGeometry args={[0.2, 0.2, 0.22]} />
      </mesh>
      <mesh geometry={useMemo(() => tube([[d.xL + 0.05, d.headTop - 0.1, 0.66], [d.xL - 0.3, d.headTop - 0.3, 0.9], [d.xL - 0.45, 1.6, 0.95], [d.xL - 0.4, 0.6, 0.7], [d.xL - 0.24, 1.15, 0.45]], 0.07, 60, 14), [d])} material={m.rubber} />
    </group>
  );
}

// ---------------------------------------------------------------------------
// Reduction gearbox: real meshing gears at the profile's ratio, propeller
// shaft offset above the crank, flange and spinner.
// ---------------------------------------------------------------------------
export function Gearbox({ x0, ratio, rA = 0.28, R = 0.66, len = 0.72 }: { x0: number; ratio: number; rA?: number; R?: number; len?: number }) {
  const live = useLive();
  const m = useMats();
  const pinion = useRef<THREE.Group>(null);
  const wheel = useRef<THREE.Group>(null);
  const prop = useRef<THREE.Group>(null);
  const teethA = 22;
  const teethB = Math.round(teethA * ratio);
  const rB = rA * teethB / teethA, cd = rA + rB;
  const geo = useMemo(() => {
    const hull = (inset: number): P2[] => {
      const pts: P2[] = [];
      for (let i = 0; i <= 24; i++) {
        const a = Math.PI + (i / 24) * Math.PI;
        pts.push(side(Math.cos(a) * (R - inset), Math.sin(a) * (R - inset)));
      }
      for (let i = 0; i <= 24; i++) {
        const a = (i / 24) * Math.PI;
        pts.push(side(Math.cos(a) * (rB + 0.18 - inset), cd + Math.sin(a) * (rB + 0.18 - inset)));
      }
      return pts;
    };
    const ring = polygon(hull(0));
    ring.holes.push(new THREE.Path(hull(0.08).map(([a, b]) => new THREE.Vector2(a, b)).reverse()));
    const plate = (holes: THREE.Path[]) => polygon(hull(0), holes);
    return {
      housing: extrudeAlongX([ring], x0, len),
      rear: extrudeAlongX([plate([circle(...side(0, 0), 0.14)])], x0, 0.06),
      front: extrudeAlongX([plate([circle(...side(0, cd), 0.12)])], x0 + len - 0.08, 0.08),
      gearA: extrudeAlongX([gearShape(teethA, rA - 0.03, rA + 0.03, 0.1)], -0.07, 0.14, false),
      gearB: extrudeAlongX([gearShape(teethB, rB - 0.03, rB + 0.03, 0.1)], -0.07, 0.14, false),
      shaft: cylX(0.09, len + 0.2, 24),
      stub: cylX(0.1, len / 2 + 0.06, 24),
      flange: cylX(0.3, 0.07, 48),
      spinnerCone: (() => { const c = new THREE.ConeGeometry(0.32, 0.6, 48); c.rotateZ(-Math.PI / 2); return c; })(),
    };
  }, [x0, cd, rA, rB, R, len, teethA, teethB]);
  useFrame(() => {
    if (pinion.current) pinion.current.rotation.x = live.crank;
    // External mesh reverses direction; half-tooth phase so the teeth interleave.
    if (wheel.current) wheel.current.rotation.x = -live.crank / ratio + Math.PI / teethB;
    if (prop.current) prop.current.rotation.x = -live.crank / ratio;
  });
  return (
    <group>
      <Solid geometry={geo.housing} material={m.castAlu} />
      <Solid geometry={geo.rear} material={m.castAlu} />
      <Solid geometry={geo.front} material={m.castAlu} />
      <group ref={pinion} position={[x0 + len / 2, 0, 0]}>
        <mesh geometry={geo.gearA} material={m.int.gear} />
        <mesh geometry={geo.stub} material={m.int.steel} position={[-len / 4, 0, 0]} />
      </group>
      <group position={[x0 + len / 2, cd, 0]}>
        <group ref={wheel}><mesh geometry={geo.gearB} material={m.int.gear} /></group>
      </group>
      <group ref={prop} position={[x0 + len + 0.1, cd, 0]}>
        <mesh geometry={geo.shaft} material={m.int.steel} position={[-len / 2 + 0.05, 0, 0]} />
        <mesh geometry={geo.flange} material={m.machined} position={[0.1, 0, 0]} castShadow />
        {Array.from({ length: 6 }, (_, i) => {
          const a = (i / 6) * Math.PI * 2;
          return (
            <mesh key={i} position={[0.15, Math.cos(a) * 0.21, Math.sin(a) * 0.21]} material={m.steelDark}>
              <boxGeometry args={[0.04, 0.05, 0.05]} />
            </mesh>
          );
        })}
        <mesh geometry={geo.spinnerCone} position={[0.46, 0, 0]} material={m.machined} castShadow />
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------

/** Invisible per-cylinder pick volumes: the cylinder is one of four columns of a monoblock. */
function PickVolumes({ g, d, onSelect, onHover }: { g: Geometry; d: Dims; onSelect: (i: number) => void; onHover: (i: number | null) => void }) {
  return (
    <>
      {g.frames.map((f) => (
        <mesh
          key={f.index}
          position={[f.x, (d.barrelBottom + d.coverTop) / 2, 0]}
          onClick={(e) => { e.stopPropagation(); onSelect(f.index); }}
          onPointerOver={(e) => { e.stopPropagation(); onHover(f.index); }}
          onPointerOut={() => onHover(null)}
        >
          <boxGeometry args={[g.pitch - 0.02, d.coverTop - d.barrelBottom + 0.2, d.headHalfW * 2 + 0.3]} />
          <meshBasicMaterial colorWrite={false} depthWrite={false} />
        </mesh>
      ))}
    </>
  );
}

/** Floor halo under a faulted cylinder: plain geometry, readable from the back of a room. */
function Halo({ f, y }: { f: CylFrame; y: number }) {
  const live = useLive();
  const mat = useMemo(() => new THREE.MeshBasicMaterial({ color: GLOW_FAULT, transparent: true, opacity: 0, depthWrite: false, toneMapped: false, blending: THREE.AdditiveBlending }), []);
  useFrame(() => {
    const h = live.parts.get(`cyl:${f.index}`);
    mat.opacity = h ? Math.min(0.55, h.p * 0.6) : 0;
    if (h) mat.color.set(h.sensor ? '#22d3ee' : '#ff7a1a');
  });
  return (
    <mesh position={[f.x, y, 0.3]} rotation={[-Math.PI / 2, 0, 0]} material={mat}>
      <circleGeometry args={[0.8, 48]} />
    </mesh>
  );
}

export function Inline4({ g, profile, onSelect, onHover }: {
  g: Geometry; profile: EngineProfile; onSelect: (i: number) => void; onHover: (i: number | null) => void;
}) {
  const d = useMemo(() => inlineDims(g), [g]);
  return (
    <group>
      <Block g={g} d={d} />
      {g.frames.map((f, i) => <HeadSlice key={f.index} g={g} d={d} f={f} last={i === g.frames.length - 1} />)}
      {g.frames.map((f) => <Valves key={f.index} d={d} f={f} />)}
      <Camshaft g={g} d={d} z={-0.2} />
      <Camshaft g={g} d={d} z={0.2} />
      <CamCover d={d} profile={profile} />
      <FuelSystem g={g} d={d} />
      <Intake g={g} d={d} />
      <Exhaust g={g} d={d} />
      <Bottom d={d} />
      <FrontEnd d={d} />
      <Gearbox x0={d.xR} ratio={profile.gearRatio} />
      <Internals g={g} crankLength={d.L + 0.3} />
      {g.frames.map((f) => <Halo key={f.index} f={f} y={d.sumpBottom - 0.04} />)}
      <PickVolumes g={g} d={d} onSelect={onSelect} onHover={onHover} />
    </group>
  );
}

export const INLINE_FLOOR = (g: Geometry) => inlineDims(g).sumpBottom - 0.05;
