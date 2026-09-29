import { useCallback, useEffect, useRef, useState } from 'react';
import { useMission } from '../state/missionStore';
import {
  builtinCards, fetchCatalog, profileFromCard, simOnlyReason,
  type Catalog, type EngineCard,
} from '../config/engineCatalog';
import { AddEngine } from './AddEngine';

/** The UI's built-in simulation exists only for 4 cylinders, so an engine that cannot run live and is not 4-cylinder cannot be opened. */
const cardOff = (c: EngineCard) => c.cylinders_supported === false || (!c.runnable && c.cylinders !== 4);

/**
 * Landing screen: choose the engine to open, or add a new one with the "+" card.
 *
 * Each card says plainly what it will do: a LIVE twin (the backend runs that engine's physics) or SIMULATION
 * ONLY (the UI's built-in simulation, because the backend cannot run it — e.g. an incomplete profile), and
 * whether a trained model exists (otherwise the app says "no diagnosis", it never borrows another engine's).
 * ?engine=<id> in the URL skips this screen, for rehearsal.
 */
export function EnginePicker() {
  const choose = useMission((s) => s.chooseEngine);
  const [cat, setCat] = useState<Catalog | null>(null);
  const [offline, setOffline] = useState(false);
  const [adding, setAdding] = useState(false);
  const auto = useRef(false);

  const load = useCallback(async () => {
    try {
      setCat(await fetchCatalog());
      setOffline(false);
    } catch {
      setCat(builtinCards());
      setOffline(true);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const pick = useCallback((c: EngineCard, off: boolean) => {
    choose(profileFromCard(c), c.runnable && !off);
  }, [choose]);

  useEffect(() => {
    if (auto.current || !cat) return;
    const id = new URLSearchParams(window.location.search).get('engine');
    const c = id ? cat.engines.find((e) => e.id === id) : undefined;
    if (c) { auto.current = true; pick(c, offline); }
  }, [cat, offline, pick]);

  return (
    <div className="ep-overlay" role="dialog" aria-label="Choose an engine">
      <div className="ep-wrap">
        <header className="ep-head">
          <span className="brand-mark">प्रमाण</span>
          <div>
            <h1 className="ep-title">Choose an engine</h1>
            <p className="ep-sub">Open a digital twin, or add a new engine from a few specifications.</p>
          </div>
        </header>

        {offline && (
          <div className="ep-warn">
            The backend is not reachable, so engines run as the built-in simulation only and new engines cannot be added.{' '}
            <button className="ep-link" onClick={() => void load()}>Retry</button>
          </div>
        )}

        {!cat ? (
          <p className="ep-sub">Loading engines…</p>
        ) : (
          <div className="ep-grid">
            {[...cat.engines].sort((a, b) => Number(b.id === cat.default) - Number(a.id === cat.default)).map((c) => (
              <button key={c.id} className={`ep-card${cardOff(c) ? ' ep-card-off' : ''}`}
                disabled={cardOff(c)} onClick={() => pick(c, offline)}>
                <span className="ep-card-top">
                  <span className="ep-name">{c.name}</span>
                  {c.id === cat.default && <span className="ep-tag">default</span>}
                  {c.generated && <span className="ep-tag ep-tag-gen">generated</span>}
                </span>
                <span className="ep-dev">
                  {c.generated ? `Copied from ${c.base_profile ?? 'a base engine'}; unspecified values are assumed` : c.developer}
                </span>
                <span className="ep-spec">
                  {c.cylinders} cyl · {c.displacement_L.toFixed(2)} L · {c.rated_kW ? `${Math.round(c.rated_kW)} kW` : '— kW'} ·{' '}
                  {c.cycle === 'diesel' ? 'diesel' : 'spark ignition'}
                </span>
                <span className="ep-badges">
                  {c.cylinders_supported === false
                    ? <span className="ep-badge ep-warn-b">Not supported yet</span>
                    : c.runnable && !offline
                    ? <span className="ep-badge ep-ok">Live twin</span>
                    : <span className="ep-badge ep-warn-b">Simulation only</span>}
                  {c.ml_ready && !offline
                    ? <span className="ep-badge ep-ok">ML trained</span>
                    : <span className="ep-badge ep-dim">No diagnosis model</span>}
                </span>
                {(!c.runnable || offline) && (
                  <span className="ep-why">{offline ? 'backend offline' : simOnlyReason(c)}</span>
                )}
              </button>
            ))}
            <button className="ep-card ep-plus" onClick={() => setAdding(true)} disabled={offline}
              title={offline ? 'needs the backend' : 'Add a new engine'}>
              <span className="ep-plus-mark">+</span>
              <span className="ep-name">Add engine</span>
              <span className="ep-dev">Generate a profile from a few specs, optionally train its model</span>
            </button>
          </div>
        )}
      </div>

      {adding && (
        <AddEngine
          onClose={() => setAdding(false)}
          onChanged={() => void load()}
        />
      )}
    </div>
  );
}
