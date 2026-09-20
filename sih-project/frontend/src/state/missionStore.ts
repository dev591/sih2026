/**
 * Global mission state.
 *
 * The scrubber is GLOBAL, not a chart control — that is what turns "replay"
 * from a feature into a capability, and PS component E asks for it explicitly.
 * Everything on screen (3D model included) reads `tick` from here.
 */

import { create } from 'zustand';
import {
  generateFrom,
  freshSeed,
  SCRIPTED,
  MISSION_DURATION_S,
  type FaultConfig,
  type AltitudePlan,
} from '../mock/missionGenerator';
import { feed, type FeedSource } from '../net/feed';
import { VRDE_180, engineById, type EngineProfile } from '../config/engines';
import { lerpTick, smoothstep } from './interpolate';
import type { MissionTick } from '../types/telemetry';

// A fresh seed each page load, so the very first mission a viewer sees is
// already a genuine run, not a memorised script — see the note on `seed` in
// MissionState for why every regeneration downstream must either reuse this
// exact value (continuity: an already-displayed past must reproduce
// identically) or replace it deliberately (a genuinely new run).
const initialSeed = freshSeed();
const MISSION = generateFrom(SCRIPTED, VRDE_180, initialSeed);

/** How many live frames we retain for the scrubber and the heatmap window. */
const LIVE_BUFFER = 900;

/** Above this many seconds behind the live head, snap instead of catching up
 *  visibly — this much lag means a real pause or scrub, not network jitter. */
const LIVE_SNAP_THRESHOLD_S = 5;
/** Cap on live catch-up playback speed, so closing ordinary jitter is a barely
 *  perceptible speed-up rather than a visible fast-forward. */
const LIVE_CATCHUP_SPEED_CAP = 3;

/**
 * Sample the mission at a FRACTIONAL index, interpolating between neighbours.
 *
 * `index` advances every animation frame but the mission is 1 Hz, so without
 * this every readout on screen snaps once per second. Shared by the hook and
 * the imperative getter so the 3D view, the charts and the panels can never
 * disagree about what "now" means.
 */
/** A beat of healthy engine before an injected fault starts to ramp, so the
 *  operator sees the "before" rather than being dropped into the "after". */
const INJECT_LEAD_S = 2;

/** Below this much mission left there is no runway to watch a fault develop,
 *  so injection restarts the run instead of ramping from the current frame. */
const MIN_RUNWAY_S = 60;

/** Stamp every spec in a config with the same start time. */
function stampStartT(cfg: FaultConfig, startT: number): FaultConfig {
  const out: Record<string, unknown> = {};
  for (const [k, spec] of Object.entries(cfg)) {
    out[k] = spec ? { ...spec, startT } : spec;
  }
  return out as FaultConfig;
}

export function sampleTicks(ticks: MissionTick[], index: number): MissionTick {
  const last = ticks.length - 1;
  const at = Math.max(0, Math.min(index, last));
  const i = Math.floor(at);
  const u = at - i;
  if (u === 0 || i >= last) return ticks[Math.min(i, last)];
  return lerpTick(ticks[i], ticks[i + 1], u);
}

/** Which slide-out panel group is showing in simple mode. */
export type Drawer = 'faults' | 'mission' | 'trends';
export type Mode = 'simple' | 'expert';

/**
 * Ignition state. This is a PRESENTATION state, not a physics one: the twin
 * has nothing to say about an engine that is not turning, so while this is
 * anything but 'running' the panels show em-dashes rather than numbers. The
 * spool is the engine warming up, never a fabricated diagnosis.
 */
export type EngineState = 'off' | 'starting' | 'running';

/** Seconds from pressing START to the mission beginning. */
const START_DURATION_S = 10;

/**
 * Engines start RUNNING. A cold open is the better piece of theatre, but a
 * presenter who forgets to press START would be left with a dead dashboard in
 * front of a panel, so the cold start is something you opt into on purpose.
 */
const INITIAL_ENGINE_STATE: EngineState = 'running';

const MODE_KEY = 'pramana.mode';

/** Persisted so a rehearsal doesn't reset the view between reloads. */
function storedMode(): Mode {
  try {
    return localStorage.getItem(MODE_KEY) === 'expert' ? 'expert' : 'simple';
  } catch {
    return 'simple';
  }
}

interface MissionState {
  ticks: MissionTick[];
  /** Active engine profile. Everything downstream — geometry, limits, critical
   *  altitude, which parity paths exist — is derived from this. */
  engine: EngineProfile;
  index: number;
  playing: boolean;
  speed: number;
  /** Cylinder selected in the 3D view / explain drawer. null = closed. */
  selectedCylinder: number | null;
  explainOpen: boolean;
  /** Where the frames on screen actually came from. Shown in the top bar so
   *  nobody can mistake the simulation for the real engine during a demo. */
  source: FeedSource;
  feedError: string | null;
  liveFrames: number;
  /** What is currently injected. The console edits this; the scripted demo
   *  uses SCRIPTED. Both run identical physics. */
  config: FaultConfig;
  /** True while a fault has been injected but not yet revealed on screen —
   *  so a judge can choose one without the presenter seeing which. */
  blind: boolean;
  /** Simple = the engine and one verdict. Expert = the full instrument grid.
   *  Simple is the default because fourteen panels at once emphasise nothing. */
  mode: Mode;
  /** Which drawer is slid out over the simple view. null = none. */
  drawer: Drawer | null;
  /** Post-flight report sheet. Renders the frame under the scrubber, so
   *  scrubbing back to detection and printing reports THAT instant. */
  reportOpen: boolean;
  /** Ignition state, and 0..1 progress through the start sequence. */
  engineState: EngineState;
  startProgress: number;
  /** Commanded cruise altitude, ramping. null = the profile's own cruise. */
  altCmd: AltitudePlan | null;
  /**
   * The noise seed behind the CURRENT `ticks`. Actions that regenerate the
   * mission fall into two kinds, and mixing them up is a real bug either way:
   *   - CONTINUITY regenerations (an altitude command, or injecting a fault
   *     `fromNow` mid-playback) MUST reuse this exact seed — the noise stream
   *     does not branch on config, so replaying it from t=0 with the same
   *     seed reproduces the already-displayed past exactly and only the
   *     future (after the change) diverges. Reseeding here would retroactively
   *     change values the viewer already saw.
   *   - FRESH-RUN regenerations (engine start, "Demo script", "Healthy", a
   *     full fault-console restart, switching engine) draw a new seed via
   *     `freshSeed()` and store it here — this is what makes each run a
   *     genuine, independent draw rather than the same script every time.
   */
  seed: number;

  startEngine: () => void;
  stopEngine: () => void;
  /** Command a new cruise altitude. The twin follows it too, so the residuals
   *  should stay flat while every raw channel moves. */
  setAltitude: (ft: number) => void;

  tick: () => MissionTick;
  setIndex: (i: number) => void;
  advance: (dtSeconds: number) => void;
  play: () => void;
  pause: () => void;
  togglePlay: () => void;
  setSpeed: (s: number) => void;
  restart: () => void;
  selectCylinder: (i: number | null) => void;
  setExplainOpen: (open: boolean) => void;
  /** Append one frame arriving from BE-1's socket. */
  pushLive: (tick: MissionTick) => void;
  /** Replace the fault configuration and regenerate the mission.
   *  `fromNow` ramps the fault in from the frame on screen instead of
   *  restarting the timeline at a point where it has already developed. */
  applyConfig: (
    cfg: FaultConfig,
    opts?: { seekTo?: number; blind?: boolean; fromNow?: boolean }
  ) => void;
  reveal: () => void;
  setEngine: (id: string) => void;
  setMode: (m: Mode) => void;
  toggleMode: () => void;
  setDrawer: (d: Drawer | null) => void;
  setReportOpen: (open: boolean) => void;
  setFeed: (source: FeedSource, error: string | null, frames: number) => void;
  /** Jump straight to a scripted beat — used by the demo shortcut bar. */
  seekTo: (t: number) => void;
}

export const useMission = create<MissionState>((set, get) => ({
  ticks: MISSION,
  index: 0,
  playing: true,
  speed: 1,
  selectedCylinder: null,
  explainOpen: false,
  source: 'connecting',
  feedError: null,
  liveFrames: 0,
  config: SCRIPTED,
  blind: false,
  engine: VRDE_180,
  mode: storedMode(),
  drawer: null,
  reportOpen: false,
  engineState: INITIAL_ENGINE_STATE,
  startProgress: INITIAL_ENGINE_STATE === 'running' ? 1 : 0,
  altCmd: null,
  seed: initialSeed,

  startEngine: () => {
    const s = get();
    // Live mode: restart the backend's run too, so the spool is followed by a
    // fresh mission rather than dropping into one already in progress. The
    // backend owns its own noise/physics there, so there is no local seed to
    // manage.
    if (s.source === 'live') {
      feed.send({ type: 'reset' });
      set({ engineState: 'starting', startProgress: 0, index: 0, playing: false, drawer: null });
      return;
    }
    // A fresh run, not a replay of the one just watched — a new seed means
    // the same scenario's detection timing genuinely differs start to start.
    const seed = freshSeed();
    const ticks = generateFrom(s.config, s.engine, seed, s.altCmd);
    set({ seed, ticks, engineState: 'starting', startProgress: 0, index: 0, playing: false, drawer: null });
  },

  stopEngine: () =>
    set({ engineState: 'off', startProgress: 0, playing: false, drawer: null }),

  setAltitude: (ft) => {
    const s = get();
    const nowT = s.tick().slow.t;
    const altCmd: AltitudePlan = {
      startT: nowT,
      from_ft: s.tick().slow.altitude_ft,
      to_ft: ft,
    };

    if (s.source === 'live') {
      // The backend owns its own altitude profile; tell it, and let the frames
      // carry the result back rather than simulating the climb locally.
      feed.send({ type: 'altitude', ft });
      set({ altCmd });
      return;
    }

    // Regenerating mid-climb is safe for the same reason fault injection is:
    // the noise stream does not branch on altitude, so the past is reproduced
    // exactly and only the future bends.
    // Same seed, deliberately: continuity, not a new run — see `seed` above.
    set({ altCmd, ticks: generateFrom(s.config, s.engine, s.seed, altCmd) });
  },

  tick: () => {
    const { ticks, index } = get();
    return sampleTicks(ticks, index);
  },

  setIndex: (i) =>
    set((s) => ({ index: Math.max(0, Math.min(i, s.ticks.length - 1)) })),

  advance: (dt) =>
    set((s) => {
      // The start sequence runs on the same clock as everything else, so the
      // scrubber's frame loop drives it and there is no second timer to keep
      // in step.
      if (s.engineState === 'starting') {
        const p = s.startProgress + dt / START_DURATION_S;
        return p >= 1
          ? { engineState: 'running' as EngineState, startProgress: 1, playing: true, index: 0 }
          : { startProgress: p };
      }
      if (s.engineState === 'off') return {};

      if (!s.playing) return {};
      const head = s.ticks.length - 1;

      // In live mode the socket sets the DESTINATION and this clock walks
      // toward it, so 1 Hz frames render as continuous motion instead of a
      // once-a-second jolt. It costs us up to one frame of latency — a jitter
      // buffer, and the price of smoothness.
      if (s.source === 'live') {
        if (s.index >= head) return {};
        const lag = head - s.index;
        // A real pause or a scrub-back leaves a large gap — closing that
        // smoothly would mean visibly fast-forwarding for several seconds,
        // which reads as "it just jumped forward" on its own. Snap to just
        // behind the head instead; only ordinary network jitter (a couple of
        // frames) gets the smooth catch-up, capped so it is a barely
        // perceptible speed-up rather than a jump.
        if (lag > LIVE_SNAP_THRESHOLD_S) return { index: Math.max(0, head - 1) };
        const speed = Math.min(LIVE_CATCHUP_SPEED_CAP, Math.max(1, lag));
        return { index: Math.min(head, s.index + dt * speed) };
      }

      const next = s.index + dt * s.speed;
      if (next >= head) return { index: head, playing: false };
      return { index: next };
    }),

  play: () => set({ playing: true }),
  pause: () => set({ playing: false }),
  togglePlay: () => set((s) => ({ playing: !s.playing })),
  setSpeed: (speed) => set({ speed }),
  restart: () => {
    const s = get();
    // In live mode, resetting the LOCAL index does nothing on its own: the
    // backend keeps streaming whatever fault/altitude state was already
    // active, and the live-catchup snap (advance, above) pulls the display
    // straight back to the head one frame later — so "Restart" looked like it
    // did nothing. Tell the backend to clear its state too, same as the
    // FaultConsole's "Healthy" button, so the frame it snaps back to is
    // actually a clean one.
    if (s.source === 'live') feed.send({ type: 'reset' });
    set({ index: 0, playing: true });
  },
  selectCylinder: (i) => set({ selectedCylinder: i, explainOpen: i !== null }),
  setExplainOpen: (explainOpen) => set({ explainOpen }),
  seekTo: (t) => set({ index: Math.max(0, Math.min(t, MISSION_DURATION_S)) }),

  applyConfig: (cfg, opts) => {
    const s = get();
    const live = s.source === 'live';
    // Mission seconds, which is what `startT` is measured in at both ends.
    // Equal to `index` in the scripted mission, and the backend's own clock
    // when live — the two are not interchangeable.
    const nowT = s.tick().slow.t;

    let config = cfg;
    let nextIndex = Math.max(0, Math.min(opts?.seekTo ?? 0, MISSION_DURATION_S));
    // Preserving the visible past only means something when there IS a
    // visible past being preserved — i.e. genuinely continuing forward from
    // partway through a run. Anything that lands back at t=0 (no fromNow at
    // all — "Demo script", "Healthy" — or fromNow with too little runway
    // left, forcing a restart) is a fresh run and earns a fresh seed.
    let continuity = false;

    if (opts?.fromNow) {
      // Begin the ramp just ahead of the frame being watched and leave the
      // clock alone, so the fault grows forward out of the picture instead of
      // the timeline teleporting into an already-developed one. Safe to
      // regenerate mid-playback while keeping the SAME seed: the generator's
      // noise is a seeded stream that does not branch on fault config, so the
      // visible past is reproduced identically and only the future diverges.
      const restart = !live && MISSION_DURATION_S - nowT < MIN_RUNWAY_S;
      config = stampStartT(cfg, (restart ? 0 : nowT) + INJECT_LEAD_S);
      nextIndex = restart ? 0 : s.index;
      continuity = !restart;
    }

    if (live) {
      if (Object.keys(config).length === 0) {
        feed.send({ type: 'reset' });
      } else {
        feed.send({ type: 'fault_config', config });
      }
      // The backend owns the timeline here. Regenerating the scripted mission
      // would drop simulated frames into a feed labelled LIVE.
      set({
        config,
        blind: opts?.blind ?? false,
        selectedCylinder: null,
        explainOpen: false,
      });
      return;
    }

    // Carry the commanded altitude across: injecting a fault must not silently
    // put the aircraft back at its book cruise altitude.
    const seed = continuity ? s.seed : freshSeed();
    const ticks = generateFrom(config, s.engine, seed, s.altCmd);
    set({
      ticks,
      config,
      seed,
      blind: opts?.blind ?? false,
      index: Math.max(0, Math.min(nextIndex, ticks.length - 1)),
      playing: true,
      selectedCylinder: null,
      explainOpen: false,
    });
  },

  reveal: () => set({ blind: false }),

  setMode: (mode) => {
    try { localStorage.setItem(MODE_KEY, mode); } catch { /* private window */ }
    // Drawers belong to the simple view; leaving one open behind the grid would
    // cover the right-hand column.
    set({ mode, drawer: null });
  },
  toggleMode: () => get().setMode(get().mode === 'simple' ? 'expert' : 'simple'),
  setDrawer: (drawer) => set({ drawer }),

  // Pause on open: a report is of one instant, and a moving scrubber
  // underneath it would print a different frame than the one reviewed.
  setReportOpen: (reportOpen) =>
    set(reportOpen ? { reportOpen, playing: false } : { reportOpen }),

  /** Swap the engine. Everything downstream — geometry, limits, critical
   *  altitude, which parity paths exist — follows from the profile, so this
   *  is genuinely a configuration change and not a rebuild. */
  setEngine: (id) => {
    const engine = engineById(id);
    // Index resets to 0 — no visible past to preserve, so this is a fresh run.
    const seed = freshSeed();
    const ticks = generateFrom(get().config, engine, seed, get().altCmd);
    set({ engine, ticks, seed, index: 0, playing: true, selectedCylinder: null, explainOpen: false });
  },

  pushLive: (tick) =>
    set((s) => {
      // First live frame replaces the simulated mission entirely, so the two
      // are never interleaved on one timeline. Testing `source === 'live'` is
      // NOT enough: the status callback flips source before the first frame
      // arrives, so that test appended live frames onto the 301 scripted ones.
      // Identity against MISSION is exactly "no live frame has landed yet".
      const first = s.ticks === MISSION;
      const base = first ? [] : s.ticks;

      // The backend times each CONNECTION from zero, so a reconnect makes the
      // mission clock jump backwards. Carrying the old frames over would leave
      // the buffer non-monotonic in `slow.t` and draw the strip charts as a
      // fold-back. A clock that went backwards means a new run, not new data.
      const prev = base[base.length - 1];
      if (first || (prev && tick.slow.t < prev.slow.t)) {
        return { ticks: [tick], index: 0, playing: true };
      }

      const appended = [...base, tick];
      const dropped = Math.max(0, appended.length - LIVE_BUFFER);
      const ticks = dropped ? appended.slice(dropped) : appended;

      // Follow the head only if the operator has not scrubbed back to look at
      // something — a live feed that yanks the timeline out from under a judge
      // mid-question is worse than one that waits. The smooth clock deliberately
      // trails the head by ~1 frame, so the tolerance has to clear that.
      return {
        ticks,
        // Do NOT snap to the head: `advance` walks the clock there smoothly,
        // and does NOT touch `playing` — that stays exactly what the play/pause
        // button last set it to. This used to auto-pause once the operator
        // fell 4 frames behind the live head, which is normal jitter, not a
        // deliberate scrub-back — the result was the transport pausing itself
        // with no click, and the eventual catch-up feeding into the OTHER bug
        // in `advance` unbounded catch-up speed to produce a visible jump.
        // Once the ring buffer starts dropping frames off the front, every
        // index shifts with it or "now" would slide forward through the data.
        index: Math.max(0, s.index - dropped),
      };
    }),

  setFeed: (source, error, frames) =>
    set((s) => {
      // Dropping back to the simulation restores the scripted mission so the
      // demo still has something coherent to show.
      if (source !== 'live' && s.source === 'live') {
        return { source, feedError: error, liveFrames: frames, ticks: MISSION, index: 0 };
      }
      return { source, feedError: error, liveFrames: frames };
    }),
}));

// ---------------------------------------------------------------------------
// Wire the socket to the store. Runs once, at module load.
// ---------------------------------------------------------------------------
feed.onStatus((st) =>
  useMission.getState().setFeed(st.source, st.error, st.framesReceived)
);
feed.onFrame((tick) => useMission.getState().pushLive(tick));
feed.start();

// Dev-only handle, so the running timeline can be inspected from the console
// during a rehearsal without wiring temporary logging through components.
if (import.meta.env.DEV) {
  (window as unknown as Record<string, unknown>).__mission = useMission;
}

/** Interpolated current tick — the store index is fractional while playing. */
export function useCurrentTick(): MissionTick {
  const ticks = useMission((s) => s.ticks);
  const index = useMission((s) => s.index);
  return sampleTicks(ticks, index);
}

/**
 * The nearest WHOLE tick, without interpolation.
 *
 * For consumers that are either expensive to re-render (the residual heatmap
 * rebuilds ~990 cells) or read only discrete state (diagnosis labels), where a
 * fresh object 60 times a second buys nothing. Anything that should visibly
 * move belongs on `useCurrentTick` instead.
 */
export function useSnappedTick(): MissionTick {
  const ticks = useMission((s) => s.ticks);
  const index = useMission((s) => s.index);
  return ticks[Math.max(0, Math.min(Math.round(index), ticks.length - 1))];
}

export function useMissionTime(): number {
  return useMission((s) => s.index);
}

/** Ambient metal temperature for a cold engine, degC. */
const COLD_METAL_C = 15;

/**
 * What the ENGINE should look like right now, as opposed to what the twin
 * thinks. During the start sequence these are spooled from cold; once running
 * they are simply the live values.
 *
 * Only rpm and metal temperature are shaped here. Nothing diagnostic is —
 * an engine that is not yet turning has no residuals, and inventing some to
 * fill the panels during the animation would be exactly the kind of fabricated
 * number this codebase refuses to print elsewhere.
 */
export function useEngineDisplay() {
  const engineState = useMission((s) => s.engineState);
  const startProgress = useMission((s) => s.startProgress);
  const tick = useCurrentTick();

  if (engineState === 'running') {
    return { engineState, running: true, rpm: tick.slow.rpm, chtScale: 1, startProgress: 1 };
  }
  // Cranking dominates the first third, then it catches and spools up.
  const p = engineState === 'off' ? 0 : startProgress;
  const cranked = smoothstep(0, 0.28, p) * 0.22;
  const caught = smoothstep(0.25, 1, p) * 0.78;
  return {
    engineState,
    running: false,
    rpm: tick.slow.rpm * (cranked + caught),
    // Metal lags the gas path badly on a cold start; it is still climbing when
    // the engine is already at speed.
    chtScale: smoothstep(0.15, 1.25, p),
    startProgress: p,
  };
}

/** Blend a live channel back toward cold-metal for the start animation. */
export function coldBlend(value: number, chtScale: number): number {
  return COLD_METAL_C + (value - COLD_METAL_C) * chtScale;
}

export { MISSION_DURATION_S };
