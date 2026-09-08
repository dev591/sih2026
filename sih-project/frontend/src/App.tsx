import { Engine3D } from './components/Engine3D';
import {
  DiagnosisPanel, HealthParamsPanel, LimitsPanel, MissionPanel,
  ResidualHeatmap, RulPanel, VirtualSensorPanel,
} from './components/Panels';
import { ChtChart, EgtChart } from './components/StripChart';
import { BeatBar, Scrubber } from './components/Scrubber';
import { ExplainDrawer } from './components/ExplainDrawer';
import { useCurrentTick, useMission } from './state/missionStore';
import './App.css';

/** Where the data on screen came from. Deliberately prominent: a demo must
 *  never let anyone mistake the simulator for a real engine, and being the ones
 *  who say so first is worth more than hoping nobody asks. */
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

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">प्रमाण</span>
          <span className="brand-name">PRAMANA</span>
          <span className="brand-sub">Over-determined engine twin · VRDE 180 hp aero-diesel</span>
        </div>
        <div className="brand-right">
          <SourceBadge />
          <span className="ps-tag">SIH26054 · DRDO</span>
        </div>
      </header>

      <StatusBar />
      <BeatBar />

      <main className="grid">
        <div className="col col-left">
          <LimitsPanel />
          <DiagnosisPanel />
          <RulPanel />
        </div>

        <div className="col col-mid">
          <section className="panel panel-3d">
            <header className="panel-head">
              <span className="panel-title">Engine twin</span>
              <span className="panel-sub">live state · per-cylinder</span>
            </header>
            <div className="panel-body panel-body-3d">
              <Engine3D />
            </div>
          </section>
          <EgtChart />
          <ChtChart />
        </div>

        <div className="col col-right">
          <MissionPanel />
          <ResidualHeatmap />
          <HealthParamsPanel />
          <VirtualSensorPanel />
        </div>
      </main>

      <Scrubber />
      <ExplainDrawer />
    </div>
  );
}
