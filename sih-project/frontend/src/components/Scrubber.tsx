/**
 * Global timeline scrubber — PS component E.
 *
 * It drives EVERYTHING: charts, heatmap, panels and the 3D model. Making it
 * global rather than a chart control is what turns "replay" from a feature
 * into a capability.
 */

import { useEffect, useRef } from 'react';
import { useMission, MISSION_DURATION_S } from '../state/missionStore';
import { SCRIPT_BEATS } from '../mock/missionGenerator';

export function Scrubber() {
  const index = useMission((s) => s.index);
  const playing = useMission((s) => s.playing);
  const speed = useMission((s) => s.speed);
  const advance = useMission((s) => s.advance);
  const setIndex = useMission((s) => s.setIndex);
  const togglePlay = useMission((s) => s.togglePlay);
  const setSpeed = useMission((s) => s.setSpeed);
  const restart = useMission((s) => s.restart);

  const raf = useRef<number>(0);
  const last = useRef<number>(performance.now());

  useEffect(() => {
    const loop = (now: number) => {
      const dt = (now - last.current) / 1000;
      last.current = now;
      advance(dt);
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf.current);
  }, [advance]);

  // Space to play/pause, arrows to step — a judge will reach for these.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
      if (e.code === 'ArrowRight') setIndex(index + (e.shiftKey ? 10 : 1));
      if (e.code === 'ArrowLeft') setIndex(index - (e.shiftKey ? 10 : 1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [index, setIndex, togglePlay]);

  const mm = Math.floor(index / 60);
  const ss = Math.floor(index % 60);

  return (
    <div className="scrubber">
      <button className="transport" onClick={togglePlay} title="Space">
        {playing ? '❚❚' : '▶'}
      </button>
      <button className="transport" onClick={restart} title="Restart">↺</button>

      <span className="clock">
        {mm}:{ss.toString().padStart(2, '0')}
      </span>

      <div className="track-wrap">
        <input
          className="track"
          type="range"
          min={0}
          max={MISSION_DURATION_S}
          step={0.1}
          value={index}
          onChange={(e) => setIndex(Number(e.target.value))}
        />
        {/* scripted beats as ticks on the timeline — so nobody has to remember
            when the interesting things happen during a live demo */}
        <div className="beats">
          {SCRIPT_BEATS.map((b) => (
            <button
              key={b.t}
              className="beat"
              style={{ left: `${(b.t / MISSION_DURATION_S) * 100}%` }}
              title={`${b.label} — jump to ${b.t}s`}
              onClick={() => setIndex(b.t)}
            />
          ))}
        </div>
      </div>

      <div className="speeds">
        {[0.5, 1, 2, 4].map((s) => (
          <button
            key={s}
            className={`speed${speed === s ? ' speed-on' : ''}`}
            onClick={() => setSpeed(s)}
          >
            {s}×
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Named jumps to each scripted beat. Rehearsed demos still go wrong; being able
 * to get back to the right moment in one action is cheap insurance.
 *
 * The bar WRAPS rather than scrolling horizontally. A scrolling bar moves the
 * chips under the cursor, so the one you reach for is not the one you hit —
 * which is fine in development and a real hazard on stage. Every beat is
 * therefore visible at once, and each also has a number key, because under
 * pressure a keystroke beats a small target.
 */
export function BeatBar() {
  const setIndex = useMission((s) => s.setIndex);
  const index = useMission((s) => s.index);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const n = Number(e.key);
      if (Number.isInteger(n) && n >= 1 && n <= SCRIPT_BEATS.length) {
        e.preventDefault();
        setIndex(SCRIPT_BEATS[n - 1].t);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setIndex]);

  return (
    <div className="beat-bar">
      {SCRIPT_BEATS.map((b, i) => {
        const next = SCRIPT_BEATS[i + 1]?.t ?? MISSION_DURATION_S;
        const active = index >= b.t && index < next;
        return (
          <button
            key={b.t}
            className={`beat-chip${active ? ' beat-chip-on' : ''}`}
            onClick={() => setIndex(b.t)}
            title={`Press ${i + 1} to jump here`}
          >
            <span className="beat-key">{i + 1}</span>
            <span className="beat-body">
              <span className="beat-time">
                {Math.floor(b.t / 60)}:{(b.t % 60).toString().padStart(2, '0')}
              </span>
              <span className="beat-label">{b.label}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
