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

/** Identifies this browser tab to the backend so the assistant reads THIS tab's engine session. */
export const CLIENT_ID = Math.random().toString(36).slice(2, 12);

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
  /** Engine the socket asks the backend to run (?engine=<id>). null = simulation only, no socket. */
  private engineId: string | null = null;

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

  private url() {
    return this.engineId
      ? `${PRAMANA_WS}?engine=${encodeURIComponent(this.engineId)}&client=${CLIENT_ID}`
      : `${PRAMANA_WS}?client=${CLIENT_ID}`;
  }

  /** Point the feed at an engine and (re)connect. null = this engine has no live model: stay on the simulation. */
  setEngine(id: string | null) {
    this.stop();
    this.engineId = id;
    this.attempt = 0;
    if (id === null) {
      this.stopped = true;
      this.setStatus({ source: 'simulated', error: 'simulation only for this engine', lastFrameAt: null, framesReceived: 0 });
      return;
    }
    this.setStatus({ source: 'connecting', error: null, lastFrameAt: null, framesReceived: 0, url: this.url() });
    this.start();
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
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url());
      this.ws = ws;
    } catch {
      this.fallback('could not open socket');
      return;
    }

    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempt = 0;
      this.setStatus({ source: 'live', error: null });
      this.armStaleTimer();
    };

    ws.onmessage = (ev) => {
      if (this.ws !== ws) return;
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

    ws.onerror = () => {
      if (this.ws !== ws) return;
      this.setStatus({ error: 'socket error' });
    };

    ws.onclose = () => {
      if (this.ws !== ws) return;
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
