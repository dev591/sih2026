/**
 * Global timeline scrubber — PS component E.
 *
 * It drives EVERYTHING: charts, heatmap, panels and the 3D model. Making it
 * global rather than a chart control is what turns "replay" from a feature
 * into a capability.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useMission, MISSION_DURATION_S, type Drawer } from '../state/missionStore';
import { SCRIPT_BEATS, SCRIPTED } from '../mock/missionGenerator';

export function Scrubber() {
  const index = useMission((s) => s.index);
  const playing = useMission((s) => s.playing);
  const speed = useMission((s) => s.speed);
  const advance = useMission((s) => s.advance);
  const setIndex = useMission((s) => s.setIndex);
  const togglePlay = useMission((s) => s.togglePlay);
  const setSpeed = useMission((s) => s.setSpeed);
  const restart = useMission((s) => s.restart);
  const engineState = useMission((s) => s.engineState);
  const startProgress = useMission((s) => s.startProgress);
  const startEngine = useMission((s) => s.startEngine);
  const stopEngine = useMission((s) => s.stopEngine);
  const jumpTo = useBeatJump();

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
      {/* Ignition is separate from the replay transport: one is the engine,
          the other is the tape. Keeping them adjacent but distinct stops
          "paused" from ever being mistaken for "shut down". */}
      {engineState === 'running' ? (
        <button className="transport ignition" onClick={stopEngine} title="Shut the engine down">
          STOP
        </button>
      ) : (
        <button
          className="transport ignition ignition-go"
          onClick={startEngine}
          disabled={engineState === 'starting'}
          title="Start the engine"
        >
          {engineState === 'starting' ? `${Math.round(startProgress * 100)}%` : 'START'}
        </button>
      )}

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
              onClick={() => jumpTo(b.t)}
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
/**
 * Which drawer each scripted beat needs open.
 *
 * In simple mode the panels are behind edge tabs, so jumping to a beat has to
 * bring up whatever that beat's narration points at — otherwise the presenter
 * is hunting for a tab mid-sentence. Keyed by beat TIME rather than by index so
 * it cannot silently fall out of step with SCRIPT_BEATS the way a parallel
 * array would; beats not listed want the bare engine, which is the point of
 * beats 1 and 2.
 *
 * Follows docs/pitch/demo-script.md — 1:20 isolation, 2:25 the decision, 3:00
 * the sensor-drift twist where the argument is the ABSENCE of corroborating
 * residuals, so that one opens the residual heatmap.
 */
const BEAT_DRAWER: Record<number, Drawer> = {
  62: 'faults',    // anomaly crosses, every limit still green
  95: 'faults',    // isolation: injector fouling, cyl 2
  120: 'mission',  // warm air mass — cross-engine differential
  140: 'mission',  // RUL with uncertainty band
  165: 'mission',  // continue / derate / RTB — the judge drags the altitude
  200: 'trends',   // CHT sensor drifting; watch the residuals NOT move
  235: 'faults',   // sensor fault correctly identified
  260: 'trends',   // unmodelled — twin confidence drops
};

/**
 * Jump to a scripted beat. Shared by the chips and by the timeline ticks,
 * because they are the same ten beats and it would be its own small trap for
 * one of them to open the drawer and the other not to.
 */
function useBeatJump() {
  const setIndex = useMission((s) => s.setIndex);
  const setDrawer = useMission((s) => s.setDrawer);
  const simple = useMission((s) => s.mode === 'simple');
  return useCallback(
    (t: number) => {
      setIndex(t);
      // Only in simple mode — the expert grid already has every panel on screen.
      if (simple) setDrawer(BEAT_DRAWER[t] ?? null);
    },
    [setIndex, setDrawer, simple]
  );
}

export function BeatBar() {
  const index = useMission((s) => s.index);
  const onScript = useMission((s) => s.config === SCRIPTED);
  const jumpTo = useBeatJump();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const n = Number(e.key);
      if (Number.isInteger(n) && n >= 1 && n <= SCRIPT_BEATS.length) {
        e.preventDefault();
        jumpTo(SCRIPT_BEATS[n - 1].t);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [jumpTo]);

  // ?beat=N opens straight at a scripted beat, for rehearsal and for
  // capturing a specific moment without clicking through the mission.
  useEffect(() => {
    const n = Number(new URLSearchParams(window.location.search).get('beat'));
    if (Number.isInteger(n) && n >= 1 && n <= SCRIPT_BEATS.length) {
      const id = setTimeout(() => jumpTo(SCRIPT_BEATS[n - 1].t), 400);
      return () => clearTimeout(id);
    }
  }, [jumpTo]);

  // The beats describe the rehearsed mission. In the sandbox they refer to
  // events that are not in the timeline, so showing them would be a lie.
  if (!onScript) {
    return (
      <div className="beat-bar beat-bar-sandbox">
        <span className="sandbox-tag">SANDBOX</span>
        <span className="sandbox-note">
          Running an injected configuration — the scripted beats do not apply.
          Use <strong>Demo script</strong> in the fault console to return to the
          rehearsed mission.
        </span>
      </div>
    );
  }

  return (
    <div className="beat-bar">
      {SCRIPT_BEATS.map((b, i) => {
        const next = SCRIPT_BEATS[i + 1]?.t ?? MISSION_DURATION_S;
        const active = index >= b.t && index < next;
        return (
          <button
            key={b.t}
            className={`beat-chip${active ? ' beat-chip-on' : ''}`}
            onClick={() => jumpTo(b.t)}
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
