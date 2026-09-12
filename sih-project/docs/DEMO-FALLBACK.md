# Demo fallback — how to get back to a working demo fast

Fidelity work (sensor model, parity independence, diesel combustion path) happens on the
`engine-fidelity` branch. That branch will at times be mid-surgery and **must not be demoed**.
This page is the escape hatch.

## If judges arrive and the branch is broken

```bash
cd /Volumes/dev/sih2026
git stash -u                       # park whatever is half-finished (-u keeps new files)
git switch demo-safe-2026-09-12    # the known-good tag
```

Then restart both servers — a branch switch changes the files on disk under a running dev
server, so **always restart both**, don't trust hot reload:

```bash
# backend (port 8000)
cd /Volumes/dev/sih2026/sih-project/backend
source .venv/bin/activate
python main.py

# frontend (port 5174) — in a second terminal
cd /Volumes/dev/sih2026/sih-project/frontend
npm run dev -- --port 5174
```

Open http://localhost:5174. Confirm the header reads **LIVE · ENGINE TWIN FEED** (green dot)
and that the RPM/altitude digits are moving. If the header says SIMULATED, the backend isn't
up yet.

To resume the fidelity work afterwards:

```bash
git switch engine-fidelity
git stash pop
```

## The refs that matter

| Ref | What it is |
|---|---|
| `demo-safe-2026-09-12` | **Tag. Use this for any demo.** All of 2026-09-12's bug fixes plus the readable strip charts. |
| `hpc-ncmapss-realdata` | The branch that tag points into. Also safe. |
| `engine-fidelity` | Work in progress. Assume broken unless just verified. |
| `main` | ⚠️ **49 commits behind — doc-only scaffold era. NOT a demo fallback.** |

That last row is the trap: `main` looks like the obvious safe choice and is the one branch that
will definitely fail, because the working backend was never merged into it. Do not switch to
`main` under time pressure, and do not take a fresh clone of `main` for the finale without
merging first.

## Known operational gotchas

- **The Vite dev server died once on 2026-09-12** with nothing listening on 5174 and no node
  process alive. If the page won't load, check `lsof -i :5174 -sTCP:LISTEN` before debugging
  anything else, and just restart it.
- Reloading the page opens a fresh websocket, which gives a **clean healthy engine** — that is
  the fastest way to reset after a fault-injection demo. The FAULTS panel's "Healthy" and
  "Demo script" buttons also work, but a reload is the most certain.
- A fault injected through the console keeps running in the backend for that connection until
  reset, so hand over a freshly reloaded page rather than a mid-fault one.

## Before any presentation

Re-verify rather than assume:

```bash
cd /Volumes/dev/sih2026/sih-project/backend
source .venv/bin/activate
python gates_check.py   # must print 6/6
python verify.py        # must be all-pass
```
