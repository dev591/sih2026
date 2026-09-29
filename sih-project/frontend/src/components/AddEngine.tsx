import { useEffect, useRef, useState } from 'react';
import { API } from '../config/engineCatalog';

/**
 * Add an engine. Two levels, so the time cost is visible before it is spent:
 *   LOW  — write the profile YAML (each field marked user / derived / assumed), validate it, run a 60 s physics
 *          smoke test. Files only; about a minute.
 *   HIGH — also train that engine's model (noise levels, dataset, training, prognostics, commissioning) in an
 *          isolated copy of the project. The plan and its estimated time are shown first and nothing starts
 *          until the button is pressed. It never touches another engine's model.
 * "Add to app" then makes the engine a card on the landing screen (it only ADDS files for that engine's id).
 */

interface Field { key: string; label: string; unit?: string; required?: boolean; type?: 'text' | 'select'; options?: string[]; hint?: string }

const FIELDS: Field[] = [
  { key: 'id', label: 'Engine id', required: true, hint: 'lowercase, e.g. myeng_120' },
  { key: 'name', label: 'Display name' },
  { key: 'cycle', label: 'Cycle', required: true, type: 'select', options: ['diesel', 'spark_ignition'] },
  { key: 'aspiration', label: 'Aspiration', required: true, type: 'select', options: ['turbocharged', 'naturally_aspirated'] },
  { key: 'cylinders', label: 'Cylinders', required: true, hint: '4 only for now' },
  { key: 'displacement_L', label: 'Displacement', unit: 'L', required: true },
  { key: 'rated_power_kW', label: 'Rated power', unit: 'kW', required: true },
  { key: 'rated_speed_rpm', label: 'Rated speed', unit: 'rpm', required: true },
  { key: 'max_continuous_rpm', label: 'Max continuous speed', unit: 'rpm' },
  { key: 'critical_altitude_ft', label: 'Critical altitude', unit: 'ft' },
  { key: 'bore_mm', label: 'Bore', unit: 'mm' },
  { key: 'stroke_mm', label: 'Stroke', unit: 'mm' },
  { key: 'compression_ratio', label: 'Compression ratio' },
  { key: 'cht_limit_C', label: 'CHT limit', unit: '°C' },
];

interface Gen {
  id: string; yaml: string; errors: string[]; warnings: string[]; user_specified: string[];
  smoke: { ok: boolean; rpm?: number; brake_kW?: number; wall_s?: number; error?: string } | null;
  base: string; derived: string[]; limits: string[];
}
interface Plan { total: string; total_s: number; basis: string; steps: { name: string; desc: string; est_s: number }[]; limits: string[] }
interface Job { status: 'running' | 'done' | 'failed'; elapsed_s: number; log: string[]; error: string | null; result: any }

const fmt = (s: number) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);

async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail ?? `request failed (${r.status})`);
  return j as T;
}

export function AddEngine({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [v, setV] = useState<Record<string, string>>({ cycle: 'diesel', aspiration: 'turbocharged', cylinders: '4' });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [gen, setGen] = useState<Gen | null>(null);
  const [plan, setPlan] = useState<{ size: string; plan: Plan } | null>(null);
  const [job, setJob] = useState<{ kind: 'train' | 'install'; id: string; est?: number } | null>(null);
  const [jobState, setJobState] = useState<Job | null>(null);
  const [installed, setInstalled] = useState<Record<string, string> | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  const missing = FIELDS.filter((f) => f.required && !(v[f.key] ?? '').trim()).map((f) => f.label);

  const spec = () => {
    const o: Record<string, unknown> = {};
    for (const f of FIELDS) {
      const raw = (v[f.key] ?? '').trim();
      if (!raw) continue;
      o[f.key] = f.type === 'select' || f.key === 'id' || f.key === 'name' ? raw : Number(raw);
    }
    if (typeof o.id === 'string') o.id = o.id.toLowerCase();
    return o;
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const watch = (kind: 'train' | 'install', id: string, est?: number) => {
    setJob({ kind, id, est }); setJobState(null);
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(async () => {
      try {
        const j = await api<Job>(`/engines/job/${id}`);
        setJobState(j);
        if (j.status !== 'running') {
          window.clearInterval(timer.current!);
          if (kind === 'install' && j.status === 'done') { setInstalled(j.result); onChanged(); }
        }
      } catch { /* keep polling */ }
    }, 2000);
  };

  const generate = () => run(async () => {
    setPlan(null); setInstalled(null); setJob(null); setJobState(null);
    const g = await api<Gen>('/engines/generate', { spec: spec() });
    setGen(g);
  });

  const showPlan = (size: string) => run(async () => {
    setPlan({ size, plan: await api<Plan>(`/engines/plan?id=${gen!.id}&size=${size}`) });
  });

  const train = () => run(async () => {
    const r = await api<{ job: string; estimated_s: number }>('/engines/train', { id: gen!.id, size: plan!.size, confirmed: true });
    watch('train', r.job, r.estimated_s);
  });

  const install = () => run(async () => {
    const r = await api<{ job: string }>('/engines/install', { id: gen!.id });
    watch('install', r.job);
  });

  const trained = jobState?.status === 'done' && job?.kind === 'train';

  return (
    <div className="ep-modal" role="dialog" aria-label="Add an engine">
      <div className="ep-dialog">
        <header className="ep-dhead">
          <h2>Add an engine</h2>
          <button className="ep-x" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="ep-dbody">
          <p className="ep-note">
            Give the specifications you know. Anything you leave out is copied from a base engine of the same cycle and
            marked <b>assumed</b>; physics constants such as compressor and turbine sizes are not rescaled to your engine.
          </p>

          <div className="ep-form">
            {FIELDS.map((f) => (
              <label key={f.key} className="ep-field">
                <span>{f.label}{f.unit ? ` (${f.unit})` : ''}{f.required ? ' *' : ''}</span>
                {f.type === 'select' ? (
                  <select value={v[f.key] ?? ''} onChange={(e) => setV({ ...v, [f.key]: e.target.value })}>
                    {f.options!.map((o) => <option key={o} value={o}>{o.replace('_', ' ')}</option>)}
                  </select>
                ) : (
                  <input value={v[f.key] ?? ''} placeholder={f.hint ?? ''} onChange={(e) => setV({ ...v, [f.key]: e.target.value })} />
                )}
              </label>
            ))}
          </div>

          <div className="ep-actions">
            <button className="ep-btn ep-primary" disabled={busy || missing.length > 0} onClick={generate}
              title={missing.length ? `Still needed: ${missing.join(', ')}` : ''}>
              Generate profile (low level, about a minute)
            </button>
            {missing.length > 0 && <span className="ep-hint">Needed: {missing.join(', ')}</span>}
          </div>

          {err && <div className="ep-err">{err}</div>}

          {gen && (
            <section className="ep-result">
              <h3>Profile for “{gen.id}”</h3>
              <ul className="ep-list">
                <li>{gen.errors.length === 0
                  ? <span className="ep-okt">Validation passed — no missing required fields.</span>
                  : <span className="ep-errt">{gen.errors.length} required field(s) missing: {gen.errors.join('; ')}</span>}</li>
                <li>You gave {gen.user_specified.length} field(s); {gen.warnings.length} others are assumed (copied from <b>{gen.base}</b>).
                  {gen.derived.length > 0 && <> Derived from your inputs: {gen.derived.join(', ')}.</>}</li>
                <li>{gen.smoke?.ok
                  ? <>Physics smoke test passed: {gen.smoke.rpm} rpm, {gen.smoke.brake_kW} kW at 5,000 ft and 80% throttle ({gen.smoke.wall_s} s).</>
                  : <span className="ep-errt">Physics smoke test failed{gen.smoke?.error ? `: ${gen.smoke.error}` : ''}.</span>}</li>
                <li className="ep-mono">{gen.yaml}</li>
              </ul>
              <details className="ep-lim"><summary>Honest limits</summary>
                <ul>{gen.limits.map((l) => <li key={l}>{l}</li>)}</ul></details>

              {gen.errors.length === 0 && (
                <div className="ep-choices">
                  <div className="ep-choice">
                    <b>Low level — done</b>
                    <span>Files are written. Add it to the app now; it runs as a live twin with <i>no diagnosis</i> until a model is trained.</span>
                    <button className="ep-btn" disabled={busy || job?.kind === 'train' && jobState?.status === 'running'} onClick={install}>Add to app</button>
                  </div>
                  <div className="ep-choice">
                    <b>High level — also train its model</b>
                    <span>Noise levels, dataset, training, prognostics, commissioning, in an isolated copy. Takes longer; see the time first.</span>
                    <span className="ep-row">
                      <button className="ep-btn" disabled={busy} onClick={() => showPlan('quick')}>Quick plan</button>
                      <button className="ep-btn" disabled={busy} onClick={() => showPlan('standard')}>Standard plan</button>
                    </span>
                  </div>
                </div>
              )}
            </section>
          )}

          {plan && !job && (
            <section className="ep-result">
              <h3>{plan.size === 'quick' ? 'Quick' : 'Standard'} training plan — about {plan.plan.total}</h3>
              <ol className="ep-list">
                {plan.plan.steps.map((s) => <li key={s.name}>{s.desc} — about {fmt(s.est_s)}</li>)}
              </ol>
              <p className="ep-note">Estimate: {plan.plan.basis}. It heavily uses this PC's CPU and GPU while it runs, so do not record during it.</p>
              <div className="ep-actions">
                <button className="ep-btn ep-primary" disabled={busy} onClick={train}>Start training (about {plan.plan.total})</button>
                <button className="ep-btn" onClick={() => setPlan(null)}>Cancel</button>
              </div>
            </section>
          )}

          {job && (
            <section className="ep-result">
              <h3>{job.kind === 'train' ? 'Training' : 'Adding to app'} — {jobState?.status ?? 'starting'}</h3>
              {jobState && (
                <>
                  {job.kind === 'train' && job.est != null && (
                    <div className="ep-bar" title="elapsed vs estimate">
                      <div className="ep-bar-in" style={{ width: `${Math.min(100, (jobState.elapsed_s / job.est) * 100)}%` }} />
                    </div>
                  )}
                  <p className="ep-note">
                    Elapsed {fmt(jobState.elapsed_s)}{job.est != null ? ` of an estimated ${fmt(job.est)}` : ''}.
                    {jobState.log.length > 0 && <> Latest: {jobState.log[jobState.log.length - 1]}</>}
                  </p>
                  {jobState.error && <div className="ep-err">{jobState.error}</div>}
                  {trained && jobState.result?.metrics && (
                    <p className="ep-okt">
                      Trained. On held-out test runs of the twin: recall {(jobState.result.metrics.recall * 100).toFixed(1)}% of {jobState.result.metrics.faulty_runs} faulty runs,
                      top-1 diagnosis {(jobState.result.metrics.top1_after_alarm * 100).toFixed(1)}%,
                      {' '}{jobState.result.metrics.false_alarms_per_hour.toFixed(2)} false alarms per hour on {jobState.result.metrics.healthy_runs} healthy runs.
                      Simulated data only, not a real engine.
                    </p>
                  )}
                  {trained && (
                    <div className="ep-actions">
                      <button className="ep-btn ep-primary" disabled={busy} onClick={install}>Add to app with this model</button>
                    </div>
                  )}
                  {job.kind === 'install' && installed && (
                    <ul className="ep-list">
                      <li>Profile: {installed.profile}</li><li>Noise levels: {installed.sigma}</li><li>Model: {installed.model}</li>
                      <li className="ep-okt">Added. It is now a card on the landing screen.</li>
                    </ul>
                  )}
                </>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
