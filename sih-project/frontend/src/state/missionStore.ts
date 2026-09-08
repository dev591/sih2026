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
import type { MissionTick } from '../types/telemetry';

const MISSION = generateMission();

/** How many live frames we retain for the scrubber and the heatmap window. */
const LIVE_BUFFER = 900;

interface MissionState {
  ticks: MissionTick[];
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
  /** Replace the fault configuration and regenerate the mission. */
  applyConfig: (cfg: FaultConfig, opts?: { seekTo?: number; blind?: boolean }) => void;
  reveal: () => void;
  setEngine: (id: string) => void;
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

  tick: () => {
    const { ticks, index } = get();
    return ticks[Math.min(index, ticks.length - 1)];
  },

  setIndex: (i) =>
    set((s) => ({ index: Math.max(0, Math.min(i, s.ticks.length - 1)) })),

  advance: (dt) =>
    set((s) => {
      // In live mode the socket drives the timeline, not the clock.
      if (s.source === 'live' || !s.playing) return {};
      const next = s.index + dt * s.speed;
      if (next >= s.ticks.length - 1) return { index: s.ticks.length - 1, playing: false };
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
    const ticks = generateFrom(cfg, get().engine);
    set({
      ticks,
      config: cfg,
      blind: opts?.blind ?? false,
      index: Math.max(0, Math.min(opts?.seekTo ?? 0, ticks.length - 1)),
      playing: true,
      selectedCylinder: null,
      explainOpen: false,
    });
  },

  reveal: () => set({ blind: false }),

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
      // are never interleaved on one timeline.
      const base = s.source === 'live' ? s.ticks : [];
      const ticks = [...base, tick].slice(-LIVE_BUFFER);
      // Follow the head only if the operator has not scrubbed back to look at
      // something — a live feed that yanks the timeline out from under a judge
      // mid-question is worse than one that waits.
      const following = s.index >= base.length - 2;
      return {
        ticks,
        index: following ? ticks.length - 1 : s.index,
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

/** Interpolated current tick — the store index is fractional while playing. */
export function useCurrentTick(): MissionTick {
  const ticks = useMission((s) => s.ticks);
  const index = useMission((s) => s.index);
  return ticks[Math.min(Math.round(index), ticks.length - 1)];
}

export function useMissionTime(): number {
  return useMission((s) => s.index);
}

export { MISSION_DURATION_S };
