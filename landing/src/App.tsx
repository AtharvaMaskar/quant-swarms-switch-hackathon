import { useEffect, useRef, useState } from 'react'
import { MeshGradient } from '@paper-design/shaders-react'
import { start } from './engine.ts'
import { expectedMaxSharpe } from './stats.ts'
import { NAME, SWITCH_URL, GITHUB_URL, STAGES, CHECKS, gate, CANDIDATES, BOARD, FAQ } from './data.ts'
import { useBeat, useCountUp, useInView, useReveal } from './hooks.ts'
import { Hero, Nav, Ticker } from './Hero.tsx'

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
function Pin({ beat, id, vh = 220, p, children, flip = false, theme }: { beat: string; id?: string; vh?: number; p: React.RefObject<HTMLElement | null>; children: React.ReactNode; flip?: boolean; theme: Theme }) {
  return (
    <section className={`pin ${theme} dots ${flip ? 'flip' : ''}`} data-beat={beat} data-theme={theme} data-pin id={id} ref={p as React.RefObject<HTMLElement>} style={{ height: `${vh}vh` }}>
      <div className="sticky">
        <div className="pin-text layer">{children}</div>
        <div className="pin-stage" data-stage aria-hidden="true" />
      </div>
    </section>
  )
}

function Cheap() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const max = typeof innerWidth !== 'undefined' && innerWidth < 760 ? 380 : 1200
  const n = Math.round(12 + (max - 12) * Math.pow(clamp(p / 0.85), 1.6))
  const life = p < 0.33 ? 'MONTHS' : p < 0.66 ? 'WEEKS' : 'DAYS'
  return (
    <Pin beat="cheap" id="problem" p={ref} theme="paper">
      <Label n="01">The problem</Label>
      <p className="speaker" data-reveal>I’m an AI engineer building ML and quant algorithms for retail and HNI investors at a fintech.</p>
      <Mask lines={['Strategies', 'got cheap.', <>Alpha got <em>scarce.</em></>]} />
      <div className="spec-panel" data-reveal>
        <div className="sp-row"><span>STRATEGIES ON THE FLOOR</span><b className="tab">{n.toLocaleString('en-IN')}</b></div>
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
    <Pin beat="worse" p={ref} flip theme="dark">
      <Label n="02">The problem, multiplied</Label>
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
    <Pin beat="ours" p={ref} vh={200} theme="paper">
      <Label n="03">Our own swarm</Label>
      <Mask lines={['Found', 'nothing.']} />
      <div className="huge-num" data-reveal><b className="tab">{standing}</b><span>{standing ? `OF 259 STILL STANDING ON THE HOLDOUT` : '259 PROPOSED · 0 PROMOTED'}</span></div>
      <p className="body" data-reveal>Its training winners inverted on the holdout. Being long in a rising market had looked like skill.</p>
    </Pin>
  )
}

const FLOOR = [
  ['A control room', 'One room watches the whole floor: what is open, what is on paper, which alarms tripped, every family’s trial count.', 0],
  ['A room per hypothesis', 'Proposal, frozen spec, code, patches (h12v2, h12v3) and the gate verdict live in one thread.', 0.12],
  ['Fork rooms, linked', 'A fork opens a new room, h12.1, linked to its parent and carrying the family’s trial count.', 0.44],
  ['Sign-off in the room', 'Nothing reaches paper until the risk owner reads the evidence there and approves it.', 0.76],
] as const
function Solution() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const cur = FLOOR.reduce((k, f, i) => (p >= (f[2] as number) ? i : k), 0)
  return (
    <Pin beat="solution" id="solution" p={ref} theme="dark">
      <Label n="04">The solution</Label>
      <Mask lines={['So the floor', 'moved into', 'Switch.']} />
      <p className="body" data-reveal>This page is context. The product lives in Switch, where agents and people share chat rooms.</p>
      <ol className="rows" data-reveal>
        {FLOOR.map(([t, d], i) => <li key={t} className={i === cur ? 'on' : i < cur ? 'done' : ''}><span className="rn tab">0{i + 1}</span><div><b>{t}</b><span>{d}</span></div></li>)}
      </ol>
      <div className="btns" data-reveal><a className="btn solid" href={SWITCH_URL}>Open the research floor in Switch ↗</a></div>
    </Pin>
  )
}

const CHAT: [string, React.ReactNode, number][] = [
  ['researcher', <><kbd>@reader</kbd> <kbd>implement</kbd> arxiv.org/abs/2611.03424</>, 0],
  ['@reader', 'Opened #lit-arxiv-2611-03424. Read it. Memory: two similar ideas died (costs, decay), one is live.', 0.22],
  ['researcher', 'Weekly expiries and options STT change this in India. Try bank options?', 0.46],
  ['researcher', <><kbd>@reader</kbd> <kbd>propose</kbd></>, 0.68],
  ['@reader', 'Strategy card h43 created from the paper. Entering the loop.', 0.8],
]
function Paper() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  return (
    <Pin beat="paper" p={ref} flip theme="paper">
      <Label n="05">Bring a paper</Label>
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

function Loop() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  const active = Math.min(9, Math.floor(clamp(p / 0.95) * 10))
  return (
    <Pin beat="loop" id="loop" p={ref} vh={260} theme="dark">
      <Label n="06">The loop</Label>
      <Mask lines={['Agents propose.', 'Code decides.']} />
      <div className="now" data-reveal aria-live="polite">
        <span className="mlabel tab">STAGE {String(active + 1).padStart(2, '0')} / 10</span>
        <b>{STAGES[active][0]}</b><span className="desc">{STAGES[active][1]}</span>
      </div>
    </Pin>
  )
}

function Gate() {
  const [on, setOn] = useState(() => [...gate.on])
  const [ref, seen] = useInView<HTMLDivElement>(0.4)
  const survivors = CHECKS.reduce((n, c, i) => (on[i] ? n * c.rate : n), CANDIDATES)
  const shown = useCountUp(survivors, seen, 900)
  function toggle(i: number) {
    const next = on.map((v, j) => (j === i ? !v : v))
    gate.on = next; setOn(next); dispatchEvent(new Event('gatechange'))
  }
  return (
    <section className="gate paper dots" id="gate" data-beat="gate" data-theme="paper">
      <div className="gate-text layer">
        <Label n="07">The gate</Label>
        <Mask lines={['Twelve checks.', 'Switch any off.']} />
        <p className="body" data-reveal>Deterministic code, no model in the loop. Each band catches what fails it. Pass rates are illustrative; the checks follow López de Prado’s <em>Advances in Financial Machine Learning.</em></p>
        <div className="tally" ref={ref} data-reveal>
          <b className="tab">{Math.round(shown)}</b>
          <span>OF {CANDIDATES} CANDIDATES<br />REACH PAPER TRADING</span>
        </div>
        <div className="console" data-ui>
          <div className="console-head"><span>CHECK</span><span>KEEPS</span><span>ON</span></div>
          <ul>
            {CHECKS.map((c, i) => (
              <li key={c.name} className={on[i] ? 'on' : ''}>
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
      <Label n="08">Rooms and forks</Label>
      <Mask lines={['Forks inherit', 'the count.']} />
      <div className="huge-num" data-reveal><b className="tab">41</b><span>TRIALS h12.1 IS CHARGED WITH. NEVER 1.</span></div>
      <table className="lineage" data-reveal>
        <tbody>
          <tr><td>h12</td><td>hypothesis room</td><td>NEW</td></tr>
          <tr><td>h12v2</td><td>patch, same room · max 4</td><td>PATCH</td></tr>
          <tr className={p > 0.5 ? 'on' : ''}><td>h12.1</td><td>fork, new room, linked</td><td>FORK</td></tr>
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
        <Label n="09">Indian costs</Label>
        <Mask lines={['Costs,', 'dated.']} />
        <p className="body" data-reveal>A backtest without STT is a story. Every trade pays brokerage, securities transaction tax, exchange and SEBI fees, stamp duty and GST, plus slippage. They live in one dated file the gate reads, so a rate change is a one-line edit with a date on it.</p>
        <figure className="photo" data-reveal>
          <img src="/img/bse-front.jpg" width={1600} height={1339} loading="lazy" alt="The BSE building in Mumbai with its red sign, framed by palm trees." />
          <figcaption>BSE, DALAL STREET · PHOTO NIYANTHA SHEKHAR · CC BY 2.0</figcaption>
        </figure>
      </div>
      <div className="code-card layer" data-reveal data-ui>
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
      <Label n="10">Decay</Label>
      <Mask lines={['Decay', 'retires it.']} />
      <ol className="rows tight" data-reveal>{DECAY.map(([t, at], i) => <li key={t} className={p >= at && (i === DECAY.length - 1 || p < DECAY[i + 1][1]) ? 'on' : p >= at ? 'done' : ''}><span className="rn tab">0{i + 1}</span><div><b>{t}</b></div></li>)}</ol>
      <p className="body" data-reveal>A CUSUM detector and an entropy check watch live returns. When the edge fades past the threshold, the strategy stops, and what it taught the swarm goes back into the loop.</p>
      <p className="note" data-reveal>TASTE, LEARNED: A RANKER SCORES NEW PROPOSALS AGAINST WHAT EARLIER ONES BECAME. AFTER AFTER42 AND IDEATASTE.</p>
    </Pin>
  )
}

function Board() {
  return (
    <section className="board paper">
      <div className="board-head layer">
        <div><Label n="11">Leaderboard</Label><Mask lines={['The board.']} /></div>
        <p className="note" data-reveal>ILLUSTRATIVE ROWS. NO REAL STRATEGY RESULTS. THE LIVE BOT AND BUY-AND-HOLD ARE REFERENCE ROWS.</p>
      </div>
      <div className="table-wrap layer" data-reveal data-ui>
        <table>
          <thead><tr><th>ID</th><th>Strategy</th><th>Status</th><th className="num">DSR</th><th className="num">Trials</th><th className="num">Days</th></tr></thead>
          <tbody>
            {BOARD.map(r => (
              <tr key={r.name} className={r.ref ? 'ref' : r.status.startsWith('retired') ? 'retired' : 'live'}>
                <td className="id">{r.ref ? 'REF' : r.name.split(' · ')[0]}</td>
                <td>{r.ref ? r.name : r.name.split(' · ')[1]}</td>
                <td><span className="status">{r.status.toUpperCase()}</span></td>
                <td className="num tab">{r.dsr}</td><td className="num tab">{r.trials}</td><td className="num tab">{r.days}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Faq() {
  return (
    <section className="faq dark dots" id="faq">
      <div className="layer"><Label n="12">FAQ</Label><Mask lines={['Questions', 'from the', 'judges’ table.']} /></div>
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

const MANIFESTO = 'AI exists, so we can explore the extreme ends of human creativity.'.split(' ')
function Manifesto() {
  const ref = useRef<HTMLElement>(null), p = useBeat(ref)
  return (
    <section className="manifesto electric" data-beat="manifesto" data-theme="electric" data-pin ref={ref} style={{ height: '220vh' }}>
      <div className="sticky">
        <p className="mlabel layer">13 / WHY</p>
        <p className="manifesto-text layer">
          {MANIFESTO.map((w, i) => <span key={i} className={`${p > (i / MANIFESTO.length) * 0.7 + 0.05 ? 'on' : ''} ${w.startsWith('creativity') ? 'it' : ''}`}>{w} </span>)}
        </p>
      </div>
    </section>
  )
}

const CREDITS = [
  ['Switch', 'Chat rooms where AI agents and people work together. The platform this runs on.', 'https://switchagents.ai'],
  ['López de Prado', 'Advances in Financial Machine Learning: purged CV, CPCV, Deflated Sharpe, PBO, strategy failure.', ''],
  ['After42', '“Can AI Propose Interesting Research Questions?” The question behind the ranker.', ''],
  ['IdeaTaste', 'arXiv 2611.03424. The method behind learning taste from outcomes.', 'https://arxiv.org/abs/2611.03424'],
]
function Credits() {
  return (
    <section className="credits paper">
      <div className="layer"><Label n="14">Credits</Label><Mask lines={['Standing on', 'other people’s work.']} /></div>
      <ul className="layer">{CREDITS.map(([t, d, href], i) => <li key={t} data-reveal><span className="mlabel">C{i + 1}</span>{href ? <a href={href}><b>{t} ↗</b></a> : <b>{t}</b>}<span>{d}</span></li>)}</ul>
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
          <p><a href={GITHUB_URL}>GitHub ↗</a> · <a href={SWITCH_URL}>Open in Switch ↗</a></p>
          <p className="fine">BSE PHOTO: NIYANTHA SHEKHAR, <a href="https://commons.wikimedia.org/w/index.php?curid=21583922">CC BY 2.0</a>. NOT INVESTMENT ADVICE; PAPER TRADING ONLY. © 2026</p>
        </div>
        <p className="giant" aria-hidden="true">{NAME}</p>
      </div>
    </footer>
  )
}

export default function App() {
  useReveal()
  return (
    <>
      <Canvas />
      <Nav />
      <main>
        <Hero />
        <Ticker />
        <Cheap />
        <Worse />
        <Ours />
        <Solution />
        <Paper />
        <Loop />
        <Gate />
        <Forks />
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
