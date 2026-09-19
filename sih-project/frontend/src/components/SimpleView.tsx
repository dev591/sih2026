/**
 * The default view: the engine, and one sentence about it.
 *
 * The expert grid puts fourteen panels on screen at once. Everything a judge
 * needs is there, which is exactly the problem — nothing is emphasised, so
 * nothing reads, and a stranger given four minutes cannot tell where to look.
 * Here the 3D engine is the whole stage and every panel lives one click away
 * behind an edge tab.
 *
 * What could NOT be hidden is the threshold monitor. demo-script.md is explicit
 * that the limits panel stays visible for the whole demo, because the argument
 * is not "the twin found a fault" — it is "every limit is green AND the twin
 * already knows". So the verdict card below states both halves side by side.
 * That is the pitch compressed, not the pitch discarded.
 */

import { useState } from 'react';
import { EngineSlot } from './EngineSlot';
import {
  DiagnosisPanel, HealthParamsPanel, LimitsPanel, MissionPanel,
  ResidualHeatmap, RulPanel, TwinConfidencePanel, VirtualSensorPanel,
} from './Panels';
import { ChtChart, EgtChart } from './StripChart';
import { CrossEnginePanel } from './CrossEngine';
import { FaultConsole } from './FaultConsole';
import { MissionMap } from './MissionMap';
import { EdgeDrawer, EdgeTab } from './EdgeDrawer';
import { useCurrentTick, useMission } from '../state/missionStore';
import { FAULT_LABELS, residualRow, type RulEstimate } from '../types/telemetry';
import { useEpisode, mmss } from '../state/episode';

/**
 * Plain-English name for each of the 11 parity residuals, in the same order
 * as `residualRow()`. ExplainDrawer.tsx has the real version of this (ρ
 * symbols, the full incidence matrix) for someone who wants the engineering
 * detail — this is the same underlying numbers, translated for someone who
 * has never seen a residual before and just wants "what evidence, exactly."
 */
const PLAIN_RESIDUAL = [
  'the incoming air, measured two independent ways',
  'the fuel-to-air ratio',
  'the air-restriction check',
  "the engine's overall energy balance (fuel in vs. heat and work out)",
  'the power produced vs. what the propeller is actually absorbing',
  "cylinder 1's temperature, compared to the other three",
  "cylinder 2's temperature, compared to the other three",
  "cylinder 3's temperature, compared to the other three",
  "cylinder 4's temperature, compared to the other three",
  'oil pressure',
  'how smoothly the crankshaft is turning',
];

/**
 * The two-up contrast. Left is what a conventional system sees; right is what
 * the twin sees. Putting them in one card is the entire product in one glance.
 */
function VerdictCard() {
  const { health } = useCurrentTick();
  const engineState = useMission((s) => s.engineState);
  const { diagnosis, limits_state, novelty } = health;

  // No frames, no findings. An engine that is not running has no residuals to
  // be flat or otherwise, and printing "Healthy" over a stopped engine would be
  // asserting something the twin has not established.
  if (engineState !== 'running') {
    return (
      <div className="verdict verdict-idle">
        <div className="verdict-half">
          <span className="verdict-cap">Thresholds</span>
          <span className="verdict-limits tone-dim">—</span>
          <span className="verdict-note">no channels to compare</span>
        </div>
        <div className="verdict-rule" aria-hidden="true" />
        <div className="verdict-half verdict-half-main">
          <span className="verdict-cap">The twin</span>
          <span className="verdict-fault tone-dim">
            {engineState === 'starting' ? 'Acquiring…' : 'Engine off'}
          </span>
          <span className="verdict-note">
            {engineState === 'starting'
              ? 'waiting for the first residuals'
              : 'start the engine to begin'}
          </span>
        </div>
      </div>
    );
  }
  const top = diagnosis.top[0];
  const healthy = top.fault === 'healthy';
  const tone = healthy ? 'ok' : diagnosis.is_sensor_fault ? 'sensor' : 'alert';

  // The scripted mission has a beat titled 'the twin says "I do not know"', but
  // the only evidence for it lived in the residual drawer — on the main view
  // the card showed a confident diagnosis and nothing else, which is the
  // opposite of what that beat is demonstrating. Surface the unexplained
  // fraction here so refusing to guess is visible without opening a panel.
  const unexplained = novelty?.exceeded ? novelty.index : 0;

  const limitsTone =
    limits_state === 'green' ? 'ok' : limits_state === 'caution' ? 'warn' : 'alert';
  const limitsWord =
    limits_state === 'green' ? 'ALL GREEN'
      : limits_state === 'caution' ? 'CAUTION'
      : 'EXCEEDED';

  return (
    <div className={`verdict verdict-${tone}`}>
      <div className="verdict-half">
        <span className="verdict-cap">Thresholds</span>
        <span className={`verdict-limits tone-${limitsTone}`}>{limitsWord}</span>
        <span className="verdict-note">what a conventional system sees</span>
      </div>

      <div className="verdict-rule" aria-hidden="true" />

      <div className="verdict-half verdict-half-main">
        <span className="verdict-cap">The twin</span>
        <span className={`verdict-fault tone-${tone}`}>
          {FAULT_LABELS[top.fault]}
          {/* Loose null check on purpose: a producer that omits `cylinder`
              entirely gives undefined, and `undefined !== null` rendered
              "cyl NaN" on stage. */}
          {top.cylinder != null && <em className="verdict-cyl">cyl {top.cylinder + 1}</em>}
        </span>
        <span className="verdict-note">
          {healthy
            ? 'residuals flat across every parity path'
            : `${(top.p * 100).toFixed(0)}% confident · ${
                diagnosis.is_sensor_fault ? 'instrumentation, not the engine' : 'component fault'
              }`}
        </span>
        {unexplained > 0 && (
          <span className="verdict-novelty">
            {(unexplained * 100).toFixed(0)}% of the residual is unexplained — part of
            this is outside the fault library
          </span>
        )}
      </div>

      {!healthy && <EarlyWarning rul={health.rul} />}
    </div>
  );
}

/**
 * The third column: what the twin's head start is worth. For a component
 * fault that is time ahead of every threshold alarm plus the estimated time
 * before it needs attention; for a sensor fault it is the false alarm, or the
 * needless abort, that did not happen.
 */
function EarlyWarning({ rul }: { rul: RulEstimate }) {
  const ep = useEpisode();
  if (!ep) return null;
  return (
    <>
      <div className="verdict-rule" aria-hidden="true" />
      <div className="verdict-half verdict-half-warn">
        <span className="verdict-cap">Early warning</span>
        {ep.isSensor ? (
          <>
            <span className="verdict-lead tone-sensor">False alarm avoided</span>
            <span className="verdict-note">the engine is fine; a threshold system would trust this sensor</span>
          </>
        ) : (
          <>
            <span className="verdict-lead tone-alert">
              {ep.limitsTripped ? '' : '+'}{mmss(ep.aheadS)}
              <em>{ep.limitsTripped ? 'before the first limit tripped' : 'ahead of every limit alarm'}</em>
            </span>
            <span className="verdict-note">
              {rul.component !== 'none'
                ? `about ${rul.reported_h.toFixed(1)} h before it needs attention (${rul.p10_h.toFixed(1)} to ${rul.p90_h.toFixed(1)} h)`
                : `detected at ${mmss(ep.detectedT)} into the flight`}
            </span>
          </>
        )}
      </div>
    </>
  );
}

/**
 * The Mission drawer's headline, in plain English, before any of the
 * fourteen panels below it. A judge who has never seen a Monte Carlo
 * reliability number needs "Problem: sensor, not the engine — keep flying"
 * and "Recommendation: continue, 99%" before "P(complete) M=200" means
 * anything to them. Everything below stays available — nothing is removed,
 * it's just not the first thing they see.
 */
export function MissionSummary() {
  const { health } = useCurrentTick();
  const { diagnosis, mission, rul, rho } = health;
  const top = diagnosis.top[0];
  const healthy = top.fault === 'healthy';
  const problemTone = healthy ? 'ok' : diagnosis.is_sensor_fault ? 'sensor' : 'alert';

  // The evidence chain, in plain English: which real measurements moved,
  // by how much, and what that combination implies. This is the same
  // residual vector ExplainDrawer.tsx uses for its ρ-symbol version — same
  // live numbers, just narrated as "this, this, this → therefore" instead
  // of a signature-matrix table.
  const values = residualRow(rho);
  const ranked = values
    .map((v, i) => ({ i, v: v ?? 0, available: v !== null }))
    .filter((r) => r.available)
    .sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  const moved = ranked.filter((r) => Math.abs(r.v) > 1.5).slice(0, 3);
  const quietCount = ranked.length - moved.length;

  const recLabel =
    mission.recommended === 'continue' ? 'Continue as planned'
      : mission.recommended === 'derate' ? `Reduce power to ${mission.recommended_power_pct.toFixed(0)}%`
      : 'Return to base';
  const recP =
    mission.recommended === 'continue' ? mission.p_complete_continue
      : mission.recommended === 'derate' ? mission.p_complete_derate
      : mission.p_complete_rtb;
  const recTone = mission.recommended === 'continue' ? 'ok' : 'warn';

  return (
    <div className="mission-summary">
      <div className="mission-summary-row">
        <span className="mission-summary-label">Problem</span>
        <span className={`mission-summary-value tone-${problemTone}`}>
          {healthy ? 'None — engine is healthy' : FAULT_LABELS[top.fault]}
          {top.cylinder != null && <span className="mission-summary-cyl"> · cylinder {top.cylinder + 1}</span>}
        </span>
        {!healthy && (
          <span className="mission-summary-note">
            {diagnosis.is_sensor_fault
              ? "It's a faulty sensor, not the engine — safe to keep flying."
              : `${(top.p * 100).toFixed(0)}% confident, from the engine's own measurements.`}
          </span>
        )}
      </div>

      {!healthy && moved.length > 0 && (
        <div className="mission-summary-row">
          <span className="mission-summary-label">Why we think this</span>
          <ul className="why-list">
            {moved.map((r) => (
              <li key={r.i}>
                {PLAIN_RESIDUAL[r.i]} is {r.v > 0 ? 'higher' : 'lower'} than it
                should be right now
              </li>
            ))}
            {quietCount > 0 && (
              <li className="why-quiet">
                {quietCount} other check{quietCount === 1 ? '' : 's'}{' '}
                {quietCount === 1 ? 'is' : 'are'} still normal
              </li>
            )}
          </ul>
          <span className="mission-summary-note why-conclusion">
            →{' '}
            {diagnosis.is_sensor_fault
              ? "Since everything else checks out, this points to a faulty sensor, not the engine itself."
              : 'Since these moved together in a matching pattern, this points to a real engine problem.'}
          </span>
        </div>
      )}

      <div className="mission-summary-row">
        <span className="mission-summary-label">Recommendation</span>
        <span className={`mission-summary-value tone-${recTone}`}>{recLabel}</span>
        <span className="mission-summary-note">
          {(recP * 100).toFixed(0)}% chance of completing the mission this way
        </span>
      </div>

      {!healthy && rul.component !== 'none' && (
        <div className="mission-summary-row">
          <span className="mission-summary-label">Time before this needs attention</span>
          <span className="mission-summary-value">{rul.reported_h.toFixed(1)} hours</span>
        </div>
      )}
    </div>
  );
}

/** Everything below the plain-English summary — the same panels as before,
 *  just behind one click instead of first on screen. */
function MissionDrawerContent() {
  const [showDetails, setShowDetails] = useState(false);
  return (
    <>
      <MissionSummary />
      <button
        type="button"
        className="mission-details-toggle"
        onClick={() => setShowDetails((v) => !v)}
      >
        {showDetails ? 'Hide details & reasoning ▴' : 'View details & reasoning ▾'}
      </button>
      {showDetails && (
        <>
          <MissionPanel />
          <MissionMap />
          <RulPanel />
          <CrossEnginePanel />
        </>
      )}
    </>
  );
}

export function SimpleView() {
  const { health } = useCurrentTick();
  const { diagnosis, limits_state } = health;
  const healthy = diagnosis.top[0].fault === 'healthy';

  return (
    <div className="stage">
      <EngineSlot className="engine-slot" />
      <VerdictCard />

      <nav className="edge-tabs-left">
        <EdgeTab
          id="faults"
          side="left"
          label="FAULTS"
          flag={healthy ? undefined : diagnosis.is_sensor_fault ? 'sensor' : 'alert'}
        />
      </nav>
      <nav className="edge-tabs-right">
        <EdgeTab id="mission" side="right" label="MISSION" />
      </nav>
      <nav className="edge-tabs-bottom">
        <EdgeTab
          id="trends"
          side="bottom"
          label="TRENDS &amp; RESIDUALS"
          flag={limits_state === 'green' ? undefined : 'warn'}
        />
      </nav>

      <EdgeDrawer id="faults" side="left" title="Faults" subtitle="inject · limits · diagnosis">
        {() => (
          <>
            <FaultConsole />
            <LimitsPanel />
            <DiagnosisPanel />
          </>
        )}
      </EdgeDrawer>

      <EdgeDrawer id="mission" side="right" title="Mission" subtitle="what's wrong · what to do">
        {() => <MissionDrawerContent />}
      </EdgeDrawer>

      <EdgeDrawer id="trends" side="bottom" title="Trends & residuals" subtitle="measured vs twin">
        {() => (
          <div className="edge-cols">
            <div className="edge-col">
              <EgtChart />
              <ChtChart />
            </div>
            <div className="edge-col">
              <ResidualHeatmap />
            </div>
            <div className="edge-col">
              <TwinConfidencePanel />
              <HealthParamsPanel />
              <VirtualSensorPanel />
            </div>
          </div>
        )}
      </EdgeDrawer>
    </div>
  );
}
