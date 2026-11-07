import { useEffect, useRef, useState, type RefObject } from 'react'

const reduce = () => matchMedia('(prefers-reduced-motion: reduce)').matches

/** Progress (0..1) the swarm engine reports for a [data-beat] section. */
export function useBeat(ref: RefObject<HTMLElement | null>) {
  const [p, setP] = useState(0)
  useEffect(() => {
    const el = ref.current!
    const on = (e: Event) => setP((e as CustomEvent<number>).detail)
    el.addEventListener('beat', on)
    return () => el.removeEventListener('beat', on)
  }, [ref])
  return p
}

export function useInView<T extends HTMLElement>(threshold = 0.3) {
  const ref = useRef<T>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); io.disconnect() } }, { threshold })
    io.observe(ref.current!)
    return () => io.disconnect()
  }, [threshold])
  return [ref, seen] as const
}

/** Counts from 0 to `to` once `go` turns true; jumps straight there with reduced motion. */
export function useCountUp(to: number, go: boolean, ms = 1400) {
  const [v, setV] = useState(0)
  useEffect(() => {
    if (!go) return
    if (reduce()) { setV(to); return }
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const k = Math.min(1, (now - t0) / ms)
      setV(to * (1 - Math.pow(1 - k, 4)))
      if (k < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [to, go, ms])
  return v
}

/** Adds .in to every [data-reveal] once it scrolls into view. */
export function useReveal() {
  useEffect(() => {
    const io = new IntersectionObserver(es => {
      for (const e of es) if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target) }
    }, { threshold: 0.2, rootMargin: '0px 0px -8% 0px' })
    document.querySelectorAll('[data-reveal], [data-pop]').forEach(el => io.observe(el))
    return () => io.disconnect()
  }, [])
}

/** Types `lines` one character at a time once `go` is true. Returns the visible text per line. */
export function useTyping(lines: string[], go: boolean, cps = 70) {
  const total = lines.reduce((n, l) => n + l.length, 0)
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!go) return
    if (reduce()) { setN(total); return }
    const id = setInterval(() => setN(v => (v >= total ? (clearInterval(id), v) : v + 1)), 1000 / cps)
    return () => clearInterval(id)
  }, [go, total, cps])
  let left = n
  return lines.map(l => { const s = l.slice(0, Math.max(0, left)); left -= l.length; return s })
}
