import { useCallback, useEffect, useRef, useState } from 'react';
import { useMission } from '../state/missionStore';
import { API } from '../config/engineCatalog';
import { CLIENT_ID } from '../net/feed';

/**
 * In-app assistant: chat and push-to-talk, on the engine that is open.
 *
 * Everything runs on this machine (Whisper for speech-to-text, the local LLM through Ollama, Kokoro for
 * speech). It is read-only: it explains the live twin, answers questions about the code, and can walk through
 * adding an engine, but it cannot change the engine or inject a fault, and decision questions are answered by
 * the mission layer, not the model. It only answers from the LIVE feed of the open engine; on a
 * simulation-only engine it says it has no live data.
 *
 * Talk: hold the mic button, or hold F9. Release to send.
 */

type Role = 'user' | 'assistant';
interface Msg { role: Role; text: string; meta?: string }
type Phase = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking';

const sid = () => Math.random().toString(36).slice(2, 10);

async function post<T>(path: string, body: unknown, raw = false): Promise<T> {
  const r = await fetch(`${API}${path}`, raw
    ? { method: 'POST', headers: { 'Content-Type': (body as Blob).type || 'audio/webm' }, body: body as Blob }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.detail ?? `request failed (${r.status})`);
  }
  return (await r.json()) as T;
}

type Action =
  | { type: 'altitude'; ft: number }
  | { type: 'reset' }
  | { type: 'faults'; mode: 'add' | 'replace'; config: Record<string, { cyl?: number; rate: number }> };

/** Run what the user commanded, through exactly the code the Fault console uses, so the console, the live
 *  backend and the 3D view all stay in step. Faults start a couple of seconds ahead of the frame on screen. */
function runActions(actions: Action[]) {
  const st = useMission.getState();
  for (const a of actions) {
    if (a.type === 'altitude') st.setAltitude(a.ft);
    else if (a.type === 'reset') st.applyConfig({});
    else if (a.type === 'faults') {
      // Live: the backend's clock is the NEWEST frame, not the (possibly lagging or scrubbed-back) one on screen;
      // a start time in the past would make the backend treat the fault as already developed.
      const head = st.ticks[st.ticks.length - 1];
      const startT = (st.source === 'live' ? head.slow.t : st.tick().slow.t) + 2;
      const stamped = Object.fromEntries(Object.entries(a.config).map(([k, v]) => [k, { ...v, startT }]));
      st.applyConfig(a.mode === 'add' ? { ...st.config, ...stamped } : stamped);
    }
  }
}

/** What is worth reading aloud: first sentences only when the reply is long (paths and plans are read on screen). */
function speakable(text: string): string[] {
  const clean = text.replace(/[*_`#]/g, '').replace(/\s+/g, ' ').trim();
  const sents = clean.split(/(?<=[.!?])\s+/);
  const picked = clean.length > 260 ? sents.slice(0, 3) : sents;
  return picked.filter(Boolean).slice(0, 6);
}

export function AssistantChat() {
  const engineId = useMission((s) => s.pickedEngineId);
  const engineName = useMission((s) => s.engine.short);
  const source = useMission((s) => s.source);
  const live = source === 'live';

  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [watching, setWatching] = useState(false);
  const [speak, setSpeak] = useState(true);
  const [warm, setWarm] = useState<'no' | 'loading' | 'ready' | 'failed'>('no');
  const [lat, setLat] = useState<string | null>(null);

  const session = useRef(sid());
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stream = useRef<MediaStream | null>(null);
  const t0 = useRef(0);
  const audio = useRef<HTMLAudioElement | null>(null);
  const cancelSpeech = useRef(0);
  const scroller = useRef<HTMLDivElement | null>(null);
  const keyDown = useRef(false);
  const observeTimer = useRef<number | null>(null);
  const observeUntil = useRef(0);

  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }); }, [msgs, phase]);

  // A new engine is a new conversation: the old answers were about another machine.
  useEffect(() => { session.current = sid(); setMsgs([]); setLat(null); }, [engineId]);

  useEffect(() => {
    if (!live || warm !== 'no') return;
    setWarm('loading');
    post<{ ms: number }>('/assistant/warm', {})
      .then(() => setWarm('ready')).catch(() => setWarm('failed'));
  }, [live, warm]);

  const stopSpeech = useCallback(() => {
    cancelSpeech.current += 1;
    audio.current?.pause();
    audio.current = null;
  }, []);

  const say = useCallback(async (reply: string, started: number) => {
    const my = ++cancelSpeech.current;
    const parts = speakable(reply);
    setPhase('speaking');
    let first = true;
    // synthesise the next sentence while the current one plays
    let next: Promise<Blob> | null = parts.length
      ? fetch(`${API}/assistant/tts`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: parts[0] }) }).then((r) => r.blob())
      : null;
    for (let i = 0; i < parts.length && my === cancelSpeech.current; i++) {
      const blob = await next!;
      next = i + 1 < parts.length
        ? fetch(`${API}/assistant/tts`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: parts[i + 1] }) }).then((r) => r.blob())
        : null;
      if (my !== cancelSpeech.current) break;
      const el = new Audio(URL.createObjectURL(blob));
      audio.current = el;
      if (first) { first = false; setLat((l) => `${l ?? ''} · first audio ${((performance.now() - started) / 1000).toFixed(1)} s`); }
      await new Promise<void>((res) => { el.onended = () => res(); el.onerror = () => res(); void el.play().catch(() => res()); });
    }
    if (my === cancelSpeech.current) setPhase('idle');
  }, []);

  const stopObserving = useCallback(() => {
    if (observeTimer.current) { window.clearInterval(observeTimer.current); observeTimer.current = null; }
    setWatching(false);
  }, []);

  // After a command runs (test injected, altitude changed, reset), keep quietly checking the live frame
  // and speak up only when something actually moved — the alarm fires, the diagnosis appears or changes,
  // an ambiguity or novelty flag flips. Most polls cost one dict comparison on the backend, no LLM call.
  const startObserving = useCallback((engine: string) => {
    stopObserving();
    setWatching(true);
    observeUntil.current = Date.now() + 150_000;               // most faults alarm well inside this window
    observeTimer.current = window.setInterval(async () => {
      if (Date.now() > observeUntil.current) { stopObserving(); return; }
      try {
        const r = await post<{ changed: boolean; text: string | null; ms: number }>('/assistant/observe',
          { engine, client: CLIENT_ID, session: session.current });
        if (r.changed && r.text) {
          setMsgs((m) => [...m, { role: 'assistant', text: r.text!, meta: 'observing' }]);
          if (speak) void say(r.text, performance.now());
        }
      } catch { /* a missed poll is not worth surfacing */ }
    }, 7000);
  }, [speak, say, stopObserving]);

  useEffect(() => stopObserving, [stopObserving]);
  useEffect(() => { stopObserving(); }, [engineId, stopObserving]);   // switching engines ends the old watch

  const ask = useCallback(async (q: string, viaVoice: boolean, sttMs?: number) => {
    const question = q.trim();
    if (!question || !engineId) return;
    stopSpeech();
    setMsgs((m) => [...m, { role: 'user', text: question, meta: viaVoice ? 'voice' : undefined }]);
    setPhase('thinking');
    const started = performance.now();
    try {
      const r = await post<{ reply: string; kind: string; ms: number; actions?: Action[] }>('/assistant/chat', { text: question, engine: engineId, session: session.current, client: CLIENT_ID });
      if (r.actions && r.actions.length) {
        runActions(r.actions);
        // 'reset' alone means "back to healthy" — nothing left to watch develop.
        if (r.actions.some((a) => a.type !== 'reset')) startObserving(engineId);
        else stopObserving();
      }
      setMsgs((m) => [...m, { role: 'assistant', text: r.reply,
        meta: r.kind === 'command' ? 'applied to the live twin'
          : r.kind === 'decision' ? 'from the mission layer'
          : r.kind === 'nodata' || r.kind === 'stale' ? 'no live data' : undefined }]);
      setLat(`${sttMs != null ? `speech-to-text ${sttMs} ms · ` : ''}answer ${(r.ms / 1000).toFixed(1)} s`);
      if (speak && (viaVoice || speak)) await say(r.reply, started);
      else setPhase('idle');
    } catch (e) {
      setMsgs((m) => [...m, { role: 'assistant', text: `I could not answer: ${(e as Error).message}`, meta: 'error' }]);
      setPhase('idle');
    }
  }, [engineId, speak, say, stopSpeech, startObserving, stopObserving]);

  const startRec = useCallback(async () => {
    if (phase === 'listening' || !live) return;
    stopSpeech();
    try {
      if (!stream.current) stream.current = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch {
      setMsgs((m) => [...m, { role: 'assistant', text: 'I cannot use the microphone: the browser blocked it or none is available. You can still type.', meta: 'error' }]);
      return;
    }
    chunks.current = [];
    const mr = new MediaRecorder(stream.current);
    mr.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data); };
    mr.onstop = async () => {
      const blob = new Blob(chunks.current, { type: mr.mimeType || 'audio/webm' });
      if (performance.now() - t0.current < 350 || blob.size < 800) { setPhase('idle'); return; }
      setPhase('transcribing');
      try {
        const r = await post<{ text: string; ms: number }>('/assistant/stt', blob, true);
        if (!r.text) { setMsgs((m) => [...m, { role: 'assistant', text: 'I did not catch that.', meta: 'no speech heard' }]); setPhase('idle'); return; }
        await ask(r.text, true, r.ms);
      } catch (e) {
        setMsgs((m) => [...m, { role: 'assistant', text: `I could not transcribe that: ${(e as Error).message}`, meta: 'error' }]);
        setPhase('idle');
      }
    };
    rec.current = mr;
    t0.current = performance.now();
    mr.start();
    setPhase('listening');
  }, [phase, live, ask, stopSpeech]);

  const stopRec = useCallback(() => {
    if (rec.current && rec.current.state === 'recording') rec.current.stop();
  }, []);

  // Keys work whether or not the panel is open.
  //   hold F9 = talk (opens the panel if it is closed), release = send      F8 = stop speaking
  const openRef = useRef(open);
  openRef.current = open;
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'F8') {
        e.preventDefault();
        stopSpeech();
        setPhase((p) => (p === 'speaking' ? 'idle' : p));
        return;
      }
      if (e.key === 'F9') {
        e.preventDefault();
        if (e.repeat || keyDown.current) return;
        keyDown.current = true;
        if (!openRef.current) setOpen(true);
        void startRec();
        return;
      }
      if (e.key === 'Escape' && openRef.current) setOpen(false);
    };
    const up = (e: KeyboardEvent) => {
      if (e.key !== 'F9') return;
      keyDown.current = false;
      stopRec();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); };
  }, [startRec, stopRec, stopSpeech]);

  useEffect(() => () => { stopSpeech(); stream.current?.getTracks().forEach((t) => t.stop()); }, [stopSpeech]);

  const busy = phase === 'transcribing' || phase === 'thinking';
  const status: Record<Phase, string> = {
    idle: warm === 'loading' ? 'Warming up the speech and language models…' : watching ? 'Watching the test develop…' : 'Ready',
    listening: 'Listening… release to send', transcribing: 'Transcribing…', thinking: 'Thinking…', speaking: 'Speaking…',
  };

  return (
    <>
      {!open && (
        <button className="as-fab" onClick={() => setOpen(true)} aria-label="Open the assistant">
          <span className="as-fab-mic" aria-hidden>●</span> Assistant
          <span className="as-fab-keys">hold F9 to talk</span>
        </button>
      )}
      {open && (
        <aside className="as-panel" role="dialog" aria-label="Assistant">
          <header className="as-head">
            <div>
              <div className="as-title">Assistant · {engineName}</div>
              <div className="as-sub">local · hold F9 to talk · F8 stops the voice</div>
            </div>
            <button className="as-x" onClick={() => { stopSpeech(); setOpen(false); }} aria-label="Close">×</button>
          </header>

          {!live && (
            <div className="as-warn">
              No live feed from this engine, so I have no data to explain. Open an engine marked “Live twin”.
            </div>
          )}

          <div className="as-msgs" ref={scroller} aria-live="polite">
            {msgs.length === 0 && (
              <div className="as-hint">
                Ask about the engine, or hold the mic (or F9, even with this panel closed) and talk. F8 stops the voice. Or command it: “test at 12,000 feet with injector fouling on cylinder 3”, “reset to healthy”. Try “how is the engine doing?”, “what is the oil
                temperature?”, “how does the alarm work?”, or “add a new engine”.
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={`as-msg as-${m.role}`}>
                <div className="as-bubble">{m.text}</div>
                {m.meta && <div className="as-meta">{m.meta}</div>}
              </div>
            ))}
            {busy && <div className="as-msg as-assistant"><div className="as-bubble as-dots">{status[phase]}</div></div>}
          </div>

          <footer className="as-foot">
            <div className="as-status" data-phase={phase}>
              <span>{status[phase]}</span>
              {lat && <span className="as-lat">{lat}</span>}
            </div>
            <form className="as-input" onSubmit={(e) => { e.preventDefault(); const q = text; setText(''); void ask(q, false); }}>
              <button type="button" className={`as-mic${phase === 'listening' ? ' as-mic-on' : ''}`}
                disabled={!live || busy}
                onPointerDown={(e) => { e.preventDefault(); void startRec(); }}
                onPointerUp={stopRec} onPointerLeave={stopRec} onPointerCancel={stopRec}
                aria-label="Hold to talk" title="Hold to talk (or hold F9). F8 stops the voice.">
                <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden>
                  <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z" />
                </svg>
              </button>
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a question…" disabled={busy} aria-label="Type a question" />
              <button type="submit" className="as-send" disabled={!text.trim() || busy}>Send</button>
            </form>
            <label className="as-speak">
              <input type="checkbox" checked={speak} onChange={(e) => { setSpeak(e.target.checked); if (!e.target.checked) stopSpeech(); }} />
              Speak answers
              {phase === 'speaking' && <button type="button" className="as-stop" onClick={() => { stopSpeech(); setPhase('idle'); }}>Stop</button>}
            </label>
          </footer>
        </aside>
      )}
    </>
  );
}
