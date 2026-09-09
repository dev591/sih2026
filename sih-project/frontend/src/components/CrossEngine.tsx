/**
 * Cross-engine differential — the free reference channel nobody exploits.
 *
 * A MALE UAV of this class is twin-engined. Two nominally identical engines,
 * built to the same specification, drawing from the same fuel tanks, flying the
 * same profile through the same air, for eighteen hours. That is the best
 * controlled experiment available in aviation, and it costs nothing to use.
 *
 *     Delta_k(t) = y_k^(A)(t) - y_k^(B)(t)
 *
 * Common-mode variation — ambient temperature, fuel batch quality, air density,
 * altitude — appears identically on both engines and CANCELS EXACTLY. What
 * survives the subtraction is engine-specific, which is to say it is
 * degradation or instrumentation.
 *
 * The differential's noise floor sits far below either absolute channel, so
 * slow drifts become visible earlier. Every team that models a single engine
 * forfeits this entirely.
 *
 * The operational rule, which is how a maintenance officer already thinks:
 *   BOTH engines drifting together is the environment or the fuel.
 *   ONE engine drifting alone is that engine.
 */

import { useMemo } from 'react';
import { useMission } from '../state/missionStore';
import { Note, Panel } from './Panels';
import { N_CYL } from '../types/telemetry';

interface Channel {
  key: string;
  label: string;
  unit: string;
  a: number;
  b: number;
  /** typical build-to-build spread, used to decide what counts as a real gap */
  tol: number;
  decimals: number;
}

const WINDOW = 90;

export function CrossEnginePanel() {
  const ticks = useMission((s) => s.ticks);
  const index = useMission((s) => s.index);
  const now = Math.round(index);
  const tick = ticks[Math.min(now, ticks.length - 1)];

  if (!tick.slowB) return null;
  const A = tick.slow;
  const B = tick.slowB;

  const channels: Channel[] = [
    ...Array.from({ length: N_CYL }, (_, i) => ({
      key: `cht${i}`, label: `CHT cyl ${i + 1}`, unit: '°C',
      a: A.cht_C[i], b: B.cht_C[i], tol: 2.5, decimals: 1,
    })),
    ...Array.from({ length: N_CYL }, (_, i) => ({
      key: `egt${i}`, label: `EGT cyl ${i + 1}`, unit: '°C',
      a: A.egt_C[i], b: B.egt_C[i], tol: 9, decimals: 0,
    })),
    { key: 'iat', label: 'Intake air temp', unit: 'K', a: A.iat_K, b: B.iat_K, tol: 1.5, decimals: 1 },
    { key: 'oilp', label: 'Oil pressure', unit: 'bar', a: A.oil_press_bar, b: B.oil_press_bar, tol: 0.12, decimals: 2 },
    { key: 'fuel', label: 'Fuel flow', unit: 'kg/h', a: A.fuel_flow_kgps * 3600, b: B.fuel_flow_kgps * 3600, tol: 0.35, decimals: 2 },
    { key: 'lambda', label: 'λ', unit: '', a: A.lambda, b: B.lambda, tol: 0.03, decimals: 3 },
  ];

  // How much of each channel is COMMON to both engines right now, relative to
  // where it started. This is the number that makes the argument: it can be
  // large (the whole fleet is in warmer air) while the differential is zero.
  const start = ticks[0];
  // Measured on INTAKE AIR TEMPERATURE, because that is the channel that
  // actually tracks ambient. Cylinder head temperature is driven by combustion
  // and barely moves with outside air, so quoting CHT here would be weak
  // evidence for a strong claim.
  const commonShift = useMemo(() => {
    if (!start.slowB) return 0;
    const meanNow = (A.iat_K + B.iat_K) / 2;
    const meanStart = (start.slow.iat_K + start.slowB.iat_K) / 2;
    return meanNow - meanStart;
  }, [A, B, start]);

  const oatShift = A.oat_K - start.slow.oat_K;

  // Which channels have a differential that exceeds build-to-build spread.
  const flagged = channels.filter((c) => Math.abs(c.a - c.b) > c.tol);

  // Differential history for the sparkline of the worst channel.
  const worst = flagged.length
    ? flagged.reduce((m, c) => (Math.abs(c.a - c.b) / c.tol > Math.abs(m.a - m.b) / m.tol ? c : m))
    : null;

  const history = useMemo(() => {
    if (!worst) return [];
    const from = Math.max(0, now - WINDOW);
    return ticks.slice(from, now + 1).map((t) => {
      if (!t.slowB) return 0;
      const m = worst.key.match(/^(cht|egt)(\d)$/);
      if (m) {
        const i = Number(m[2]);
        return m[1] === 'cht' ? t.slow.cht_C[i] - t.slowB.cht_C[i] : t.slow.egt_C[i] - t.slowB.egt_C[i];
      }
      if (worst.key === 'iat') return t.slow.iat_K - t.slowB.iat_K;
      if (worst.key === 'oilp') return t.slow.oil_press_bar - t.slowB.oil_press_bar;
      if (worst.key === 'fuel') return (t.slow.fuel_flow_kgps - t.slowB.fuel_flow_kgps) * 3600;
      return t.slow.lambda - t.slowB.lambda;
    });
  }, [ticks, now, worst]);

  const commonSignificant = Math.abs(commonShift) > 3 || Math.abs(oatShift) > 3;

  return (
    <Panel
      title="Cross-engine differential"
      subtitle="Δ = A − B · common mode cancels"
      flag={flagged.length ? 'alert' : 'ok'}
    >
      {/* The two statements are independent and both can be true at once —
          which is the strongest case for this channel. A warm air mass moves
          every absolute reading on both engines, AND a fouling injector moves
          one channel on one engine. The differential separates them without
          being told which is which. */}
      <div className={`cross-verdict ${flagged.length ? 'cross-eng' : commonSignificant ? 'cross-env' : 'cross-ok'}`}>
        {commonSignificant && (
          <div className="cross-stmt">
            <strong>Common mode: both engines moved together.</strong>
            Ambient is {oatShift >= 0 ? '+' : ''}{oatShift.toFixed(1)} K from mission start and
            both engines' intake air temperature has moved{' '}
            {commonShift >= 0 ? '+' : ''}{commonShift.toFixed(1)} K with it.
            Every absolute channel moved — and <em>none of it survives the subtraction</em>.
            This is the environment, not an engine.
          </div>
        )}
        {flagged.length ? (
          <div className="cross-stmt">
            <strong>
              Engine-specific: {flagged.length} channel{flagged.length > 1 ? 's' : ''} beyond build spread.
            </strong>
            {flagged.slice(0, 3).map((c) => c.label).join(', ')}
            {flagged.length > 3 ? ` +${flagged.length - 3} more` : ''}. B is flying the same
            profile through the same air on the same fuel, so what survives belongs to A.
          </div>
        ) : (
          !commonSignificant && (
            <div className="cross-stmt">
              <strong>Both engines tracking.</strong> All differentials inside
              build-to-build spread.
            </div>
          )
        )}
      </div>

      <div className="cross-table">
        <div className="cross-head">
          <span />
          <span>A</span>
          <span>B</span>
          <span>Δ</span>
        </div>
        {channels.map((c) => {
          const d = c.a - c.b;
          const over = Math.abs(d) > c.tol;
          const mag = Math.min(1, Math.abs(d) / (c.tol * 3));
          return (
            <div className={`cross-row${over ? ' cross-row-hit' : ''}`} key={c.key}>
              <span className="cross-label">{c.label}</span>
              <span className="cross-num">{c.a.toFixed(c.decimals)}</span>
              <span className="cross-num cross-b">{c.b.toFixed(c.decimals)}</span>
              <span className="cross-delta">
                <span className="cross-track">
                  <span
                    className="cross-bar"
                    style={{
                      width: `${mag * 48}%`,
                      left: d >= 0 ? '50%' : `${50 - mag * 48}%`,
                      background: over ? 'var(--alert)' : 'var(--line-2)',
                    }}
                  />
                </span>
                <span className={`cross-dv${over ? ' tone-alert' : ''}`}>
                  {d >= 0 ? '+' : ''}{d.toFixed(c.decimals)}
                </span>
              </span>
            </div>
          );
        })}
      </div>

      {worst && history.length > 4 && (
        <div className="cross-spark">
          <div className="cross-spark-head">
            <span>{worst.label} differential · last {WINDOW}s</span>
            <span className="cross-spark-tol">build spread ±{worst.tol}{worst.unit}</span>
          </div>
          <svg viewBox={`0 0 ${history.length} 40`} preserveAspectRatio="none" className="cross-svg">
            <line x1={0} y1={20} x2={history.length} y2={20} stroke="var(--line-2)" strokeWidth={0.5} />
            <polyline
              points={history
                .map((v, i) => {
                  const scale = worst.tol * 4;
                  const y = 20 - Math.max(-19, Math.min(19, (v / scale) * 20));
                  return `${i},${y}`;
                })
                .join(' ')}
              fill="none" stroke="var(--alert)" strokeWidth={1} vectorEffect="non-scaling-stroke"
            />
          </svg>
        </div>
      )}

      <Note>
        Common-mode variation — ambient, fuel batch, density, altitude — appears
        identically on both engines and cancels exactly, so the differential's
        noise floor sits far below either absolute channel and slow drifts show
        up earlier. <strong>Both engines drifting together is the environment;
        one drifting alone is that engine.</strong> Every team that models a
        single engine forfeits this.
      </Note>
    </Panel>
  );
}
