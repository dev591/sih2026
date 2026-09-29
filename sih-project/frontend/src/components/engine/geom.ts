/**
 * Geometry builders. Castings are extrusions of real cross-sections (bores,
 * water-jacket passages and crank holes are true holes), so they are closed
 * solids the section cap can fill, and the cut shows metal where metal is.
 */

import * as THREE from 'three';

export type P2 = [number, number];

export function polygon(pts: P2[], holes: THREE.Path[] = []): THREE.Shape {
  const s = new THREE.Shape(pts.map(([a, b]) => new THREE.Vector2(a, b)));
  s.holes.push(...holes);
  return s;
}

export function circle(cx: number, cy: number, r: number, cw = true): THREE.Path {
  const p = new THREE.Path();
  p.absarc(cx, cy, r, 0, Math.PI * 2, cw);
  return p;
}

export function roundRect(x0: number, y0: number, x1: number, y1: number, r: number, asHole = false): THREE.Shape {
  const s = new THREE.Shape();
  s.moveTo(x0 + r, y0);
  s.lineTo(x1 - r, y0);
  s.quadraticCurveTo(x1, y0, x1, y0 + r);
  s.lineTo(x1, y1 - r);
  s.quadraticCurveTo(x1, y1, x1 - r, y1);
  s.lineTo(x0 + r, y1);
  s.quadraticCurveTo(x0, y1, x0, y1 - r);
  s.lineTo(x0, y0 + r);
  s.quadraticCurveTo(x0, y0, x0 + r, y0);
  if (asHole) return new THREE.Shape(s.getPoints(4).reverse());
  return s;
}

const BEVEL = { bevelEnabled: true, bevelSize: 0.012, bevelThickness: 0.012, bevelSegments: 2, curveSegments: 28 };

/**
 * Vertical extrusion: `build` receives a mapper from world (x, z) to shape
 * space and returns shapes; the solid spans world y0..y0+h.
 */
export function extrudeUp(shapes: THREE.Shape[], y0: number, h: number, bevel = true): THREE.ExtrudeGeometry {
  const b = bevel ? BEVEL : { bevelEnabled: false, curveSegments: 28 };
  const bt = bevel ? BEVEL.bevelThickness : 0;
  const g = new THREE.ExtrudeGeometry(shapes, { depth: h - 2 * bt, ...b });
  g.rotateX(-Math.PI / 2);          // shape y -> world -z, depth -> world +y
  g.translate(0, y0 + bt, 0);
  return g;
}
/** World (x, z) -> shape coordinates for extrudeUp. */
export const up = (x: number, z: number): P2 => [x, -z];

/** Extrusion along +X: profile given in world (z, y); spans x0..x0+len. */
export function extrudeAlongX(shapes: THREE.Shape[], x0: number, len: number, bevel = true): THREE.ExtrudeGeometry {
  const b = bevel ? BEVEL : { bevelEnabled: false, curveSegments: 28 };
  const bt = bevel ? BEVEL.bevelThickness : 0;
  const g = new THREE.ExtrudeGeometry(shapes, { depth: len - 2 * bt, ...b });
  g.rotateY(Math.PI / 2);           // shape x -> world -z, depth -> world +x
  g.translate(x0 + bt, 0, 0);
  return g;
}
/** World (z, y) -> shape coordinates for extrudeAlongX. */
export const side = (z: number, y: number): P2 => [-z, y];

/** Spur gear outline with a bore, for extrusion along X. */
export function gearShape(teeth: number, rRoot: number, rTip: number, bore: number): THREE.Shape {
  const s = new THREE.Shape();
  const steps = teeth * 4;
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    const phase = i % 4;
    const r = phase === 1 || phase === 2 ? rTip : rRoot;
    const x = Math.cos(a) * r, y = Math.sin(a) * r;
    if (i === 0) s.moveTo(x, y); else s.lineTo(x, y);
  }
  s.holes.push(circle(0, 0, bore));
  return s;
}

/**
 * Turbo volute: a tube spiralling once around the wheel axis (+X here) with
 * a cross-section growing toward the outlet, which is the actual shape of a
 * turbine or compressor housing: the scroll collects flow along its length.
 */
export function volute(rMean: number, tube0: number, tube1: number, turn = Math.PI * 1.85): THREE.TubeGeometry {
  class Spiral extends THREE.Curve<THREE.Vector3> {
    constructor() { super(); }
    getPoint(u: number, out = new THREE.Vector3()) {
      const a = u * turn;
      const r = rMean + (tube1 - tube0) * u * 0.9;
      return out.set(0, Math.cos(a) * r, Math.sin(a) * r);
    }
  }
  const segs = 64, radial = 20;
  const g = new THREE.TubeGeometry(new Spiral(), segs, 1, radial, false);
  // TubeGeometry has a single radius; rescale each ring about its centre so
  // the scroll grows along its length.
  const pos = g.attributes.position;
  const curve = new Spiral();
  const c = new THREE.Vector3();
  for (let i = 0; i <= segs; i++) {
    const u = i / segs;
    curve.getPoint(u, c);
    const rad = tube0 + (tube1 - tube0) * u;
    for (let j = 0; j <= radial; j++) {
      const k = i * (radial + 1) + j;
      pos.setXYZ(
        k,
        c.x + (pos.getX(k) - c.x) * rad,
        c.y + (pos.getY(k) - c.y) * rad,
        c.z + (pos.getZ(k) - c.z) * rad,
      );
    }
  }
  pos.needsUpdate = true;
  g.computeVertexNormals();
  return g;
}

export function tube(points: [number, number, number][], radius: number, segs = 40, radial = 14): THREE.TubeGeometry {
  const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)), false, 'centripetal');
  return new THREE.TubeGeometry(curve, segs, radius, radial, false);
}

/** Closed cylinder along +X centred at the origin. */
export function cylX(r: number, len: number, seg = 32, r2 = r): THREE.CylinderGeometry {
  const g = new THREE.CylinderGeometry(r2, r, len, seg);
  g.rotateZ(-Math.PI / 2);
  return g;
}
/** Closed cylinder along +Z centred at the origin. */
export function cylZ(r: number, len: number, seg = 32): THREE.CylinderGeometry {
  const g = new THREE.CylinderGeometry(r, r, len, seg);
  g.rotateX(Math.PI / 2);
  return g;
}
