/**
 * The palette. One source, three consumers.
 *
 * three.js and uPlot draw to a canvas and cannot read CSS custom properties, so
 * every colour used to be typed twice — once in App.css and again in
 * materials.ts / StripChart.tsx / MissionMap.tsx — and the copies drifted.
 * (`#f59e0b` was typed in four files; the cylinder tag in parts.tsx used
 * `#fbbf24` for the same state the 3D glow drew in `#f59e0b`.)
 *
 * So: this module is the source of truth, `:root` in App.css is its mirror for
 * class-based styles, and the canvas code imports `C` directly. If you change a
 * colour, change it here and in the `:root` block — nowhere else.
 *
 * Light theme. Every foreground below clears 4.5:1 on `panel`.
 */

export const C = {
  // grounds
  bg: '#f4f6f9',
  bg2: '#eaeef4',
  panel: '#ffffff',
  panel2: '#f7f9fc',
  sunken: '#e8edf3', // was --void: recessed tracks and wells
  line: '#dde4ec',
  line2: '#c6d1de',

  // type
  // All three clear WCAG AA against bg. textDimr was #8a97a7 (2.75:1) and
  // textDim #5a6a7d; see the note in App.css, which these mirror.
  text: '#16202c',
  textDim: '#4d5b6b',
  textDimr: '#647284',

  // status
  ok: '#047857',
  warn: '#b45309',
  alert: '#be123c',
  sensor: '#0e7490',
  accent: '#0369a1',

  /** Pale grounds for status-tinted rows. On dark these were unnamed literals
   *  (`#0b1a24` appeared five times); naming them is what makes the theme
   *  swappable at all. */
  tintOk: '#e7f6f0',
  tintWarn: '#fdf3e3',
  tintAlert: '#fdeaee',
  tintSensor: '#e4f4f8',
  tintAccent: '#e6f1fa',

  /** Per-cylinder series. Darkened from the old dark-theme hues so they clear
   *  4.5:1 on white — same hue identity, readable ink. */
  cyl: ['#1d4ed8', '#b45309', '#047857', '#6d28d9'],
  /** Twin prediction — deliberately neutral, it is a reference not a series. */
  twin: '#64748b',
  /** Point of no return. */
  pnr: '#a21caf',
} as const;

/**
 * The 3D scene. Kept apart from the UI palette because these are *lighting*
 * decisions, not ink: they answer "what does this room look like", and they are
 * the values that had to change most when the ground went from black to white.
 */
export const SCENE = {
  /** Dark studio. A lit metal engine reads as hardware against near-black and
   *  as a grey silhouette against white; glow (faults, hot exhaust,
   *  combustion) only carries on a dark ground. Graphite rather than black so
   *  a washed-out projector still shows the floor. Fog only fades the far
   *  floor: the camera sits ~16 units out, so it must start beyond that. */
  bg: '#0b0e13',
  fogNear: 24,
  fogFar: 52,
  grid: '#dfe6ee',
  gridSub: '#e9eef4',

  hemiSky: '#ffffff',
  hemiGround: '#c3ccd6',

  key: '#fff6ea',
  fill: '#cfe4ff',
  rim: '#dbeafe',

  /**
   * Bloom threshold. This is the load-bearing number of the light theme.
   *
   * On the old black ground almost nothing exceeded 0.6, so a faulted cylinder
   * lifting its emissive above that threshold was the only thing that bloomed —
   * which is what made it read as GLOWING rather than merely tinted. On a white
   * ground nearly every pixel exceeds 0.6, so the same setting would bloom the
   * whole frame into mush.
   *
   * Set above the backdrop's post-tonemap luminance, and push the faulted
   * cylinder's emissiveIntensity past it (see FAULT_EMISSIVE), so once again the
   * fault is the only thing in frame that blooms.
   */
  bloomThreshold: 1.05,
  bloomIntensity: 0.7,
  /** Peak emissive on a fully-anomalous cylinder. Must exceed bloomThreshold. */
  faultEmissive: 2.6,

  /**
   * Fault glow, as LIGHT rather than as ink.
   *
   * Deliberately not C.warn / C.sensor. Those were darkened so they clear 4.5:1
   * as text on a white panel, and a dark-brown amber used as an emissive comes
   * out of the tone mapper pastel — it says "beige cylinder", not "hot".
   * Emitted light has no contrast requirement to meet, so it gets the vivid
   * value and the UI keeps the legible one. Same hue, different job.
   */
  faultGlow: '#f97316',
  sensorGlow: '#06b6d4',
} as const;
