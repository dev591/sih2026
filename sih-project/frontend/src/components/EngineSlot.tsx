/**
 * A hole in the layout where the engine should appear.
 *
 * Renders nothing itself — it measures its own box and publishes it, and the
 * single persistent <Engine3D/> layer in App positions itself over whatever the
 * current slot reports. See state/engineSlot.ts for why the Canvas is hoisted.
 */
import { useLayoutEffect, useRef } from 'react';
import { useEngineSlot } from '../state/engineSlot';

export function EngineSlot({ className }: { className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const setRect = useEngineSlot((s) => s.setRect);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const publish = () => {
      const r = el.getBoundingClientRect();
      // A slot can measure zero on its first layout pass. Publishing that would
      // collapse the Canvas and make FitCamera solve against a degenerate
      // aspect; skip and wait for the ResizeObserver to deliver a real box.
      if (r.width < 1 || r.height < 1) return;
      setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    };

    publish();
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    window.addEventListener('resize', publish);
    // Expert view scrolls its columns, and the layer is position:fixed, so it
    // has to follow. Capture phase catches scrolls on any ancestor.
    window.addEventListener('scroll', publish, true);

    return () => {
      ro.disconnect();
      window.removeEventListener('resize', publish);
      window.removeEventListener('scroll', publish, true);
    };
  }, [setRect]);

  return <div ref={ref} className={className} />;
}
