/**
 * Surface detail generated at startup, so the demo needs no network and ships
 * no image files. A smooth grey cylinder reads as plastic; the same cylinder
 * with sand-cast grain in its roughness and a faint bump reads as a casting.
 */

import * as THREE from 'three';

function hash(x: number, y: number, seed: number): number {
  let h = (x * 374761393 + y * 668265263 + seed * 144269504) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

function valueNoise(x: number, y: number, period: number, seed: number): number {
  const xi = Math.floor(x), yi = Math.floor(y);
  const xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const w = (i: number, j: number) => hash(((xi + i) % period + period) % period, ((yi + j) % period + period) % period, seed);
  return (w(0, 0) * (1 - u) + w(1, 0) * u) * (1 - v) + (w(0, 1) * (1 - u) + w(1, 1) * u) * v;
}

function makeTexture(size: number, fill: (x: number, y: number) => number, repeat: number): THREE.DataTexture {
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const val = Math.max(0, Math.min(255, Math.round(fill(x, y) * 255)));
      const i = (y * size + x) * 4;
      data[i] = data[i + 1] = data[i + 2] = val;
      data[i + 3] = 255;
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = 4;
  tex.needsUpdate = true;
  return tex;
}

let _grain: THREE.DataTexture | null = null;
/** Sand-cast grain: fine fBm around mid-grey, tileable. */
export function castGrain(): THREE.DataTexture {
  if (_grain) return _grain;
  const size = 256;
  _grain = makeTexture(size, (x, y) => {
    let n = 0, amp = 0.5, f = 1 / 4;
    for (let o = 0; o < 4; o++) {
      const p = Math.round(size * f);
      n += valueNoise(x * f, y * f, p, o + 3) * amp;
      amp *= 0.5; f *= 2;
    }
    return 0.62 + (n - 0.47) * 0.75;
  }, 3);
  return _grain;
}

let _brushed: THREE.DataTexture | null = null;
/** Machined / brushed finish: long streaks in one direction. */
export function brushed(): THREE.DataTexture {
  if (_brushed) return _brushed;
  const size = 256;
  const rows = Array.from({ length: size }, (_, y) => hash(0, y, 91));
  _brushed = makeTexture(size, (x, y) => {
    const streak = rows[y] * 0.55 + valueNoise(x / 64, y / 2, size / 64, 17) * 0.45;
    return 0.72 + (streak - 0.5) * 0.35;
  }, 2);
  return _brushed;
}

let _hatch: THREE.CanvasTexture | null = null;
/** Engineering section hatch for cut faces, the convention every drawing uses. */
export function sectionHatch(): THREE.CanvasTexture {
  if (_hatch) return _hatch;
  const size = 128;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d')!;
  g.fillStyle = '#b8322a';
  g.fillRect(0, 0, size, size);
  g.strokeStyle = 'rgba(255,214,196,0.55)';
  g.lineWidth = 3;
  for (let i = -size; i < size * 2; i += 16) {
    g.beginPath();
    g.moveTo(i, 0);
    g.lineTo(i + size, size);
    g.stroke();
  }
  _hatch = new THREE.CanvasTexture(c);
  _hatch.wrapS = _hatch.wrapT = THREE.RepeatWrapping;
  _hatch.repeat.set(10, 10);
  _hatch.colorSpace = THREE.SRGBColorSpace;
  _hatch.anisotropy = 4;
  return _hatch;
}

const _plates = new Map<string, THREE.CanvasTexture>();
/** Cast/etched identification plate. System fonts only: no font download. */
export function nameplate(title: string, sub: string): THREE.CanvasTexture {
  const key = `${title}|${sub}`;
  const hit = _plates.get(key);
  if (hit) return hit;
  const w = 1024, h = 256;
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d')!;
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, '#2c323b');
  grad.addColorStop(1, '#1b1f25');
  g.fillStyle = grad;
  g.fillRect(0, 0, w, h);
  g.strokeStyle = '#8b939e';
  g.lineWidth = 6;
  g.strokeRect(10, 10, w - 20, h - 20);
  g.fillStyle = '#d9dee5';
  g.font = '700 104px "Helvetica Neue", Helvetica, Arial, sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText(title, w / 2, h * 0.42);
  g.fillStyle = '#9aa3ae';
  g.font = '500 40px "Helvetica Neue", Helvetica, Arial, sans-serif';
  g.fillText(sub, w / 2, h * 0.78);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  _plates.set(key, tex);
  return tex;
}

let _floor: THREE.CanvasTexture | null = null;
/** Soft pool of light on the studio floor, fully transparent at the edge. */
export function floorPool(): THREE.CanvasTexture {
  if (_floor) return _floor;
  const size = 512;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d')!;
  const r = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  r.addColorStop(0, 'rgba(58,70,86,0.95)');
  r.addColorStop(0.45, 'rgba(34,42,54,0.6)');
  r.addColorStop(1, 'rgba(10,13,18,0)');
  g.fillStyle = r;
  g.fillRect(0, 0, size, size);
  _floor = new THREE.CanvasTexture(c);
  _floor.colorSpace = THREE.SRGBColorSpace;
  return _floor;
}
