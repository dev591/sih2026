/**
 * PRAMANA — ground control station panels.
 *
 * Aesthetic rules, applied throughout: dark ground, tabular numerals
 * everywhere, units labelled on everything, no rounded pastel cards.
 * QGroundControl vocabulary, about ten years newer.
 */

import type { ReactNode } from 'react';
import { useCurrentTick, useMission } from '../state/missionStore';
import {
  FAULT_LABELS, N_CYL, RESIDUAL_ROWS, residualRow,
} from '../types/telemetry';

// ---------------------------------------------------------------------------
// Shared shell
// ---------------------------------------------------------------------------

/**
 * Explanatory prose, collapsed by default.
 *
 * Nearly every panel carries a paragraph explaining WHY its number means what
 * it means, and those paragraphs are load-bearing — they are most of what
 * separates this from a dashboard that merely draws telemetry, and they are the
 * answers to the questions a judge actually asks.
 *
 * But eight of them open at once is a wall of 10px grey text, and the effect is
 * that none of them get read. Collapsed, the panel reads as instruments; one
 * click and the argument is there when it is wanted. Native <details>, so it
 * costs no state and keyboard/screen-reader behaviour comes free.
 */
export function Note({ children }: { children: ReactNode }) {
  return (
    <details className="note note-fold">
      <summary className="note-sum">Why this matters</summary>
      {children}
    </details>
  );
}

export function Panel({
  title, subtitle, children, flag,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  flag?: 'ok' | 'warn' | 'alert' | 'sensor';
}) {
  const flagColour =
    flag === 'alert' ? 'var(--alert)'
      : flag === 'warn' ? 'var(--warn)'
      : flag === 'sensor' ? 'var(--sensor)'
      : 'var(--ok)';
  return (
    <section className="panel">
      <header className="panel-head">
        <span className="panel-title">{title}</span>
        {subtitle && <span className="panel-sub">{subtitle}</span>}
        {flag && <span className="panel-flag" style={{ background: flagColour }} />}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}

function Metric({
  label, value, unit, tone, wide,
}: {
  label: string; value: string; unit?: string;
  tone?: 'ok' | 'warn' | 'alert' | 'sensor' | 'dim'; wide?: boolean;
}) {
  return (
    <div className={`metric${wide ? ' metric-wide' : ''}`}>
      <span className="metric-label">{label}</span>
      <span className={`metric-value tone-${tone ?? 'ok'}`}>
        {value}
        {unit && <span className="metric-unit">{unit}</span>}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The residual heatmap — the most information-dense square on the screen.
// Sensors down, time across, colour as deviation. One glance shows both THAT
// something is wrong and WHICH channels are wrong.
// ---------------------------------------------------------------------------
export function ResidualHeatmap() {
  const ticks = useMission((s) => s.ticks);
  // Subscribe to the ROUNDED index, not the fractional one. This grid is ~990
  // cells and only ever moves a whole column at a time, so selecting the whole
  // number lets zustand bail out between seconds instead of reconciling the
  // entire heatmap on every animation frame.
  const now = useMission((s) => Math.round(s.index));

  const WINDOW = 90;
  const start = Math.max(0, now - WINDOW);
  const slice = ticks.slice(start, now + 1);

  const cellColour = (v: number | null) => {
    if (v === null) return 'var(--void)';
    const a = Math.min(1, Math.abs(v) / 4);
    // Positive deviation amber, negative cyan — sign carries information,
    // and the incidence matrix is read by sign as much as magnitude.
    //
    // The alpha floor is 0.16 rather than 0.06: on the old black ground a
    // near-zero residual at 6% alpha still read as a faintly warm cell, but on
    // white it is indistinguishable from the page, and a heatmap whose quiet
    // cells are invisible stops showing that the quiet channels ARE quiet —
    // which is half of what isolation is read from. The ramp is squared so the
    // floor does not wash the whole grid into mid-tone.
    const alpha = 0.16 + a * a * 0.84;
    return v >= 0
      ? `color-mix(in srgb, var(--warn) ${(alpha * 100).toFixed(1)}%, var(--panel))`
      : `color-mix(in srgb, var(--sensor) ${(alpha * 100).toFixed(1)}%, var(--panel))`;
  };

  return (
    <Panel title="Parity residuals" subtitle={`ρ₁–ρ₁₁ · last ${WINDOW}s · σ units`}>
      <div className="heatmap">
        {RESIDUAL_ROWS.map((meta, r) => (
          <div className="heat-row" key={meta.key}>
            <span className="heat-label" title={meta.detail}>{meta.label}</span>
            <div className="heat-cells">
              {slice.map((t, c) => {
                const v = residualRow(t.health.rho)[r];
                return (
                  <span
                    key={c}
                    className="heat-cell"
                    style={{ background: cellColour(v) }}
                    title={v === null ? 'path unavailable' : `${v.toFixed(2)} σ`}
                  />
                );
              })}
            </div>
          </div>
        ))}
      </div>
      <Note>
        Nominally zero and operating-point invariant by construction — which is
        why the detector does not fire every time the throttle moves.
        ρ₃ is <strong>null</strong>: an unthrottled FADEC aero-diesel has no
        metering restriction, so Path 4 does not exist on this engine.
      </Note>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Diagnosis
// ---------------------------------------------------------------------------
export function DiagnosisPanel() {
  const tick = useCurrentTick();
  const { diagnosis, anomaly } = tick.health;
  const setExplain = useMission((s) => s.setExplainOpen);
  const selectCyl = useMission((s) => s.selectCylinder);

  const top = diagnosis.top[0];
  const healthy = top.fault === 'healthy';
  const flag = healthy ? 'ok' : diagnosis.is_sensor_fault ? 'sensor' : 'alert';
  // The healthy row's probability is not a confidence: the ML path reports
  // 1 − score/threshold, the physics-only fallback a fixed 0.98. Derive the
  // margin here so it means the same thing on both paths.
  const alarmMargin = anomaly.score != null && anomaly.threshold
    ? Math.min(1, Math.max(0, 1 - anomaly.score / anomaly.threshold))
    : 0;

  if (diagnosis.unavailable) {
    return (
      <Panel title="Diagnosis" subtitle="ML layer offline" flag="alert">
        <div className="callout callout-alert">
          <strong>No diagnosis this frame.</strong>
          The anomaly and isolation models did not run, so nothing is shown in
          their place. The threshold monitor below still works on its own.
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      title="Diagnosis"
      subtitle={diagnosis.ambiguous ? 'ambiguous — probing' : 'isolated'}
      flag={flag}
    >
      <div className="diag-head">
        <div className={`diag-name tone-${flag}`}>
          {FAULT_LABELS[top.fault]}
          {top.cylinder != null && <span className="diag-cyl">cyl {top.cylinder + 1}</span>}
        </div>
        {healthy ? (
          <div className="diag-conf" title="1 − anomaly score / threshold. A healthy engine sits well inside the alarm threshold, not at 100%.">
            {(alarmMargin * 100).toFixed(0)}%
            <span className="diag-conf-label">alarm margin remaining</span>
          </div>
        ) : (
          <div className="diag-conf">{(top.p * 100).toFixed(0)}%</div>
        )}
      </div>

      {diagnosis.inseparable_from && diagnosis.inseparable_from.length > 0 && (
        <div className="callout callout-probe">
          <strong>Cannot yet be separated from {diagnosis.inseparable_from.map((f) => FAULT_LABELS[f]).join(', ')}.</strong>
          On held-out engines these faults produced residual signatures the
          current sensor set measurably cannot tell apart. Both are reported
          rather than one being picked.
        </div>
      )}

      {diagnosis.is_sensor_fault && (
        <div className="callout callout-sensor">
          <strong>Instrumentation fault — the engine is healthy.</strong>
          No corroborating residual on any other channel: no ripple, no
          fuel-flow change, no energy-balance shift. A threshold system would
          abort the mission for a forty-dollar thermocouple.
        </div>
      )}

      {diagnosis.probe?.running && (
        <div className="callout callout-probe">
          <strong>Active diagnosis running — cylinder {diagnosis.probe.cylinder + 1}</strong>
          Commanding ±{diagnosis.probe.amplitude_pct.toFixed(0)}% fuel trim
          (inside existing FADEC authority) and measuring the <em>gain</em> of
          the response, not its level. A sensor's constant bias cancels
          identically from the alternating component.
          <div className="probe-row">
            <span>gain Ĝ = {diagnosis.probe.gain_estimate.toFixed(2)}</span>
            <span>Λ = {diagnosis.probe.log_likelihood_ratio.toFixed(1)}</span>
            <span>{diagnosis.probe.elapsed_s.toFixed(0)}s</span>
          </div>
        </div>
      )}

      <div className="hyp-list">
        {diagnosis.top.map((h, i) => (
          <div className="hyp" key={i}>
            <span className="hyp-bar" style={{ width: `${h.p * 100}%` }} />
            <span className="hyp-name">
              {FAULT_LABELS[h.fault]}
              {h.cylinder != null && ` · cyl ${h.cylinder + 1}`}
            </span>
            <span className="hyp-src">{h.source}</span>
            <span className="hyp-p">
              {(((h.fault === 'healthy' ? alarmMargin : h.p)) * 100).toFixed(0)}%
            </span>
          </div>
        ))}
      </div>

      <div className="metric-grid">
        <Metric
          label="Anomaly score"
          value={anomaly.score == null ? '—' : anomaly.score.toFixed(3)}
          tone={anomaly.persistence.met ? 'alert' : 'ok'}
        />
        <Metric label="Threshold" value={anomaly.threshold == null ? '—' : anomaly.threshold.toFixed(3)} tone="dim" />
        <Metric
          label="Windows over threshold"
          value={`${anomaly.persistence.n}/${anomaly.persistence.of}`}
          tone={anomaly.persistence.met ? 'warn' : 'dim'}
        />
      </div>

      {!healthy && (
        <button
          className="btn"
          onClick={() => { selectCyl(top.cylinder ?? 0); setExplain(true); }}
        >
          Explain this diagnosis →
        </button>
      )}
      <Note>
        Threshold is the 99.5th percentile of reconstruction error on held-out
        healthy data — never a hand-picked constant.
      </Note>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Health parameters — the UKF estimates, in physical units, with covariance.
// ---------------------------------------------------------------------------
export function HealthParamsPanel() {
  const { theta } = useCurrentTick().health;
  if (!theta) {
    return (
      <Panel title="Health parameters" subtitle="ML layer offline">
        <div className="rul-none">No estimate this frame</div>
      </Panel>
    );
  }

  const opt = (label: string, e?: { value: number; sigma: number }) =>
    e ? [{ label, v: e.value, s: e.sigma }] : [];
  const rows: { label: string; v: number; s: number }[] = [
    { label: 'Volumetric efficiency scale  η_v', v: theta.eta_v_scale.value, s: theta.eta_v_scale.sigma },
    { label: 'Compressor efficiency scale  η_c', v: theta.eta_c_scale.value, s: theta.eta_c_scale.sigma },
    { label: 'Head cooling conductance  (hA)', v: theta.hA_scale.value, s: theta.hA_scale.sigma },
    { label: 'Friction scale  f_fric', v: theta.f_fric_scale.value, s: theta.f_fric_scale.sigma },
    ...opt('Radiator effectiveness', theta.rad_eff_scale),
    ...opt('Coolant pump flow', theta.cool_pump_scale),
    ...opt('Oil pump delivery', theta.oil_pump_scale),
    ...opt('Fuel supply (filter)', theta.fuel_rail_scale),
  ];

  return (
    <Panel title="Health parameters" subtitle="Kalman filter on a measured engine-model sensitivity">
      <div className="param-list">
        {rows.map((r) => {
          const dev = Math.abs(1 - r.v);
          const tone = dev > 0.08 ? 'alert' : dev > 0.03 ? 'warn' : 'ok';
          return (
            <div className="param" key={r.label}>
              <span className="param-label">{r.label}</span>
              <span className={`param-value tone-${tone}`}>
                {r.v.toFixed(3)} <span className="param-sigma">± {r.s.toFixed(3)}</span>
              </span>
            </div>
          );
        })}
        {theta.cd_inj.value.map((v, i) => {
          const dev = 1 - v;
          const tone = dev > 0.08 ? 'alert' : dev > 0.03 ? 'warn' : 'ok';
          return (
            <div className="param" key={`inj${i}`}>
              <span className="param-label">Injector discharge coeff · cyl {i + 1}</span>
              <span className={`param-value tone-${tone}`}>
                {v.toFixed(3)} <span className="param-sigma">± {theta.cd_inj.sigma[i].toFixed(3)}</span>
              </span>
            </div>
          );
        })}
      </div>
      <Note>
        These <em>are</em> the health indicators — physical quantities an engineer
        can accept or dispute, each with a covariance. Not a health score.
      </Note>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// RUL — two heads, always both, always with the band.
// ---------------------------------------------------------------------------
export function RulPanel() {
  const { rul } = useCurrentTick().health;
  if (rul.component === 'none') {
    return (
      <Panel title="Remaining useful life" subtitle="no degrading component">
        <div className="rul-none">All components nominal</div>
      </Panel>
    );
  }
  const label = FAULT_LABELS[rul.component as keyof typeof FAULT_LABELS] ?? rul.component;
  if (rul.reported_h == null || rul.p10_h == null || rul.p50_h == null || rul.p90_h == null) {
    return (
      <Panel title="Remaining useful life" subtitle={label}>
        <div className="rul-none">No redline predicted yet</div>
        <Note>
          The fault is diagnosed, but at its current estimated rate the engine
          model predicts no limit exceedance inside the fault's modelled range —
          or there is not yet enough trend history to fit a rate.
        </Note>
      </Panel>
    );
  }
  // Demo fault rates are compressed into minutes; show minutes below an hour
  // rather than rounding a real 20-minute estimate to "0.3 h".
  const fmt = (h: number | null) => (h == null ? '—' : h < 1 ? (h * 60).toFixed(0) : h.toFixed(1));
  const unit = rul.reported_h < 1 ? 'min' : 'h';
  const span = Math.max(rul.p90_h - rul.p10_h, 1e-3);
  const pos = (v: number) => ((v - (rul.p10_h ?? 0)) / span) * 100;

  return (
    <Panel title="Remaining useful life" subtitle={`${label} · time to first redline`} flag={rul.reported_h < 4 ? 'warn' : 'ok'}>
      <div className="rul-big">
        {fmt(rul.reported_h)}<span className="rul-unit">{unit}</span>
      </div>
      <div className="rul-band">
        <div className="rul-track">
          <div className="rul-fill" style={{ left: 0, width: '100%' }} />
          <div className="rul-marker" style={{ left: `${pos(rul.p50_h)}%` }} />
        </div>
        <div className="rul-ticks">
          <span>p10 {fmt(rul.p10_h)}{unit}</span>
          <span>p50 {fmt(rul.p50_h)}{unit}</span>
          <span>p90 {fmt(rul.p90_h)}{unit}</span>
        </div>
      </div>
      <div className="metric-grid">
        <Metric label="Physics head (Kalman θ)" value={fmt(rul.physics_h)} unit={unit} />
        <Metric label="Network head (M3)" value={fmt(rul.network_h)} unit={unit} />
        <Metric label="Advised on" value={fmt(rul.reported_h)} unit={unit} tone="warn" />
      </div>
      <Note>
        Two independent estimates of the time until the engine model predicts a
        limit exceedance, and we advise on the conservative one.
        {rul.heads_disagree && ' Heads disagree beyond the predictive interval — surfaced as a warning in its own right.'}
      </Note>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Mission decision — the part nobody else will build.
// ---------------------------------------------------------------------------
export function MissionPanel() {
  const { mission } = useCurrentTick().health;

  const options = [
    { key: 'continue', label: 'Continue', p: mission.p_complete_continue, cost: '—' },
    { key: 'derate', label: 'Derate to 78% power', p: mission.p_complete_derate,
      cost: mission.derate_cost_min_on_station == null ? 'station cost not modelled' : `−${mission.derate_cost_min_on_station} min on station` },
    { key: 'rtb', label: 'Return to base', p: mission.p_complete_rtb, cost: 'mission ends' },
  ];
  const pct = (p: number | null) => (p == null ? '—' : `${(p * 100).toFixed(0)}%`);

  const pnrMin = Math.max(0, mission.point_of_no_return_s / 60);

  return (
    <Panel title="Mission reliability" subtitle="P(complete) · Monte Carlo, M = 200" flag={mission.recommended === 'continue' ? 'ok' : 'warn'}>
      <div className="opt-list">
        {options.map((o) => (
          <div className={`opt${mission.recommended === o.key ? ' opt-rec' : ''}`} key={o.key}>
            <span className="opt-bar" style={{ width: `${(o.p ?? 0) * 100}%` }} />
            <span className="opt-label">{o.label}</span>
            <span className="opt-cost">{o.cost}</span>
            <span className="opt-p">{pct(o.p)}</span>
          </div>
        ))}
      </div>
      <div className="metric-grid">
        <Metric label="Point of no return" value={pnrMin.toFixed(0)} unit="min" tone={pnrMin < 40 ? 'warn' : 'ok'} />
        <Metric label="Boost ceiling" value={mission.recommended_boost_hPa == null ? '—' : mission.recommended_boost_hPa.toFixed(0)} unit="hPa" />
        <Metric label="Power ceiling" value={mission.recommended_power_pct == null ? '—' : mission.recommended_power_pct.toFixed(0)} unit="%" />
      </div>
      <Note>
        Each of the 200 draws samples a fault from the diagnosis probabilities
        and a severity and degradation rate from their estimated uncertainty,
        then asks the engine model how long until a redline at each power
        setting. Fuel state is from the metered flow. Assumed, not engine facts:
        fuel load, reserve and return-to-base transit time.
      </Note>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Limits panel — what a THRESHOLD system would be showing right now.
// Stays on screen the whole time. The contrast IS the argument.
// ---------------------------------------------------------------------------
export function LimitsPanel() {
  const tick = useCurrentTick();
  const engine = useMission((s) => s.engine);
  const { slow, health } = tick;

  // Limits come from the ACTIVE ENGINE PROFILE, never from a literal in this
  // file. Switch engines and every redline on screen changes with it.
  const value: Record<string, number> = {
    cht: Math.max(...slow.cht_C),
    oilp: slow.oil_press_bar,
    oilt: slow.oil_temp_C,
    map: slow.map_hPa,
    rpm: slow.rpm,
    gbox: slow.gearbox_oil_C ?? 0,
    coolant: slow.coolant_temp_C ?? 0,
  };
  const rows = engine.limits.map((l) => ({
    label: l.label,
    v: value[l.key] ?? 0,
    lim: l.limit,
    unit: l.unit,
    inverted: l.inverted,
    decimals: l.decimals,
    provenance: l.provenance,
  }));

  return (
    <Panel
      title="Threshold monitor"
      subtitle="what a conventional system sees"
      flag={health.limits_state === 'green' ? 'ok' : health.limits_state === 'caution' ? 'warn' : 'alert'}
    >
      <div className="limit-list">
        {rows.map((r) => {
          const frac = r.inverted ? r.lim / r.v : r.v / r.lim;
          const tone = frac > 1 ? 'alert' : frac > 0.9 ? 'warn' : 'ok';
          return (
            <div className="limit" key={r.label}>
              <span className="limit-label">{r.label}</span>
              <span className="limit-track">
                <span className={`limit-fill tone-bg-${tone}`} style={{ width: `${Math.min(100, frac * 100)}%` }} />
              </span>
              <span className={`limit-value tone-${tone}`}>
                {r.v.toFixed(r.decimals ?? 0)}<span className="metric-unit">{r.unit}</span>
              </span>
            </div>
          );
        })}
      </div>
      <div className={`limits-verdict tone-${health.limits_state === 'green' ? 'ok' : 'warn'}`}>
        {health.limits_state === 'green' ? 'ALL PARAMETERS GREEN' : health.limits_state.toUpperCase()}
      </div>
      <Note>
        Limits are read from the <strong>{engine.short}</strong> profile, not
        hardcoded — {engine.limits.every((l) => l.provenance === 'published')
          ? 'all published and citable'
          : 'some are engineering estimates and are labelled as such in the profile'}.
        Note the EGT row is absent: the type certificate for this class publishes
        <strong> no EGT limit at all</strong>. EGT is a trend parameter, not a
        redline parameter — a threshold system has no way to use it.
      </Note>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Virtual sensors — analytical redundancy, which DRDO asks for by name.
// ---------------------------------------------------------------------------
export function VirtualSensorPanel() {
  const { virtual } = useCurrentTick().health;
  return (
    <Panel title="Virtual sensors" subtitle="recovered by analytical redundancy">
      <div className="metric-grid">
        <Metric label="Air mass flow" value={(virtual.air_mass_flow_kgps * 1000).toFixed(1)} unit="g/s" />
        <Metric label="Brake power" value={virtual.brake_power_kW.toFixed(1)} unit="kW" />
        <Metric label="BSFC (degraded)" value={virtual.bsfc_g_per_kWh.toFixed(0)} unit="g/kWh" />
        <Metric label="Knock margin" value={virtual.knock_margin_deg == null ? 'not modelled' : virtual.knock_margin_deg.toFixed(1)} unit={virtual.knock_margin_deg == null ? '' : '°'} tone={virtual.knock_margin_deg == null ? 'dim' : undefined} />
        <Metric label="Turbo shaft" value={(virtual.turbo_shaft_rpm_est / 1000).toFixed(1)} unit="k rpm" />
      </div>
      <div className="cyl-strip">
        {Array.from({ length: N_CYL }, (_, i) => (
          <div className="cyl-chip" key={i}>
            <span className="cyl-chip-n">CYL {i + 1}</span>
            <span className="cyl-chip-v">{virtual.peak_cyl_press_bar ? `${virtual.peak_cyl_press_bar[i].toFixed(0)} bar` : 'p_max —'}</span>
            <span className="cyl-chip-e">{virtual.comb_efficiency ? `${(virtual.comb_efficiency[i] * 100).toFixed(1)}% η` : 'η —'}</span>
          </div>
        ))}
      </div>
      <Note>
        None of these are instrumented on the aircraft. They are recoverable
        because the same quantity is reachable along several independent paths.
      </Note>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// TWIN CONFIDENCE — the channel that lets the system say "I don't know".
//
// Every classifier is forced to pick from its list. This one reports how much
// of the live residual its fault library can actually account for, and when
// that fraction collapses it declines to name a fault at all.
//
// See docs/spec/novelty-detection.md.
// ---------------------------------------------------------------------------
export function TwinConfidencePanel() {
  const { novelty, twin_confidence } = useCurrentTick().health;

  // Degrade gracefully: if the backend has not published these yet, show
  // nothing rather than a wrong number.
  if (!novelty || !twin_confidence) return null;

  const conf = twin_confidence.value;
  const flag = novelty.exceeded ? 'alert' : conf < 0.75 ? 'warn' : 'ok';

  return (
    <Panel
      title="Twin confidence"
      subtitle={`ν = ${novelty.index.toFixed(2)} · null-space dim ${novelty.null_space_dim}`}
      flag={flag}
    >
      <div className="conf-big">
        <span className={`tone-${flag}`}>{(conf * 100).toFixed(0)}</span>
        <span className="conf-unit">%</span>
      </div>

      <div className="conf-split">
        <div className="conf-track">
          <span className="conf-explained" style={{ width: `${(1 - novelty.index) * 100}%` }} />
          <span className="conf-unexplained" style={{ width: `${novelty.index * 100}%` }} />
        </div>
        <div className="conf-legend">
          <span><i className="sw-explained" /> explained by fault library</span>
          <span><i className="sw-unexplained" /> unexplained</span>
        </div>
      </div>

      {novelty.exceeded && (
        <div className="callout callout-alert">
          <strong>Excitation pattern is not in the fault library.</strong>
          {(novelty.index * 100).toFixed(0)}% of this residual is orthogonal to
          every fault direction we modelled. This is either an unmodelled failure
          mode or a twin that has drifted from the engine — so the system
          declines to name a fault rather than confidently naming the wrong one.
          <strong style={{ marginTop: 6 }}>Recommend human inspection.</strong>
        </div>
      )}

      <div className="metric-grid">
        <Metric label="‖ρ‖" value={novelty.residual_norm.toFixed(2)} unit="σ" />
        <Metric label="‖ρ⊥‖" value={novelty.unexplained_norm.toFixed(2)} unit="σ"
          tone={novelty.exceeded ? 'alert' : 'dim'} />
        <Metric label="rank F" value={`${novelty.effective_rank}/11`} tone="dim" />
      </div>

      <Note>
        The fault signatures span {novelty.effective_rank} of 11 residual
        dimensions, leaving {novelty.null_space_dim} in which a fault we have
        never modelled can still be seen. ν is weighted by residual
        significance — below the noise floor there is nothing to explain, so
        confidence is full. A tool that never says
        <strong> "I don't know"</strong> cannot be trusted when it does answer.
      </Note>
    </Panel>
  );
}
