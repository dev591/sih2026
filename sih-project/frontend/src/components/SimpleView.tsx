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
import { FAULT_LABELS } from '../types/telemetry';

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
    </div>
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

      <EdgeDrawer id="mission" side="right" title="Mission" subtitle="reliability · route · RUL">
        {() => (
          <>
            <MissionPanel />
            <MissionMap />
            <RulPanel />
            <CrossEnginePanel />
          </>
        )}
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
