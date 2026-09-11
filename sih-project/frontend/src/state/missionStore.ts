/**
 * Global mission state.
 *
 * The scrubber is GLOBAL, not a chart control — that is what turns "replay"
 * from a feature into a capability, and PS component E asks for it explicitly.
 * Everything on screen (3D model included) reads `tick` from here.
 */

import { create } from 'zustand';
import {
  generateMission,
  generateFrom,
  SCRIPTED,
  MISSION_DURATION_S,
  type FaultConfig,
} from '../mock/missionGenerator';
import { feed, type FeedSource } from '../net/feed';
import { VRDE_180, engineById, type EngineProfile } from '../config/engines';
import { lerpTick, smoothstep } from './interpolate';
import type { MissionTick } from '../types/telemetry';

const MISSION = generateMission();

/** How many live frames we retain for the scrubber and the heatmap window. */
const LIVE_BUFFER = 900;

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

  startEngine: () => void;
  stopEngine: () => void;

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

  startEngine: () => {
    // Live mode: restart the backend's run too, so the spool is followed by a
    // fresh mission rather than dropping into one already in progress.
    if (get().source === 'live') feed.send({ type: 'reset' });
    set({ engineState: 'starting', startProgress: 0, index: 0, playing: false, drawer: null });
  },

  stopEngine: () =>
    set({ engineState: 'off', startProgress: 0, playing: false, drawer: null }),

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
      // buffer, and the price of smoothness. Playback rate scales with how far
      // behind we are, so the buffer self-levels at ~1 frame and a tab that was
      // hidden catches up instead of drifting further behind forever.
      if (s.source === 'live') {
        if (s.index >= head) return {};
        const lag = head - s.index;
        return { index: Math.min(head, s.index + dt * Math.max(1, lag)) };
      }

      const next = s.index + dt * s.speed;
      if (next >= head) return { index: head, playing: false };
      return { index: next };
    }),

  play: () => set({ playing: true }),
  pause: () => set({ playing: false }),
  togglePlay: () => set((s) => ({ playing: !s.playing })),
  setSpeed: (speed) => set({ speed }),
  restart: () => set({ index: 0, playing: true }),
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

    if (opts?.fromNow) {
      // Begin the ramp just ahead of the frame being watched and leave the
      // clock alone, so the fault grows forward out of the picture instead of
      // the timeline teleporting into an already-developed one. Safe to
      // regenerate mid-playback: the generator's noise is a seeded stream that
      // does not branch on fault config, so the visible past is reproduced
      // identically and only the future diverges.
      const restart = !live && MISSION_DURATION_S - nowT < MIN_RUNWAY_S;
      config = stampStartT(cfg, (restart ? 0 : nowT) + INJECT_LEAD_S);
      nextIndex = restart ? 0 : s.index;
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

    const ticks = generateFrom(config, s.engine);
    set({
      ticks,
      config,
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
    const ticks = generateFrom(get().config, engine);
    set({ engine, ticks, index: 0, playing: true, selectedCylinder: null, explainOpen: false });
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
      const following = s.index >= base.length - 4;
      return {
        ticks,
        // Do NOT snap to the head: `advance` walks the clock there smoothly.
        // Once the ring buffer starts dropping frames off the front, every
        // index shifts with it or "now" would slide forward through the data.
        index: Math.max(0, s.index - dropped),
        playing: following ? s.playing : false,
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
