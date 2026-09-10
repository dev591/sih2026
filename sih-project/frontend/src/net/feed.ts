/**
 * Live telemetry feed.
 *
 * The frontend's contract is pramana.health.v1 (docs/spec/telemetry-schema.md),
 * and this is the only place that knows where it comes from.
 *
 * BEHAVIOUR:
 *   - On start it tries BE-1's WebSocket.
 *   - If it connects, every frame that arrives is appended and the dashboard is
 *     running on REAL data from the mean-value engine model. Source reads LIVE.
 *   - If it cannot connect, or the socket drops, the locally generated mission
 *     takes over so the UI is never blank, and the source badge says SIMULATED
 *     so nobody can mistake one for the other on stage.
 *   - It keeps retrying with backoff, so starting the backend mid-session
 *     upgrades the dashboard to live without a reload.
 *
 * BE-1: publish MissionTick-shaped JSON at PRAMANA_WS below. If you would
 * rather send the three lanes separately, send {slow, fast, health, predicted}
 * in one envelope — that is what this expects, and it matches the spec doc.
 */

import type { MissionTick } from '../types/telemetry';

export type FeedSource = 'live' | 'simulated' | 'connecting';

export interface FeedStatus {
  source: FeedSource;
  url: string;
  lastFrameAt: number | null;
  framesReceived: number;
  error: string | null;
}

/** Override with VITE_PRAMANA_WS in .env.local if BE-1 serves elsewhere. */
const PRAMANA_WS =
  (import.meta.env.VITE_PRAMANA_WS as string | undefined) ??
  `ws://${window.location.hostname}:8000/ws/telemetry`;

const RETRY_MS = [1000, 2000, 4000, 8000, 15000];
/** If frames stop arriving for this long we treat the link as dead and fall
 *  back, rather than showing a frozen dashboard that still claims to be live. */
const STALE_MS = 4000;

type FrameHandler = (tick: MissionTick) => void;
type StatusHandler = (s: FeedStatus) => void;

class TelemetryFeed {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: number | null = null;
  private staleTimer: number | null = null;
  private frameHandlers = new Set<FrameHandler>();
  private statusHandlers = new Set<StatusHandler>();
  private stopped = false;

  private status: FeedStatus = {
    source: 'connecting',
    url: PRAMANA_WS,
    lastFrameAt: null,
    framesReceived: 0,
    error: null,
  };

  onFrame(h: FrameHandler) {
    this.frameHandlers.add(h);
    return () => this.frameHandlers.delete(h);
  }

  onStatus(h: StatusHandler) {
    this.statusHandlers.add(h);
    h(this.status);
    return () => this.statusHandlers.delete(h);
  }

  send(msg: any) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && this.status.source === 'live') {
      this.ws.send(JSON.stringify(msg));
    }
  }

  getStatus() {
    return this.status;
  }

  private setStatus(patch: Partial<FeedStatus>) {
    this.status = { ...this.status, ...patch };
    this.statusHandlers.forEach((h) => h(this.status));
  }

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.timer) window.clearTimeout(this.timer);
    if (this.staleTimer) window.clearTimeout(this.staleTimer);
    this.ws?.close();
    this.ws = null;
  }

  private connect() {
    if (this.stopped) return;
    try {
      this.ws = new WebSocket(PRAMANA_WS);
    } catch {
      this.fallback('could not open socket');
      return;
    }

    this.ws.onopen = () => {
      this.attempt = 0;
      this.setStatus({ source: 'live', error: null });
      this.armStaleTimer();
    };

    this.ws.onmessage = (ev) => {
      try {
        const tick = JSON.parse(ev.data as string) as MissionTick;
        // Guard against a half-implemented backend: a frame missing the health
        // block would blank the whole dashboard, which is worse than staying
        // on the simulation.
        if (!tick?.health || tick.health.schema !== 'pramana.health.v1') return;
        this.setStatus({
          source: 'live',
          lastFrameAt: Date.now(),
          framesReceived: this.status.framesReceived + 1,
          error: null,
        });
        this.armStaleTimer();
        this.frameHandlers.forEach((h) => h(tick));
      } catch {
        /* a malformed frame is not worth tearing the link down for */
      }
    };

    this.ws.onerror = () => {
      this.setStatus({ error: 'socket error' });
    };

    this.ws.onclose = () => {
      this.fallback('backend not connected');
    };
  }

  private armStaleTimer() {
    if (this.staleTimer) window.clearTimeout(this.staleTimer);
    this.staleTimer = window.setTimeout(() => {
      this.setStatus({ source: 'simulated', error: 'no frames — link stale' });
    }, STALE_MS);
  }

  private fallback(reason: string) {
    this.ws = null;
    if (this.stopped) return;
    this.setStatus({ source: 'simulated', error: reason });
    const delay = RETRY_MS[Math.min(this.attempt, RETRY_MS.length - 1)];
    this.attempt += 1;
    this.timer = window.setTimeout(() => this.connect(), delay);
  }
}

export const feed = new TelemetryFeed();
