import { useEffect, useState } from 'react';
import { API } from '../config/engineCatalog';

/**
 * Mission history / replay — PS requirement E ("replay of historical mission data").
 *
 * Deliberately self-contained: it fetches its own data and holds its own local state, and never touches
 * the live missionStore or feed. Opening, browsing or replaying a past mission cannot affect what is on
 * screen live — the two are fully independent, which matters most for a recording in progress.
 *
 * Each backend/main.py websocket session is recorded to a flat file when it ends (reset or disconnect);
 * see backend/missions.py. This drawer lists those files and lets you scrub back through one exactly like
 * the live Scrubber, driven by a local frame index instead of the live clock.
 */

interface MissionMeta {
  id: string; engine: string; recorded_at: string; duration_s: number; n_ticks: number;
  healthy: boolean; top_fault: string | null; top_cylinder: number | null;
  first_alarm_s: number | null; final_recommendation: string | null;
}
interface Frame {
  t: number; rpm: number; map_hPa: number; cht_C: number[]; egt_C: number[];
  oil_press_bar: number; oil_temp_C: number; altitude_ft: number; throttle_pct: number;
  fault: string | null; fault_p: number | null; cylinder: number | null; is_sensor_fault: boolean;
  alarm: boolean; limits_state: string; recommended: string | null; reported_h: number | null;
}

const faultLabel = (f: string | null) => !f || f === 'healthy' ? 'Healthy' : f === 'unknown' ? 'Unknown (novel)' :
  f.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

function Sparkline({ series, height = 44, colors }: { series: number[][]; height?: number; colors: string[] }) {
  const all = series.flat().filter(Number.isFinite);
  if (!all.length) return null;
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = Math.max(hi - lo, 1e-6);
  const w = 100;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="mh-spark" role="img" aria-hidden>
      {series.map((s, i) => {
        const step = w / Math.max(s.length - 1, 1);
        const d = s.map((v, j) => `${j === 0 ? 'M' : 'L'} ${(j * step).toFixed(2)} ${(height - ((v - lo) / span) * height).toFixed(2)}`).join(' ');
        return <path key={i} d={d} fill="none" stroke={colors[i % colors.length]} strokeWidth={1.4} vectorEffect="non-scaling-stroke" />;
      })}
    </svg>
  );
}

export function MissionHistory() {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<MissionMeta[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ meta: MissionMeta; frames: Frame[] } | null>(null);
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    if (!open || list) return;
    fetch(`${API}/missions`).then((r) => r.json()).then((d) => setList(d.missions ?? []))
      .catch((e) => setErr((e as Error).message));
  }, [open, list]);

  const openMission = (id: string) => {
    setDetail(null); setErr(null);
    fetch(`${API}/missions/${id}`).then((r) => {
      if (!r.ok) throw new Error(`could not load (${r.status})`);
      return r.json();
    }).then((d) => { setDetail(d); setIdx(d.frames.length - 1); }).catch((e) => setErr((e as Error).message));
  };

  const f = detail?.frames[idx];

  return (
    <>
      <button className="mode-btn" onClick={() => setOpen(true)} title="Replay a past recorded mission">
        HISTORY
      </button>
      {open && (
        <div className="mh-overlay" role="dialog" aria-label="Mission history">
          <div className="mh-dialog">
            <header className="mh-head">
              <div>
                <h2>Mission history</h2>
                <p className="mh-sub">Past missions recorded from the live twin — replay only, no effect on the live session</p>
              </div>
              <button className="ep-x" onClick={() => { setOpen(false); setDetail(null); }} aria-label="Close">×</button>
            </header>

            {!detail ? (
              <div className="mh-body">
                {err && <div className="ep-err">{err}</div>}
                {!list ? <p className="ep-sub">Loading…</p> : list.length === 0 ? (
                  <p className="ep-sub">No completed missions recorded yet. Run a fault to completion and reset — it will appear here.</p>
                ) : (
                  <ul className="mh-list">
                    {list.map((m) => (
                      <li key={m.id}>
                        <button className="mh-row" onClick={() => openMission(m.id)}>
                          <span className="mh-row-main">
                            <span className={`mh-badge ${m.healthy ? 'ep-ok' : 'ep-warn-b'}`}>{faultLabel(m.top_fault)}</span>
                            <span>{m.engine}</span>
                            <span className="mh-dim">{m.recorded_at}</span>
                          </span>
                          <span className="mh-row-sub">
                            {m.duration_s.toFixed(0)}s
                            {m.first_alarm_s != null && ` · alarm at ${m.first_alarm_s.toFixed(0)}s`}
                            {m.top_cylinder != null && ` · cyl ${m.top_cylinder + 1}`}
                            {m.final_recommendation && ` · ended: ${m.final_recommendation}`}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : (
              <div className="mh-body">
                <button className="ep-btn" onClick={() => setDetail(null)}>← Back to list</button>
                <div className="mh-summary">
                  <span className={`mh-badge ${detail.meta.healthy ? 'ep-ok' : 'ep-warn-b'}`}>{faultLabel(detail.meta.top_fault)}</span>
                  <span>{detail.meta.engine} · {detail.meta.recorded_at} · {detail.meta.duration_s.toFixed(0)}s recorded</span>
                </div>

                <div className="mh-charts">
                  <div>
                    <div className="mh-chart-label">CHT °C (per cylinder)</div>
                    <Sparkline series={f?.cht_C ? detail.frames[0].cht_C.map((_, c) => detail.frames.map((r) => r.cht_C[c])) : []}
                      colors={['#0369a1', '#047857', '#b45309', '#be123c']} />
                  </div>
                  <div>
                    <div className="mh-chart-label">EGT °C (per cylinder)</div>
                    <Sparkline series={f?.egt_C ? detail.frames[0].egt_C.map((_, c) => detail.frames.map((r) => r.egt_C[c])) : []}
                      colors={['#0369a1', '#047857', '#b45309', '#be123c']} />
                  </div>
                </div>

                <input type="range" min={0} max={detail.frames.length - 1} value={idx}
                  onChange={(e) => setIdx(Number(e.target.value))} className="mh-scrub" aria-label="Scrub through the mission" />

                {f && (
                  <div className="mh-instant">
                    <div><b>T+{f.t.toFixed(0)}s</b> · {f.altitude_ft.toFixed(0)} ft · {f.throttle_pct.toFixed(0)}% throttle · {f.rpm.toFixed(0)} rpm</div>
                    <div>
                      Diagnosis: <b>{faultLabel(f.fault)}</b>{f.fault_p != null && ` (${(f.fault_p * 100).toFixed(0)}%)`}
                      {f.cylinder != null && `, cylinder ${f.cylinder + 1}`}
                      {f.is_sensor_fault && ' — sensor fault'}
                      {f.alarm && <span className="mh-badge ep-warn-b" style={{ marginLeft: 8 }}>ALARM</span>}
                    </div>
                    <div>
                      CHT {f.cht_C.map((v) => v.toFixed(0)).join(' / ')} °C · EGT {f.egt_C.map((v) => v.toFixed(0)).join(' / ')} °C ·
                      {' '}Oil {f.oil_press_bar.toFixed(2)} bar / {f.oil_temp_C.toFixed(0)}°C · Limits: {f.limits_state}
                    </div>
                    <div>
                      Mission: recommended <b>{(f.recommended ?? 'no estimate').toUpperCase()}</b>
                      {f.reported_h != null && ` · RUL ${f.reported_h.toFixed(2)} h`}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
