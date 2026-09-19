/**
 * Cutaway with true section faces.
 *
 * Clipping alone leaves a cut casting hollow: you look through the missing
 * front wall at the inside of the back wall, which reads as a paper shell.
 * Real cutaway engines (and every engineering drawing) show the cut face as
 * solid, hatched metal. This is the stencil technique from three.js's
 * webgl_clipping_stencil example:
 *
 *   1. every closed Solid draws its back faces (+1) and front faces (-1) into
 *      the stencil buffer, clipped by the section plane, colour/depth off;
 *   2. wherever the count is non-zero the eye ray crosses the plane inside
 *      metal, so a hatched cap quad lying on the plane is drawn there.
 *
 * Only CLOSED geometry may be a Solid (extrusions, boxes, capped cylinders).
 * Open tubes are clipped without a cap and rendered double-sided instead.
 */

import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { useLive } from './live';
import { sectionHatch } from './textures';

interface SectionCtx {
  back: THREE.MeshBasicMaterial;
  front: THREE.MeshBasicMaterial;
}

const Ctx = createContext<SectionCtx | null>(null);

function stencilMaterial(side: THREE.Side, op: THREE.StencilOp, plane: THREE.Plane) {
  return new THREE.MeshBasicMaterial({
    side,
    colorWrite: false,
    depthWrite: false,
    depthTest: false,
    stencilWrite: true,
    stencilFunc: THREE.AlwaysStencilFunc,
    stencilFail: op,
    stencilZFail: op,
    stencilZPass: op,
    clippingPlanes: [plane],
  });
}

export function SectionProvider({ children }: { children: ReactNode }) {
  const live = useLive();
  const gl = useThree((s) => s.gl);
  gl.localClippingEnabled = true;

  const value = useMemo<SectionCtx>(() => ({
    back: stencilMaterial(THREE.BackSide, THREE.IncrementWrapStencilOp, live.plane),
    front: stencilMaterial(THREE.FrontSide, THREE.DecrementWrapStencilOp, live.plane),
  }), [live.plane]);

  const cap = useMemo(() => {
    const map = sectionHatch().clone();
    map.repeat.set(70, 70);
    map.needsUpdate = true;
    const m = new THREE.MeshStandardMaterial({
      map,
      roughness: 0.82,
      metalness: 0.08,
      emissive: new THREE.Color('#5a1410'),
      emissiveIntensity: 0.35,
      stencilWrite: true,
      stencilRef: 0,
      stencilFunc: THREE.NotEqualStencilFunc,
      stencilFail: THREE.ReplaceStencilOp,
      stencilZFail: THREE.ReplaceStencilOp,
      stencilZPass: THREE.ReplaceStencilOp,
    });
    return m;
  }, []);

  const capMesh = useMemo(() => {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), cap);
    mesh.renderOrder = 1.1;
    mesh.onAfterRender = (renderer) => renderer.clearStencil();
    return mesh;
  }, [cap]);

  useFrame(() => {
    const on = live.cut > 0.002;
    value.back.visible = on;
    value.front.visible = on;
    cap.visible = on;
    capMesh.position.z = live.plane.constant;
  });

  return (
    <Ctx.Provider value={value}>
      {children}
      <primitive object={capMesh} />
    </Ctx.Provider>
  );
}

export interface SolidProps {
  geometry: THREE.BufferGeometry;
  material: THREE.Material;
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: number | [number, number, number];
  castShadow?: boolean;
  receiveShadow?: boolean;
  name?: string;
}

/** A closed mesh that participates in the section cap. */
export function Solid({ geometry, material, position, rotation, scale, castShadow = true, receiveShadow = true, name }: SolidProps) {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('Solid outside SectionProvider');
  return (
    <group position={position} rotation={rotation} scale={scale} name={name}>
      <mesh geometry={geometry} material={material} castShadow={castShadow} receiveShadow={receiveShadow} />
      <mesh geometry={geometry} material={ctx.back} renderOrder={1} />
      <mesh geometry={geometry} material={ctx.front} renderOrder={1} />
    </group>
  );
}
