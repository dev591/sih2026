# Role: QA + Judge-Readiness
**Team member:** Person 3
**Repo folder suggestion:** `/docs/qa/`

---

## Your job in one line
Catch what's broken before judges do, and make sure the team never overpromises what the demo can actually show.

---

## Timeline

### 7:00–8:00 — PS reveal + ideation
Listen for constraints and assumptions — these become your future test cases. If someone says "user uploads a photo," you'll later test what happens with a bad photo, no photo, huge file, etc.

### 8:00–8:30 — Scope lock
Write down the locked MVP scope — this is your checklist baseline. Anything not on this list doesn't need testing today.

### 8:30–9:00 — Edge case list
Work with Person 1 to draft realistic edge cases per feature (empty states, bad input, slow network, first-time user vs returning user). Keep this list growing all day — you'll work through it once features start landing.

### 9:00–11:00 — Status tracker setup
Start a running doc: one row per MVP feature, status column (not started / in progress / working / broken). Update this constantly — it becomes the single source of truth for "what's actually done" that Person 2 needs for the demo script.

### 11:00–2:30 — Light early testing
As features land even partially, poke at them. Don't wait until 3:30 to start testing — early bugs caught now are cheap to fix, the same bugs caught at 4:30 are expensive or unfixable in time.
- Report bugs immediately to whoever owns that piece (frontend/backend), don't batch them up

### 2:30–3:30 — Buffer
Full pass on whatever's stabilized. Update the status tracker.

### 3:30–5:00 — Integration window — your busiest hours
This is where teams usually break things by connecting parts that worked alone. Go hard:
- Click through every flow out of order
- Test with bad/missing input
- Test on a second device/browser if possible (something judges will likely do)
- Report bugs live, prioritize by "does this break the demo path" vs "minor polish issue" — demo-path bugs get fixed first, always

### 5:00–6:00 — Final verification
- Confirm status tracker matches reality — no feature listed as "working" that isn't
- Sit in on pitch rehearsal, flag anything Person 2's script claims that doesn't actually work live
- Do one final full click-through of exactly the demo path that will be shown to judges — nothing else matters as much as this path working

### 6:00–7:00 — Final buffer
Last check on the exact demo path only. Do not test unrelated features this late — no time to fix anything found now unless it's on the demo path.

---

## Deliverables to drop in the repo
- `status-tracker.md` — feature-by-feature status, updated continuously
- `edge-cases.md` — shared with Person 1, list of test scenarios
- `known-issues.md` — anything broken that didn't get fixed, so it's consciously avoided during the live demo

## Golden rule
Your job isn't to find every bug — it's to make sure the exact path judges will see during the demo works perfectly, every time, in order.
