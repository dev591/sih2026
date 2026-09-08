# SIH Hackathon — Frontend Build Doc (for Claude Code)

Give this whole doc to Claude Code at the start of the frontend build, before writing any prompts for individual screens. It defines the design system, the tech stack, and the rule for when to use which tool. Do not ask the user to re-explain stack choices — follow this doc.

---

## 1. Core Principle

There are two layers to this build:

- **Identity layer** — colors, typography, spacing, border radius, shadows, easing curves. This is IDENTICAL across every screen: hero, login, dashboard, forms, everything. It's what makes the product feel like one coherent thing instead of a hero page bolted onto a generic app.
- **Immersive layer** — Three.js, GSAP scroll-triggers, Lenis smooth scroll, heavy Framer Motion sequences. This is HERO/LANDING ONLY. Functional screens (login, dashboard, forms, data views) get a much lighter motion budget — they need to be scannable in under 2 seconds, not "experienced."

Never apply immersive-layer techniques to a screen the user needs to operate quickly. A judge who has to scroll-dive to find a login button is a usability failure, not a wow factor.

---

## 2. Tech Stack

**Core:**
- Vite + React + TypeScript
- Tailwind CSS
- shadcn/ui (Radix-based, copy-paste components, styled via Tailwind)
- Lucide React (icons — use exclusively, never mix icon sets)
- react-hook-form + zod (any form-heavy flow — citizen registration, complaint filing, etc.)
- Recharts (any data viz — SIH problem statements are frequently dashboard/monitoring type)

**Motion — split by screen type:**

| Screen type | Tools | Notes |
|---|---|---|
| Hero / landing | Framer Motion + Lenis + GSAP (ScrollTrigger) | Full immersive layer. Sync `lenis.on('scroll', ScrollTrigger.update)`. Three.js optional for a hero 3D element only — keep it to one focal piece, not the whole page |
| Login / auth | Framer Motion only | Subtle entrance transitions, no scroll-triggered effects |
| Dashboard / data screens | Framer Motion only | Micro-interactions: hover states, list stagger, modal transitions. No GSAP, no scroll-triggers |
| Forms | Framer Motion only | Field-level validation feedback animations, step transitions if multi-step |

Rule of thumb: **if it's a dashboard/app screen → Framer Motion. If it's a landing/hero page → Lenis + GSAP + Framer Motion.** Don't mix GSAP into app screens — it fights React's lifecycle under time pressure, Framer Motion is faster to write correctly.

**Design generation:**
- Primary tool: **Claude Design** (Design tab in claude.ai). It reads the existing codebase to extract the design system (colors, typography, components already in this doc) and applies it automatically to new screens. Output is live HTML, testable, with direct handoff to Claude Code.
- Secondary/reference only: 21st.dev — use for inspiration or one-off component patterns, not as the primary generation path. Anything pulled from it must be re-styled to match the tokens below before being wired in.
- Do not use Google Stitch or similar disconnected tools — they don't know this design system and their output requires a manual re-translation pass that costs more time than it saves.

---

## 3. Design Tokens (Identity Layer — universal, every screen)

### Color — base
```
--background: #FFFFFF        /* true white, not off-white */
--foreground: #0A0A0A        /* near-black, not pure #000 */
--muted-foreground: #71717A
--border: #E4E4E7
--card: #FFFFFF               /* differentiate cards via border + subtle shadow, never a gray fill */
```

### Color — semantic (fixed, never changes per genre)
```
--success: #16A34A
--error: #DC2626
--warning: #D97706
```

### Color — accent (swap ONE variable per problem statement genre, everything else stays identical)

| Genre | Accent hex | Use case |
|---|---|---|
| Citizen/Gov portal | `#1E40AF` | Education, rural dev, heritage & culture |
| Agriculture / Clean Tech | `#166534` | Agriculture, sustainability, renewable energy |
| Health / MedTech | `#0E7490` | Health, biotech |
| Transportation / Logistics | `#C2410C` | Transportation, smart automation |
| Security / Fintech | `#4338CA` | Blockchain, cybersecurity |

Only `--accent` changes based on the revealed problem statement. Everything else in this doc stays fixed. This is a config change, not a redesign.

### Typography
- Use a clear weight jump between heading and body (don't leave everything at font-medium) — this alone reads as "designed" rather than default
- Consistent type scale across all screens

### Shape
- Border radius: consistent across buttons, cards, inputs (pick one scale, e.g. `rounded-lg` for cards, `rounded-md` for inputs/buttons, apply everywhere)
- Shadows: reserve for modals/dropdowns/popovers only. Cards use border, not shadow, to avoid the "floaty AI card" look
- Generous whitespace — don't crowd cards edge to edge

### Motion consistency
- Whatever easing curve/spring config is used for hero entrance animations, reuse that exact config for micro-interactions on every other screen (buttons, cards, transitions). This is what makes hero → login → dashboard feel like one product rather than three.
- Page-to-page navigation (landing → login → dashboard) uses a consistent Framer Motion transition pattern (fade/slide) throughout.
- If hero has a background texture/gradient, echo a much lighter version of it on dashboard background — don't drop it to zero.

---

## 4. Workflow for Claude Code on the day

1. Read this doc in full before generating any component.
2. Confirm the accent color based on the revealed problem statement's genre (table above). If genre is ambiguous, default to Citizen/Gov portal blue.
3. For hero/landing: apply full immersive layer (Framer Motion + Lenis + GSAP), Three.js only if a single focal 3D element adds real value.
4. For every other screen (login, dashboard, forms): identity layer only, Framer Motion for micro-interactions, no scroll-triggers.
5. Use shadcn components as the base for all UI primitives (inputs, buttons, dialogs, dropdowns) — do not hand-roll these.
6. If a screen was generated via Claude Design first, treat that output as the reference — wire in real data/logic/routes without altering the visual tokens it already applied (it was onboarded on this same codebase, so it should already match).
7. Any component pulled from 21st.dev must be re-skinned to match section 3 tokens before merging — do not merge with its original styling.

---

## 5. Screen priority (build order under time pressure)

1. Login/auth (unblocks everything else)
2. Core dashboard/main screen (this is what judges spend the most time on — prioritize polish here over hero)
3. Secondary flows (forms, detail views)
4. Landing/hero — only after 1-3 are functional. It's the least important screen for judging in most SIH PS types (dashboard/gov-tool judging cares about the working product, not the marketing page). If time is short, cut hero scope first, not dashboard scope.
