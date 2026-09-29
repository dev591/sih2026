/**
 * FAULT INJECTION CONSOLE — the part you hand to a judge.
 *
 * A scripted replay can look rehearsed, because it is. This is the answer: the
 * evaluator chooses the fault themselves, so there is no possible suspicion it
 * was staged. Whatever they pick runs through the SAME physics, the SAME
 * residual generator and the SAME diagnosis code as the rehearsed demo — there
 * is no separate sandbox whose behaviour could differ from the thing being
 * demonstrated.
 *
 * BLIND MODE is the move worth planning the demo around.
 *
 *   "Pick any fault. Any cylinder. Any severity. Or break a sensor instead.
 *    Don't tell me which."
 *
 * The console collapses after injecting, so the presenter cannot see the
 * choice either. Then the system says ENGINE or INSTRUMENTATION, and it is
 * right. A judge who caused the fault believes the answer in a way a judge who
 * watched a recording never will.
 */

import { useEffect, useState } from 'react';
import { useMission } from '../state/missionStore';
import { Panel } from './Panels';
import { SCRIPTED, type FaultConfig } from '../mock/missionGenerator';
import { N_CYL } from '../types/telemetry';

type Kind =
  | 'injector' | 'misfire' | 'detonation' | 'turbo' | 'cooling' | 'coolantPump'
  | 'bearing' | 'oilLeak' | 'ringWear' | 'fuelFilter'
  | 'chtSensor' | 'egtSensor' | 'mapSensor' | 'lambdaSensor' | 'unmodelled';

interface KindDef {
  kind: Kind;
  label: string;
  /** COMPONENT changes the engine. INSTRUMENTATION changes only the reading. */
  family: 'component' | 'instrument' | 'unknown';
  perCylinder: boolean;
  unit: string;
  min: number;
  max: number;
  step: number;
  def: number;
}

// Units are the live engine model's own (backend/twin/faults.py): each fault
// is a parameter change ramped at `rate` per minute from the moment it is
// injected, never a drawn signature.
const KINDS: KindDef[] = [
  { kind: 'injector',     label: 'Injector fouling',           family: 'component',  perCylinder: true,  unit: '/min C_d',       min: 0.01, max: 0.12, step: 0.005, def: 0.045 },
  { kind: 'misfire',      label: 'Misfire',                    family: 'component',  perCylinder: true,  unit: '/min P(skip)',   min: 0.03, max: 0.30, step: 0.01,  def: 0.10 },
  { kind: 'detonation',   label: 'Detonation',                 family: 'component',  perCylinder: true,  unit: '/min severity',  min: 0.03, max: 0.30, step: 0.01,  def: 0.10 },
  { kind: 'turbo',        label: 'Turbocharger degradation',   family: 'component',  perCylinder: false, unit: '/min η_c',       min: 0.01, max: 0.10, step: 0.005, def: 0.04 },
  { kind: 'cooling',      label: 'Radiator fouling',           family: 'component',  perCylinder: false, unit: '/min radiator',  min: 0.02, max: 0.20, step: 0.01,  def: 0.08 },
  { kind: 'coolantPump',  label: 'Coolant pump degradation',   family: 'component',  perCylinder: false, unit: '/min flow',      min: 0.02, max: 0.20, step: 0.01,  def: 0.08 },
  { kind: 'bearing',      label: 'Bearing wear',               family: 'component',  perCylinder: false, unit: '/min friction',  min: 0.03, max: 0.40, step: 0.01,  def: 0.12 },
  { kind: 'oilLeak',      label: 'Oil pump wear / leak',       family: 'component',  perCylinder: false, unit: '/min delivery',  min: 0.03, max: 0.30, step: 0.01,  def: 0.10 },
  { kind: 'ringWear',     label: 'Piston ring wear',           family: 'component',  perCylinder: false, unit: '/min η_v',       min: 0.01, max: 0.10, step: 0.005, def: 0.04 },
  { kind: 'fuelFilter',   label: 'Fuel filter clog',           family: 'component',  perCylinder: false, unit: '/min supply',    min: 0.02, max: 0.20, step: 0.01,  def: 0.06 },
  { kind: 'chtSensor',    label: 'CHT sensor drift',           family: 'instrument', perCylinder: true,  unit: '°C/min',         min: 5,    max: 60,   step: 1,     def: 24 },
  { kind: 'egtSensor',    label: 'EGT sensor drift',           family: 'instrument', perCylinder: true,  unit: '°C/min',         min: 10,   max: 150,  step: 5,     def: 70 },
  { kind: 'mapSensor',    label: 'MAP sensor drift',           family: 'instrument', perCylinder: false, unit: 'hPa/min',        min: 5,    max: 60,   step: 1,     def: 20 },
  { kind: 'lambdaSensor', label: 'λ sensor drift',             family: 'instrument', perCylinder: false, unit: '/min λ',         min: 0.005, max: 0.06, step: 0.005, def: 0.03 },
  // Not in the classifier's training library on purpose — the novelty test.
  { kind: 'unmodelled',   label: 'Propeller blade damage (outside the library)', family: 'unknown', perCylinder: false, unit: '/min C_P', min: 0.01, max: 0.10, step: 0.005, def: 0.04 },
];


export function FaultConsole() {
  const applyConfig = useMission((s) => s.applyConfig);
  const blind = useMission((s) => s.blind);
  const reveal = useMission((s) => s.reveal);
  const config = useMission((s) => s.config);

  const [kind, setKind] = useState<Kind>('injector');
  const [cyl, setCyl] = useState(1);
  const [rate, setRate] = useState(0.045);
  const live = useMission((s) => s.source) === 'live';
  const [blindMode, setBlindMode] = useState(true);

  const def = KINDS.find((k) => k.kind === kind)!;

  const pick = (k: Kind) => {
    const d = KINDS.find((x) => x.kind === k)!;
    setKind(k);
    setRate(d.def);
  };

  const inject = () => {
    const cfg: FaultConfig = {
      // startT is restamped to the frame on screen by applyConfig's `fromNow`,
      // so the fault ramps in from here rather than the clock jumping into a
      // fault that has already developed.
      [kind]: { startT: 0, cyl: def.perCylinder ? cyl : undefined, rate },
    } as FaultConfig;
    applyConfig(cfg, { blind: blindMode, fromNow: true });
  };

  const isScript = config === SCRIPTED;

  // Blind mode: the console hides its own selection until revealed, so the
  // presenter is as unsighted as the room.
  if (blind) {
    return (
      <Panel title="Fault injected" subtitle="selection hidden" flag="warn">
        <div className="blind-box">
          <div className="blind-title">A fault has been injected.</div>
          <p className="blind-note">
            Neither the operator nor this panel is showing which one. Read the
            diagnosis, then reveal — the system had no more information than you do.
          </p>
          <button className="btn btn-primary" onClick={reveal}>Reveal what was injected</button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      title="Fault injection"
      subtitle={isScript ? 'demo script loaded' : 'sandbox'}
      flag={isScript ? 'ok' : 'warn'}
    >
      <div className="fc-kinds">
        {KINDS.map((k) => {
          // Reflects the ACTUAL running config, not the picker selection —
          // a fault is "active" only once injected, not merely highlighted
          // while the operator is still choosing severity/cylinder for it.
          const active = Boolean((config as Record<string, unknown>)[k.kind]);
          return (
            <button
              key={k.kind}
              className={`fc-kind fc-${k.family}${kind === k.kind ? ' fc-kind-on' : ''}`}
              onClick={() => pick(k.kind)}
            >
              <span className={`fc-dot${active ? ' fc-dot-active' : ''}`} />
              <span className="fc-fam">
                {k.family === 'component' ? 'ENGINE' : k.family === 'instrument' ? 'SENSOR' : 'UNKNOWN'}
              </span>
              {k.label}
            </button>
          );
        })}
      </div>

      {/* Always rendered, disabled when the fault is not per-cylinder. If this
          row appears and disappears, the Inject button moves between renders
          and a fast click lands on the wrong control — which is exactly the
          kind of thing that costs you five seconds in front of a judge. */}
      <div className={`fc-row${def.perCylinder ? '' : ' fc-row-off'}`}>
        <span className="fc-lab">Cylinder</span>
        <div className="fc-cyls">
          {Array.from({ length: N_CYL }, (_, i) => (
            <button
              key={i}
              className={`fc-cyl${def.perCylinder && cyl === i ? ' fc-cyl-on' : ''}`}
              onClick={() => setCyl(i)}
              disabled={!def.perCylinder}
            >
              {i + 1}
            </button>
          ))}
        </div>
        {!def.perCylinder && <span className="fc-na">engine-wide</span>}
      </div>

      <div className="fc-row fc-row-col">
        <div className="fc-lab-row">
          <span className="fc-lab">Severity</span>
          <span className="fc-val">{rate}<em>{def.unit}</em></span>
        </div>
        <input
          className="alt-slider"
          type="range"
          min={def.min}
          max={def.max}
          step={def.step}
          value={rate}
          onChange={(e) => setRate(Number(e.target.value))}
        />
      </div>

      <label className="fc-blind">
        <input
          type="checkbox"
          checked={blindMode}
          onChange={(e) => setBlindMode(e.target.checked)}
        />
        <span>
          <strong>Blind mode</strong> — hide the selection after injecting, so the
          presenter cannot see it either
        </span>
      </label>

      <div className="fc-actions">
        <button className="btn btn-primary" onClick={inject}>Inject fault</button>
        <button className="btn" onClick={() => applyConfig({}, { seekTo: 0 })}>Healthy</button>
        <button className="btn" onClick={() => applyConfig(SCRIPTED, { seekTo: 0 })}>Demo script</button>
      </div>

      <p className="note">
        {live ? (
          <>
            Injected into the <strong>engine model on the backend</strong> as a
            parameter change. The diagnosis you see is computed from the
            residuals alone — nothing on the backend that produces it can read
            which fault was chosen.
          </>
        ) : (
          <>
            No backend connected: this runs the browser's local simulator, not
            the engine model. Connect the live twin for the real pipeline.
          </>
        )}
      </p>
    </Panel>
  );
}

/** Shown after reveal, so the room can check the answer against the truth. */
/**
 * What was actually injected. Team-only: on a judge's screen it gives away the
 * answer before the twin finds it, which is the one moment the demo exists
 * for. Shown with ?team in the URL, toggled with G.
 */
export function InjectedTruth() {
  const config = useMission((s) => s.config);
  const blind = useMission((s) => s.blind);
  const [team, setTeam] = useState(() => new URLSearchParams(window.location.search).has('team'));
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'g' || e.key === 'G') setTeam((v) => !v);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  if (blind || !team) return null;

  const entries = (Object.keys(config) as (keyof FaultConfig)[]).filter(
    (k) => k !== 'warmAirMass' && config[k]
  );
  if (!entries.length) return null;

  return (
    <div className="truth-strip">
      <span className="truth-tag">GROUND TRUTH</span>
      {entries.map((k) => {
        const spec = config[k] as { cyl?: number; rate: number; startT: number };
        const d = KINDS.find((x) => x.kind === k);
        return (
          <span key={String(k)} className={`truth-item truth-${d?.family ?? 'unknown'}`}>
            {d?.label ?? String(k)}
            {spec.cyl !== undefined ? ` · cyl ${spec.cyl + 1}` : ''}
            <em>{spec.rate}{d?.unit ?? ''}</em>
          </span>
        );
      })}
    </div>
  );
}
