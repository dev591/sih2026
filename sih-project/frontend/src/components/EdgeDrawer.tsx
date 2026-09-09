/**
 * One slide-out panel group, anchored to an edge of the stage.
 *
 * The simple view's whole premise is that the fourteen panels still exist and
 * are one click away, rather than all competing for attention at once. So this
 * is a shell only: it holds the SAME panel components the expert grid renders,
 * never a second copy of them.
 *
 * Children are a render function rather than elements so that a closed drawer
 * mounts nothing. That matters more than it looks: ResidualHeatmap alone is
 * ~990 DOM nodes, each with a title attribute, rebuilt every tick — leaving
 * three drawers live behind a closed edge would cost more than the 3D scene.
 */

import { useEffect, type ReactNode } from 'react';
import { useMission, type Drawer } from '../state/missionStore';

export type Side = 'left' | 'right' | 'bottom';

export function EdgeDrawer({
  id, side, title, subtitle, children,
}: {
  id: Drawer;
  side: Side;
  title: string;
  subtitle?: string;
  children: () => ReactNode;
}) {
  const open = useMission((s) => s.drawer === id);
  const setDrawer = useMission((s) => s.setDrawer);

  // Esc closes whichever drawer is open. Registered only while open so it never
  // competes with the explain drawer's own handler.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawer(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, setDrawer]);

  return (
    <aside className={`edge edge-${side}${open ? ' edge-open' : ''}`} aria-hidden={!open}>
      <header className="edge-head">
        <span className="edge-title">{title}</span>
        {subtitle && <span className="edge-sub">{subtitle}</span>}
        <button className="edge-close" onClick={() => setDrawer(null)} aria-label={`Close ${title}`}>
          ✕
        </button>
      </header>
      <div className="edge-body">{open && children()}</div>
    </aside>
  );
}

/** The always-visible tab that pulls a drawer out. */
export function EdgeTab({
  id, side, label, flag,
}: {
  id: Drawer;
  side: Side;
  label: string;
  /** Status dot — lets a closed drawer still say that something inside it wants
   *  attention, which is the price of hiding it in the first place. */
  flag?: 'ok' | 'warn' | 'alert' | 'sensor';
}) {
  const open = useMission((s) => s.drawer === id);
  const setDrawer = useMission((s) => s.setDrawer);
  return (
    <button
      className={`edge-tab edge-tab-${side}${open ? ' edge-tab-on' : ''}`}
      onClick={() => setDrawer(open ? null : id)}
      aria-expanded={open}
    >
      {flag && <span className={`edge-dot tone-bg-${flag}`} />}
      {label}
    </button>
  );
}
