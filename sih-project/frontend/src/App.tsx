import { Engine3D } from './components/Engine3D';
import {
  DiagnosisPanel, HealthParamsPanel, LimitsPanel, MissionPanel,
  ResidualHeatmap, RulPanel, TwinConfidencePanel, VirtualSensorPanel,
} from './components/Panels';
import { ChtChart, EgtChart } from './components/StripChart';
import { CrossEnginePanel } from './components/CrossEngine';
import { FaultConsole, InjectedTruth } from './components/FaultConsole';
import { MissionMap } from './components/MissionMap';
import { BeatBar, Scrubber } from './components/Scrubber';
import { ExplainDrawer } from './components/ExplainDrawer';
import { FlightReport } from './components/FlightReport';
import { EngineSlot } from './components/EngineSlot';
import { SimpleView, MissionSummary } from './components/SimpleView';
import { useCurrentTick, useMission } from './state/missionStore';
import { useEngineSlot } from './state/engineSlot';
import { ENGINES } from './config/engines';
import './App.css';

/** Where the data on screen came from. Deliberately prominent: a demo must
 *  never let anyone mistake the simulator for a real engine, and being the ones
 *  who say so first is worth more than hoping nobody asks. */
/**
 * Engine selector. "A new engine is a config change, not a rewrite" is one of
 * the claims that separates this from every other submission — so it has to be
 * demonstrable, not merely asserted. Switching here re-derives geometry,
 * limits, critical altitude and WHICH PARITY PATHS EXIST from the profile.
 */
function EngineSelector() {
  const engine = useMission((s) => s.engine);
  const setEngine = useMission((s) => s.setEngine);
  return (
    <div className="eng-sel">
      {ENGINES.map((e) => (
        <button
          key={e.id}
          className={`eng-btn${engine.id === e.id ? ' eng-btn-on' : ''}`}
          onClick={() => setEngine(e.id)}
          title={`${e.name} — ${e.developer}`}
        >
          {e.short}
        </button>
      ))}
    </div>
  );
}

function SourceBadge() {
  const source = useMission((s) => s.source);
  const frames = useMission((s) => s.liveFrames);
  const error = useMission((s) => s.feedError);

  const label =
    source === 'live' ? 'LIVE · ENGINE TWIN FEED'
      : source === 'connecting' ? 'CONNECTING…'
      : 'SIMULATED · NO BACKEND';

  return (
    <span
      className={`src-badge src-${source}`}
      title={
        source === 'live'
          ? `${frames} frames received from the twin`
          : `${error ?? 'not connected'} — running the local mission generator`
      }
    >
      <span className="src-dot" />
      {label}
    </span>
  );
}

function StatusBar() {
  const tick = useCurrentTick();
  const { slow, health } = tick;
  const diag = health.diagnosis.top[0];
  const alerting = diag.fault !== 'healthy';

  return (
    <div className={`ribbon${alerting ? (health.diagnosis.is_sensor_fault ? ' ribbon-sensor' : ' ribbon-alert') : ''}`}>
      <span className="ribbon-tag">ENGINE {slow.engine_id}</span>
      <span className="ribbon-item">{slow.rpm.toFixed(0)} <em>rpm</em></span>
      <span className="ribbon-item">{slow.prop_rpm.toFixed(0)} <em>prop rpm</em></span>
      {/* null on a fixed-pitch propeller (Rotax) — a governed blade angle only
          exists where there is a governor to read it, never fabricated. */}
      {slow.blade_angle_deg != null && (
        <span className="ribbon-item">{slow.blade_angle_deg.toFixed(1)}<em>° blade</em></span>
      )}
      <span className="ribbon-item">{slow.altitude_ft.toFixed(0)} <em>ft</em></span>
      <span className="ribbon-item">{slow.map_hPa.toFixed(0)} <em>hPa</em></span>
      <span className="ribbon-item">{(slow.fuel_flow_kgps * 3600).toFixed(1)} <em>kg/h</em></span>
      <span className="ribbon-item">λ {slow.lambda.toFixed(2)}</span>
      <span className="ribbon-item">{slow.bus_voltage_V.toFixed(1)} <em>V</em></span>
      <span className="ribbon-item">{slow.alternator_A.toFixed(1)} <em>A</em></span>
      <span className="ribbon-item">inj {slow.inj_timing_deg.toFixed(1)}<em>°</em></span>
      <span className="ribbon-spacer" />
      <span className="ribbon-status">
        {alerting
          ? (health.diagnosis.is_sensor_fault ? 'INSTRUMENTATION FAULT' : 'COMPONENT FAULT')
          : 'NOMINAL'}
      </span>
    </div>
  );
}

function EngineSubtitle() {
  const engine = useMission((s) => s.engine);
  const mode = useMission((s) => s.mode);
  const paths = Object.values(engine.parityPaths).filter(Boolean).length;
  // A first-time viewer needs to know what they are looking at, not how it
  // works; the parity-path count is for the engineer who opens expert mode.
  if (mode === 'simple') {
    return (
      <span className="brand-sub">
        Digital twin of the {engine.name} · finds faults before any limit alarm
      </span>
    );
  }
  return (
    <span className="brand-sub">
      Over-determined engine twin · {engine.name} · {paths} parity paths →{' '}
      {paths - 1} independent air-path residuals
    </span>
  );
}

/**
 * The one and only 3D engine, positioned over whichever view is asking for it.
 *
 * Mounted here for the life of the app rather than inside each view: the mode
 * toggle is a ternary, so a per-view Canvas was destroyed and rebuilt on every
 * switch, taking its EffectComposer with it and leaving the default demo view
 * blank for about eight seconds. See state/engineSlot.ts.
 */
function Engine3DLayer() {
  const rect = useEngineSlot((s) => s.rect);
  const reportOpen = useMission((s) => s.reportOpen);
  if (!rect) return null;
  return (
    <div
      className="engine3d-layer"
      style={{
        top: rect.top, left: rect.left, width: rect.width, height: rect.height,
        // Hidden rather than unmounted while the report is up — unmounting
        // would cost the context we just went to this trouble to keep.
        visibility: reportOpen ? 'hidden' : 'visible',
      }}
    >
      <Engine3D />
    </div>
  );
}

/**
 * Opens the post-flight report for the frame currently under the scrubber —
 * demo-script.md's 3:30 beat. Scrub back to detection, then print.
 */
function ReportButton() {
  const setReportOpen = useMission((s) => s.setReportOpen);
  return (
    <button
      className="mode-btn"
      onClick={() => setReportOpen(true)}
      title="Post-flight report for the frame under the scrubber"
    >
      REPORT
    </button>
  );
}

/**
 * Simple ⇄ expert. The label names the destination, not the current state.
 */
function ModeToggle() {
  const mode = useMission((s) => s.mode);
  const toggleMode = useMission((s) => s.toggleMode);
  return (
    <button
      className={`mode-btn${mode === 'expert' ? ' mode-btn-on' : ''}`}
      onClick={toggleMode}
      title={
        mode === 'simple'
          ? 'Show every instrument at once'
          : 'Back to the engine and one verdict'
      }
    >
      {mode === 'simple' ? 'EXPERT ▸' : '◂ SIMPLE'}
    </button>
  );
}

/**
 * The full instrument grid — fourteen panels, three columns.
 *
 * Lifted OUT of App unchanged rather than rebuilt, deliberately. This is the
 * view that has been rehearsed against, and demo-script.md's fallback table
 * assumes each layer can still be shown on its own. Keeping it byte-identical
 * means the simple view can never cost us a panel mid-demo.
 */
function ExpertGrid() {
  return (
    <main className="grid">
      <div className="col col-left">
        <FaultConsole />
        <LimitsPanel />
        <DiagnosisPanel />
        <TwinConfidencePanel />
        <CrossEnginePanel />
        <RulPanel />
      </div>

      <div className="col col-mid">
        <section className="panel panel-3d">
          <header className="panel-head">
            <span className="panel-title">Engine twin</span>
            <span className="panel-sub">live state · per-cylinder</span>
          </header>
          <div className="panel-body panel-body-3d">
            <EngineSlot className="engine-slot" />
          </div>
        </section>
        <EgtChart />
        <ChtChart />
      </div>

      <div className="col col-right">
        <MissionPanel />
        <MissionMap />
        <section className="panel">
          <header className="panel-head">
            <span className="panel-title">Plain-English summary</span>
            <span className="panel-sub">same numbers, in words</span>
          </header>
          <div className="panel-body">
            <MissionSummary />
          </div>
        </section>
        <ResidualHeatmap />
        <HealthParamsPanel />
        <VirtualSensorPanel />
      </div>
    </main>
  );
}

export default function App() {
  const mode = useMission((s) => s.mode);
  const simple = mode === 'simple';

  return (
    // The report is a SIBLING of .app, not a child: printing hides .app
    // entirely and lets the report stand alone as the page.
    <>
    <div className={`app app-${mode}`}>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">प्रमाण</span>
          <span className="brand-name">PRAMANA</span>
          <EngineSubtitle />
        </div>
        <div className="brand-right">
          <EngineSelector />
          <SourceBadge />
          <ReportButton />
          <ModeToggle />
          <span className="ps-tag">SIH26054 · DRDO</span>
        </div>
      </header>

      {/* The nine-readout telemetry ribbon is expert-only: in simple mode it is
          nine numbers competing with the one sentence that matters. The verdict
          card carries the status word instead. */}
      {!simple && <StatusBar />}
      <InjectedTruth />
      <BeatBar />

      {simple ? <SimpleView /> : <ExpertGrid />}

      <Scrubber />
      <ExplainDrawer />
    </div>
    <Engine3DLayer />
    <FlightReport />
    </>
  );
}
