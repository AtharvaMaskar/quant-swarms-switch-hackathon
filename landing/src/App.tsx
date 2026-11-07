import { useEffect, useRef, useState } from 'react'
import { MeshGradient } from '@paper-design/shaders-react'
import { start } from './engine.ts'
import { expectedMaxSharpe } from './stats.ts'
import { NAME, SWITCH_URL, FLOOR_URL, GITHUB_URL, STAGES, CHECKS, gate, loopState, CANDIDATES, BOARD, FAQ } from './data.ts'
import { useBeat, useInView, useReveal } from './hooks.ts'
import { Hero, Nav, Ticker } from './Hero.tsx'
import { BigMarquee, DitherBg, Odometer, startMotion, useFlip } from './fx.tsx'

const clamp = (v: number) => Math.max(0, Math.min(1, v))
type Theme = 'dark' | 'paper' | 'electric'

/** A headline whose lines rise out of their own masks. */
function Mask({ lines, className = '' }: { lines: React.ReactNode[]; className?: string }) {
  return <h2 className={`mask ${className}`} data-reveal>{lines.map((l, i) => <span key={i}><span>{l}</span></span>)}</h2>
}
const Label = ({ n, children }: { n: string; children: React.ReactNode }) => <p className="mlabel" data-reveal>{n} / {children}</p>

function Canvas() {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => start(ref.current!), [])
  return <canvas ref={ref} className="swarm" aria-hidden="true" />
}

/** A scroll-pinned chapter: a poster of text on one side, the swarm's stage on the other. */
function Pin({ beat, id, vh = 220, p, children, flip = false, wide = false, theme }: { beat: string; id?: string; vh?: number; p: React.RefObject<HTMLElement | null>; children: React.ReactNode; flip?: boolean; wide?: boolean; theme: Theme }) {
  return (
    <section className={`pin ${theme} dots ${flip ? 'flip' : ''} ${wide ? 'wide' : ''}`} data-beat={beat} data-theme={theme} data-pin id={id} ref={p as React.RefObject<HTMLElement>} style={{ height: `${vh}vh` }}>
      <div className="sticky">
        {theme === 'dark' && <DitherBg />}
        <div className="pin-text layer">{children}</div>
        <div className="pin-stage" data-stage aria-hidden="true" />
      </div>
    </section>
  )
}

const TANGENT_NOTES = [
  ['10 tangents', 'a paper that might apply · a filter that might fix last quarter · a signal from another market', 0],
  ['9 dead ends · months lost', '', 0.36],
  ['Recall', 'it already died once, of costs. The fix is known.', 0.58],
  ['h47 · pass', 'tested in days, not months', 0.74],
] as const
function Tangent() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  return (
    <Pin beat="tangent" id="problem" p={ref} vh={240} theme="paper" wide>
      <Label n="01">The tangent trap</Label>
      <p className="kicker" data-reveal><em>Every quant knows this week.</em></p>
      <Mask className="h-sm" lines={['One idea.', 'Ten tangents.', 'Nine dead ends.']} />
      <p className="lead-line" data-reveal>Chase all ten and you lose months finding out.</p>
      <ol className="annos">
        {TANGENT_NOTES.map(([t, d, at], i) => <li key={t} className={p >= at ? 'on' : ''}><b>{t}</b>{d && <em>{d}</em>}</li>)}
      </ol>
    </Pin>
  )
}

function Cheap() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const max = typeof innerWidth !== 'undefined' && innerWidth < 760 ? 380 : 1200
  const n = Math.round(12 + (max - 12) * Math.pow(clamp(p / 0.85), 1.6))
  const life = p < 0.33 ? 'MONTHS' : p < 0.66 ? 'WEEKS' : 'DAYS'
  return (
    <Pin beat="cheap" p={ref} theme="dark">
      <Label n="02">Alpha decay</Label>
      <Mask lines={['Strategies', 'got cheap.', <>Alpha got <span className="hard">scarce.</span></>]} />
      <div className="spec-panel" data-pop>
        <div className="sp-row"><span>STRATEGIES ON THE FLOOR</span><b className="tab"><Odometer value={n.toLocaleString('en-IN')} /></b></div>
        <div className="sp-row"><span>HOW LONG AN EDGE LASTS</span><b>{life}</b></div>
        <div className="tl-track"><div className="tl-bar" style={{ width: `${100 - 92 * clamp(p / 0.8)}%` }} /></div>
        <div className="sp-row small"><span>4–5 YEARS AGO: MONTHS</span><span>NOW: WEEKS, OR DAYS</span></div>
      </div>
      <p className={`punch ${p > 0.72 ? 'on' : ''}`}>By the time you find an edge, it’s gone.</p>
    </Pin>
  )
}

function Worse() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const n = Math.pow(10, 1 + 3 * clamp(p / 0.9)), row = Math.min(3, Math.round(Math.log10(n) - 1))
  return (
    <Pin beat="worse" p={ref} flip theme="paper">
      <Label n="03">The problem, multiplied</Label>
      <Mask lines={['Swarms make', 'it worse.']} />
      <p className="body" data-reveal>Every extra trial lifts the best backtest by <em>luck</em> alone. Expected best Sharpe from N strategies with zero skill:</p>
      <ul className="giants" data-reveal>
        {[10, 100, 1000, 10000].map((t, i) => (
          <li key={t} className={i === row ? 'on' : ''}><b className="tab">{expectedMaxSharpe(t).toFixed(2)}</b><span>N = {t.toLocaleString('en-IN')}</span></li>
        ))}
      </ul>
      <p className="note" data-reveal>FALSE STRATEGY THEOREM · BAILEY & LÓPEZ DE PRADO · NOTHING REAL IN THE CLOUD</p>
    </Pin>
  )
}

function Ours() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const standing = Math.max(0, 259 - Math.floor(clamp(p / 0.84) * 259))
  return (
    <Pin beat="ours" p={ref} vh={200} theme="dark">
      <Label n="04">We tried it</Label>
      <Mask lines={['Zero', 'survived.']} />
      <div className="huge-num" data-reveal><b className="tab"><Odometer value={String(standing).padStart(3, '0')} /></b><span>{standing ? `OF 259 STILL STANDING ON THE HOLDOUT` : '259 PROPOSED · 0 PROMOTED'}</span></div>
      <p className="body" data-reveal>A swarm we built proposed 259 strategies. Zero survived. The winners on training data lost on the holdout: long exposure in a rising market looked like skill.</p>
    </Pin>
  )
}

const FLOOR = [
  ['One control channel', 'One Slack channel watches the whole floor: what is open, what is on paper, which alarms tripped, every family’s trial count.', 0],
  ['A thread per hypothesis', 'Each hypothesis is a Switch room, shown as a thread in that one channel. Proposal, frozen spec, code, patches (h12v2, h12v3) and the gate verdict live there.', 0.12],
  ['Forks, with lineage', 'A fork opens a new thread, h12.1, its parent in the title and the family’s trial count on its back.', 0.44],
  ['Sign-off in the thread', 'Nothing reaches paper until the risk owner reads the evidence there and approves it.', 0.76],
] as const
function Solution() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const cur = FLOOR.reduce((k, f, i) => (p >= (f[2] as number) ? i : k), 0)
  return (
    <Pin beat="solution" id="solution" p={ref} theme="dark">
      <Label n="05">The solution</Label>
      <Mask lines={['So the floor', 'moved into', 'Switch.']} />
      <p className="body" data-reveal>This page is context. The product lives in Switch, where agents and people share one Slack channel, a thread per idea.</p>
      <ol className="rows" data-reveal>
        {FLOOR.map(([t, d], i) => <li key={t} className={i === cur ? 'on' : i < cur ? 'done' : ''}><span className="rn tab">0{i + 1}</span><div><b>{t}</b><span>{d}</span></div></li>)}
      </ol>
      <div className="btns" data-reveal><a className="btn solid" href={SWITCH_URL} target="_blank" rel="noopener">Open the research floor in Switch ↗</a></div>
    </Pin>
  )
}

const CHAT: [string, React.ReactNode, number][] = [
  ['researcher', <><kbd>deflate</kbd> <kbd>implement</kbd> arxiv.org/abs/2103.09692</>, 0],
  ['@reader', 'Paper thread open. Read it, equations and all. It’s a review: rough vol is testable as a vol filter; crowding only partly, we only have OI.', 0.22],
  ['researcher', 'Good. Gate NIFTY futures on the vol forecast. Skip the crowding part.', 0.46],
  ['researcher', <><kbd>@reader</kbd> <kbd>propose</kbd></>, 0.68],
  ['@reader', 'Strategy card h43 created from the paper. Entering the loop.', 0.8],
]
function Paper() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  return (
    <Pin beat="paper" p={ref} flip theme="paper">
      <Label n="06">Bring a paper</Label>
      <Mask className="h-sm" lines={['Tag a paper.', 'Get a tested', 'strategy,', <><em>or a reason it won’t work.</em></>]} />
      <ol className="chat" data-reveal>
        {CHAT.map(([who, text, at], i) => (
          <li key={i} className={`${who === '@reader' ? 'agent' : 'human'} ${p >= at ? 'on' : ''}`}>
            <span className="who">{who}</span><span className="txt">{text}</span>
          </li>
        ))}
      </ol>
    </Pin>
  )
}

// Detents: scroll position picks the stage (floor), so slow scrolling still jumps station to station.
// While the chapter is pinned and the reader is idle, it auto-advances; the gate dwells longer.
function useStation(p: number) {
  const fromScroll = Math.min(9, Math.floor(clamp(p / 0.95) * 10))
  const [auto, setAuto] = useState(0)
  const lastScroll = useRef(0)
  useEffect(() => {
    const on = () => { lastScroll.current = performance.now() }
    addEventListener('scroll', on, { passive: true })
    return () => removeEventListener('scroll', on)
  }, [])
  useEffect(() => setAuto(0), [fromScroll])
  const station = (fromScroll + auto) % 10
  const pinned = p > 0.001 && p < 0.999
  useEffect(() => {
    if (!pinned || matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const dwell = station === 4 ? 2200 : 1400
    const id = setTimeout(function tick() {
      if (performance.now() - lastScroll.current > 1000) setAuto(a => a + 1)
      else setTimeout(tick, 400)
    }, dwell)
    return () => clearTimeout(id)
  }, [station, pinned])
  loopState.station = station
  return station
}

function Loop() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const active = useStation(p)
  return (
    <Pin beat="loop" id="loop" p={ref} vh={260} theme="dark">
      <Label n="07">The loop</Label>
      <Mask lines={['Agents propose.', 'Code decides.']} />
      <div className={`now ${active === 4 ? 'heavy' : ''}`} aria-live="polite">
        <span className="mlabel tab">STAGE <Odometer value={String(active + 1).padStart(2, '0')} /> / 10</span>
        <div className="flip" key={active}><b>{STAGES[active][0]}</b><span className="desc">{STAGES[active][1]}</span></div>
      </div>
    </Pin>
  )
}

function Gate() {
  const [on, setOn] = useState(() => [...gate.on])
  const [ref, seen] = useInView<HTMLDivElement>(0.4)
  const survivors = CHECKS.reduce((n, c, i) => (on[i] ? n * c.rate : n), CANDIDATES)
  function toggle(i: number) {
    const next = on.map((v, j) => (j === i ? !v : v))
    gate.on = next; setOn(next); dispatchEvent(new Event('gatechange'))
  }
  return (
    <section className="gate paper dots" id="gate" data-beat="gate" data-theme="paper">
      <div className="gate-text layer">
        <Label n="08">The gate</Label>
        <Mask lines={['Twelve checks.', 'Switch any off.']} />
        <p className="body" data-reveal>Deterministic code, no model in the loop. Each band catches what fails it. Pass rates are illustrative; the checks follow López de Prado’s <em>Advances in Financial Machine Learning.</em></p>
        <div className="tally" ref={ref} data-reveal>
          <b className="tab"><Odometer value={String(seen ? Math.round(survivors) : CANDIDATES).padStart(3, '0')} /></b>
          <span>OF {CANDIDATES} CANDIDATES<br />REACH PAPER TRADING</span>
        </div>
        <div className="console" data-ui data-pop>
          <div className="console-head"><span>CHECK</span><span>KEEPS</span><span>ON</span></div>
          <ul>
            {CHECKS.map((c, i) => (
              <li key={c.name} className={on[i] ? 'on' : ''} style={{ '--i': i } as React.CSSProperties}>
                <span className="cn tab">{String(i + 1).padStart(2, '0')}</span>
                <div><b>{c.name}</b><span>{c.note}</span></div>
                <span className="keep tab">{c.rate < 1 ? `${Math.round(c.rate * 100)}%` : '—'}</span>
                <button role="switch" aria-checked={on[i]} aria-label={c.name} className="switch" onClick={() => toggle(i)}><i /></button>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="gate-stage-wrap"><div className="gate-stage" data-stage aria-hidden="true" /></div>
    </section>
  )
}

function Forks() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  return (
    <Pin beat="forks" id="rooms" p={ref} vh={200} flip theme="dark">
      <Label n="09">Threads and forks</Label>
      <Mask lines={['Forks inherit', 'the count.']} />
      <div className="huge-num grad" data-reveal><b className="tab">41</b><span>TRIALS h12.1 IS CHARGED WITH. NEVER 1.</span></div>
      <table className="lineage" data-reveal>
        <tbody>
          <tr><td>h12</td><td>hypothesis thread</td><td>NEW</td></tr>
          <tr><td>h12v2</td><td>patch, same thread · max 4</td><td>PATCH</td></tr>
          <tr className={p > 0.5 ? 'on' : ''}><td>h12.1</td><td>fork, new thread, lineage in title</td><td>FORK</td></tr>
        </tbody>
      </table>
    </Pin>
  )
}

const YAML = `# costs/in-fno.yaml
as_of: 2026-04-01          # dated: re-check before trusting
brokerage:
  broker: groww
  per_order_inr: 20        # F&O, flat per order
stt:                       # from 2026-04-01
  futures: 0.0005          # 0.05%
  options: 0.0015          # 0.15%
exchange_txn: 0.0000297
sebi_fee:     0.000001
stamp_duty:   0.00003      # buy side
gst:          0.18         # on brokerage + exchange + SEBI fees
slippage_bps: { liquid: 3, midcap: 8 }   # illustrative`
function Costs() {
  return (
    <section className="costs paper">
      <div className="costs-text layer">
        <Label n="10">Indian costs</Label>
        <Mask lines={['Costs,', 'dated.']} />
        <p className="body" data-reveal>A backtest without STT is a story. Every trade pays brokerage, securities transaction tax, exchange and SEBI fees, stamp duty and GST, plus slippage. They live in one dated file the gate reads, so a rate change is a one-line edit with a date on it.</p>
        <figure className="photo" data-reveal>
          <img src="/img/bse-front.jpg" width={1600} height={1339} loading="lazy" alt="The BSE building in Mumbai with its red sign, framed by palm trees." />
          <figcaption>BSE, DALAL STREET · PHOTO NIYANTHA SHEKHAR · CC BY 2.0</figcaption>
        </figure>
      </div>
      <div className="code-card layer" data-pop data-tilt data-ui>
        <div className="term-bar"><i /><i /><i /><span>costs/in-fno.yaml</span></div>
        <pre><code>{YAML.split('\n').map((l, i) => {
          const k = l.indexOf('#')
          return <span key={i}><span className="ln">{String(i + 1).padStart(2, ' ')}</span>{k < 0 ? l : <>{l.slice(0, k)}<span className="cm">{l.slice(k)}</span></>}{'\n'}</span>
        })}</code></pre>
      </div>
    </section>
  )
}

const DECAY = [['Equity draws on paper', 0], ['The edge flattens', 0.3], ['CUSUM crosses the threshold', 0.5], ['Retired; its notes seed h40–h42', 0.6]] as const
function Decay() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  return (
    <Pin beat="decay" p={ref} theme="dark">
      <Label n="11">Decay</Label>
      <Mask lines={['Decay', 'retires it.']} />
      <ol className="rows tight" data-reveal>{DECAY.map(([t, at], i) => <li key={t} className={p >= at && (i === DECAY.length - 1 || p < DECAY[i + 1][1]) ? 'on' : p >= at ? 'done' : ''}><span className="rn tab">0{i + 1}</span><div><b>{t}</b></div></li>)}</ol>
      <p className="body" data-reveal>A CUSUM detector and an entropy check watch live returns. When the edge fades past the threshold, the strategy stops, and what it taught the swarm goes back into the loop.</p>
      <p className="note" data-reveal>TASTE, LEARNED: A RANKER SCORES NEW PROPOSALS AGAINST WHAT EARLIER ONES BECAME. AFTER AFTER42 AND IDEATASTE.</p>
    </Pin>
  )
}

// Rows arrive in proposal order, then sort themselves: references first, live by DSR, then retired.
const RANK = (r: typeof BOARD[number]) => (r.ref ? 0 : r.status.startsWith('retired') ? 2 : 1) * 10 - Number(r.dsr === '—' ? 0 : r.dsr)
function Board() {
  const [ref, seen] = useInView<HTMLDivElement>(0.3)
  const [sorted, setSorted] = useState(false)
  useEffect(() => { if (seen) { const id = setTimeout(() => setSorted(true), 700); return () => clearTimeout(id) } }, [seen])
  const rows = sorted ? [...BOARD].sort((x, y) => RANK(x) - RANK(y)) : [BOARD[5], BOARD[2], BOARD[0], BOARD[6], BOARD[3], BOARD[1], BOARD[4]]
  const flip = useFlip(rows.map(r => r.name))
  return (
    <section className="board paper">
      <div className="board-head layer">
        <div><Label n="12">Leaderboard</Label><Mask lines={['The board.']} /></div>
        <p className="note" data-reveal>ILLUSTRATIVE ROWS. NO REAL STRATEGY RESULTS. THE LIVE BOT AND BUY-AND-HOLD ARE REFERENCE ROWS. SORTING BY DSR…</p>
      </div>
      <div className="table-wrap layer" ref={ref} data-pop data-ui>
        <table>
          <thead><tr><th>ID</th><th>Strategy</th><th>Status</th><th className="num">DSR</th><th className="num">Trials</th><th className="num">Days</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.name} ref={flip(r.name)} className={r.ref ? 'ref' : r.status.startsWith('retired') ? 'retired' : 'live'}>
                <td className="id">{r.ref ? 'REF' : r.name.split(' · ')[0]}</td>
                <td>{r.ref ? r.name : r.name.split(' · ')[1]}</td>
                <td><span className="status">{r.status.toUpperCase()}</span></td>
                <td className="num tab">{r.dsr}</td><td className="num tab">{r.trials}</td><td className="num tab">{r.days}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="board-cta layer" data-reveal>
        <a className="btn solid" href={FLOOR_URL} target="_blank" rel="noopener">Open the live research floor ↗</a>
        <p className="note">THE REAL BOARD: EVERY TRIAL COUNTED, LIVE FROM SWITCH</p>
      </div>
    </section>
  )
}

function Faq() {
  return (
    <section className="faq dark dots" id="faq">
      <DitherBg shape="warp" />
      <div className="layer"><Label n="13">FAQ</Label><Mask lines={['Questions', 'from the', 'judges’ table.']} /></div>
      <div className="faq-list layer">
        {FAQ.map(([q, a], i) => (
          <details key={q} data-reveal>
            <summary><span className="qn tab">Q{String(i + 1).padStart(2, '0')}</span><span className="q">{q}</span><span className="plus" aria-hidden="true" /></summary>
            <p>{a}</p>
          </details>
        ))}
      </div>
    </section>
  )
}

// Quiet, scroll-lit close: each word lights as the pinned section scrolls; "excellence." finishes in lavender.
const MANIFESTO = ['AI exists, so we can', 'explore the extreme ends', 'of human excellence.']
function Manifesto() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const reduce = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  const words = MANIFESTO.map(l => l.split(' '))
  const total = words.flat().length
  let n = 0
  return (
    <section className="manifesto quiet" data-beat="manifesto" data-theme="dark" data-pin ref={ref} style={{ height: '220vh' }}>
      <div className="sticky">
        <p className="mlabel layer">14 / WHY</p>
        <p className="manifesto-line layer" aria-label={MANIFESTO.join(' ')}>
          {words.map((line, li) => (
            <span key={li} className="ml" aria-hidden="true">
              {line.map(w => {
                const i = n++, lit = reduce ? 1 : clamp((p - 0.08 - (i / total) * 0.6) / 0.08)
                return <span key={i} className={w.startsWith('excellence') ? 'em' : ''} style={{ opacity: 0.18 + 0.82 * lit, transform: `translateY(${(1 - lit) * 4}px)` }}>{w} </span>
              })}
            </span>
          ))}
        </p>
        <p className="manifesto-after layer">every trial counted · every idea earned</p>
      </div>
    </section>
  )
}

const AFML = 'https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086'
const CREDITS: [string, React.ReactNode, string][] = [
  ['Switch', 'Chat rooms where AI agents and people work together. The platform this runs on.', 'https://switchagents.ai'],
  ['López de Prado', <>Advances in Financial Machine Learning: purged CV, CPCV, Deflated Sharpe, PBO, strategy failure. <q>The purpose of a backtest is to discard bad models, not to improve them.</q> <cite>CH. 11</cite></>, AFML],
  ['After42', '“Can AI Propose Interesting Research Questions?” The question behind the ranker.', 'https://after42.org/research/can-ai-propose-interesting-research-questions/'],
  ['IdeaTaste', 'arXiv 2611.03424. The method behind learning taste from outcomes.', 'https://arxiv.org/abs/2611.03424'],
]
function Credits() {
  return (
    <section className="credits paper">
      <div className="layer"><Label n="15">Acknowledgements</Label><Mask lines={['Standing on', 'other people’s work.']} /></div>
      <figure className="pull layer" data-reveal>
        <blockquote>
          <p className="pq">“Most discoveries in finance are false, due to multiple testing and selection bias.”</p>
          <p className="pq2">“…careless users will produce false discoveries at an ever-greater speed.” <cite>CH. 1</cite></p>
        </blockquote>
        <figcaption>MARCOS LÓPEZ DE PRADO · <a href={AFML}>ADVANCES IN FINANCIAL MACHINE LEARNING ↗</a> · CH. 1</figcaption>
      </figure>
      <ul className="layer">{CREDITS.map(([t, d, href], i) => <li key={t} data-reveal data-tilt><span className="mlabel">C{i + 1}</span>{href ? <a href={href}><b>{t} ↗</b></a> : <b>{t}</b>}<span>{d}</span></li>)}</ul>
    </section>
  )
}

function Footer() {
  const reduce = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
  return (
    <footer className="footer" data-beat="footer" data-theme="dark">
      <MeshGradient className="footer-bg" colors={['#000000', '#070B2A', '#141C6B', '#3A45C9']} distortion={0.7} swirl={0.25} speed={reduce ? 0 : 0.15} />
      <div className="footer-stage" data-stage aria-hidden="true" />
      <div className="footer-inner layer">
        <div className="footer-right">
          <p>Built on Switch. With thanks to López de Prado (AFML), After42 and IdeaTaste.</p>
          <p><a href={SWITCH_URL} target="_blank" rel="noopener">Open in Switch ↗</a> · <a href={FLOOR_URL} target="_blank" rel="noopener">Live board ↗</a> · <a href={GITHUB_URL} target="_blank" rel="noopener">GitHub ↗</a></p>
          <p className="fine">BSE PHOTO: NIYANTHA SHEKHAR, <a href="https://commons.wikimedia.org/w/index.php?curid=21583922">CC BY 2.0</a>. NOT INVESTMENT ADVICE; PAPER TRADING ONLY. © 2026</p>
        </div>
        <p className="giant" aria-hidden="true">{NAME}</p>
      </div>
    </footer>
  )
}

export default function App() {
  useReveal()
  useEffect(startMotion, [])
  return (
    <>
      <Canvas />
      <Nav />
      <main>
        <Hero />
        <Ticker />
        <Tangent />
        <Cheap />
        <Worse />
        <Ours />
        <BigMarquee words={['Propose', 'Prove', 'Retire', 'Fork']} />
        <Solution />
        <Paper />
        <Loop />
        <Gate />
        <Forks />
        <BigMarquee dark words={['Count every trial', 'No appeals']} />
        <Costs />
        <Decay />
        <Board />
        <Ticker dark />
        <Faq />
        <Manifesto />
        <Credits />
      </main>
      <Footer />
    </>
  )
}
