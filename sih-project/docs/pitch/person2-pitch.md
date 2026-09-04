# Role: Pitch Deck + Demo Script Owner
**Team member:** Person 2
**Repo folder suggestion:** `/docs/pitch/`

---

## Your job in one line
Build the story of the product alongside the build itself, so by 5pm the pitch matches what's actually working — not what was planned at 8am.

---

## Timeline

### 7:00–8:00 — PS reveal + ideation
Listen closely during ideation. You're not just waiting to write later — you're absorbing the "why" of the idea now so the pitch has a real narrative, not a bolted-on justification.

### 8:00–8:30 — Scope lock
Write down the locked MVP scope. This is your source of truth — the pitch can never promise more than this.

### 8:30–9:00 — Deck skeleton
Use the pre-built template structure: **Problem → Solution → Demo → Tech Stack → Impact/Feasibility**
- Fill Problem section using Person 1's research (org context + stats)
- Draft Solution section based on locked scope — one clear sentence describing what the product does, then 3-4 bullet features max

### 9:00–11:00 — Deck continues
- Rough out Impact/Feasibility section — who benefits, how does this scale, why is it realistic (not just "cool idea")
- Start a rough demo script structure: what screen do we show first, what's the story arc of the demo (e.g. "user has a problem → opens app → here's how it's solved in 3 clicks")

### 11:00–2:30 — Narration draft
Write the actual spoken words for the demo, not just bullet points. Practice the difference: "here's the dashboard" (weak) vs "a farmer opens the app and immediately sees today's weather risk for their exact field" (strong, tells a story).
- Check in with frontend/backend on what's actually functional — don't script a feature that isn't built yet

### 2:30–3:30 — Buffer
Update deck/script to match whatever got cut or changed during the buffer window.

### 3:30–5:00 — Integration window
This is critical: as bugs get found and features get finalized, update your demo script in real time. If a feature breaks and doesn't get fixed, cut it from the script immediately — don't let the script drift from reality.

### 5:00–6:00 — Rehearsal
- Do a full rehearsal with whoever's presenting, at least twice
- Time it — SIH pitches usually have a strict time limit, know yours and rehearse to it
- Person 1 and Person 3 sit in: Person 1 checks facts, Person 3 checks that every claimed feature actually works live

### 6:00–7:00 — Final buffer
Final polish on deck slides, last run-through if needed.

---

## Deliverables to drop in the repo
- `pitch-deck.pdf` or link to Slides/Canva (by ~5:30pm)
- `demo-script.md` — the actual narration, screen by screen (by ~5:00pm)

## Golden rule
The pitch should never claim something the live demo can't show. If it's not working, it's not in the script — no matter how good it sounds on paper.

---

## Building the deck with Claude Design

Use Claude Design (Design tab in claude.ai) to generate the actual slides. Give it structure, not just topics — the order and principle behind each slide matters more than the content quality alone. The structure below is the standard format used by actual SIH winning teams, not a generic template — stick to it.

### Basic rules to give Claude Design first, before any slide content
1. **Judging timing is unknown.** There will be 2-3 rounds of judging before the final round, and we don't know exactly when each judge will visit. The deck must work as a "snapshot of progress" at any point in the day, not just as a finished story told at 6pm.
2. **Problem always comes before solution.** Never let a slide describe what we built before the audience understands what's broken and for whom.
3. **Diagrams over paragraphs.** Architecture, flow, and tech stack slides should be visual (boxes, arrows, icons) — judges skim fast in a 3-5 min pitch, dense text loses them.
4. **One phase-progress slide is mandatory** (see below) — this is what lets an early judge instantly see how much of the plan is done without us having to explain verbally.

### Official SIH deck structure (fixed — follow this order)

```
1. Title & Team Introduction
2. Problem Statement
3. Proposed Solution
4. How the Solution Works
5. System Architecture
6. Technology Stack
7. Innovation / Unique Features
8. Implementation & Feasibility
9. Impact & Benefits
10. Future Scope
```

### New slide to insert — Phase Progress (place right after slide 1, before Problem Statement)

This is the slide that solves the "judge could arrive anytime" problem. It shows the whole project broken into phases as boxes, with a clear visual marker for what's done vs pending at the current moment. Update it live through the day — it should always reflect the truth of what's built right now.

**What it needs:**
- The full project broken into N phases (e.g. Ideation & Scoping → Backend Core → Frontend Core → Integration → Polish & Demo Prep) — keep it to 4-6 phases, not a long list
- Each phase shown as a box/card in sequence
- A clear visual state per box: done (filled/checked), in progress (highlighted/partial), pending (outlined/greyed)
- A small label near it: "Progress as of [time]" — update this timestamp each time you refresh it before a judging round

This single slide does the job of a status update without you having to explain verbally — a judge glancing at it instantly sees you're organized and exactly how far along you are.

### Prompt template for Claude Design (fill in the blanks as info becomes available)

```
Create a Smart India Hackathon pitch deck. Follow this exact slide order —
this is the standard format used by actual SIH winning teams, do not deviate:

1. TITLE & TEAM — Project name, one-line tagline, team name, college
2. PHASE PROGRESS — Show our project broken into these phases as a row of
   boxes/cards: [insert phase list, e.g. Scoping, Backend, Frontend,
   Integration, Demo Prep]. Mark each box clearly as Done / In Progress /
   Pending using color and an icon (check / half-fill / outline only).
   Add a small "Progress as of [TIME]" label. This slide must update
   easily since judging happens in multiple unscheduled rounds through
   the day.
3. PROBLEM STATEMENT — State the real-world problem BEFORE any solution talk.
   Stats: [insert Person 1's research stats]
   Who is affected: [insert target user/beneficiary]
   Why existing solutions fall short: [insert gap from research]
4. PROPOSED SOLUTION — One clear sentence describing what we built, then
   3-4 feature bullets max. No implementation detail yet.
5. HOW THE SOLUTION WORKS — Simple user-journey flow diagram, 3-5 steps.
6. SYSTEM ARCHITECTURE — Visual diagram of how components connect
   (frontend/backend/DB/external services) — boxes and arrows, not text.
7. TECHNOLOGY STACK — Icons/logos of: [insert final stack]. Visual grid,
   not a bullet list.
8. INNOVATION / UNIQUE FEATURES — What makes this different from existing
   solutions (use the gap identified in Problem Statement).
9. IMPLEMENTATION & FEASIBILITY — Why this is realistic to actually deploy,
   not just a hackathon demo.
10. IMPACT & BENEFITS — Who benefits and how much, use scale numbers from
    research.
11. FUTURE SCOPE — What's next if this continued past the hackathon.

Design direction: white background, [insert accent color from genre table],
clean sans-serif typography, generous whitespace, minimal text per slide
(headline + 2-4 bullets max). No stock-photo-looking AI images — use simple
icons/diagrams instead. The Phase Progress slide especially should look
clean and beautiful — this is the one slide judges may see multiple times
across rounds, so it needs to look polished, not like a rough status list.
```

### Workflow
1. Fill the template above as data becomes available through the day (don't wait for everything to be final — draft with placeholders, refine later)
2. Feed to Claude Design once scope is locked (~9am) with rough content, get a first version fast
3. **Update the Phase Progress slide before every judging round** — this is the one slide that changes multiple times today, everything else should mostly stabilize by mid-afternoon
4. Refine slide-by-slide via Claude Design's inline comments/adjustment tools as real data/screenshots come in
5. Export and do final rehearsal pass by 5:00pm

### Reference decks (for visual style only, not content)
There's a `reference-decks/` folder alongside this doc with past SIH winning presentations. When prompting Claude Design:
- Pick 2-3 decks closest to our genre, not all of them — too many references dilutes the style instead of sharpening it
- Point Claude Design at them for layout/whitespace, how they visualize architecture/tech-stack diagrams, and text density per slide — never for copying content or wording
- Add this line to the prompt: "Match the visual polish and layout discipline of the attached reference decks, but all content must be original to our project."

---

## Rolling readiness checkpoints (judging visit timing is unknown)

Since we don't know when judges will visit our table, the deck and demo need to be presentable at multiple points during the day, not just complete by evening. Build in these checkpoints so there's always something ready to show:

| Checkpoint | Time | Minimum presentable state |
|---|---|---|
| **Checkpoint 1** | ~10:30am | Deck: Title + Problem slides done. Product: nothing live yet — if a judge visits this early, pitch verbally using Person 1's research, explain the plan |
| **Checkpoint 2** | ~1:00pm | Deck: Problem + Solution + How It Works done. Product: at least one core screen functional (even with mock data) to show, not just talk about |
| **Checkpoint 3** | ~3:30pm | Deck: fully drafted, all slides in place. Product: core flow working end to end, even if rough |
| **Checkpoint 4** | ~5:30pm | Deck: final, rehearsed. Product: polished, matches script exactly — this is the target state for the FINAL judging round |

At each checkpoint, Person 2 does a 2-minute internal check: "if a judge walked up right now, what would we show and say?" If the honest answer is "not much," flag it to the team immediately rather than waiting for the next checkpoint to fix it. This is also the moment to refresh the **Phase Progress slide** (slide 2) — update the timestamp and box states so it's always accurate before a judge could walk up.

This also means: don't leave the deck's early slides (Title, Problem) until late — get those done first specifically because they're what carries you through an early, unexpected judge visit even before the product has much to show.
