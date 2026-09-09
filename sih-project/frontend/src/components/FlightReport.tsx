/**
 * POST-FLIGHT REPORT — the 3:30 beat in demo-script.md.
 *
 * A maintainer does not read a dashboard. They read a work order: what is
 * wrong, on which cylinder, what the evidence was, and how long the part has
 * left. This renders exactly that, from the frame under the scrubber — so
 * scrubbing back to the moment of detection and printing gives a report of
 * THAT instant, not of now.
 *
 * Printing is window.print(): the browser's own "Save as PDF" is a real PDF
 * writer that every venue machine already has, and it cannot fail to load a
 * library over venue wifi. print.css hides the app and shows only this.
 *
 * The provenance footer is not decoration. It states on the artifact itself
 * that the engine was simulated — an evaluator who finds that disclosed reads
 * the rest as reliable.
 */

import { useMission, useCurrentTick } from '../state/missionStore';
import {
  FAULT_LABELS, RESIDUAL_ROWS, residualRow, N_CYL,
} from '../types/telemetry';

/** Residuals are in sigma units, so the threshold for "excited" is absolute. */
const EXCITED_SIGMA = 2.0;

function fmt(n: number, dp = 1): string {
  return Number.isFinite(n) ? n.toFixed(dp) : '—';
}

function hhmm(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function FlightReport() {
  const open = useMission((s) => s.reportOpen);
  const setOpen = useMission((s) => s.setReportOpen);
  const engine = useMission((s) => s.engine);
  const source = useMission((s) => s.source);
  const tick = useCurrentTick();

  if (!open) return null;

  const { slow, health } = tick;
  const { diagnosis, rul, mission, theta, anomaly, limits_state } = health;
  const top = diagnosis.top[0];
  const healthy = !top || top.fault === 'healthy';

  const rows = residualRow(health.rho);
  const excited = rows
    .map((v, i) => ({ v, meta: RESIDUAL_ROWS[i] }))
    .filter((r) => r.v !== null && Math.abs(r.v) >= EXCITED_SIGMA);
  const quiet = rows
    .map((v, i) => ({ v, meta: RESIDUAL_ROWS[i] }))
    .filter((r) => r.v !== null && Math.abs(r.v) < EXCITED_SIGMA);

  return (
    <div className="report-overlay" role="dialog" aria-label="Post-flight report">
      <div className="report-actions no-print">
        <button className="btn btn-primary" onClick={() => window.print()}>
          Save as PDF
        </button>
        <button className="btn" onClick={() => setOpen(false)}>Close</button>
      </div>

      <article className="report-sheet">
        <header className="report-head">
          <div>
            <div className="report-brand">
              <span className="report-mark">प्रमाण</span> PRAMANA
            </div>
            <div className="report-sub">Post-flight engine health report</div>
          </div>
          <div className="report-meta">
            <div><strong>{engine.name}</strong></div>
            <div>Engine {slow.engine_id} · mission T+{hhmm(slow.t)}</div>
            <div>Generated {new Date().toLocaleString()}</div>
          </div>
        </header>

        {/* ---------------- VERDICT ---------------- */}
        <section className="report-verdict">
          <div className="report-verdict-main">
            <div className="report-label">Finding</div>
            <div className={`report-fault ${healthy ? 'is-ok' : 'is-alert'}`}>
              {healthy ? 'No fault detected' : FAULT_LABELS[top.fault]}
              {!healthy && top.cylinder !== null && (
                <span className="report-cyl"> · cylinder {top.cylinder + 1}</span>
              )}
            </div>
            <div className="report-class">
              {healthy
                ? 'All residuals within healthy bounds.'
                : diagnosis.is_sensor_fault
                  ? 'Classification: INSTRUMENTATION — the engine is serviceable. '
                    + 'Replace or recalibrate the transducer; no engine action required.'
                  : 'Classification: COMPONENT — physical degradation of the engine.'}
            </div>
          </div>
          <div className="report-conf">
            <div className="report-label">Confidence</div>
            <div className="report-conf-num">
              {healthy ? '—' : `${(top.p * 100).toFixed(0)}%`}
            </div>
            <div className="report-label">Anomaly score</div>
            <div>{fmt(anomaly.score, 2)} / {fmt(anomaly.threshold, 2)}</div>
          </div>
        </section>

        {/* ---------------- CONTRAST ---------------- */}
        <section className="report-contrast">
          <strong>Threshold system would report:</strong>{' '}
          <span className={`report-limits limits-${limits_state}`}>
            {limits_state === 'green' ? 'ALL PARAMETERS NORMAL' : limits_state.toUpperCase()}
          </span>
          {limits_state === 'green' && !healthy && (
            <span className="report-contrast-note">
              {' '}— no limit exceeded at the time of detection. The finding above
              comes from residual departure, not from a redline.
            </span>
          )}
        </section>

        {/* ---------------- EVIDENCE ---------------- */}
        <section className="report-block">
          <h2>Evidence — parity residuals at detection</h2>
          <p className="report-note">
            Residuals are normalised to healthy-data standard deviations. A
            fault is identified by <em>which</em> relations depart and with what
            sign, not by any single channel crossing a limit.
          </p>

          <table className="report-table">
            <thead>
              <tr><th>Relation</th><th>Value (σ)</th><th>State</th></tr>
            </thead>
            <tbody>
              {excited.map((r) => (
                <tr key={r.meta.key} className="row-excited">
                  <td>{r.meta.label}</td>
                  <td className="num">{fmt(r.v as number, 2)}</td>
                  <td>excited</td>
                </tr>
              ))}
              {excited.length === 0 && (
                <tr><td colSpan={3}>No relation exceeded {EXCITED_SIGMA}σ.</td></tr>
              )}
            </tbody>
          </table>

          <p className="report-note">
            <strong>Corroboration —</strong> {quiet.length} of {rows.filter((v) => v !== null).length}{' '}
            relations remained within {EXCITED_SIGMA}σ. For an instrumentation
            fault this absence is the diagnosis: a failing transducer moves the
            channel it reports and nothing else, because nothing else is
            physically changing.
          </p>
        </section>

        {/* ---------------- HEALTH PARAMETERS ---------------- */}
        <section className="report-block">
          <h2>Estimated health parameters</h2>
          <p className="report-note">
            Recovered as states of the estimator, with uncertainty — physically
            interpretable quantities an engineer can accept or dispute.
          </p>
          <table className="report-table">
            <thead>
              <tr><th>Parameter</th><th>Estimate</th><th>σ</th></tr>
            </thead>
            <tbody>
              <tr>
                <td>Volumetric efficiency scale η_v</td>
                <td className="num">{fmt(theta.eta_v_scale.value, 3)}</td>
                <td className="num">{fmt(theta.eta_v_scale.sigma, 3)}</td>
              </tr>
              <tr>
                <td>Compressor efficiency scale η_c</td>
                <td className="num">{fmt(theta.eta_c_scale.value, 3)}</td>
                <td className="num">{fmt(theta.eta_c_scale.sigma, 3)}</td>
              </tr>
              <tr>
                <td>Cooling effectiveness scale (hA)</td>
                <td className="num">{fmt(theta.hA_scale.value, 3)}</td>
                <td className="num">{fmt(theta.hA_scale.sigma, 3)}</td>
              </tr>
              <tr>
                <td>Friction scale f_fric</td>
                <td className="num">{fmt(theta.f_fric_scale.value, 3)}</td>
                <td className="num">{fmt(theta.f_fric_scale.sigma, 3)}</td>
              </tr>
              {Array.from({ length: N_CYL }, (_, i) => (
                <tr key={i}>
                  <td>Injector discharge coefficient C_d — cyl {i + 1}</td>
                  <td className="num">{fmt(theta.cd_inj.value[i], 3)}</td>
                  <td className="num">{fmt(theta.cd_inj.sigma[i], 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {/* ---------------- RUL + DECISION ---------------- */}
        <section className="report-two-up">
          <div className="report-block">
            <h2>Remaining useful life</h2>
            <table className="report-table">
              <tbody>
                <tr><td>Component</td><td className="num">{rul.component}</td></tr>
                <tr><td>Physics head</td><td className="num">{fmt(rul.physics_h)} h</td></tr>
                <tr><td>Network head</td><td className="num">{fmt(rul.network_h)} h</td></tr>
                <tr><td>Interval p10–p90</td><td className="num">{fmt(rul.p10_h)}–{fmt(rul.p90_h)} h</td></tr>
                <tr className="row-strong">
                  <td>Advised</td>
                  <td className="num">{fmt(rul.reported_h)} h</td>
                </tr>
              </tbody>
            </table>
            <p className="report-note">
              Two independent estimators are reported. Advice is given on the
              conservative of the two.
              {rul.heads_disagree && ' The heads disagree beyond the interval — '
                + 'that disagreement is itself a warning and is not averaged away.'}
            </p>
          </div>

          <div className="report-block">
            <h2>Mission recommendation</h2>
            <table className="report-table">
              <tbody>
                <tr><td>Continue</td><td className="num">{(mission.p_complete_continue * 100).toFixed(0)}%</td></tr>
                <tr><td>Derate</td><td className="num">{(mission.p_complete_derate * 100).toFixed(0)}%</td></tr>
                <tr><td>Return to base</td><td className="num">{(mission.p_complete_rtb * 100).toFixed(0)}%</td></tr>
                <tr className="row-strong">
                  <td>Recommended</td>
                  <td className="num">{mission.recommended.toUpperCase()}</td>
                </tr>
                <tr>
                  <td>Cost of derate</td>
                  <td className="num">{fmt(mission.derate_cost_min_on_station, 0)} min on station</td>
                </tr>
              </tbody>
            </table>
            <p className="report-note">
              Probability of mission completion under each option, with the cost
              of the safe choice stated. The decision remains the commander's.
            </p>
          </div>
        </section>

        {/* ---------------- OPERATING POINT ---------------- */}
        <section className="report-block">
          <h2>Operating point at detection</h2>
          <div className="report-op">
            <span>Altitude <strong>{fmt(slow.altitude_ft, 0)} ft</strong></span>
            <span>RPM <strong>{fmt(slow.rpm, 0)}</strong></span>
            <span>MAP <strong>{fmt(slow.map_hPa, 0)} hPa</strong></span>
            <span>Throttle <strong>{fmt(slow.throttle_pct, 0)}%</strong></span>
            <span>OAT <strong>{fmt(slow.oat_K - 273.15, 1)} °C</strong></span>
            <span>Fuel flow <strong>{(slow.fuel_flow_kgps * 3600).toFixed(1)} kg/h</strong></span>
          </div>
        </section>

        <footer className="report-foot">
          <div>
            <strong>Provenance.</strong>{' '}
            {source === 'live'
              ? 'Frames received live from the engine simulation backend over WebSocket.'
              : 'Frames generated by the on-board simulation model — no backend connected.'}
            {' '}The engine is <strong>simulated</strong>; no physical powerplant
            was instrumented for this report. Detection, isolation and life
            estimation are computed by the same pipeline that would consume
            CAN telemetry from a production ECU.
          </div>
          <div className="report-foot-meta">
            PRAMANA · SIH26054 · DRDO · schema {health.schema} · engine profile {engine.id}
          </div>
        </footer>
      </article>
    </div>
  );
}
