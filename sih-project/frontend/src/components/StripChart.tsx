/**
 * Strip charts on uPlot — NOT Recharts.
 *
 * Recharts re-renders the React tree every frame and visibly drops frames at
 * 50 Hz with eight series. uPlot draws to canvas and handles tens of thousands
 * of points without complaint. This is the single most common performance
 * mistake in hackathon dashboards, and it shows up on stage, not in dev.
 */

import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { C } from '../theme';
import { useMission } from '../state/missionStore';
import { lerp } from '../state/interpolate';
import { Panel } from './Panels';
import { N_CYL } from '../types/telemetry';

/** Per-cylinder series. Darker than the dark-theme originals so each clears
 *  4.5:1 against a white plot ground — same hue identity, readable ink. */
const CYL_COLOURS = C.cyl;

interface StripProps {
  title: string;
  subtitle?: string;
  /** Pull one series per cylinder out of a tick. */
  pick: (t: ReturnType<typeof useMission.getState>['ticks'][number]) => number[];
  /** The twin's prediction for the same channel — drawn dashed underneath. */
  pickPredicted?: (t: ReturnType<typeof useMission.getState>['ticks'][number]) => number[];
  unit: string;
  height?: number;
}

/** Seconds of history on screen. */
const WINDOW = 120;

export function StripChart({
  title, subtitle, pick, pickPredicted, unit, height = 150,
}: StripProps) {
  const holder = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);

  // The accessors are inline arrows at the call sites, so they get a fresh
  // identity on every render. Park them in refs so the draw loop always calls
  // the current ones without having to be torn down and rebuilt.
  const pickRef = useRef(pick);
  pickRef.current = pick;
  const predRef = useRef(pickPredicted);
  predRef.current = pickPredicted;
  const hasPred = !!pickPredicted;

  /** Fractional "now", in mission seconds. The x scale is pinned to it. */
  const xNow = useRef(0);

  /**
   * Build the visible window, with a fractional leading edge.
   *
   * The mission is 1 Hz but the clock is continuous, so the newest point sits
   * between two ticks. Interpolating it — and panning the x scale to match —
   * is what makes the trace sweep instead of hopping a sample per second.
   */
  const buildData = (): uPlot.AlignedData => {
    const { ticks, index } = useMission.getState();
    const last = ticks.length - 1;
    const series: number[][] = Array.from({ length: N_CYL + (hasPred ? 1 : 0) }, () => []);
    const xs: number[] = [];
    if (last < 0) return [xs, ...series] as uPlot.AlignedData;

    const at = Math.max(0, Math.min(index, last));
    const i = Math.floor(at);
    const u = at - i;

    for (let k = Math.max(0, i - WINDOW); k <= i; k++) {
      const t = ticks[k];
      xs.push(t.slow.t);
      const v = pickRef.current(t);
      for (let c = 0; c < N_CYL; c++) series[c].push(v[c]);
      // One prediction trace is enough to make the point — the gap between
      // measurement and prediction is the entire product.
      if (hasPred) series[N_CYL].push(predRef.current!(t)[0]);
    }

    if (u > 0 && i < last) {
      const a = ticks[i];
      const b = ticks[i + 1];
      xs.push(lerp(a.slow.t, b.slow.t, u));
      const va = pickRef.current(a);
      const vb = pickRef.current(b);
      for (let c = 0; c < N_CYL; c++) series[c].push(lerp(va[c], vb[c], u));
      if (hasPred) {
        series[N_CYL].push(lerp(predRef.current!(a)[0], predRef.current!(b)[0], u));
      }
    }

    xNow.current = xs[xs.length - 1] ?? 0;
    return [xs, ...series] as uPlot.AlignedData;
  };

  useEffect(() => {
    if (!holder.current) return;

    const series: uPlot.Series[] = [
      {},
      ...Array.from({ length: N_CYL }, (_, i) => ({
        label: `cyl ${i + 1}`,
        stroke: CYL_COLOURS[i],
        width: 2,
        points: { show: false },
      })),
    ];
    if (pickPredicted) {
      series.push({
        label: 'twin',
        stroke: C.twin,
        width: 1.8,
        dash: [5, 4],
        points: { show: false },
      });
    }

    const opts: uPlot.Options = {
      width: holder.current.clientWidth,
      height,
      padding: [8, 10, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: false, y: false } },
      scales: {
        x: {
          time: false,
          // Pin the viewport to the fractional clock. Returning a range here
          // (rather than calling setScale after setData) keeps uPlot's own
          // rescale pass in charge, so y still auto-fits normally.
          range: () => [xNow.current - WINDOW, xNow.current] as [number, number],
        },
        y: {
          // A tight auto-fit hugs the traces against the top/bottom edge,
          // which makes ordinary noise look like it's slamming into a limit.
          // Padding the range 15% on each side gives the eye headroom to read
          // the trend instead of the frame.
          range: (_u, min, max) => {
            const pad = (max - min) * 0.15 || 1;
            return [min - pad, max + pad];
          },
        },
      },
      axes: [
        {
          stroke: C.textDim,
          grid: { stroke: C.line, width: 1 },
          ticks: { stroke: C.line2 },
          font: '11.5px ui-monospace, Menlo, monospace',
          // Force whole-second spacing. Without this uPlot picks fractional
          // increments and the labels repeat ("0s 0s 1s 1s") once the window
          // is short, which looks broken on stage.
          incrs: [1, 2, 5, 10, 15, 30, 60],
          values: (_u, vals) => vals.map((v) => `${v.toFixed(0)}s`),
        },
        {
          stroke: C.textDim,
          grid: { stroke: C.line, width: 1 },
          ticks: { stroke: C.line2 },
          font: '11.5px ui-monospace, Menlo, monospace',
          // More, finer gridlines than uPlot's default pick — the whole point
          // of this axis is reading off a value, and a 10-degree window with
          // only two labelled lines forces a guess at everything in between.
          // uPlot's default tick spacing is tuned for a much taller plot than
          // this strip; lowering `space` (min px between ticks) is what
          // actually lets it use the finer increments above.
          space: 28,
          incrs: [0.2, 0.5, 1, 2, 5, 10, 20, 50, 100],
          size: 56,
        },
      ],
      series,
    };

    plot.current = new uPlot(opts, buildData(), holder.current);

    const ro = new ResizeObserver(() => {
      if (plot.current && holder.current) {
        plot.current.setSize({ width: holder.current.clientWidth, height });
      }
    });
    ro.observe(holder.current);

    // Redraw from a frame loop reading the store imperatively, rather than
    // re-rendering React on every clock change. That is the whole reason this
    // file uses uPlot instead of Recharts — see the note at the top — and it
    // means the component itself subscribes to nothing.
    let raf = requestAnimationFrame(function draw() {
      raf = requestAnimationFrame(draw);
      plot.current?.setData(buildData());
    });

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      plot.current?.destroy();
      plot.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height, hasPred]);

  return (
    <Panel title={title} subtitle={subtitle ?? unit}>
      <div ref={holder} className="chart-holder" />
      <div className="chart-legend">
        {Array.from({ length: N_CYL }, (_, i) => (
          <span key={i} className="legend-item">
            <span className="legend-swatch" style={{ background: CYL_COLOURS[i] }} />
            cyl {i + 1}
          </span>
        ))}
        {pickPredicted && (
          <span className="legend-item">
            <span className="legend-swatch legend-dash" />
            twin prediction
          </span>
        )}
        <span className="legend-unit">{unit}</span>
      </div>
    </Panel>
  );
}

/** The demo's primary chart: measured EGT against what the twin says it
 *  should be. Cylinder 2 walks away from the prediction while every limit
 *  stays green. */
export function EgtChart() {
  return (
    <StripChart
      title="Exhaust gas temperature"
      subtitle="measured vs twin prediction"
      pick={(t) => t.slow.egt_C}
      pickPredicted={(t) => t.predicted.egt_C}
      unit="°C"
      height={165}
    />
  );
}

export function ChtChart() {
  return (
    <StripChart
      title="Cylinder head temperature"
      subtitle="measured vs twin prediction"
      pick={(t) => t.slow.cht_C}
      pickPredicted={(t) => t.predicted.cht_C}
      unit="°C"
      height={165}
    />
  );
}
