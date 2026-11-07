import { useEffect, useState } from 'react'
import { NAME, HEADLINE, SWITCH_URL, GITHUB_URL } from './data.ts'
import { useInView, useTyping } from './hooks.ts'
import { expectedMaxSharpe } from './stats.ts'
import { Assemble, Odometer } from './fx.tsx'

const LINKS = [['Problem', 'problem'], ['Loop', 'loop'], ['Gate', 'gate'], ['Threads', 'rooms'], ['FAQ', 'faq']] as const

export function Nav() {
  const [active, setActive] = useState('')
  useEffect(() => {
    const io = new IntersectionObserver(es => {
      for (const e of es) if (e.isIntersecting) setActive(e.target.id)
    }, { rootMargin: '-45% 0px -50% 0px' })
    LINKS.forEach(([, id]) => { const el = document.getElementById(id); if (el) io.observe(el) })
    return () => io.disconnect()
  }, [])
  return (
    <nav className="nav" aria-label="Main">
      <a className="nav-mark" href="#top">{NAME}<span className="nav-tag">on Switch</span></a>
      <ul className="nav-links">
        {LINKS.map(([label, id]) => <li key={id}><a href={`#${id}`} className={active === id ? 'on' : ''}>{label}</a></li>)}
      </ul>
      <a className="nav-cta" href={SWITCH_URL}>Open in Switch ↗</a>
    </nav>
  )
}

const I = {
  fork: <path d="M6 3v6a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3v6M6 3a2 2 0 1 0 0 .1M18 21a2 2 0 1 0 0-.1M6 9v12" />,
  collapse: <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />,
  share: <path d="M12 3v12M7 8l5-5 5 5M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6" />,
  panel: <path d="M4 4h16v16H4zM15 4v16" />,
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
}
const Icon = ({ d, label }: { d: keyof typeof I; label: string }) => (
  <button className="ico" aria-label={label} title={label}>
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="square">{I[d]}</svg>
  </button>
)

// The app frame shows a REAL run (7 Nov 2026, #deflate-control on Slack), lightly trimmed.
// One channel; every hypothesis or paper is a thread in it.
const RAIL = [
  { id: 'h2', name: '🧪 h2 · vix-gated-drift', meta: 'PATCH 1/4' },
  { id: 'paper', name: '📄 arxiv-2103-09692 · Radical Complexity', meta: 'READER BRAINSTORMING' },
  { id: 'h1', name: '🧪 h1 · late-day-momentum', meta: 'PROPOSED' },
]

/** The real gate verdict for h2, as a printed, perforated receipt with a stamp. */
export function Receipt({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`receipt ${compact ? 'compact' : ''}`} data-ui data-pop data-tilt>
      <p className="r-head"><span>GATE FAIL</span><span>h2</span></p>
      <p className="r-sub">11 of 15 checks failed · deterministic</p>
      <dl>
        <div><dt>Deflated Sharpe</dt><dd>0.43 <i>/ 0.95</i></dd></div>
        <div><dt>PSR</dt><dd>0.49</dd></div>
        <div><dt>PBO</dt><dd>0.87</dd></div>
        <div><dt>CPCV</dt><dd>N=12 <i>median SR −0.50</i></dd></div>
        <div><dt>Return on costs</dt><dd>0.97× <i>needs 3×</i></dd></div>
        <div><dt>Bets · net</dt><dd>343 <i>−₹0.3L</i></dd></div>
      </dl>
      <p className="r-foot">REAL RUN · 7 NOV 2026</p>
      <span className="stamp fail" aria-label="Fail">FAIL</span>
    </div>
  )
}

function Patches({ used, of }: { used: number; of: number }) {
  return (
    <div className="patches" aria-label={`Patch ${used} of ${of}`}>
      <span className="mlabel">PATCHES</span>
      {Array.from({ length: of }, (_, i) => <i key={i} className={i < used ? 'on' : ''} />)}
      <b className="tab">{used} / {of}</b>
    </div>
  )
}

function App() {
  return (
    <div className="app" data-ui data-pop>
      <div className="app-bar">
        <span className="app-title"><span className="dim">Switch /</span> #deflate-control <span className="chip">1 CHANNEL · THREADS PER HYPOTHESIS</span></span>
        <span className="app-icons"><Icon d="fork" label="Fork" /><Icon d="collapse" label="Collapse" /><Icon d="share" label="Share" /><Icon d="panel" label="Panel" /><Icon d="more" label="More" /></span>
      </div>
      <div className="app-body">
        <aside className="rail">
          <p className="mlabel">THREADS</p>
          <ul>
            {RAIL.map((r, i) => (
              <li key={r.id} style={{ '--i': i } as React.CSSProperties} className={r.id === 'h2' ? 'cur' : ''}>
                <span className="rn">{r.name}</span><span className="rm">{r.meta}</span>
              </li>
            ))}
          </ul>
        </aside>
        <div className="thread">
          <Msg who="orchestrator" kind="agent">🧪 h2 · vix-gated-drift: research thread for h2</Msg>
          <Msg who="coder" kind="agent">spec written: the buy-and-hold reference plus one gate, long at 09:21 only when yesterday’s VIX close is above p.vthr <span className="chip">deflate freeze h2 specs/h2.yaml</span></Msg>
          <Msg who="orchestrator" kind="agent">spec frozen · sha 2cee9836 · schema ok</Msg>
          <Msg who="verifier" kind="agent">spec matches card</Msg>
          <div className="msg"><span className="av code">G</span><div><p className="who">gate</p><div className="receipt-wrap"><Receipt compact /></div></div></div>
          <Msg who="coder" kind="agent">patch 1/4 → h2v2: VIX above its own 250-day median instead of a fixed 14</Msg>
          <Msg who="orchestrator" kind="agent"><span className="refused">freeze refused: a patch must change exactly one rule; h2v2 changes 7 fields</span></Msg>
          <Msg who="orchestrator" kind="agent"><span className="chip fix">AFTER FIX</span> rule-level check: 1 rule changed (entry.long) · <span className="accepted">accepted</span></Msg>
          <Patches used={1} of={4} />
        </div>
      </div>
    </div>
  )
}

function Msg({ who, kind, time, children }: { who: string; kind: 'agent' | 'human'; time?: string; children: React.ReactNode }) {
  return (
    <div className="msg">
      <span className={`av ${kind}`}>{who[0].toUpperCase()}</span>
      <div><p className="who">{kind === 'agent' ? '@' : ''}{who} {time && <span className="time">{time}</span>}</p><p>{children}</p></div>
    </div>
  )
}

const TERM = [
  '> deflate // gate h2',
  'Status   : Connected (switch agent stream)',
  'Trials   : 1 in family · N_eff 1.7',
  'CPCV     : N=12 · median SR −0.50',
  'Verdict  : FAIL (DSR 0.43 < 0.95) → patch 1/4',
  '> deflate freeze h2v2',
  'Freeze   : accepted · 1 rule changed (entry.long)',
]
function Terminal() {
  const [ref, seen] = useInView<HTMLDivElement>(0.5)
  const typed = useTyping(TERM, seen)
  return (
    <div className="term" ref={ref} data-ui aria-label={TERM.join('. ')}>
      <div className="term-bar"><i /><i /><i /><span>deflate — real run · 7 Nov 2026</span></div>
      <pre aria-hidden="true">{typed.map((l, i) => l && <span key={i} className={l.startsWith('>') ? 'cmd' : l.startsWith('Verdict') ? 'bad' : l.startsWith('Freeze') && l.length === TERM[6].length ? 'ok' : ''}>{l}{'\n'}</span>)}<span className="caret" /></pre>
    </div>
  )
}

type Stat = { trials: number; passes: number; roots: number; forks: number; luck: number }
function useStat(): Stat {
  const [stat, setStat] = useState<Stat>({ trials: 0, passes: 0, roots: 0, forks: 0, luck: 0 })
  useEffect(() => {
    const on = (e: Event) => setStat((e as CustomEvent<Stat>).detail)
    addEventListener('trials', on)
    return () => removeEventListener('trials', on)
  }, [])
  return stat
}

/** The swarm board, drawn as a chart: Sharpe on the y-axis, the luck line across it. */
function Board() {
  return (
    <div className="led" data-ui data-pop>
      <div className="led-bar"><span><b className="live-dot" /> ILLUSTRATIVE SWARM</span><span>EACH CLUSTER IS A HYPOTHESIS, PLOTTED AT ITS BACKTEST SHARPE</span></div>
      <div className="led-stage" data-stage aria-hidden="true" />
      <div className="led-foot"><span>■ TESTING</span><span className="g">■ ABOVE LUCK → PASS, FORKS</span><span className="r">■ BELOW LUCK → FAIL, DROPS</span></div>
    </div>
  )
}

/** Four hard-ruled cells under the hero, live from the board. */
function StatBar({ stat }: { stat: Stat }) {
  const cells: [string, string, string][] = [
    ['TRIALS', String(stat.trials).padStart(3, '0'), 'illustrative swarm · forks included'],
    ['LUCK ALONE', stat.luck.toFixed(2), 'σ · E[max SR] of zero-skill trials'],
    ['PASS RATE', `${stat.passes}/${stat.trials}`, 'rooms that beat luck'],
    ['FAMILIES', String(stat.roots).padStart(2, '0'), 'hypotheses, before forks'],
  ]
  return (
    <div className="statbar layer" aria-label="Illustrative swarm statistics">
      {cells.map(([k, v, d]) => (
        <div key={k} className="cell"><span className="mlabel">{k}</span><b className="tab"><Odometer value={v} /></b><span className="cd">{d}</span></div>
      ))}
    </div>
  )
}

/** The headline. Its last word is struck through and carries a live chart callout tied to the board's trial count. */
function Headline({ trials, luck }: { trials: number; luck: number }) {
  const words = HEADLINE.split(' ')
  const last = words.pop()!
  let i = 0
  return (
    <h1 className="mega assemble" aria-label={HEADLINE}>
      {words.map(w => <span key={w}><span className="wd" aria-hidden="true"><Assemble text={w} start={(i += w.length) - w.length} /></span>{' '}</span>)}
      <span className="wd struck" aria-hidden="true">
        <Assemble text={last} start={i} />
        <span className="callout tab">E[max SR] = {(luck || expectedMaxSharpe(2)).toFixed(2)}σ @ {Math.max(trials, 2).toLocaleString('en-IN')} trials</span>
      </span>
    </h1>
  )
}

const TICKS = [
  ['h12v5', 'FAIL', 'DSR 0.71'], ['h7.3', 'PAPER', 'DAY 11'], ['h40', 'FROZEN', 'SPEC 2c81'], ['h12.1', 'FORKED', '41 TRIALS'],
  ['h4.2', 'RETIRED', 'CUSUM'], ['h43', 'PROPOSED', 'ARXIV 2611.03424'], ['h31', 'PASS', 'DSR 0.95'], ['h44', 'FAIL', 'LOOK-AHEAD'],
  ['h9.1', 'RETIRED', 'ENTROPY'], ['h45', 'FAIL', 'PBO 0.62'], ['h46', 'FAIL', 'NULL BEATS IT'], ['h12.1.2', 'PAPER', 'DAY 23'],
]
export function Ticker({ dark = false }: { dark?: boolean }) {
  const row = TICKS.map(([id, st, m], i) => (
    <span key={i} className="tick"><b>{id}</b><span className={`st ${st === 'FAIL' || st === 'RETIRED' ? 'r' : st === 'PASS' || st === 'PAPER' ? 'g' : ''}`}>{st}</span>{m}</span>
  ))
  return (
    <div className={`ticker ${dark ? 'dark' : ''}`} aria-label="Illustrative feed of gate verdicts">
      <div className="ticker-track" aria-hidden="true"><span className="tick lead">ILLUSTRATIVE FEED</span>{row}<span className="tick lead">ILLUSTRATIVE FEED</span>{row}</div>
    </div>
  )
}

export function Hero() {
  const stat = useStat()
  return (
    <header className="hero dark dots" data-beat="hero" data-theme="dark" id="top">
      <div className="hero-fold">
        <div className="hero-left layer">
          <p className="mlabel">00 / {NAME.toUpperCase()} — A RESEARCH FLOOR ON SWITCH</p>
          <Headline trials={stat.trials} luck={stat.luck} />
          <p className="pos" data-reveal>The research floor for agentic quant teams.</p>
          <p className="lead" data-reveal>{NAME} is a research swarm where agents propose, code decides, and humans sign off, running in Switch rooms. Self-hosted and open-source.</p>
          <div className="btns" data-reveal>
            <a className="btn solid" href={SWITCH_URL}>Open in Switch ↗</a>
            <a className="btn line" href={GITHUB_URL}>GitHub</a>
          </div>
        </div>
        <div className="hero-right layer"><Board /></div>
      </div>
      <StatBar stat={stat} />
      <div className="hero-app layer" data-reveal><p className="mlabel real-tag">REAL RUN · 7 NOV 2026 · #DEFLATE-CONTROL ON SLACK</p><App /><Terminal /></div>
    </header>
  )
}
