/**
 * The early-warning number: how long the twin has known about a problem that
 * every conventional threshold is still calling green.
 *
 * Built only from what the twin itself produced (anomaly persistence and its
 * diagnosis), never from the injected ground truth. In live flight nobody
 * knows when a fault "began", so the honest claim is "detected at T, limits
 * still green since", which this measures directly.
 */

import { useMemo } from 'react';
import { useMission } from './missionStore';
import type { MissionTick } from '../types/telemetry';

export interface Episode {
  /** mission time the current alert began */
  detectedT: number;
  nowT: number;
  /** seconds the twin has been ahead of every threshold (0 once a limit trips) */
  aheadS: number;
  /** a threshold has tripped at some point during this episode */
  limitsTripped: boolean;
  isSensor: boolean;
}

const alerting = (t: MissionTick) =>
  t.health.diagnosis.top[0].fault !== 'healthy' || t.health.anomaly.persistence.met;

export function useEpisode(): Episode | null {
  const ticks = useMission((s) => s.ticks);
  const i = useMission((s) => Math.max(0, Math.min(Math.round(s.index), s.ticks.length - 1)));
  return useMemo(() => {
    const now = ticks[i];
    if (!now || !alerting(now)) return null;
    let s = i;
    while (s > 0 && alerting(ticks[s - 1])) s--;
    let tripped = -1;
    for (let k = s; k <= i; k++) {
      if (ticks[k].health.limits_state !== 'green') { tripped = k; break; }
    }
    const detectedT = ticks[s].slow.t;
    const endT = tripped >= 0 ? ticks[tripped].slow.t : now.slow.t;
    return {
      detectedT,
      nowT: now.slow.t,
      aheadS: Math.max(0, endT - detectedT),
      limitsTripped: tripped >= 0,
      isSensor: now.health.diagnosis.is_sensor_fault,
    };
  }, [ticks, i]);
}

export function mmss(s: number): string {
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${r.toString().padStart(2, '0')}`;
}
