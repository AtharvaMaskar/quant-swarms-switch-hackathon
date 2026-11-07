/*
  The motion layer: scroll velocity, custom cursor, magnetic buttons, tilt,
  dithered wave backgrounds, odometer numbers, giant marquees.
  Everything here is skipped with prefers-reduced-motion and on touch screens where it makes no sense.
*/
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Dithering, GrainGradient } from '@paper-design/shaders-react'

export const reduced = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
const fine = () => typeof matchMedia !== 'undefined' && matchMedia('(pointer: fine)').matches

/* ---------- Scroll velocity: one smoothed number, published as --v (signed) and --va (absolute) ---------- */
let vel = 0
const subs = new Set<(v: number) => void>()
export function useVelocity(onChange: (v: number) => void) {
  useEffect(() => { subs.add(onChange); return () => { subs.delete(onChange) } }, [onChange])
}
export function startMotion() {
  if (reduced()) return () => {}
  const root = document.documentElement
  let lastY = scrollY, lastT = performance.now(), target = 0, raf = 0
  const onScroll = () => {
    const now = performance.now(), dy = scrollY - lastY, dt = Math.max(8, now - lastT)
    target = Math.max(-1, Math.min(1, dy / dt / 4))
    lastY = scrollY; lastT = now
  }
  const tick = () => {
    target *= 0.9
    vel += (target - vel) * 0.18
    if (Math.abs(vel) < 0.001) vel = 0
    root.style.setProperty('--v', vel.toFixed(3))
    root.style.setProperty('--va', Math.abs(vel).toFixed(3))
    subs.forEach(f => f(vel))
    raf = requestAnimationFrame(tick)
  }
  addEventListener('scroll', onScroll, { passive: true })
  raf = requestAnimationFrame(tick)

  // Custom LED cursor, magnetic buttons and tilt cards: fine pointers only.
  let stopPointer = () => {}
  if (fine()) {
    const dot = document.createElement('div'); dot.className = 'led-cursor'; document.body.appendChild(dot)
    root.classList.add('has-cursor')
    let x = -100, y = -100, cx = -100, cy = -100, craf = 0
    const loop = () => { cx += (x - cx) * 0.35; cy += (y - cy) * 0.35; dot.style.transform = `translate(${cx}px, ${cy}px)`; craf = requestAnimationFrame(loop) }
    craf = requestAnimationFrame(loop)
    let magnet: HTMLElement | null = null, tilt: HTMLElement | null = null
    const move = (e: PointerEvent) => {
      x = e.clientX; y = e.clientY
      const t = e.target as HTMLElement
      dot.classList.toggle('big', !!t.closest('a, button, summary, [data-tilt], [role=switch]'))
      const m = t.closest<HTMLElement>('.btn, .nav-cta')
      if (magnet && magnet !== m) { magnet.style.setProperty('--mx', '0px'); magnet.style.setProperty('--my', '0px') }
      magnet = m
      if (m) {
        const r = m.getBoundingClientRect()
        m.style.setProperty('--mx', `${(x - r.left - r.width / 2) * 0.25}px`)
        m.style.setProperty('--my', `${(y - r.top - r.height / 2) * 0.35}px`)
      }
      const k = t.closest<HTMLElement>('[data-tilt]')
      if (tilt && tilt !== k) { tilt.style.setProperty('--rx', '0deg'); tilt.style.setProperty('--ry', '0deg') }
      tilt = k
      if (k) {
        const r = k.getBoundingClientRect()
        k.style.setProperty('--ry', `${((x - r.left) / r.width - 0.5) * 10}deg`)
        k.style.setProperty('--rx', `${-((y - r.top) / r.height - 0.5) * 8}deg`)
      }
    }
    addEventListener('pointermove', move, { passive: true })
    stopPointer = () => { cancelAnimationFrame(craf); removeEventListener('pointermove', move); dot.remove(); root.classList.remove('has-cursor') }
  }
  return () => { cancelAnimationFrame(raf); removeEventListener('scroll', onScroll); stopPointer() }
}

/** Mounts its children only while the element is within a viewport of the screen, so at most a couple of shaders run. */
function useNear<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [near, setNear] = useState(false)
  useEffect(() => {
    const io = new IntersectionObserver(([e]) => setNear(e.isIntersecting), { rootMargin: '50% 0px' })
    io.observe(ref.current!)
    return () => io.disconnect()
  }, [])
  return [ref, near] as const
}

/* ---------- Dithered wave field behind the dark chapters. Speeds up with scroll, leans toward the cursor. ---------- */
export function DitherBg({ shape = 'wave' as const }: { shape?: 'wave' | 'warp' | 'simplex' | 'ripple' }) {
  const [ref, near] = useNear<HTMLDivElement>()
  const [speed, setSpeed] = useState(0.35)
  const [off, setOff] = useState<[number, number]>([0, 0])
  const last = useRef(0)
  useVelocity(v => {
    const s = Math.round((0.35 + Math.abs(v) * 3) * 10) / 10
    if (s !== last.current) { last.current = s; setSpeed(s) }
  })
  useEffect(() => {
    if (!fine() || reduced()) return
    let t = 0
    const move = (e: PointerEvent) => {
      if (t) return
      t = requestAnimationFrame(() => { t = 0; setOff([(e.clientX / innerWidth - 0.5) * 0.12, (e.clientY / innerHeight - 0.5) * 0.12]) })
    }
    addEventListener('pointermove', move, { passive: true })
    return () => removeEventListener('pointermove', move)
  }, [])
  return (
    <div ref={ref} className="dither-bg" aria-hidden="true">
      {near && <Dithering style={{ width: '100%', height: '100%' }} colorBack="#1A1F6B" colorFront="#2D35AC" shape={shape} type="4x4" size={3}
        scale={0.9} offsetX={off[0]} offsetY={off[1]} speed={reduced() ? 0 : speed} maxPixelCount={1600 * 1000} />}
    </div>
  )
}

/** A printed-looking grain gradient: electric indigo → violet → hot magenta. */
export function GrainBg() {
  const [ref, near] = useNear<HTMLDivElement>()
  return (
    <div ref={ref} className="dither-bg" aria-hidden="true">
      {near && <GrainGradient style={{ width: '100%', height: '100%' }} colorBack="#2B2FD9" colors={['#2B2FD9', '#7B2FF7', '#FF2E9A', '#2B2FD9']}
        softness={0.35} intensity={0.55} noise={0.4} shape="wave" speed={reduced() ? 0 : 0.6} maxPixelCount={1600 * 1000} />}
    </div>
  )
}

/* ---------- Odometer: every digit is a vertical strip that springs to its value. ---------- */
export function Odometer({ value, className = '' }: { value: string; className?: string }) {
  return (
    <span className={`odo ${className}`} aria-label={value}>
      {value.split('').map((ch, i) => /\d/.test(ch)
        ? <span key={`${value.length}-${i}`} className="odo-d" aria-hidden="true"><span style={{ transform: `translateY(${-Number(ch) * 10}%)` }}>{'0123456789'.split('').map(d => <span key={d}>{d}</span>)}</span></span>
        : <span key={`${value.length}-${i}`} aria-hidden="true">{ch}</span>)}
    </span>
  )
}

/* ---------- Giant marquee between chapters; it skews with scroll speed. ---------- */
export function BigMarquee({ words, dark = false }: { words: string[]; dark?: boolean }) {
  const row = words.map((w, i) => <span key={i} className={i % 2 ? 'hollow' : 'fill'}>{w}<i>·</i></span>)
  return (
    <div className={`bigmarquee ${dark ? 'dark' : 'paper'}`} aria-hidden="true">
      <div className="bm-skew"><div className="bm-track">{row}{row}</div></div>
    </div>
  )
}

/* ---------- Hero headline: each character assembles from condensed to expanded with a spring. ---------- */
export function Assemble({ text, start = 0 }: { text: string; start?: number }) {
  return <>{text.split('').map((c, i) => <span key={i} className="ch" style={{ '--i': start + i } as React.CSSProperties}>{c === ' ' ? ' ' : c}</span>)}</>
}

/** FLIP: rows animate from where they were to where they are after the order changes. */
export function useFlip<K extends string>(keys: K[]) {
  const els = useRef(new Map<K, HTMLElement>())
  const prev = useRef(new Map<K, number>())
  useLayoutEffect(() => {
    els.current.forEach((el, k) => {
      const top = el.getBoundingClientRect().top + scrollY, old = prev.current.get(k)
      if (old !== undefined && old !== top && !reduced()) {
        el.style.transition = 'none'; el.style.transform = `translateY(${old - top}px)`
        el.getBoundingClientRect()
        el.style.transition = 'transform 0.9s cubic-bezier(0.34, 1.4, 0.64, 1)'; el.style.transform = ''
      }
      prev.current.set(k, top)
    })
  }, [keys.join('|')])
  return (k: K) => (el: HTMLElement | null) => { if (el) els.current.set(k, el); else els.current.delete(k) }
}
