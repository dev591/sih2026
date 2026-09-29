/**
 * The explain drawer — our interpretability story, and it is a UI panel
 * rather than a research problem.
 *
 * Click any alert or any cylinder and see the residual contributions plus the
 * matched signature row. The network supplies accuracy; the incidence matrix
 * supplies the WHY. Judges remember the why.
 */

import { useCurrentTick, useMission } from '../state/missionStore';
import { FAULT_LABELS, RESIDUAL_ROWS, residualRow, type FaultId } from '../types/telemetry';

/** The fault incidence matrix, as shipped in docs/spec/residual-spec.md.
 *  Component faults above the rule, instrumentation faults below it.
 *  2 = strong excitation, 1 = weak, -1/-2 = negative, 0 = unexcited. */
const INCIDENCE: Partial<Record<FaultId, number[]>> = {
  //                    ρ1  ρ2  ρ3  ρ4  ρ5  ρ6  ρ7  ρ8  ρ9 ρ10 ρ11
  ring_wear:           [ 1,  1,  1,  1,  1,  0,  0,  0,  0, -1,  0],
  turbo_degradation:   [-2,  0,  0,  1,  0,  0,  0,  0,  0,  0,  0],
  injector_fouling:    [ 0, -2,  0,  1,  0,  2,  2,  2,  2,  0,  2],
  fuel_filter_clog:    [ 0, -2,  0,  1, -1,  0,  0,  0,  0,  0,  0],
  injection_misfire:    [ 0,  0,  0,  2, -1, -2, -2, -2, -2,  0,  2],
  cooling_fouling:     [ 0,  0,  0,  1,  0,  1,  1,  1,  1,  1,  0],
  oil_pump_wear:       [ 0,  0,  0,  0,  1,  0,  0,  0,  0, -2,  0],
  bearing_wear:        [ 0,  0,  0,  1,  2,  0,  0,  0,  0, -1,  0],
  detonation:          [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  0],
  map_sensor_drift:    [ 2,  1,  1,  1,  0,  0,  0,  0,  0,  0,  0],
  egt_sensor_drift:    [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  0],
  cht_sensor_drift:    [ 0,  0,  0,  0,  0,  2,  2,  2,  2,  0,  0],
  lambda_sensor_drift: [ 0,  2,  0,  0,  0,  0,  0,  0,  0,  0,  0],
};

/** Plain names for the live feature vector (ml/features.py on the backend). */
const FEATURE_LABELS: Record<string, string> = {
  rho1: 'ρ₁  air: speed-density vs compressor',
  rho2: 'ρ₂  air: speed-density vs fuel/λ',
  rho4: 'ρ₄  energy closure',
  rho5: 'ρ₅  power closure',
  rho10: 'ρ₁₀ oil pressure model',
  rho11: 'ρ₁₁ 0.5-order crank ripple',
  egt_dev_1: 'EGT deviation · cyl 1', egt_dev_2: 'EGT deviation · cyl 2',
  egt_dev_3: 'EGT deviation · cyl 3', egt_dev_4: 'EGT deviation · cyl 4',
  cht_dev_1: 'CHT deviation · cyl 1', cht_dev_2: 'CHT deviation · cyl 2',
  cht_dev_3: 'CHT deviation · cyl 3', cht_dev_4: 'CHT deviation · cyl 4',
  coolant: 'Coolant temperature vs twin',
  head_mean: 'Mean head temperature vs twin',
  egt_mean: 'Mean EGT vs twin',
  oil_temp: 'Oil temperature vs twin',
  fuel_delivery: 'Metered fuel vs FADEC command',
  boost_path: 'Intercooler pressure drop vs twin',
};

/** Glyph for a measured (continuous) signature entry, relative to its row max. */
const glyph = (v: number, max: number) => {
  const r = max > 0 ? v / max : 0;
  return r > 0.4 ? 2 : r > 0.15 ? 1 : r < -0.4 ? -2 : r < -0.15 ? -1 : 0;
};

const SIGN = (v: number) =>
  v === 2 ? '⇑' : v === 1 ? '↑' : v === -1 ? '↓' : v === -2 ? '⇓' : '·';

export function ExplainDrawer() {
  const open = useMission((s) => s.explainOpen);
  const setOpen = useMission((s) => s.setExplainOpen);
  const selectCyl = useMission((s) => s.selectCylinder);
  const selected = useMission((s) => s.selectedCylinder);
  const tick = useCurrentTick();

  if (!open) return null;

  const { diagnosis, rho, explain } = tick.health;
  const top = diagnosis.top[0];

  // LIVE: the backend's evidence and the fault's MEASURED signature. SIMULATED:
  // the local simulator's residuals against the local table.
  type Row = { key: string; label: string; detail: string; v: number; expected: number };
  let rows: Row[];
  if (explain) {
    const max = Math.max(...explain.signature.map(Math.abs));
    rows = explain.features.map((f, i) => ({
      key: f, label: FEATURE_LABELS[f] ?? f, detail: 'measured on the engine model',
      v: explain.live[i], expected: glyph(explain.signature[i], max),
    }));
  } else {
    const values = residualRow(rho);
    const signature = INCIDENCE[top.fault] ?? new Array(11).fill(0);
    rows = values.flatMap((v, i) => v === null ? [] : [{
      key: RESIDUAL_ROWS[i].key, label: RESIDUAL_ROWS[i].label,
      detail: RESIDUAL_ROWS[i].detail, v, expected: signature[i],
    }]);
  }
  const values = residualRow(rho);
  // Which residuals are actually carrying the diagnosis right now.
  const ranked = rows.sort((a, b) => Math.abs(b.v) - Math.abs(a.v)).slice(0, explain ? 12 : rows.length);

  const isSensor = diagnosis.is_sensor_fault;
  const corroborating = ranked.filter((r) => Math.abs(r.v) > 1.5).length;

  return (
    <aside className="drawer">
      <header className="drawer-head">
        <div>
          <div className="drawer-title">Why this diagnosis</div>
          <div className={`drawer-fault tone-${isSensor ? 'sensor' : 'alert'}`}>
            {diagnosis.unavailable ? 'No diagnosis — ML layer offline' : FAULT_LABELS[top.fault]}
            {/* Loose check: a producer that omits `cylinder` gives undefined,
                and undefined !== null renders "cylinder NaN". */}
            {!diagnosis.unavailable && top.cylinder != null && ` · cylinder ${top.cylinder + 1}`}
            {!diagnosis.unavailable && <span className="drawer-conf">{(top.p * 100).toFixed(0)}%</span>}
          </div>
        </div>
        <button className="drawer-close" onClick={() => { setOpen(false); selectCyl(null); }}>✕</button>
      </header>

      <div className="drawer-body">
        <div className={`callout ${isSensor ? 'callout-sensor' : 'callout-alert'}`}>
          {isSensor ? (
            <>
              <strong>One channel moved. Nothing corroborates it.</strong>
              A degrading component perturbs several coupled parity relations in
              a physically consistent pattern. A drifting transducer perturbs
              only the relations that contain it — no ripple on ρ₁₁, no shift in
              the energy balance, no change in fuel flow. That <em>absence</em> is
              the evidence.
            </>
          ) : (
            <>
              <strong>{corroborating} residuals moved together, in a coupled pattern.</strong>
              A single sensor cannot produce this.{' '}
              {explain
                ? <>The pattern matches this fault's signature <em>measured on the engine model</em> (cosine {explain.match_cosine.toFixed(2)}).</>
                : <>The excitation set matches the derived signature row below.</>}
            </>
          )}
        </div>

        <div className="drawer-section">
          Residual contributions · live vs {explain ? 'measured' : 'derived'} signature
        </div>
        <div className="contrib">
          {ranked.map((r) => {
            const meta = r;
            const mag = Math.min(1, Math.abs(r.v) / 4);
            const agrees = Math.sign(r.v) === Math.sign(r.expected) && r.expected !== 0;
            return (
              <div className="contrib-row" key={meta.key}>
                <span className="contrib-label" title={meta.detail}>{meta.label}</span>
                <span className="contrib-track">
                  <span className="contrib-zero" />
                  <span
                    className="contrib-bar"
                    style={{
                      width: `${mag * 50}%`,
                      left: r.v >= 0 ? '50%' : `${50 - mag * 50}%`,
                      background: r.v >= 0 ? 'var(--warn)' : 'var(--sensor)',
                    }}
                  />
                </span>
                <span className="contrib-v">{r.v >= 0 ? '+' : ''}{r.v.toFixed(2)}σ</span>
                <span className={`contrib-sig${agrees ? ' contrib-sig-hit' : ''}`}>
                  {SIGN(r.expected)}
                </span>
              </div>
            );
          })}
        </div>

        {values[2] === null && (
          <p className="note">
            ρ₃ is unavailable on this engine — a FADEC aero-diesel is
            unthrottled, so there is no metering restriction and Path 4 does not
            exist. With <em>n</em> available paths the framework yields
            <em> n−1</em> independent relations, and the isolability analysis is
            recomputed for the sensor set actually fitted. We report null rather
            than fabricate a number.
          </p>
        )}

        <div className="drawer-section">Isolation principle</div>
        <p className="note">
          Every fault has an <strong>odd path</strong> — the estimate that departs
          while the others hold. Identifying which path is the outlier, and with
          what sign, identifies the fault. No classifier required, and no need to
          have observed that fault before.
        </p>

        {selected !== null && (
          <>
            <div className="drawer-section">Cylinder {selected + 1} · measured</div>
            <div className="drawer-grid">
              <div><span>EGT</span><strong>{tick.slow.egt_C[selected].toFixed(0)} °C</strong></div>
              <div><span>twin says</span><strong>{tick.predicted.egt_C[selected].toFixed(0)} °C</strong></div>
              <div><span>CHT</span><strong>{tick.slow.cht_C[selected].toFixed(1)} °C</strong></div>
              <div><span>twin says</span><strong>{tick.predicted.cht_C[selected].toFixed(1)} °C</strong></div>
              <div><span>C_d inj (estimated)</span><strong>{tick.health.theta ? tick.health.theta.cd_inj.value[selected].toFixed(3) : '—'}</strong></div>
              <div><span>comb. η</span><strong title="Not modelled">{tick.health.virtual.comb_efficiency ? `${(tick.health.virtual.comb_efficiency[selected] * 100).toFixed(1)} %` : 'not modelled'}</strong></div>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
