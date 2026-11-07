/*
  One persistent swarm that choreographs the page.

  A single fixed canvas sits between the page background and the content. Every
  section that takes part carries data-beat="<name>" and one [data-stage] element:
  the empty region where its figure may be drawn, so particles never sit under text.
  Each frame the engine finds the section crossing the middle of the viewport,
  measures its progress p (0..1), hands p to the matching beat, which sets a target
  for every particle it uses, and springs the particles toward those targets.
  Particles a beat does not use fade out where they are, so moving from one beat to
  the next reads as the same swarm morphing into a new figure.
*/
import { expectedMaxSharpe } from './stats.ts'
import { CHECKS, gate, loopState } from './data.ts'

// Particle colours by role, for paper and dark fields. Index = role.
const PAPER = ['#2B2FD9', '#7B7FD8', '#0A0A0B', '#C9C6BB', '#FF3B2F', '#0F9D58', '#2B2FD9']
const DARK = ['#C9CDFB', '#8A90E0', '#FFFFFF', '#5A60B8', '#FF4D3D', '#3DDC84', '#C9CDFB']
const INDIGO = 0, PERI = 1, INK = 2, LAV = 3, CORAL = 4, GREEN = 5, PALE = 6
// Line and label colours for whichever beat is drawing; set before each under()/over().
let dark = false
const UI = {
  paper: { ink: '#0A0A0B', acc: '#2B2FD9', acc2: '#8F8D84', red: '#E5281C', green: '#0F9D58', muted: '#6E6C66', field: '#F2F1EC' },
  dark: { ink: '#FFFFFF', acc: '#C9CDFB', acc2: '#6C72CF', red: '#FF4D3D', green: '#3DDC84', muted: '#9DA2E0', field: '#1A1F6B' },
}
const col = (k: keyof typeof UI.paper) => (dark ? UI.dark : UI.paper)[k]

type P = {
  x: number; y: number; vx: number; vy: number; tx: number; ty: number
  a: number; ta: number; r: number; tr: number; c: number; snap: boolean
  s1: number; s2: number; s3: number; z: number; z2: number; u: number[]
  gy: number; gState: number; gT: number
}
type F = {
  L: number; T: number; W: number; H: number; p: number; t: number; dt: number
  phone: boolean; reduce: boolean; mx: number; my: number; N: number; dark: boolean
}
type Beat = {
  targets(ps: P[], f: F): void
  under?(g: CanvasRenderingContext2D, f: F, mix: number): void
  over?(g: CanvasRenderingContext2D, f: F, mix: number): void
  k?: number; damp?: number
}

const clamp = (v: number, a = 0, b = 1) => Math.max(a, Math.min(b, v))
const ease = (v: number) => 1 - Math.pow(1 - clamp(v), 3)
const hash = (n: number) => { const s = Math.sin(n * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s) }
function normal() { return Math.sqrt(-2 * Math.log(Math.random() || 1e-9)) * Math.cos(2 * Math.PI * Math.random()) }

function set(p: P, x: number, y: number, a: number, r: number, c: number) { p.tx = x; p.ty = y; p.ta = a; p.tr = r; p.c = c }
function label(g: CanvasRenderingContext2D, text: string, x: number, y: number, o: { size?: number; weight?: number; color?: string; align?: CanvasTextAlign; alpha?: number } = {}) {
  g.globalAlpha = o.alpha ?? 1
  g.font = `${o.weight ?? 500} ${o.size ?? 11}px "JetBrains Mono", ui-monospace, monospace`
  g.fillStyle = o.color ?? col('muted')
  g.textAlign = o.align ?? 'left'
  g.fillText(text, x, y)
  g.globalAlpha = 1
}

/* ---------- Hero: an LED chart. Each cluster is a room, plotted at its backtest Sharpe.
   The dashed line is what luck alone predicts for the trials run so far; rooms under it FAIL and drop,
   the rare one above it PASSes and forks. ---------- */
const SLOTS = 8, PERIOD = 10, SR_MAX = 4
let lastStat = ''
const AX = 46 // px reserved for the y-axis inside the board
function heroScale(f: F) {
  const top = f.T + 18, bottom = f.T + f.H - 22
  return { x0: f.L + AX, x1: f.L + f.W - 16, y: (sr: number) => bottom - (sr / SR_MAX) * (bottom - top), bottom, top }
}
function trialsAt(t: number) {
  let roots = 0, forks = 0, passes = 0
  for (let s = 0; s < SLOTS; s++) {
    const off = s * (PERIOD / SLOTS), done = Math.max(0, Math.floor((t + off) / PERIOD))
    roots += done + 1
    for (let c = 0; c < done; c++) if (hash(s * 17 + c * 3) < 0.14) { passes++; forks++ }
  }
  return { roots, forks, passes, trials: 12 * SLOTS + roots + forks } // the swarm had a head start before you arrived
}
function slot(s: number, t: number, f: F, luck: number) {
  const off = s * (PERIOD / SLOTS)
  const cycle = Math.floor((t + off) / PERIOD), lt = f.reduce ? 6.5 : (t + off) % PERIOD
  const pass = hash(s * 17 + cycle * 3) < 0.14
  const sr = pass ? luck + 0.25 + 0.45 * hash(s + cycle * 7) : 0.3 + (luck - 0.5) * hash(s * 3 + cycle * 11)
  const sc = heroScale(f)
  const cx = sc.x0 + (sc.x1 - sc.x0) * (0.08 + 0.84 * ((s + 0.5 * hash(cycle + s)) / SLOTS))
  const target = sc.y(sr)
  // A room forms near the floor, climbs to its Sharpe, then the gate rules on it.
  const climb = clamp((lt - 0.4) / 2.4)
  const cy = sc.bottom - (sc.bottom - target) * (1 - Math.pow(1 - climb, 3))
  const form = clamp(lt / 1)
  const drop = !pass && lt > 6 ? Math.pow(lt - 6, 2) * f.H * 0.3 : 0
  const life = pass ? (lt > 9.3 ? clamp(1 - (lt - 9.3) / 0.6) : form) : clamp(1 - (lt - 7.4) / 1) * form
  const child = { x: cx + 54, y: target - 26, on: pass && lt > 6 ? clamp((lt - 6) / 0.8) * life : 0 }
  const verdict = lt > 5.4 ? (pass ? GREEN : CORAL) : -1
  return { cx, cy, sr, life, child, verdict, drop, id: 100 + cycle * SLOTS + s }
}
const hero: Beat = {
  targets(ps, f) {
    const stat = trialsAt(f.t), luck = expectedMaxSharpe(stat.trials)
    const key = `${stat.trials}|${stat.passes}|${stat.roots}`
    if (key !== lastStat) { lastStat = key; dispatchEvent(new CustomEvent('trials', { detail: { ...stat, luck } })) }
    const n = Math.min(ps.length, f.phone ? 240 : 640)
    const sig = Math.min(f.W, f.H) * (f.phone ? 0.04 : 0.032)
    const slots = Array.from({ length: SLOTS }, (_, s) => slot(s, f.t, f, luck))
    for (let i = 0; i < n; i++) {
      const p = ps[i], o = slots[i % SLOTS], isChild = Math.floor(i / SLOTS) % 4 === 0
      const rot = f.t * 0.3 * (p.s3 > 0.5 ? 1 : -1)
      const ox = (p.z * Math.cos(rot) - p.z2 * Math.sin(rot)) * sig, oy = (p.z * Math.sin(rot) + p.z2 * Math.cos(rot)) * sig * 0.8
      let x = (isChild ? o.child.x : o.cx) + ox * (isChild ? 0.6 : 1)
      let y = (isChild ? o.child.y : o.cy) + oy * (isChild ? 0.6 : 1) + (isChild ? 0 : o.drop * (0.6 + p.s1 * 0.8))
      const d = Math.hypot(f.mx - x, f.my - y)
      if (d < 120) { const k = 0.35 * (1 - d / 120); x += (f.mx - x) * k; y += (f.my - y) * k }
      const c = o.verdict === CORAL && !isChild ? CORAL : o.verdict === GREEN ? GREEN : i % 5 === 0 ? INK : INDIGO
      set(p, x, y, isChild ? o.child.on : o.life, 1.6 + p.s1 * 1, c)
      if (o.drop > 0 && !isChild) p.snap = true
    }
  },
  under(g, f, mix) {
    const sc = heroScale(f), luck = expectedMaxSharpe(trialsAt(f.t).trials)
    g.globalAlpha = 0.5 * mix; g.strokeStyle = col('acc'); g.lineWidth = 1
    g.beginPath(); g.moveTo(sc.x0, sc.top); g.lineTo(sc.x0, sc.bottom); g.lineTo(sc.x1, sc.bottom); g.stroke()
    for (let k = 1; k < SR_MAX; k++) {
      const y = sc.y(k)
      g.globalAlpha = 0.5 * mix; g.beginPath(); g.moveTo(sc.x0 - 5, y); g.lineTo(sc.x0, y); g.stroke()
      g.globalAlpha = 0.1 * mix; g.beginPath(); g.moveTo(sc.x0, y); g.lineTo(sc.x1, y); g.stroke()
      label(g, `${k}σ`, sc.x0 - 9, y + 4, { align: 'right', alpha: mix })
    }
    label(g, 'SR', sc.x0 - 9, sc.top + 4, { align: 'right', alpha: mix })
    // Luck alone: the best Sharpe zero-skill trials would show by now.
    const y = sc.y(luck)
    g.globalAlpha = 0.9 * mix; g.strokeStyle = col('red'); g.lineWidth = 1.5; g.setLineDash([6, 5])
    g.beginPath(); g.moveTo(sc.x0, y); g.lineTo(sc.x1, y); g.stroke(); g.setLineDash([])
    label(g, `LUCK ALONE ${luck.toFixed(2)}σ`, sc.x1, y - 8, { align: 'right', color: col('red'), weight: 700, alpha: mix })
    g.globalAlpha = 1
  },
  over(g, f, mix) {
    const luck = expectedMaxSharpe(trialsAt(f.t).trials)
    const R = Math.min(f.W, f.H) * (f.phone ? 0.04 : 0.032) * 2.4 // cluster radius (~2.4σ of its spread)
    // Greedy label placement: up and right of each cluster's box, with a 1px leader; never on top of another label.
    const placed: [number, number, number, number][] = []
    const free = (x: number, y: number, w: number, h: number) => placed.every(([a, b, c, d]) => x + w < a || x > a + c || y + h < b || y > b + d)
    const tag = (cx: number, cy: number, line1: string, line2: string, c2: string, alpha: number) => {
      const w = Math.max(line1.length, line2.length) * 7 + 6, h = line2 ? 28 : 15
      let x = cx + R * 0.8, y = cy - R - h - 6
      for (let k = 0; k < 6 && !free(x, y, w, h); k++) { y -= h + 4; if (k === 2) { x = cx - R * 0.8 - w; y = cy - R - h - 6 } }
      placed.push([x, y, w, h])
      g.globalAlpha = 0.6 * alpha; g.strokeStyle = col('acc'); g.lineWidth = 1
      g.beginPath(); g.moveTo(cx + Math.sign(x - cx) * R * 0.55, cy - R * 0.55); g.lineTo(x < cx ? x + w : x, y + h); g.stroke()
      label(g, line1, x + 3, y + 11, { color: col('ink'), weight: 600, alpha })
      if (line2) label(g, line2, x + 3, y + 25, { color: c2, weight: 700, alpha })
    }
    for (let s = 0; s < SLOTS; s++) {
      const o = slot(s, f.t, f, luck)
      const a = Math.min(o.life, clamp(1 - o.drop / 60)) * mix
      if (a > 0.02 && o.verdict >= 0) tag(o.cx, o.cy, `h${o.id}`, o.verdict === GREEN ? 'PASS' : 'FAIL', o.verdict === GREEN ? col('green') : col('red'), a)
      if (o.child.on > 0) {
        g.globalAlpha = 0.7 * o.child.on * mix; g.strokeStyle = col('green'); g.lineWidth = 1
        g.setLineDash([2, 4]); g.beginPath(); g.moveTo(o.cx, o.cy); g.lineTo(o.child.x, o.child.y); g.stroke(); g.setLineDash([])
        tag(o.child.x, o.child.y, `h${o.id}.1`, 'FORK', col('green'), o.child.on * mix)
      }
    }
    g.globalAlpha = 1
  },
}

/* ---------- Tangent trap: one idea sprouts ten tangents, nine die, memory rescues one. ---------- */
const SURV = 6, DEAD_MEM = 2
function tangentGeom(f: F) {
  const root = [f.L + f.W * 0.06, f.T + f.H * 0.5]
  const tips = Array.from({ length: 10 }, (_, k) => [
    f.L + f.W * (k === SURV ? 0.94 : 0.5 + 0.3 * hash(k + 40)),
    f.T + f.H * (0.06 + 0.88 * (k / 9)),
  ])
  return {
    root, tips,
    grow: ease(f.p / 0.32), die: ease((f.p - 0.36) / 0.18),
    link: ease((f.p - 0.58) / 0.16), win: ease((f.p - 0.74) / 0.14),
  }
}
const tangent: Beat = {
  targets(ps, f) {
    const { root, tips, grow, die, win } = tangentGeom(f)
    const per = f.phone ? 10 : 18
    let i = 0
    for (let k = 0; k < 10; k++) {
      const dead = k !== SURV
      for (let j = 1; j <= per; j++) {
        const p = ps[i++], t = j / per
        const tip = tips[k]
        const reach = dead ? 1 : 0.62 + 0.38 * win // the survivor only reaches its tip once memory rescues it
        const x = root[0] + (tip[0] - root[0]) * t * reach, y = root[1] + (tip[1] - root[1]) * t * reach
        const on = t <= grow
        const c = dead ? (die > 0.05 ? CORAL : INDIGO) : win > 0.2 ? GREEN : INDIGO
        const a = !on ? 0 : dead ? 1 - die * (t > 0.5 ? 0.45 : 0.25) : 1
        set(p, x, y + (dead && die > 0 && t > 0.8 ? die * die * 40 * (t - 0.8) * 5 : 0), a, j === per ? 3.4 : 2, c)
      }
    }
    for (let j = 0; j < 24; j++) {
      const p = ps[i++]
      set(p, root[0] + p.z * 8, root[1] + p.z2 * 8, 1, 2.4, INK)
    }
  },
  under(g, f, mix) {
    // The tree: every tangent is a visible dotted line from the one idea.
    const { root, tips, grow, die, win } = tangentGeom(f)
    tips.forEach((t, k) => {
      const dead = k !== SURV, reach = (dead ? 1 : 0.62 + 0.38 * win) * grow
      g.globalAlpha = (dead && die > 0.05 ? 0.35 : 0.5) * mix
      g.strokeStyle = dead && die > 0.05 ? col('red') : k === SURV && win > 0.2 ? col('green') : col('acc')
      g.lineWidth = 1; g.setLineDash([2, 4])
      g.beginPath(); g.moveTo(root[0], root[1]); g.lineTo(root[0] + (t[0] - root[0]) * reach, root[1] + (t[1] - root[1]) * reach); g.stroke()
    })
    g.setLineDash([]); g.globalAlpha = 1
  },
  over(g, f, mix) {
    const { root, tips, grow, die, link, win } = tangentGeom(f)
    label(g, 'ONE IDEA', root[0] - 4, root[1] - 26, { color: col('ink'), weight: 700, alpha: mix })
    if (die > 0.3) tips.forEach((t, k) => { if (k !== SURV) label(g, '✕', t[0] + 10, t[1] + 4, { color: col('red'), weight: 700, size: 13, alpha: mix * die }) })
    const mid = [root[0] + (tips[SURV][0] - root[0]) * 0.62, root[1] + (tips[SURV][1] - root[1]) * 0.62]
    if (link > 0.01) {
      const a = tips[DEAD_MEM]
      g.globalAlpha = mix; g.strokeStyle = col('acc'); g.lineWidth = 1.5; g.setLineDash([5, 4])
      g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(a[0] + (mid[0] - a[0]) * link, a[1] + (mid[1] - a[1]) * link); g.stroke(); g.setLineDash([])
    }
    if (f.phone) { g.globalAlpha = 1; return } // on phones the annotations are a list under the figure
    const L = f.L, T = f.T, W = f.W, H = f.H
    const lead = (x: number, y: number, bx: number, by: number, a: number) => {
      g.globalAlpha = 0.7 * a; g.strokeStyle = col('ink'); g.lineWidth = 1
      g.beginPath(); g.moveTo(x, y); g.lineTo(bx, by); g.stroke()
      g.fillStyle = col('ink'); g.fillRect(x - 2, y - 2, 4, 4)
    }
    const note = (bx: number, by: number, title: string, lines: string[], color: string, a: number) => {
      if (a <= 0.01) return
      label(g, title, bx, by, { color, weight: 700, size: 11, alpha: a })
      g.font = 'italic 400 16px "Instrument Serif", Georgia, serif'; g.fillStyle = col('ink'); g.textAlign = 'left'; g.globalAlpha = a
      lines.forEach((l, i) => g.fillText(l, bx, by + 20 + i * 18))
      g.globalAlpha = 1
    }
    const a1 = mix * clamp((grow - 0.75) / 0.25)
    const p1 = [root[0] + (tips[1][0] - root[0]) * 0.45, root[1] + (tips[1][1] - root[1]) * 0.45]
    if (a1 > 0.01) { lead(p1[0], p1[1], L + W * 0.13, T + H * 0.04 + 66, a1); note(L + W * 0.13, T + H * 0.04, '10 TANGENTS', ['a paper that might apply', 'a filter that might fix last quarter', 'a signal from another market'], col('acc'), a1) }
    const a2 = mix * clamp((die - 0.5) / 0.5)
    if (a2 > 0.01) { lead(tips[9][0], tips[9][1], L + W * 0.2, T + H * 0.9, a2); note(L + W * 0.03, T + H * 0.88, '9 DEAD ENDS · MONTHS LOST', [], col('red'), a2) }
    const a3 = mix * clamp((link - 0.5) / 0.5)
    const lm = [(tips[DEAD_MEM][0] + mid[0]) / 2, (tips[DEAD_MEM][1] + mid[1]) / 2]
    if (a3 > 0.01) { lead(lm[0], lm[1], lm[0] + W * 0.08, lm[1] - 4, a3); note(lm[0] + W * 0.08 + 6, lm[1] - 8, 'RECALL', ['it already died once, of costs.', 'The fix is known.'], col('acc'), a3) }
    const a4 = mix * clamp((win - 0.5) / 0.5)
    const t4 = tips[SURV]
    if (a4 > 0.01) { lead(t4[0], t4[1], t4[0] - 10, t4[1] + 40, a4); note(t4[0] - W * 0.2, t4[1] + 56, 'h47 · PASS', ['tested in days, not months'], col('green'), a4) }
    g.globalAlpha = 1
  },
}

/* ---------- Cheap: agents multiply. ---------- */
const cheap: Beat = {
  k: 0.09, damp: 0.7,
  targets(ps, f) {
    const max = f.phone ? 380 : 1200
    const n = Math.round(12 + (max - 12) * Math.pow(clamp(f.p / 0.85), 1.6))
    const cx = f.L + f.W / 2, cy = f.T + f.H / 2, R = Math.min(f.W, f.H) * 0.47
    for (let i = 0; i < Math.min(ps.length, max); i++) {
      const p = ps[i], rr = Math.sqrt((i + 0.5) / max) * R, ang = i * 2.39996
      const breathe = 1 + 0.02 * Math.sin(f.t * 1.5 + i * 0.05)
      if (i < n) set(p, cx + Math.cos(ang) * rr * breathe, cy + Math.sin(ang) * rr * breathe, 0.85, i < 12 ? 3.2 : 1.9, i % 7 === 0 ? PERI : INDIGO)
      else { set(p, cx, cy, 0, 1, INDIGO) }
    }
  },
}

/* ---------- Worse: a cloud of zero-skill backtest Sharpes and the best-by-luck line. ---------- */
function worseGeom(f: F) {
  const n = Math.pow(10, 1 + 3 * clamp(f.p / 0.9))
  const zeroY = f.T + f.H * 0.74, scale = (f.H * 0.6) / 4.2
  return { n, zeroY, scale, best: expectedMaxSharpe(n) }
}
const worse: Beat = {
  targets(ps, f) {
    const { n, zeroY, scale, best } = worseGeom(f)
    const max = f.phone ? 380 : 1200, k = Math.min(max, Math.round(n))
    for (let i = 0; i < Math.min(ps.length, max); i++) {
      const p = ps[i]
      const z = Math.min(p.z, best - 0.05)
      const near = z > best - 0.45
      set(p, f.L + f.W * (0.04 + 0.92 * p.s1), zeroY - z * scale, i < k ? (near ? 1 : 0.7) : 0, near ? 2.6 : 1.8, near ? INDIGO : PERI)
    }
  },
  under(g, f, mix) {
    const { zeroY } = worseGeom(f)
    g.globalAlpha = 0.25 * mix; g.strokeStyle = col('ink'); g.lineWidth = 1
    g.beginPath(); g.moveTo(f.L, zeroY); g.lineTo(f.L + f.W, zeroY); g.stroke()
    label(g, 'zero skill: true Sharpe 0', f.L, zeroY + 20, { alpha: mix })
  },
  over(g, f, mix) {
    const { n, zeroY, scale, best } = worseGeom(f)
    const y = zeroY - best * scale
    g.globalAlpha = mix; g.strokeStyle = col('ink'); g.lineWidth = 2
    g.beginPath(); g.moveTo(f.L, y); g.lineTo(f.L + f.W, y); g.stroke()
    label(g, `best by luck  ${best.toFixed(2)}σ`, f.L + f.W, y - 12, { align: 'right', color: col('ink'), size: 14, weight: 600, alpha: mix })
    label(g, `${Math.round(n).toLocaleString('en-IN')} trials, no skill in any`, f.L + f.W, y + 22, { align: 'right', alpha: mix })
  },
}

/* ---------- Ours: 259 candidates fall through the holdout. ---------- */
const PERM = Array.from({ length: 259 }, (_, i) => i).sort((a, b) => hash(a + 7) - hash(b + 7))
const RANK = new Array(259); PERM.forEach((v, j) => (RANK[v] = j))
const ours: Beat = {
  targets(ps, f) {
    const cols = f.phone ? 20 : 37, rows = Math.ceil(259 / cols)
    const cell = Math.min(f.W / cols, (f.H * 0.42) / rows)
    const gx = f.L + (f.W - cell * cols) / 2, gy = f.T + f.H * 0.06
    for (let i = 0; i < 259 && i < ps.length; i++) {
      const p = ps[i], st = (RANK[i] / 259) * 0.82, fall = clamp((f.p - st) / 0.14)
      const x = gx + (i % cols + 0.5) * cell, y0 = gy + (Math.floor(i / cols) + 0.5) * cell
      set(p, x + fall * (p.s1 - 0.5) * 30, y0 + fall * fall * f.H * 0.78, 1 - fall, cell * 0.24, fall > 0 ? CORAL : INDIGO)
      p.snap = fall > 0 && !f.reduce ? false : p.snap
    }
  },
  under(g, f, mix) {
    const y = f.T + f.H * 0.9
    g.globalAlpha = 0.3 * mix; g.strokeStyle = col('red'); g.setLineDash([4, 6]); g.lineWidth = 1
    g.beginPath(); g.moveTo(f.L, y); g.lineTo(f.L + f.W, y); g.stroke(); g.setLineDash([])
    label(g, 'holdout', f.L, y + 20, { alpha: mix, color: col('red') })
  },
}

/* ---------- Solution: rooms in Switch, linked by lineage. ---------- */
const ROOMS = [
  { id: 'control', x: 0.22, y: 0.0, w: 0.56, h: 0.2, label: 'control channel', sub: 'watches the whole floor', at: 0, c: PERI },
  { id: 'h12', x: 0.0, y: 0.38, w: 0.42, h: 0.2, label: '#h12', sub: 'thread', at: 0.12, c: INDIGO },
  { id: 'h7', x: 0.0, y: 0.76, w: 0.42, h: 0.2, label: '#h7', sub: 'thread', at: 0.28, c: INDIGO },
  { id: 'h12.1', x: 0.58, y: 0.38, w: 0.42, h: 0.2, label: '#h12.1', sub: 'fork · 41 trials', at: 0.44, c: INDIGO },
  { id: 'h12.1.2', x: 0.58, y: 0.76, w: 0.42, h: 0.2, label: '#h12.1.2', sub: 'fork · count carries', at: 0.6, c: INDIGO },
]
function roomRect(r: typeof ROOMS[number], f: F) { return { x: f.L + r.x * f.W, y: f.T + r.y * f.H, w: r.w * f.W, h: r.h * f.H } }
const solution: Beat = {
  targets(ps, f) {
    const per = f.phone ? 56 : 130
    const ctrl = roomRect(ROOMS[0], f)
    ROOMS.forEach((room, ri) => {
      const R = roomRect(room, f), on = ease((f.p - room.at) / 0.12)
      const cols = Math.round(Math.sqrt(per * (R.w / (R.h - 30)))), rows = Math.ceil(per / cols)
      for (let j = 0; j < per; j++) {
        const p = ps[ri * per + j]; if (!p) return
        const x = R.x + 16 + ((j % cols) + 0.5 + (p.s1 - 0.5) * 0.5) * ((R.w - 32) / cols)
        const y = R.y + 34 + (Math.floor(j / cols) + 0.5 + (p.s2 - 0.5) * 0.5) * ((R.h - 44) / rows)
        if (on > 0.01) set(p, x, y, 0.85 * on, 1.8, room.c)
        else set(p, ctrl.x + ctrl.w / 2, ctrl.y + ctrl.h / 2, 0, 1.5, room.c)
      }
    })
  },
  under(g, f, mix) {
    const ctrl = roomRect(ROOMS[0], f)
    ROOMS.forEach((room, i) => {
      const on = ease((f.p - room.at) / 0.12) * mix; if (on <= 0.01) return
      const R = roomRect(room, f)
      if (i > 0) {
        g.globalAlpha = 0.25 * on; g.strokeStyle = col('acc2'); g.setLineDash([2, 5]); g.lineWidth = 1
        g.beginPath(); g.moveTo(ctrl.x + ctrl.w / 2, ctrl.y + ctrl.h); g.lineTo(R.x + R.w / 2, R.y); g.stroke(); g.setLineDash([])
      }
      g.globalAlpha = 1 * on; g.fillStyle = col('field')
      g.beginPath(); g.rect(R.x, R.y, R.w, R.h); g.fill()
      g.globalAlpha = 0.9 * on; g.strokeStyle = col('ink'); g.stroke()
    })
    for (const [a, b, at] of [['h12', 'h12.1', 0.44], ['h12.1', 'h12.1.2', 0.6]] as const) {
      const on = ease((f.p - at) / 0.12) * mix; if (on <= 0.01) continue
      const A = roomRect(ROOMS.find(r => r.id === a)!, f), B = roomRect(ROOMS.find(r => r.id === b)!, f)
      const side = B.x > A.x + A.w
      const x1 = side ? A.x + A.w : A.x + A.w / 2, y1 = side ? A.y + A.h / 2 : A.y + A.h
      const x2 = side ? B.x : B.x + B.w / 2, y2 = side ? B.y + B.h / 2 : B.y
      g.globalAlpha = 0.9 * on; g.strokeStyle = col('acc'); g.lineWidth = 2
      g.beginPath(); g.moveTo(x1, y1); g.lineTo(x1 + (x2 - x1) * on, y1 + (y2 - y1) * on); g.stroke()
      g.fillStyle = col('acc'); g.beginPath(); g.arc(x1, y1, 3.5, 0, Math.PI * 2); g.fill()
    }
    g.globalAlpha = 1
  },
  over(g, f, mix) {
    ROOMS.forEach(room => {
      const on = ease((f.p - room.at) / 0.12) * mix; if (on <= 0.01) return
      const R = roomRect(room, f)
      label(g, room.label, R.x + 16, R.y + 22, { color: col('ink'), weight: 600, size: 13, alpha: on })
      if (!f.phone) label(g, room.sub, R.x + R.w - 16, R.y + 22, { align: 'right', alpha: on })
    })
  },
}

/* ---------- Paper: a tagged paper dissolves into a lit room, memory lights up, a hypothesis room is born. ---------- */
const MEMORY = [
  { x: 0.93, y: 0.1, label: 'h4.2 · died: costs ate it', c: CORAL },
  { x: 0.95, y: 0.46, label: 'h12.1.2 · live on paper', c: GREEN },
  { x: 0.16, y: 0.6, label: 'h9.1 · died: decayed in 8 days', c: CORAL },
]
function paperGeom(f: F) {
  const drop = ease(f.p / 0.18), dissolve = ease((f.p - 0.22) / 0.2)
  const memory = ease((f.p - 0.46) / 0.16), born = ease((f.p - 0.68) / 0.2)
  const page = { x: f.L + f.W * 0.02, y: f.T + f.H * 0.04 - (1 - drop) * 60, w: f.W * (f.phone ? 0.4 : 0.32), h: f.H * 0.62 }
  const lit = [f.L + f.W * 0.6, f.T + f.H * 0.3]
  const room = { x: f.L + f.W * 0.38, y: f.T + f.H * 0.8, w: f.W * 0.6, h: Math.max(96, f.H * 0.15) }
  return { drop, dissolve, memory, born, page, lit, room }
}
const paper: Beat = {
  targets(ps, f) {
    const { drop, dissolve, memory, born, page, lit, room } = paperGeom(f)
    const n = f.phone ? 110 : 240
    const gap = f.phone ? 5 : 6, perLine = Math.floor((page.w - 32) / gap)
    for (let i = 0; i < n; i++) {
      const p = ps[i], line = Math.floor(i / perLine), col = i % perLine
      const ragged = col < perLine * (0.6 + 0.4 * hash(line + 3))
      const px = page.x + 16 + col * gap, py = page.y + 58 + line * (f.phone ? 11 : 14)
      const lx = lit[0] + p.z * 24, ly = lit[1] + p.z2 * 24
      let x = px + (lx - px) * dissolve, y = py + (ly - py) * dissolve
      if (i % 5 < 2 && born > 0) {
        const j = Math.floor(i / 5) * 2 + (i % 5), cols = 24
        const rx = room.x + 16 + ((j % cols) + 0.5) * ((room.w - 32) / cols), ry = room.y + 38 + (Math.floor(j / cols) + 0.5) * ((room.h - 48) / Math.ceil(n * 0.4 / cols))
        x += (rx - x) * born; y += (ry - y) * born
      }
      const a = dissolve > 0 ? 0.9 : ragged ? 0.75 * drop : 0
      set(p, x, y, a, dissolve > 0.5 ? 2 : 1.5, dissolve > 0.5 ? INDIGO : INK)
      p.snap = dissolve === 0 && !f.reduce
    }
    MEMORY.forEach((m, k) => {
      for (let j = 0; j < 14; j++) {
        const p = ps[n + k * 14 + j]
        set(p, f.L + f.W * m.x + p.z * 7, f.T + f.H * m.y + p.z2 * 7, 0.9 * memory, 2, m.c)
      }
    })
  },
  under(g, f, mix) {
    const { drop, dissolve, memory, born, page, lit, room } = paperGeom(f)
    const pa = drop * (1 - dissolve) * mix
    if (pa > 0.01) {
      g.globalAlpha = pa; g.fillStyle = col('field'); g.beginPath(); g.rect(page.x, page.y, page.w, page.h); g.fill()
      g.globalAlpha = 0.9 * pa; g.strokeStyle = col('ink'); g.stroke()
      label(g, 'arXiv 2103.09692', page.x + 16, page.y + 26, { color: col('ink'), weight: 600, alpha: pa })
      label(g, 'Abstract', page.x + 16, page.y + 44, { size: 11, alpha: pa })
    }
    if (memory > 0.01) MEMORY.forEach(m => {
      const x = f.L + f.W * m.x, y = f.T + f.H * m.y
      g.globalAlpha = 0.45 * memory * mix; g.strokeStyle = m.c === GREEN ? col('green') : col('red'); g.lineWidth = 1.2
      g.setLineDash([3, 5]); g.beginPath(); g.moveTo(lit[0], lit[1]); g.lineTo(x, y); g.stroke(); g.setLineDash([])
      label(g, m.label, x, y + 26, { align: m.x < 0.5 ? 'left' : 'right', color: col('ink'), alpha: memory * mix })
    })
    if (born > 0.01) {
      g.globalAlpha = 0.75 * born * mix; g.fillStyle = col('field'); g.beginPath(); g.rect(room.x, room.y, room.w, room.h); g.fill()
      g.globalAlpha = 0.9 * born * mix; g.strokeStyle = col('ink'); g.stroke()
      g.globalAlpha = 0.8 * born * mix; g.strokeStyle = col('acc'); g.lineWidth = 2
      g.beginPath(); g.moveTo(lit[0], lit[1] + 30); g.lineTo(room.x + room.w / 2, room.y); g.stroke()
      label(g, f.phone ? '#h43 · from the paper' : 'h43 · strategy card from arXiv 2103.09692', room.x + 16, room.y + 22, { color: col('ink'), weight: 600, size: 13, alpha: born * mix })
    }
    if (dissolve > 0.3) label(g, 'paper thread · arxiv-2103-09692', lit[0], lit[1] - 62, { align: 'center', color: col('ink'), weight: 600, size: 13, alpha: ease((dissolve - 0.3) / 0.4) * mix })
    g.globalAlpha = 1
  },
}

/* ---------- Loop: a stepped mechanism, like a station clock's second hand.
   The agent dwells at a stage, then snaps to the next with a back-out overshoot. Never drifts. ---------- */
const STAGE_NAMES = ['propose', 'freeze spec', 'code', 'verify', 'gate', 'sign-off', 'paper', 'decay watch', 'retire', 'fork']
const GATE_STAGE = 4
const snapState = { at: 0, from: 0, to: 0, t0: -10 }
const backOut = (x: number, s: number) => 1 + (s + 1) * Math.pow(x - 1, 3) + s * Math.pow(x - 1, 2)
function loopGeom(f: F) {
  const cx = f.L + f.W / 2, cy = f.T + f.H / 2, R = Math.min(f.W, f.H) * (f.phone ? 0.36 : 0.38)
  const active = loopState.station
  if (active !== snapState.to) { snapState.from = snapState.to; snapState.to = active; snapState.t0 = f.t }
  const dur = active === GATE_STAGE ? 0.28 : 0.2
  const k = f.reduce ? 1 : clamp((f.t - snapState.t0) / dur)
  // Always travel forward around the ring, wrapping from fork back to propose.
  const span = ((snapState.to - snapState.from) % 10 + 10) % 10
  const pos = snapState.from + span * (k >= 1 ? 1 : backOut(k, active === GATE_STAGE ? 2.6 : 1.7))
  const since = f.reduce ? 1 : f.t - snapState.t0 - dur
  return { cx, cy, R, pos, active, since }
}
const ang = (k: number) => -Math.PI / 2 + (k / 10) * Math.PI * 2
const loop: Beat = {
  targets(ps, f) {
    const { cx, cy, R, pos, active, since } = loopGeom(f)
    const per = f.phone ? 8 : 16, ring = f.phone ? 70 : 160
    let i = 0
    const trav = ps[i++], a0 = ang(pos)
    set(trav, cx + Math.cos(a0) * R, cy + Math.sin(a0) * R, 1, active === GATE_STAGE ? 8 : 6, INK); trav.snap = true
    // Arrival punch: the station's cluster contracts hard, then breathes back out.
    const punch = since >= 0 && since < 0.3 ? 1 - since / 0.3 : 0
    for (let k = 0; k < 10; k++) {
      const a = ang(k), now = k === active
      const sig = now ? 11 - 8 * punch * (k === GATE_STAGE ? 1.2 : 1) : 6
      for (let j = 0; j < per; j++) {
        const p = ps[i++]
        set(p, cx + Math.cos(a) * R + p.z * sig, cy + Math.sin(a) * R + p.z2 * sig, now ? 1 : 0.55, now ? 2.8 + punch * 1.5 : 2.1, now ? INK : INDIGO)
        if (now) p.snap = true
      }
    }
    for (let j = 0; j < ring; j++) {
      const p = ps[i++], a = (j / ring) * Math.PI * 2
      set(p, cx + Math.cos(a) * R, cy + Math.sin(a) * R, 0.9, 1.3, LAV)
    }
  },
  under(g, f, mix) {
    const { cx, cy, R, active, since } = loopGeom(f)
    // A tick on the ring for every stage reached; the newest one draws in.
    for (let k = 0; k <= active; k++) {
      const a = ang(k), len = k === active ? 14 * clamp((since + 0.2) / 0.15) : 10
      g.globalAlpha = (k === active ? 1 : 0.5) * mix; g.strokeStyle = k === GATE_STAGE ? col('red') : col('acc'); g.lineWidth = k === active ? 3 : 2
      g.beginPath(); g.moveTo(cx + Math.cos(a) * (R - len), cy + Math.sin(a) * (R - len)); g.lineTo(cx + Math.cos(a) * (R - 2), cy + Math.sin(a) * (R - 2)); g.stroke()
    }
    g.globalAlpha = 1
  },
  over(g, f, mix) {
    const { cx, cy, R, active, since } = loopGeom(f)
    STAGE_NAMES.forEach((name, k) => {
      const a = ang(k), c = Math.cos(a), s = Math.sin(a)
      const now = k === active, flash = now && since >= 0 && since < 0.25
      label(g, now ? name.toUpperCase() : name, cx + c * (R + 28), cy + s * (R + 28) + 4, {
        align: Math.abs(c) < 0.2 ? 'center' : c > 0 ? 'left' : 'right',
        color: now ? (flash ? col('field') : col('ink')) : col('muted'), weight: now ? 700 : 500, size: f.phone ? 11 : 13, alpha: (now ? 1 : 0.6) * mix,
      })
      if (flash) {
        g.globalAlpha = mix; g.fillStyle = col('ink')
        const w = g.measureText(name.toUpperCase()).width + 10, x = cx + c * (R + 28), al = Math.abs(c) < 0.2 ? x - w / 2 : c > 0 ? x - 5 : x - w + 5
        g.fillRect(al, cy + s * (R + 28) - 10, w, 18)
        label(g, name.toUpperCase(), cx + c * (R + 28), cy + s * (R + 28) + 4, { align: Math.abs(c) < 0.2 ? 'center' : c > 0 ? 'left' : 'right', color: col('field'), weight: 700, size: f.phone ? 11 : 13, alpha: mix })
      }
    })
  },
}

/* ---------- Gate: particles rain through twelve filter bands. ---------- */
const bandY = (j: number, f: F) => f.T + f.H * (0.06 + 0.8 * (j + 0.5) / 12)
const bandHit = new Array(12).fill(-10)
function respawnRain(p: P, f: F) {
  p.gy = f.T - Math.random() * f.H * 0.9; p.gState = 0
  for (let j = 0; j < 12; j++) p.u[j] = Math.random()
  p.s1 = Math.random(); p.x = f.L + f.W * (0.03 + 0.94 * p.s1); p.y = p.gy; p.a = 0
}
const gateBeat: Beat = {
  targets(ps, f) {
    const n = f.phone ? 120 : 280, bottom = f.T + f.H * 0.95
    for (let i = 0; i < n && i < ps.length; i++) {
      const p = ps[i], x = f.L + f.W * (0.03 + 0.94 * p.s1)
      if (f.reduce) {
        const j = CHECKS.findIndex((c, k) => gate.on[k] && p.u[k] > c.rate)
        set(p, x, j < 0 ? bottom : bandY(j, f) - 3, 0.9, 2, j < 0 ? GREEN : CORAL); p.snap = true; continue
      }
      if (p.gState === 0 && p.gy === 0) respawnRain(p, f)
      if (p.gState === 0) {
        const prev = p.gy
        p.gy += f.dt * f.H * (0.16 + 0.1 * p.s3)
        for (let j = 0; j < 12; j++) {
          const y = bandY(j, f)
          if (prev < y && p.gy >= y && gate.on[j] && p.u[j] > CHECKS[j].rate) { p.gState = 1; p.gT = f.t; p.gy = y - 3; bandHit[j] = f.t; break }
        }
        if (p.gState === 0 && p.gy >= bottom) { p.gState = 2; p.gT = f.t; p.gy = bottom }
      } else if (f.t - p.gT > (p.gState === 2 ? 3.5 : 1.4)) respawnRain(p, f)
      const age = f.t - p.gT
      const a = p.gy < f.T ? 0 : p.gState === 0 ? 0.85 : clamp(1 - (age - (p.gState === 2 ? 2.5 : 0.6)) / 0.8)
      set(p, x, p.gy, a, p.gState === 2 ? 3.5 : 2.6, p.gState === 1 ? CORAL : p.gState === 2 ? GREEN : INDIGO)
      p.snap = true; p.a = a
    }
  },
  under(g, f, mix) {
    for (let j = 0; j < 12; j++) {
      const y = bandY(j, f), on = gate.on[j], flash = clamp(1 - (f.t - bandHit[j]) / 0.5)
      g.globalAlpha = (on ? 0.3 + 0.5 * flash : 0.12) * mix
      g.strokeStyle = on ? col('acc') : col('ink'); g.lineWidth = on ? 1 + flash : 1
      g.setLineDash(on ? [] : [3, 5])
      g.beginPath(); g.moveTo(f.L, y); g.lineTo(f.L + f.W, y); g.stroke()
      g.setLineDash([])
      label(g, CHECKS[j].short, f.L + f.W - 8, y - 6, { align: 'right', size: 11, alpha: (on ? 0.9 : 0.4) * mix })
    }
    const by = f.T + f.H * 0.95
    g.globalAlpha = 0.6 * mix; g.strokeStyle = col('green'); g.lineWidth = 2
    g.beginPath(); g.moveTo(f.L, by + 4); g.lineTo(f.L + f.W, by + 4); g.stroke()
    label(g, 'PROMOTED TO PAPER', f.L + 8, by + 22, { color: col('green'), alpha: mix })
  },
}

/* ---------- Forks: a fork splits off carrying its family's 41 trials. ---------- */
function bez(t: number, a: number[], c: number[], b: number[]) {
  const u = 1 - t
  return [u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]]
}
function forkGeom(f: F) {
  const A = [f.L + f.W * 0.2, f.T + f.H * 0.62], B = [f.L + f.W * 0.8, f.T + f.H * 0.36], C = [f.L + f.W * 0.5, f.T - f.H * 0.05]
  return { A, B, C, q: ease((f.p - 0.12) / 0.6), settle: ease((f.p - 0.72) / 0.2) }
}
const forks: Beat = {
  targets(ps, f) {
    const { A, B, C, q, settle } = forkGeom(f)
    const lead = ps[0], [lx, ly] = bez(q, A, C, B)
    set(lead, lx, ly, 1, 6, INK)
    for (let i = 1; i <= 41; i++) {
      const p = ps[i], [tx, ty] = bez(clamp(q - i * 0.016), A, C, B)
      const a = i * 2.39996, rr = 10 + Math.sqrt(i / 41) * 26, rx = B[0] + Math.cos(a) * rr, ry = B[1] + Math.sin(a) * rr
      set(p, tx + (rx - tx) * settle, ty + (ry - ty) * settle, q > 0.01 ? 0.95 - (i / 41) * 0.4 * (1 - settle) : 0, 2.4, INDIGO)
    }
    for (let i = 0; i < 41; i++) {
      const p = ps[42 + i], a = i * 2.39996, rr = Math.sqrt((i + 0.5) / 41) * 30
      set(p, A[0] + Math.cos(a) * rr, A[1] + Math.sin(a) * rr, 0.5, 2.2, PERI)
    }
  },
  under(g, f, mix) {
    const { A, B, C, q } = forkGeom(f)
    g.globalAlpha = 0.35 * mix; g.strokeStyle = col('acc'); g.setLineDash([3, 6]); g.lineWidth = 1.2
    g.beginPath(); g.moveTo(A[0], A[1]); g.quadraticCurveTo(C[0], C[1], B[0], B[1]); g.stroke(); g.setLineDash([])
    label(g, 'h12 · family trials 41', A[0], A[1] + 58, { align: 'center', color: col('ink'), weight: 600, size: 13, alpha: mix })
    if (q > 0.6) label(g, 'h12.1 · carries all 41', B[0], B[1] + 62, { align: 'center', color: col('ink'), weight: 600, size: 13, alpha: mix * ease((q - 0.6) / 0.3) })
  },
}

/* ---------- Decay: equity draws, flattens, CUSUM crosses, the strategy retires and seeds new ideas. ---------- */
function equity(x: number) {
  const base = x < 0.45 ? 0.12 + 0.7 * Math.pow(x / 0.45, 0.8) : x < 0.68 ? 0.82 : 0.82 - (x - 0.68) * 1.1
  return base + 0.025 * Math.sin(x * 47) + 0.015 * Math.sin(x * 121)
}
const ALARM_X = 0.8
function decayGeom(f: F) {
  const d = clamp(f.p / 0.62)
  const eq = { x: f.L, y: f.T + f.H * 0.06, w: f.W, h: f.H * 0.5 }
  const cu = { x: f.L, y: f.T + f.H * 0.66, w: f.W, h: f.H * 0.28 }
  const pa = ALARM_X * 0.62
  return { d, eq, cu, pa, alarm: f.p >= pa }
}
const cusum = (x: number) => (x < 0.55 ? 0.05 * Math.sin(x * 30) : -(x - 0.55) * 2.6)
const decay: Beat = {
  targets(ps, f) {
    const { d, eq, pa, alarm } = decayGeom(f)
    const n = f.phone ? 70 : 170
    for (let i = 0; i < n; i++) {
      const x = i / (n - 1)
      set(ps[i], eq.x + x * eq.w, eq.y + (1 - equity(x)) * eq.h, x <= d ? (alarm ? 0.7 : 1) : 0, 2, alarm ? PERI : INDIGO)
    }
    const head = ps[n], hx = eq.x + d * eq.w, hy = eq.y + (1 - equity(d)) * eq.h
    set(head, hx, hy, alarm ? 0.35 : 1, alarm ? 4 : 6, alarm ? INK : GREEN); head.snap = !alarm && !f.reduce
    const burst = ease((f.p - pa - 0.08) / 0.2)
    for (let i = 0; i < 12; i++) {
      const p = ps[n + 1 + i], k = i % 3
      const tx = eq.x + eq.w * (0.06 + k * 0.13) + p.z * 10, ty = eq.y + eq.h * (0.22 + (k % 2) * 0.12) + p.z2 * 10
      set(p, hx + (tx - hx) * burst, hy + (ty - hy) * burst, burst > 0.01 ? 0.95 : 0, 2.4, PERI)
    }
  },
  under(g, f, mix) {
    const { d, eq, cu, pa } = decayGeom(f)
    // CUSUM panel: threshold band, then the line drawn up to d.
    const thr = cu.y + cu.h * 0.72
    g.globalAlpha = 0.09 * mix; g.fillStyle = col('red'); g.fillRect(cu.x, thr, cu.w, cu.y + cu.h - thr)
    label(g, 'CUSUM alarm threshold', cu.x + 6, cu.y + cu.h - 10, { color: col('red'), alpha: mix })
    label(g, 'equity, paper', eq.x, eq.y - 4, { alpha: mix })
    label(g, 'CUSUM of live returns', cu.x, cu.y - 10, { alpha: mix })
    g.globalAlpha = mix; g.strokeStyle = col('ink'); g.lineWidth = 1.6
    g.beginPath()
    const steps = 80
    for (let s = 0; s <= steps * d; s++) {
      const x = s / steps, y = cu.y + cu.h * 0.3 - cusum(x) * cu.h * 0.62
      if (s === 0) g.moveTo(cu.x + x * cu.w, y); else g.lineTo(cu.x + x * cu.w, y)
    }
    g.stroke()
    if (f.p >= pa) {
      const ax = cu.x + ALARM_X * cu.w, ay = cu.y + cu.h * 0.3 - cusum(ALARM_X) * cu.h * 0.62
      const ph = f.reduce ? 0.5 : (f.t * 0.9) % 1
      g.globalAlpha = (1 - ph) * mix; g.strokeStyle = col('red'); g.lineWidth = 2
      g.beginPath(); g.arc(ax, ay, 6 + ph * 40, 0, Math.PI * 2); g.stroke()
      g.fillStyle = col('red'); g.globalAlpha = mix; g.beginPath(); g.arc(ax, ay, 5, 0, Math.PI * 2); g.fill()
      label(g, 'alarm · retired', ax + 12, ay - 12, { color: col('red'), weight: 600, alpha: mix })
      const burst = ease((f.p - pa - 0.08) / 0.2)
      if (burst > 0.3) ['h40', 'h41', 'h42'].forEach((h, k) =>
        label(g, h, eq.x + eq.w * (0.06 + k * 0.13), eq.y + eq.h * (0.22 + (k % 2) * 0.12) - 18, { align: 'center', color: col('ink'), weight: 600, alpha: mix * burst }))
    }
    g.globalAlpha = 1
  },
}

/* ---------- Footer: the swarm settles into a halftone. ---------- */
const footer: Beat = {
  targets(ps, f) {
    const max = Math.min(ps.length, f.phone ? 380 : 1200)
    const s = Math.sqrt((f.W * f.H) / max), cols = Math.ceil(f.W / s), rows = Math.ceil(f.H / s)
    for (let i = 0; i < max; i++) {
      const p = ps[i], c = i % cols, r = Math.floor(i / cols)
      if (r >= rows) { p.ta = 0; continue }
      const dx = c / cols, dy = 1 - r / rows, d = Math.hypot(dx, dy) / Math.SQRT2
      const wave = f.reduce ? 1 : 0.85 + 0.15 * Math.sin(f.t * 1.2 + c * 0.35 + r * 0.25)
      set(p, f.L + (c + 0.5) * s, f.T + (r + 0.5) * s, 0.55, (0.5 + 2.6 * Math.pow(1 - d, 1.6)) * wave, PALE)
    }
  },
}

const BEATS: Record<string, Beat> = { hero, tangent, cheap, worse, ours, solution, paper, loop, gate: gateBeat, forks, decay, footer }

export function start(canvas: HTMLCanvasElement) {
  const g = canvas.getContext('2d')!
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
  let phone = innerWidth < 760
  const N = phone ? 400 : 1500
  const ps: P[] = Array.from({ length: N }, () => ({
    x: Math.random() * innerWidth, y: Math.random() * innerHeight, vx: 0, vy: 0, tx: 0, ty: 0,
    a: 0, ta: 0, r: 1.5, tr: 1.5, c: INDIGO, snap: false,
    s1: Math.random(), s2: Math.random(), s3: Math.random(), z: normal(), z2: normal(),
    u: Array.from({ length: 12 }, Math.random), gy: 0, gState: 0, gT: 0,
  }))
  const sections = [...document.querySelectorAll<HTMLElement>('[data-beat]')]
  const mix: Record<string, number> = {}
  const lastP = new Map<HTMLElement, number>()
  let w = 0, h = 0, raf = 0, last = performance.now(), t0 = last, mx = -9999, my = -9999

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2)
    w = innerWidth; h = innerHeight; phone = w < 760
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr)
    // Size in px, not 100vh: on phones 100vh and innerHeight differ, which would stretch the drawing.
    canvas.style.width = `${w}px`; canvas.style.height = `${h}px`
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
  }

  function frame(now: number) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now
    const t = reduce ? 0 : (now - t0) / 1000
    // Read every rect first, then write, so the browser lays out once per frame.
    const rows = sections.map(el => {
      const r = el.getBoundingClientRect(), stage = el.querySelector<HTMLElement>('[data-stage]')
      return { el, r, s: (stage ?? el).getBoundingClientRect(), beat: el.dataset.beat!, dark: el.dataset.theme !== 'paper' }
    })
    let active: (typeof rows)[number] | undefined
    const frames = new Map<string, F>()
    for (const row of rows) {
      const { r } = row
      const p = row.el.dataset.pin !== undefined
        ? clamp(-r.top / Math.max(1, r.height - h))
        : clamp((h - r.top) / Math.max(1, Math.min(r.height, h) + h * 0.2))
      frames.set(row.beat, { L: row.s.left, T: row.s.top, W: row.s.width, H: row.s.height, p, t, dt, phone, reduce, mx, my, N, dark: row.dark })
      if (r.top <= h * 0.5 && r.bottom > h * 0.5) active = row
      if (Math.abs((lastP.get(row.el) ?? -1) - p) > 0.003) {
        lastP.set(row.el, p)
        row.el.style.setProperty('--p', p.toFixed(3))
        row.el.dispatchEvent(new CustomEvent('beat', { detail: p }))
      }
    }
    for (const p of ps) { p.ta = 0; p.snap = reduce }
    const beat = active ? BEATS[active.beat] : undefined
    if (beat && active) beat.targets(ps, frames.get(active.beat)!)
    for (const name in BEATS) mix[name] = reduce ? (active?.beat === name ? 1 : 0) : (mix[name] ?? 0) + ((active?.beat === name ? 1 : 0) - (mix[name] ?? 0)) * 0.08

    const k = beat?.k ?? 0.055, damp = beat?.damp ?? 0.8
    for (const p of ps) {
      if (p.snap) { p.x = p.tx; p.y = p.ty; p.vx = p.vy = 0 }
      else if (p.ta > 0 || p.a > 0.01) {
        p.vx = (p.vx + (p.tx - p.x) * k) * damp; p.vy = (p.vy + (p.ty - p.y) * k) * damp
        p.x += p.vx; p.y += p.vy
      }
      p.a += (p.ta - p.a) * (reduce || p.snap ? 1 : 0.07)
      p.r += (p.tr - p.r) * (reduce ? 1 : 0.1)
    }

    g.clearRect(0, 0, w, h)
    const draw = (layer: 'under' | 'over') => {
      for (const name in BEATS) {
        const fr = frames.get(name)
        if (mix[name] > 0.01 && fr) {
          dark = fr.dark
          g.save(); g.beginPath(); g.rect(fr.L - 24, fr.T - 24, fr.W + 48, fr.H + 48); g.clip()
          BEATS[name][layer]?.(g, fr, mix[name])
          g.restore()
        }
      }
    }
    draw('under')
    // Hard square dots snapped to a 3px grid: an LED board, not soft particles.
    const af = active && frames.get(active.beat)
    const pal = af && af.dark ? DARK : PAPER
    // Particles never leave the active stage, so they can never sit on a headline.
    g.save()
    if (af) { g.beginPath(); g.rect(af.L - 12, af.T - 12, af.W + 24, af.H + 24); g.clip() }
    for (const p of ps) {
      if (p.a < 0.02 || p.y < -20 || p.y > h + 20) continue
      g.globalAlpha = Math.min(1, p.a)
      g.fillStyle = pal[p.c]
      const sz = Math.max(2, Math.round(p.r * 1.7))
      g.fillRect(Math.round(p.x / 3) * 3 - (sz >> 1), Math.round(p.y / 3) * 3 - (sz >> 1), sz, sz)
    }
    g.restore()
    g.globalAlpha = 1
    draw('over')

    raf = !reduce && !document.hidden ? requestAnimationFrame(frame) : 0
  }
  const kick = () => { if (!raf) raf = requestAnimationFrame(frame) }
  const onMove = (e: PointerEvent) => { mx = e.clientX; my = e.clientY }
  const onLeave = () => { mx = my = -9999 }
  const onResize = () => { resize(); kick() }

  resize(); kick()
  addEventListener('resize', onResize)
  addEventListener('pointermove', onMove, { passive: true })
  document.addEventListener('pointerleave', onLeave)
  // Reduced motion: no loop. Draw a composed still on every scroll instead.
  if (reduce) addEventListener('scroll', kick, { passive: true })
  // The gate UI pokes this so a static frame updates when a check is toggled.
  addEventListener('gatechange', kick)
  document.addEventListener('visibilitychange', kick)
  return () => {
    cancelAnimationFrame(raf)
    removeEventListener('resize', onResize); removeEventListener('pointermove', onMove)
    document.removeEventListener('pointerleave', onLeave); removeEventListener('scroll', kick)
    removeEventListener('gatechange', kick); document.removeEventListener('visibilitychange', kick)
  }
}
