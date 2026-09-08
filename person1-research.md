# Role: Research + Problem Framing
**Team member:** Person 1
**Repo folder suggestion:** `/docs/research/`

---

## Your job in one line
Turn the raw problem statement into real-world context the whole team can build against — fast, before ideation starts, then keep feeding useful data through the day.

---

## Timeline

### 7:00–7:15 — PS drops
Read it once for the literal ask, then immediately start digging. Don't wait for the team discussion to start researching — you're feeding it, not participating in it first.

**Pull fast (10-15 min max):**
- Which ministry/department/organization submitted this PS — what do they actually do, what's their existing digital presence (a website, an app, a portal)
- Is there an existing government scheme or portal already trying to solve this? (If yes, that's your "how we're different/better" angle later)
- 2-3 real numbers/stats that show the problem is real and big (e.g. "X% of farmers lack access to Y", "Z lakh cases pending annually") — search government data sites, PIB releases, news articles

### 7:15–8:00 — Ideation
Join the team discussion. Your job here is to keep the idea grounded — if someone proposes something that already exists as a government app, say so. If someone proposes something that ignores a real constraint (e.g. rural users with low connectivity), flag it.

### 8:00–8:30 — Scope lock
Note down the final locked MVP scope. Keep this — you'll reference it constantly.

### 8:30–11:00 — Support Person 2 + Person 3
- Hand your research + stats directly to Person 2 for the pitch deck's "Problem" section
- Start thinking through realistic use cases and edge cases with Person 3 — e.g. "what if the user has no internet", "what if this is a first-time user", "what if the data doesn't exist yet" — these become QA test cases later

### 11:00–2:30 — Sample data prep
Prep realistic-looking sample/demo data for whatever the app displays. An empty dashboard looks unfinished on stage — real, sensible-looking content (names, numbers, records that make sense together, not "Test123") makes the demo land better.
- Coordinate with Backend Dev B on how this data gets seeded into the database — you write/curate it, they load it

### 2:30–3:30 — Buffer
Help wherever there's a gap — most likely supporting QA (Person 3) or filling in remaining sample data.

### 3:30–5:00 — Integration window
Support bug reproduction if Person 3 finds issues — you often understand the "real world" use case better than whoever's fixing the code, so help clarify what correct behavior should look like.

### 5:00–6:00 — Rehearsal
Sit in on the pitch rehearsal. You know the research best — if the narration says something factually shaky, catch it now, not on stage.

### 6:00–7:00 — Final buffer
On call for anything last-minute.

---

## Deliverables to drop in the repo
- `research-notes.md` — org context, existing solutions, key stats (by ~9:00am)
- `sample-data.json` or `.csv` — demo content (by ~1:00pm)
- `edge-cases.md` — shared with Person 3, list of realistic scenarios to test

## Golden rule
Everything you find should answer one question for someone else on the team: "does this help build, pitch, or test the product?" If it doesn't, it's a rabbit hole — skip it.
