export const NAME = 'Deflate'
// The hero line. Its last word gets the strike and the live E[max SR] callout.
// Alternatives on the table: 'Count every trial.' · 'Prove it or retire it.' · 'Alpha decays. Prove it first.'
export const HEADLINE = 'Most backtests are luck.'
// Switch Gateway's default host port is 3000 (GATEWAY_HOST_PORT in Switch's .env.example).
export const SWITCH_URL: string = import.meta.env.VITE_SWITCH_URL || 'http://localhost:3000'
// The live leaderboard / research floor served by the swarm.
export const FLOOR_URL: string = import.meta.env.VITE_FLOOR_URL || 'http://127.0.0.1:8766'
export const GITHUB_URL = 'https://github.com/AtharvaMaskar/quant-swarms-switch-hackathon'

export const STAGES = [
  ['Propose', 'An agent posts a hypothesis in its own Switch room, a fresh thread in one Slack channel: what should work, and why.'],
  ['Freeze the spec', 'Universe, signal, costs file and holdout dates are locked before any code exists.'],
  ['Code', 'A coding agent writes the backtest against the frozen spec, nothing else.'],
  ['Verify', 'A second agent hunts look-ahead, leakage and missing costs. Patches stay in the thread as h12v2, h12v3, with a hard stop after four.'],
  ['The gate', 'Deterministic statistics, no model in the loop. Same inputs, same verdict, every time.'],
  ['Sign-off', 'The risk owner reads the evidence in the thread and says yes or no.'],
  ['Paper trading', 'Survivors trade on paper, with Indian costs and slippage applied.'],
  ['Decay watch', 'CUSUM and entropy alarms watch live returns for the edge fading.'],
  ['Retire', 'When an alarm trips, the strategy stops. No appeals.'],
  ['Learn and fork', 'What it taught us seeds new hypotheses, forked with lineage and trial count.'],
] as const

export const CHECKS = [
  { name: 'Spec frozen, hypothesis required', short: 'spec', rate: 0.9, note: 'No stated hypothesis and locked spec, no backtest.' },
  { name: 'Look-ahead probe', short: 'look-ahead', rate: 0.85, note: 'Shift future data into the past; a strategy that improves was peeking.' },
  { name: 'Purged, embargoed CPCV', short: 'CPCV', rate: 0.55, note: 'Combinatorial purged cross-validation with an embargo: many paths, no leakage.' },
  { name: 'PSR and DSR, family trials counted', short: 'DSR', rate: 0.35, note: 'Probabilistic and Deflated Sharpe, charged with every trial in the lineage.' },
  { name: 'Probability of backtest overfitting', short: 'PBO', rate: 0.55, note: 'How often the in-sample winner ranks below median out of sample.' },
  { name: 'Probability of strategy failure', short: 'PSF', rate: 0.65, note: 'Odds the precision falls short of what the payoff needs to break even.' },
  { name: 'Costs and return on costs', short: 'costs', rate: 0.6, note: 'Indian costs and slippage applied; the edge must clear execution costs by a margin.' },
  { name: 'Concentration and tails', short: 'tails', rate: 0.8, note: 'No result carried by a handful of days, names or one fat tail.' },
  { name: 'Exposure-matched null', short: 'null', rate: 0.5, note: 'Same market exposure, random timing. Being long in a rising market is not skill.' },
  { name: 'Robustness and ablation', short: 'ablation', rate: 0.7, note: 'Nudge the parameters, drop each component; the edge must not hang on one.' },
  { name: 'Synthetic exit check', short: 'exits', rate: 0.85, note: 'Exits replayed on synthetic paths, so the exit rule is not fitted to history.' },
  { name: 'Fixture calibration', short: 'fixtures', rate: 1, note: 'The gate itself is tested on no-skill strategies, which must fail. It filters nothing; it keeps the gate honest.' },
]

// Shared, mutable gate state: the Gate UI writes it, the swarm engine reads it every frame.
export const gate = { on: CHECKS.map(() => true) }
export const CANDIDATES = 200
// Shared loop stage: the Loop chapter decides it (scroll detents + idle auto-advance), the engine draws it.
export const loopState = { station: 0 }

export const BOARD = [
  { name: 'Live bot (the swarm’s own book)', status: 'reference', dsr: '—', trials: '—', days: 'since day 1', ref: true },
  { name: 'Buy and hold, NIFTY 50', status: 'reference', dsr: '—', trials: '—', days: 'benchmark', ref: true },
  { name: 'h12.1.2 · expiry momentum, banks', status: 'live, paper', dsr: '0.97', trials: '52', days: '23' },
  { name: 'h7.3 · overnight drift, PSU banks', status: 'live, paper', dsr: '0.96', trials: '18', days: '11' },
  { name: 'h31 · pairs, cement', status: 'live, paper', dsr: '0.95', trials: '9', days: '4' },
  { name: 'h4.2 · lunch-hour reversal', status: 'retired, CUSUM', dsr: '0.95', trials: '27', days: '19' },
  { name: 'h9.1 · expiry-week skew', status: 'retired, entropy', dsr: '0.96', trials: '14', days: '8' },
]

export const FAQ = [
  ['What is Switch’s role?', 'Switch is the platform; this page is only context. The research floor runs on Switch: one control channel watches the floor, every hypothesis gets a Switch room shown as a thread in that one Slack channel, forks open new threads that carry their lineage, and the risk owner signs off inside the thread. Agents and people share the same thread. Hundreds of strategies, one channel.'],
  ['Why can’t agents cheat the gate?', 'The gate is deterministic code, not a model. The spec and holdout are frozen before any code exists, a look-ahead probe runs on every backtest, every trial in a family is counted (forks inherit the count), patches stop at four, and the gate itself is calibrated on no-skill fixtures that must fail.'],
  ['Why do most strategies fail?', 'Because the best of many noisy trials looks good by luck alone: with 1,000 zero-skill trials the expected best Sharpe is 3.26 standard deviations. Our previous swarm proposed 259 candidates and promoted none. Its training winners inverted on the holdout, because being long in a rising market looked like skill.'],
  ['What data does it use?', 'Real Indian market data: NIFTY futures and spot at 1-minute bars, NIFTY options end-of-day from NSE bhavcopy, and India VIX, from 2022 to 2026. Research runs on 2022-07 to 2025-06; 2025-07 to 2026-09 is a holdout each strategy may touch once. The animations on this page are illustrative. The real results are on the research floor: in today’s run the agents tested 8 strategies on this data and the gate rejected all 8.'],
  ['How are Indian costs modelled?', 'One dated, configurable file: Groww brokerage of ₹20 per F&O order, STT of 0.05% on futures and 0.15% on options from 2026-04-01, exchange and SEBI fees, stamp duty, 18% GST and slippage. A rate change is a one-line edit with a date on it.'],
  ['Can I give it a research paper?', 'Yes. Drop a link in the control channel, for example deflate implement arxiv.org/abs/2103.09692. It opens a thread for the paper, reads it (arXiv equations exactly, from the LaTeX source), pulls memory of similar past hypotheses (what died and why, what is live), brainstorms with you how it maps to Indian markets, and @reader propose turns it into a strategy card that enters the loop. You get a tested strategy, or a reason it won’t work.'],
  ['Is this live trading?', 'No. Survivors trade on paper only, with costs and slippage applied. Nothing places real orders.'],
]
