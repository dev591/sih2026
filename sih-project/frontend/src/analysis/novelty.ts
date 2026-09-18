/**
 * Novelty detection — "is this a fault I recognise, or something else?"
 *
 * See docs/spec/novelty-detection.md for the full argument. In short:
 *
 *   Each known fault defines a DIRECTION in the 13-dimensional residual space.
 *   Stack them into F. Split the live residual into the part F can account for
 *   and the part it cannot:
 *
 *       rho_hat  = proj_span(F) rho     explained
 *       rho_perp = rho - rho_hat        UNEXPLAINED
 *       nu       = ||rho_perp|| / ||rho||
 *
 *   High ||rho|| with high nu means something real is happening that the fault
 *   library cannot explain — an unmodelled failure mode, or a twin that has
 *   drifted from the engine. Either way the correct output is "my confidence in
 *   this diagnosis is low", not a confident wrong answer.
 *
 * REFERENCE IMPLEMENTATION. BE-2: port this, but use a proper SVD for the rank
 * check and report the singular values on the validation slide. Here we use
 * rank-revealing modified Gram-Schmidt, which gives the same orthonormal basis
 * and the same effective rank without pulling in a linear-algebra dependency.
 *
 * DO NOT use normal equations (F^T F)^-1 — the columns are deliberately
 * collinear (EGT drift, CHT drift and detonation all excite rho6..9 similarly)
 * and the Gram matrix is ill-conditioned by construction.
 */

import { INCIDENCE, FAULT_ORDER } from './incidence';
import type { ResidualVector } from '../types/telemetry';

export const N_RESIDUALS = 13;

/** Relative tolerance for calling a direction linearly independent. */
const RANK_TOL = 0.05;

export interface NoveltyResult {
  /** nu in [0,1] — fraction of the residual that no known fault explains. */
  index: number;
  residualNorm: number;
  unexplainedNorm: number;
  /** rank(F) at RANK_TOL — how many genuinely independent fault directions. */
  effectiveRank: number;
  /** 13 - effectiveRank. If this is 0 the whole claim collapses; check it. */
  nullSpaceDim: number;
  /** Per-residual unexplained component, for the explain drawer. */
  unexplained: number[];
}

// ---------------------------------------------------------------------------
// Build an orthonormal basis for span(F) once, at module load.
// ---------------------------------------------------------------------------

function norm(v: number[]) {
  return Math.sqrt(v.reduce((a, x) => a + x * x, 0));
}
function dot(a: number[], b: number[]) {
  return a.reduce((s, x, i) => s + x * b[i], 0);
}

/**
 * Rank-revealing modified Gram-Schmidt.
 * Returns an orthonormal basis for the column space, skipping any direction
 * that is (numerically) already spanned by the ones before it.
 */
function orthonormalBasis(columns: number[][], tol: number): number[][] {
  const basis: number[][] = [];
  // Scale tolerance by the largest input magnitude so it is relative, not absolute.
  const scale = Math.max(...columns.map(norm), 1e-12);

  for (const col of columns) {
    let v = [...col];
    // Modified Gram-Schmidt: subtract projections one at a time, which is
    // numerically better behaved than the classical form.
    for (const q of basis) {
      const p = dot(v, q);
      v = v.map((x, i) => x - p * q[i]);
    }
    const n = norm(v);
    if (n / scale > tol) {
      basis.push(v.map((x) => x / n));
    }
    // else: this fault signature adds no new direction — it is a linear
    // combination of ones we already have. That collinearity is exactly why a
    // null space exists at all.
  }
  return basis;
}

/**
 * PER-CYLINDER EXPANSION.
 *
 * The incidence matrix lists rho6..rho9 as one group, which is right for a
 * printed table but WRONG as a direction in residual space. A single-cylinder
 * fault does not lift all four thermal deviations equally — it lifts ITS OWN
 * and pushes the other three slightly negative, because each is measured
 * against the CONDITIONAL MEAN across cylinders. Sum-to-zero is a property of
 * the residual definition, not an accident.
 *
 * Treating them as a block made a genuine, in-library, single-cylinder fault
 * look unexplainable — the detector was right and the matrix was wrong.
 *
 * So any fault whose per-cylinder block is excited is expanded into one column
 * per cylinder, with the thermal part shaped as (e_i - mean).
 */
const CYL_SLOTS = [5, 6, 7, 8]; // rho6..rho9 in display order
const N_CYL_LOCAL = CYL_SLOTS.length;

function expandColumns(): number[][] {
  const out: number[][] = [];
  for (const f of FAULT_ORDER) {
    const col = INCIDENCE[f]!;
    const thermal = CYL_SLOTS.map((i) => col[i]);
    const excited = thermal.some((v) => v !== 0);

    if (!excited) {
      out.push([...col]);
      continue;
    }

    const mag = thermal.reduce((a, b) => a + Math.abs(b), 0) / N_CYL_LOCAL;
    for (let c = 0; c < N_CYL_LOCAL; c++) {
      const v = [...col];
      for (let k = 0; k < N_CYL_LOCAL; k++) {
        // (e_c - mean), scaled by the tabulated magnitude and signed by it.
        const sign = Math.sign(thermal[k] || thermal[c] || 1);
        v[CYL_SLOTS[k]] = sign * mag * ((k === c ? 1 : 0) - 1 / N_CYL_LOCAL);
      }
      out.push(v);
    }
  }
  return out;
}

const FAULT_COLUMNS: number[][] = expandColumns();
const BASIS = orthonormalBasis(FAULT_COLUMNS, RANK_TOL);

export const EFFECTIVE_RANK = BASIS.length;
export const NULL_SPACE_DIM = N_RESIDUALS - EFFECTIVE_RANK;

// ---------------------------------------------------------------------------
// Projection
// ---------------------------------------------------------------------------

/**
 * Split a residual vector into explained and unexplained parts.
 *
 * `rho` must be in the 13-element display order of residual-spec.md. Any null
 * entry (an unavailable parity path — e.g. rho3 on an unthrottled engine) is
 * treated as zero, which is correct: an unavailable channel carries no
 * evidence either way, and must not be allowed to masquerade as novelty.
 */
export function computeNovelty(rho: (number | null)[]): NoveltyResult {
  const v = rho.map((x) => x ?? 0);
  const residualNorm = norm(v);

  // Project onto span(F): rho_hat = sum_q <v,q> q
  const explained = new Array(N_RESIDUALS).fill(0);
  for (const q of BASIS) {
    const p = dot(v, q);
    for (let i = 0; i < N_RESIDUALS; i++) explained[i] += p * q[i];
  }

  const unexplained = v.map((x, i) => x - explained[i]);
  const unexplainedNorm = norm(unexplained);

  return {
    // Guard the divide: at rest both norms are ~0 and nu is meaningless, so
    // report zero novelty rather than a ratio of noise to noise.
    index: residualNorm > 1e-6 ? unexplainedNorm / residualNorm : 0,
    residualNorm,
    unexplainedNorm,
    effectiveRank: EFFECTIVE_RANK,
    nullSpaceDim: NULL_SPACE_DIM,
    unexplained,
  };
}

/** Convenience: same thing straight off a ResidualVector. */
export function noveltyFromVector(r: ResidualVector): NoveltyResult {
  return computeNovelty([
    r.rho1_sd_vs_comp, r.rho2_sd_vs_lambda, r.rho3_sd_vs_restr,
    r.rho4_energy, r.rho5_power,
    r.rho6_9_cyl_dev[0], r.rho6_9_cyl_dev[1], r.rho6_9_cyl_dev[2], r.rho6_9_cyl_dev[3],
    r.rho10_oil, r.rho11_ripple,
    r.rho12_coolant, r.rho13_head_temp,
  ]);
}

/**
 * A direction in the null space of F, used by the mission generator to inject
 * an excitation the fault library provably cannot explain. Exposed here rather
 * than hardcoded so it stays consistent if the incidence matrix changes.
 */
export function nullSpaceDirection(seed = 0): number[] {
  // Start from a fixed arbitrary vector and remove everything span(F) explains.
  const v = Array.from({ length: N_RESIDUALS }, (_, i) =>
    Math.sin(i * 1.7 + seed * 0.9) + 0.3 * Math.cos(i * 2.9 - seed)
  );
  const explained = new Array(N_RESIDUALS).fill(0);
  for (const q of BASIS) {
    const p = dot(v, q);
    for (let i = 0; i < N_RESIDUALS; i++) explained[i] += p * q[i];
  }
  const perp = v.map((x, i) => x - explained[i]);
  const n = norm(perp);
  return n > 1e-9 ? perp.map((x) => x / n) : new Array(N_RESIDUALS).fill(0);
}
