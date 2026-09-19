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
  // grounds (dark control-room theme; mirrors :root in App.css)
  bg: '#0b0e13',
  bg2: '#10141b',
  panel: '#151a22',
  panel2: '#1a2029',
  sunken: '#0e1218',
  line: '#252d38',
  line2: '#34404e',

  // type: all three clear WCAG AA against panel
  text: '#e6ecf3',
  textDim: '#aab6c4',
  textDimr: '#8592a3',

  // status, lifted for a dark ground
  ok: '#34d399',
  warn: '#fbbf24',
  alert: '#fb7185',
  sensor: '#22d3ee',
  accent: '#38bdf8',

  tintOk: '#10261f',
  tintWarn: '#2a2112',
  tintAlert: '#2c151b',
  tintSensor: '#0f2429',
  tintAccent: '#0f2230',

  /** Per-cylinder series, bright enough to read as lines on a dark chart. */
  cyl: ['#60a5fa', '#fbbf24', '#34d399', '#c084fc'],
  /** Twin prediction — deliberately neutral, it is a reference not a series. */
  twin: '#94a3b8',
  /** Point of no return. */
  pnr: '#e879f9',
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
