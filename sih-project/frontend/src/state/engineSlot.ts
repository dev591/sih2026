/**
 * Where the single 3D engine should currently be drawn.
 *
 * The engine used to be mounted inside both SimpleView and ExpertGrid, and the
 * mode toggle is a ternary — so switching views destroyed the WebGL context and
 * built a new one, with its EffectComposer, Bloom and SMAA passes. Measured on
 * this machine that left the engine area blank for about eight seconds, on the
 * default demo view. Not survivable on stage.
 *
 * So there is now exactly ONE Canvas, mounted for the life of the app in a
 * fixed-position layer, and each view publishes an empty box saying where it
 * would like the engine drawn. The Canvas never unmounts, so the context is
 * never lost and the toggle is instant.
 */
import { create } from 'zustand';

export interface SlotRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

interface SlotState {
  rect: SlotRect | null;
  setRect: (r: SlotRect) => void;
}

export const useEngineSlot = create<SlotState>((set) => ({
  rect: null,
  // Deliberately never reset to null on unmount. During a view switch the
  // outgoing slot's cleanup and the incoming slot's effect both run in the same
  // commit; clearing here would blank the engine for a frame to fix nothing,
  // since the incoming slot overwrites this immediately anyway.
  setRect: (rect) =>
    set((s) => {
      const p = s.rect;
      // Ignore no-op publishes — ResizeObserver fires generously and every
      // state change here repositions a full-screen fixed layer.
      if (p && p.top === rect.top && p.left === rect.left
        && p.width === rect.width && p.height === rect.height) return s;
      return { rect };
    }),
}));
