/**
 * Strip charts on uPlot — NOT Recharts.
 *
 * Recharts re-renders the React tree every frame and visibly drops frames at
 * 50 Hz with eight series. uPlot draws to canvas and handles tens of thousands
 * of points without complaint. This is the single most common performance
 * mistake in hackathon dashboards, and it shows up on stage, not in dev.
 */

import { useEffect, useMemo, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { C } from '../theme';
import { useMission } from '../state/missionStore';
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

export function StripChart({
  title, subtitle, pick, pickPredicted, unit, height = 150,
}: StripProps) {
  const holder = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);

  const ticks = useMission((s) => s.ticks);
  const index = useMission((s) => s.index);
  const now = Math.round(index);

  const WINDOW = 120;

  const data = useMemo(() => {
    const start = Math.max(0, now - WINDOW);
    const slice = ticks.slice(start, now + 1);
    const xs = slice.map((t) => t.slow.t);
    const series: number[][] = [];
    for (let c = 0; c < N_CYL; c++) series.push(slice.map((t) => pick(t)[c]));
    if (pickPredicted) {
      // One prediction trace is enough to make the point — the gap between
      // measurement and prediction is the entire product.
      series.push(slice.map((t) => pickPredicted(t)[0]));
    }
    return [xs, ...series] as uPlot.AlignedData;
  }, [ticks, now, pick, pickPredicted]);

  useEffect(() => {
    if (!holder.current) return;

    const series: uPlot.Series[] = [
      {},
      ...Array.from({ length: N_CYL }, (_, i) => ({
        label: `cyl ${i + 1}`,
        stroke: CYL_COLOURS[i],
        width: 1.4,
        points: { show: false },
      })),
    ];
    if (pickPredicted) {
      series.push({
        label: 'twin',
        stroke: C.twin,
        width: 1.2,
        dash: [4, 4],
        points: { show: false },
      });
    }

    const opts: uPlot.Options = {
      width: holder.current.clientWidth,
      height,
      padding: [8, 10, 0, 0],
      legend: { show: false },
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: false } },
      axes: [
        {
          stroke: C.textDim,
          grid: { stroke: C.line, width: 1 },
          ticks: { stroke: C.line2 },
          font: '10px ui-monospace, Menlo, monospace',
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
          font: '10px ui-monospace, Menlo, monospace',
          size: 52,
        },
      ],
      series,
    };

    plot.current = new uPlot(opts, data, holder.current);

    const ro = new ResizeObserver(() => {
      if (plot.current && holder.current) {
        plot.current.setSize({ width: holder.current.clientWidth, height });
      }
    });
    ro.observe(holder.current);

    return () => {
      ro.disconnect();
      plot.current?.destroy();
      plot.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height]);

  useEffect(() => {
    plot.current?.setData(data);
  }, [data]);

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
