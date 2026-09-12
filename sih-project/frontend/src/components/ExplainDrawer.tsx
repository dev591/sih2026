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
  ignition_misfire:    [ 0,  0,  0,  2, -1, -2, -2, -2, -2,  0,  2],
  cooling_fouling:     [ 0,  0,  0,  1,  0,  1,  1,  1,  1,  1,  0],
  oil_pump_wear:       [ 0,  0,  0,  0,  1,  0,  0,  0,  0, -2,  0],
  bearing_wear:        [ 0,  0,  0,  1,  2,  0,  0,  0,  0, -1,  0],
  detonation:          [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  0],
  map_sensor_drift:    [ 2,  1,  1,  1,  0,  0,  0,  0,  0,  0,  0],
  egt_sensor_drift:    [ 0,  0,  0,  1,  0,  2,  2,  2,  2,  0,  0],
  cht_sensor_drift:    [ 0,  0,  0,  0,  0,  2,  2,  2,  2,  0,  0],
  lambda_sensor_drift: [ 0,  2,  0,  0,  0,  0,  0,  0,  0,  0,  0],
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

  const { diagnosis, rho } = tick.health;
  const top = diagnosis.top[0];
  const values = residualRow(rho);
  const signature = INCIDENCE[top.fault] ?? new Array(11).fill(0);

  // Which residuals are actually carrying the diagnosis right now.
  const ranked = values
    .map((v, i) => ({ i, v: v ?? 0, available: v !== null, expected: signature[i] }))
    .filter((r) => r.available)
    .sort((a, b) => Math.abs(b.v) - Math.abs(a.v));

  const isSensor = diagnosis.is_sensor_fault;
  const corroborating = ranked.filter((r) => Math.abs(r.v) > 1.5).length;

  return (
    <aside className="drawer">
      <header className="drawer-head">
        <div>
          <div className="drawer-title">Why this diagnosis</div>
          <div className={`drawer-fault tone-${isSensor ? 'sensor' : 'alert'}`}>
            {FAULT_LABELS[top.fault]}
            {/* Loose check: a producer that omits `cylinder` gives undefined,
                and undefined !== null renders "cylinder NaN". */}
            {top.cylinder != null && ` · cylinder ${top.cylinder + 1}`}
            <span className="drawer-conf">{(top.p * 100).toFixed(0)}%</span>
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
              A single sensor cannot produce this. The excitation set matches the
              derived signature row below — and the classifier independently
              agrees. Two mechanisms, one answer.
            </>
          )}
        </div>

        <div className="drawer-section">Residual contributions · live vs derived signature</div>
        <div className="contrib">
          {ranked.map((r) => {
            const meta = RESIDUAL_ROWS[r.i];
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
              <div><span>C_d inj</span><strong>{tick.health.theta.cd_inj.value[selected].toFixed(3)}</strong></div>
              <div><span>comb. η</span><strong>{(tick.health.virtual.comb_efficiency[selected] * 100).toFixed(1)} %</strong></div>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
