# Quant research swarm on Switch: design

Status: draft for approval · 2026-11-07 · working name TBD (landing-page agent is proposing names)
Hackathon: WeMakeDevs "Flip the Switch", Track 01 (Business Workflow). Submissions close **2026-11-07 23:59 IST**: repo + demo video + README.

---

## 1. Intent

Strategy code is now nearly free, so edges are scarce and decay in days or weeks instead of months. AI swarms make this worse: every extra trial raises the best backtest you can get by luck alone (López de Prado, AFML §11.3, Ch 14), so a leaderboard of mass-generated strategies mostly ranks noise. The bottleneck has moved from producing ideas to **deciding honestly and quickly which ideas are real, and noticing when a real one stops working**.

Evidence from the user's previous swarm (`~/projects/quant/quant-research-v0/qr`, inspiration only, no copy-paste): 259 candidates, 0 promoted. Train rankings inverted on the holdout (the long-bias × market-drift trap). The holdout was spent on 10 contenders at once. 728 earlier trials were never counted in the deflation. Agents could reach and rewrite the rules. The overfit checks had holes (extra exposure passed 36% of the time). The swarm drifted into building statistics tools instead of testing strategies.

**The product:** a continuous research loop where **agents propose and write; code they cannot touch decides; a human signs off; live paper trading is the real test; decay retires strategies, and what was learned forks new hypotheses.** Switch is the backbone: every hypothesis is a room, agents and the human work in those rooms, and room lineage is the visible research tree.

## 2. Goals, non-goals, anti-goals

**Goals**
- A deterministic validation gate implementing the AFML rules in §7, all as scripts with thresholds in a config file.
- A Switch-native loop: control room + one room per hypothesis + fork rooms linked to parents + human sign-off in the room.
- A patch loop (diagnose → one-rule patch → re-validate) with a hard stop decided by code.
- A dated, fully configurable cost and slippage model for Indian markets (§9).
- Memory and a ranker that learn from outcomes (After42 + IdeaTaste lessons, §8).
- Promotion of passed strategies to real-time paper trading with decay monitors (§11).
- A leaderboard with the reference rows (live bot, buy-and-hold).

**Non-goals**
- Real-money order placement.
- Intraday option strategies (options are end-of-day only for now).
- Survivorship-free stock universe (current F&O list only; a stated limitation).
- Our own chat UI (Switch rooms bridged to a chat app are the UI).

**Anti-goals (a failure even if it "works")**
- Any path where an LLM's opinion decides whether a strategy is real.
- Agents able to edit the gate, gate config, engine, data or DB triggers.
- A trial that runs but isn't counted. Any fork that resets its family's trial count.
- Patches fitted to the months they fix that look better only there.
- Touching the holdout more than once per strategy.
- A swarm busy building tools instead of testing strategies (measure-only scripts never count as trials and never pass through the trial runner).

## 3. Constraints

- **Time:** demo by tonight. §13 fixes what ships tonight vs later.
- **Switch is provisioning + chat, not a workflow engine.** YAML templates (v2) create agents, rooms, roles, docs and references, then post one kickoff. There are no steps, triggers, schedules or loops, and the task ops aren't ready. Hand-off happens by @mention. So the deterministic orchestrator is **our own process, registered as a Switch agent** (`POST /agents/register-known`, SSE `GET /agents/{id}/events?filter=addressed`, ops via `POST /agents/{id}/ops/{op}`).
- **Switch limits:**
  - One role per agent globally.
  - Room docs are 1 MiB with no versioning, and only their author can edit them.
  - Agents can't create room groups (admin only), but can `link_rooms`.
  - **Loop guard:** an identical kickoff text from the same agent pauses the run, so every kickoff includes `{hyp_id}` and `{round}`.
  - Agents run only while Console or the host is awake.
  - Each agent is its own provider session (cost).
- **Data:** 2022-01 → 2026-09 only (§10).

## 4. Architecture

```
            ┌───────────────────── Switch workspace ─────────────────────┐
            │  #control  (orchestrator, proposers, judge, human)          │
            │     │ create_room_from_yaml per hypothesis                  │
            │     ▼                                                       │
            │  #h12-expiry-mom ──link "forked from"──► #h12-1-…-volgate   │
            │  (coder, verifier, validator, human; spec doc; repo ref)     │
            │  #paper-h12-1v3  (decay alerts, P&L summaries)              │
            └──────────────▲───────────────────────────────▲─────────────┘
                           │ agent protocol (HTTP + SSE)   │
   ┌───────────────────────┴──────────┐        ┌───────────┴───────────┐
   │ Orchestrator (Python, our code)  │        │ Paper desk (Python)   │
   │ state machine, budgets, naming,  │        │ Choice TBT feed (RPA  │
   │ hard stops, kickoffs, archiving  │        │ token), same engine,  │
   └──────┬───────────────┬───────────┘        │ decay monitors        │
          │               │                    └───────────┬───────────┘
   ┌──────▼─────┐  ┌──────▼──────────────┐  ┌──────────────▼──────────┐
   │ Engine     │  │ Gate (read-only to  │  │ SQLite research.db      │
   │ spec→bars→ │  │ agents; hashed      │  │ append-only log, trials,│
   │ fills→PnL  │  │ config)             │  │ families, memory, board │
   └────────────┘  └─────────────────────┘  └─────────────────────────┘
```

**Deterministic (our code):**
- the orchestrator state machine
- the engine
- the gate
- the cost model
- the DB (append-only via triggers; specs write-once after the first trial)
- the trial runner (the only writer of trials)
- the leaderboard
- the paper desk
- decay monitors

**LLM:** proposing, critiquing and ranking ideas, writing specs, explaining results, and writing post-mortems.

Agents run with permissions that deny writes to `engine/`, `gate/`, `config/gate*.yaml`, `data/` and `research.db`. The gate also checks the sha256 of its own code and config at start, records it in every verdict, and refuses to run if the hash doesn't match the one pinned at run start.

### Switch layout
- **Agents (Claude Code provider unless noted), one role each:**
  - `proposer-card`, `proposer-lead`, `proposer-contrarian`
  - `judge`: **Codex provider**, a different model family from the proposers (IdeaTaste)
  - `coder`
  - `verifier`
  - `validator`: posts the gate report in plain language; it does *not* compute the verdict
  - `orchestrator`: our process
  - the human risk owner
- **Reuse.** Long-lived agents are reused across rooms. Scale by registering `coder-2` and so on, never by creating agents per round.
- **Control room.** Proposers, judge, orchestrator and the human. The leaderboard summary and round summaries are posted here.
- **Per-hypothesis room.** Created from a v2 room template with:
  - exclusive roles coder/verifier/validator
  - a frozen-spec room doc
  - a GitHub repo reference
  - `users: ["{$creator}"]`
  - kickoff `@coder {hyp_id} round {round}: spec frozen, build it.`
- **Fork room.** Same template, then `link_rooms(child → parent, label="forked from h12")`.
- **Paper room.** One per promoted strategy, for alerts and daily P&L.
- **Retirement.** The orchestrator archives the room on retire. Rooms show summaries; the DB and git hold the record.

### Naming
| Thing | ID (DB) | Room |
|---|---|---|
| Root hypothesis | `h12` | `h12-expiry-mom` |
| Patch (one-rule change, same room) | `h12v2` | same room |
| Fork (new hypothesis from h12's learnings) | `h12.1` | `h12-1-expiry-mom-volgate` |
| Fork of fork | `h12.1.2` | `h12-1-2-…` |
| Paper desk | `p-h12.1v3` | `paper-h12-1v3` |

Slugs are at most 3 words. Chat channels disallow dots, so rooms use hyphens. The hard stop (§7.4) keeps depth shallow.

## 5. The loop

1. **Propose (control room).** Proposers oversample candidates through three lenses:
   - `card`: an untried literature/library card
   - `lead`: recent post-mortems and learnings
   - `contrarian`: the losing periods of the reference rows

   Each candidate is a **five-slot card**: contribution type, problem, motivation, approach, plan. It also carries the rule sketch, the parameters with their sources, kill criteria, and the features/datasets used. A registry audit (code) drops any candidate that references features or datasets not in the registry.
2. **Filter (code + judge).** Nearest-neighbour match against memory (§8):
   - drop near-duplicates of dead ends (same *approach* slot)
   - run a cheap **sanity probe** on pre-research data. This is a fixed script that checks that the effect is larger than costs and stable at one neighbouring parameter. It doesn't count as a trial, and it never sees the research window.
3. **Develop → judge → critic.**
   - The proposer revises.
   - The judge (other model family) compares versions on explicit criteria. A revision is accepted only if the judge prefers it (After42 monotone gate).
   - A fresh critic agent repairs or drops the candidate.
4. **Rank → open rooms.** The ranker sets a prior from matched past outcomes (§8). The orchestrator opens rooms for the top `build_cap` candidates per round (default 2). Every trial raises everyone's bar.
5. **Freeze.** The coder writes the strategy spec (§6). The orchestrator hashes it and registers the hypothesis; the spec is write-once from then on. The family's trial counter is the root hypothesis's.
6. **Verify (code first, verifier second).** The verifier runs:
   - the spec schema check
   - the look-ahead probe (perturb future bars → identical past targets)
   - the determinism check
   - the fallback-function lint, if used (§6)

   It then confirms that the spec matches the card. Any failure goes back to the coder; the counter doesn't move until a trial runs.
7. **Gate (code).** The trial runner executes the spec once over the research window, logs the trial, and runs every gate test (§7). The validator posts the report in the room.
8. **Patch or stop (§7.4).** If the gate fails and the budget remains:
   - the coder diagnoses using training-fold information only
   - files one one-rule patch `h12v{n+1}` in the same room
   - repeat from step 6

   At the hard stop, the hypothesis is either a candidate for sign-off or a dead end. A dead end writes a post-mortem to memory, and may seed a fork (new room).
9. **Holdout (code, once).** For a gate pass: one touch of the holdout window (§10). The result is recorded whatever it is.
10. **Human sign-off (Switch).** The orchestrator posts the full report with an approve/reject request. The human answers in the room; an approval card is used where the agent runs in approval-required mode. Each decision is stored as calibration data for the ranker/judge (After42: human feedback raised judge–expert rank correlation 0.26→0.42).
11. **Promote → paper (§11).**
12. **Decay → retire → learn.** The monitor fires and the orchestrator retires the strategy and archives the room. The post-mortem goes to memory, and the learning may fork `h12.2`. The orchestrator posts `@proposer-lead round {N+1}` in the control room, and the loop continues.

The loop runs until `STOP` (a file or `!` command), the global budget, or the round cap.

## 5a. Paper intake: "implement this paper" (user story)

> A researcher sees a paper on arXiv or SSRN and tags it: "@reader can you implement this?" The agent and the human brainstorm, in Switch, how it could apply to our markets. The agent uses memory: past cards, dead ends, gate results, live and retired strategies. Together they formulate a strategy, and that kicks off the loop.

1. **Tag.** In `#control` (or any room) a human posts `@reader implement <arxiv|ssrn url or id>`, or attaches the PDF when SSRN blocks automated fetching.
2. **Literature room.**
   - The orchestrator creates `#lit-arxiv-2611-03424` (or `#lit-ssrn-4567890`) from `templates/lit-room.v2.yaml`, with the reader agent and the tagging human.
   - It links the room to `#control`.
   - The paper is stored as a reference with its URL, title and authors.
3. **Read.** The reader extracts a **paper card**:
   - the claimed effect
   - the mechanism
   - the data, market and frequency used
   - the reported results
   - the period
   - the obvious threats: data snooping in the paper, costs ignored, a different market microstructure
4. **Brainstorm, with memory.** Before proposing anything, the reader runs read-only memory queries:
   - `qs.cli memory search "<mechanism / approach>"`: the nearest past cards with outcomes
   - `qs.cli memory show h12`
   - `qs.cli memory deadends`
   - `qs.cli memory features`: the feature registry

   It posts what we already know: what's similar, what died and why, what's live. Then it discusses with the human how the idea maps to NIFTY futures, stocks or options EOD, which variants are worth one trial, and what would kill it. The human steers; the agent asks short questions. This is the After42/IdeaTaste lesson: the paper supplies the "what", and the conversation plus memory sharpen the "how".
5. **Formulate.** When the human says `@reader propose`, the reader writes one or more five-slot cards carrying `source: {type, id, url, title}`. It posts them as `@orchestrator propose <card>`.
   - From here it's the normal loop: filter → judge → rank → hypothesis room.
   - Each hypothesis room is linked to its `#lit-…` room with the label "from paper".
6. **Credit and memory.** The hypothesis keeps the source, so the leaderboard and memory show which papers produced live strategies and which died at the gate (paper → outcome). That becomes a "which literature actually survives" view.

Room prefixes stay distinct: `lit-` for papers, `paper-` for paper trading.

## 6. Strategy spec language

**Primary: a declarative YAML spec** interpreted by our engine. Agents fill specs; they never write engines.
```yaml
id: h12v2
instrument: {segment: futures, underlying: NIFTY, series: front}   # or equity / options (EOD)
bar: 1m
session: {start: "09:20", end: "15:15", flat_at_close: true}
features:                       # whitelist only (registry)
  ret_open: {fn: return, from: session_open}
  rv20:     {fn: realized_vol, window: 20d}
entry:
  long:  "ret_open > 0.003 and rv20 < q(rv20, 0.6, 250d) and dte <= 2"
exit:
  stop: {atr_mult: 1.5}
  take: {atr_mult: 3.0}
  time: "15:15"
sizing: {risk_per_trade: 0.01, max_lots: 4}
legs: []                        # options: [{type: CE, strike: ATM+1, qty: 1, side: sell}], EOD only
```
- **Why:**
  - Freeze = hash. "Exactly one rule changed" = structured diff (code).
  - Look-ahead is impossible by construction: the engine only passes bars up to t, and every feature fn is causal.
  - Specs are short enough for humans to review in the room.
- **Fallback:** for what the spec can't express, one pure Python function `signal(bars_upto_t, state) -> target_position`. It's linted by AST:
  - imports limited to numpy, pandas and math
  - no I/O, network, `eval`/`exec` or globals
  - it must pass the look-ahead probe and determinism check
- **Optional later:** a spec → Pine export for eyeballing on TradingView charts (viewing only, never validation).

## 7. The gate (AFML, all scripted)

Thresholds live in `config/gate.yaml`, which is versioned and hashed. Defaults below. Values marked *(call)* are mine, not the book's, and are configurable.

### 7.1 Before any backtest
1. **Hypothesis required (§1.3.1.3, sin 3).** The card must state a mechanism and kill criteria before the spec is frozen. No mechanism → reject.
2. **Spec frozen before the backtest (§11.4).** A failed spec is discarded, never tweaked in place; changes become patches (new trial) or forks.
3. **Bias checks (§11.2):**
   - look-ahead probe and determinism (code)
   - timestamps in IST with bars stamped at close
   - fills at the next bar's open plus slippage
   - holiday calendar and special sessions respected
   - point-in-time contract specs (lot/tick/expiry by date)
   - survivorship: stated limitation for stocks

### 7.2 Validation on the research window
4. **Every trial logged (§11.5, Third Law §14.7.3).** That includes failures and patches. A family = the root hypothesis plus all patches and forks. `N_eff` = independent trials clustered by return correlation (ρ̄ + (1−ρ̄)M approximation, or hierarchical clustering on trial PnL correlation).
5. **Purged + embargoed CV everywhere (§7.4).**
   - Label/holding windows `[t0, t1]` are required outputs of the engine.
   - Purge any training observation that overlaps a test window.
   - Embargo h = 0.01·T after each test block.
   - No shuffling.
   - Applies to every split: CPCV, PBO and any tuning.
6. **CPCV, not a single walk-forward (§12.4).** Use N=10 groups and k=2, which gives 45 splits and φ=9 backtest paths. Pass requires:
   - median path Sharpe > 0
   - ≥ 80% of paths with positive net P&L *(call)*
   - path Sharpe dispersion reported
   - Anti-gaming: N and k are drawn per run from {8,10,12} × {2} with a seed the agents never see *(call, from §12 "reviewer picks N,k")*.
7. **PSR and DSR (§14.7).** On non-annualised returns, including skew and kurtosis:
   - PSR(0) > 0.95
   - DSR > 0.95 using the family's `N_eff` and the variance of trial Sharpes
   - the global trial count across all families is also reported
8. **PBO by CSCV (§11.6).** Over the family's trial PnL matrix, with S=16 blocks (C(16,8)=12,870 combinations). PBO ≤ 0.10 *(call; the book gives no number)*. Families with fewer than 2 trials report PBO as n/a and need DSR alone.
9. **Probability of strategy failure (§15.4).**
   - Estimate π± and bet frequency.
   - Bootstrap precision over a 2-year evaluation horizon.
   - P[p < p_θ*] ≤ 0.05, with θ* = target annualised Sharpe 1.0 *(call)*.
10. **Costs and capacity (§11.2, §14.6).**
    - Full dated cost model (§9).
    - Return on execution costs ≥ 3× *(call; the book says "large multiple")*.
    - Gross and net both reported.
11. **Concentration and tails (§14.3, §14.5).**
    - HHI of positive and of negative returns ≤ 0.3 each *(call)*
    - top-5 days < 50% of net P&L, and net stays positive with them removed
    - 95th-percentile drawdown and time under water reported
    - max DD ≤ 20% and TuW ≤ 30% at a 15% annualised vol target (inherited from qr)
    - ≥ 50 bets
12. **Beats the right null (lesson from qr's drift trap).** It must beat:
    - buy-and-hold
    - the live-bot reference row
    - an **exposure-matched null**: the same long/short exposure time series circularly shifted, 1,000 shifts, strategy above the 95th percentile

    This kills "it's just long in a rising market".
13. **Robustness (§8, §11.5).**
    - Parameter neighbourhood: ±1 step on each parameter keeps CPCV median Sharpe > 0.
    - Ablation importance (rule-spec analogue of MDA): dropping each entry clause must hurt out-of-sample under purged CV. A clause whose removal doesn't hurt is flagged as storytelling.
    - For specs using an ML model: MDA and SFI under purged CV.
    - Breadth: if the mechanism is claimed general, it must hold on ≥ 1 related instrument (e.g. BANKNIFTY futures when data exists).
14. **Synthetic-data check for exit rules (Ch 13).**
    - Fit an O-U (or AR(1)) model to the instrument's research-window prices.
    - Simulate 10k paths.
    - The spec's stop/take/time-exit choice must sit in the top half of the 20×20 exit mesh on synthetic Sharpe.

    This flags exits tuned to the historical path *(call: flag, not hard fail, for tonight)*.

### 7.3 Gate calibration (before the gate is trusted)
**No-skill fixtures** are run through the full gate on every gate-config change:
- random entries at matched frequency
- pure long exposure
- a strategy with a planted look-ahead leak
- a lucky-seed strategy picked as the best of 100 random ones

The gate must reject ≥ 95% of each fixture. This is reported on the README and landing page. (Lesson: qr's gates passed no-skill fixtures and nobody knew until late.)

### 7.4 Patch loop and hard stop
- **Diagnosis** may use only training-fold information. The agent sees per-fold and per-month P&L of the CPCV training sets, never test folds or the holdout.
- **A patch** is a child `h12v{n}` with **exactly one** changed rule (structured diff; code enforces it). It is a new trial in the family, so DSR and PBO tighten automatically.
- **"Win where it wasn't aimed":** the patch must not lower CPCV median path Sharpe, and must improve or hold in ≥ 70% of folds it didn't target *(call)*. Otherwise it's rejected even if the total improves.
- **Hard stop** (code, whichever comes first):
  - patch budget of 4 per hypothesis
  - DSR is unreachable at the family's current `N_eff` (computed analytically)
  - 2 consecutive non-improving patches
  - per-hypothesis spend cap (Switch agent budgets plus the orchestrator counter)
- **Forks** inherit the family. A fork is a new room and a new hypothesis, but its trials count toward the root family's `N_eff`. Forking never resets the bar. Depth cap 3 *(call)*.

### 7.5 Holdout and lifecycle (§1.3.1.6)
- **Holdout:** touched once per strategy, after the gate passes. Pass = net > 0 and PSR(0) > 0.5 on the holdout *(call: it's short)*. The window is flagged "seen by a prior project" (§10).
- **Lifecycle:** embargo period (data after the backtest end) → **paper trading** → (out of scope: graduation, concave allocation) → **decommission on decay**.
- **Combining strategies:** accepted strategies are combined with HRP (Ch 16), never with in-sample mean-variance. Later; the leaderboard shows the HRP blend when ≥ 3 strategies are accepted.

## 8. Memory and ranker (After42 + IdeaTaste)

- **Append-only `messages` log** with kinds: proposal, critique, build, verify, result, analysis, decision, signoff, decay, postmortem.
- **`cards`:** five-slot schema per hypothesis, with outcome fields filled in over time:
  - gate verdict, DSR, PBO
  - holdout result
  - sign-off
  - paper P&L
  - days-to-decay
- **Ranker:**
  - For a new card, retrieve the 5 nearest past cards (BM25 + small embedding model, rank-fused).
  - The judge marks slot-level matches as JSON booleans.
  - Prior = rarity-weighted outcome of the matches. A matching *approach* weighs more than a matching *problem* (IdeaTaste slot weights).
  - A near-copy of a dead end gets downgraded.
  - Off-consensus ideas start at a low prior and must earn it through the gate. IdeaTaste found off-consensus AI ideas were weaker, contradicting the "hidden gem" view.
- **Diversity monitor:** the share of a round's proposal slots repeated by peers. A spike means collapse, and the orchestrator forces the contrarian/card lenses next round.
- **Calibration:** human sign-offs and paper outcomes are fed to the judge prompt as few-shot calibration.
- **Not done:** showing proposers the whole factor map (IdeaTaste "landscape" did worst).

## 9. Costs and slippage (`config/costs.yaml`, everything configurable)

- **Charges:**
  - A list of charge sets with `effective_from`. A trade uses the latest set with `effective_from` ≤ trade date. Later sets restate only what changed.
  - Fields per segment (equity delivery, intraday, futures, options) and exchange (NSE/BSE): brokerage, STT/CTT (side + base: turnover / premium / intrinsic on exercise), exchange txn, IPFT, SEBI fee, stamp (buy side), GST on (brokerage + exch + SEBI + IPFT), DP charge, rounding rules.
- **Brokerage: Groww.**
  - F&O ₹20 per executed order.
  - Equity: min(₹20, 0.1%) per order. *(Groww's help page says ₹20 for F&O; one pricing snippet shows ₹50. Verify at groww.in/pricing/futures-and-options before the demo; the config carries `verify: true`.)*
- **STT history:**
  - futures 0.01% → 0.0125% (2023-04-01) → 0.02% (2024-10-01) → 0.05% (2026-04-01)
  - options premium 0.05% → 0.0625% → 0.1% → 0.15% (same dates)
  - exercise 0.125% → 0.15% (2026-04-01)
  - The 2026 rates were seen in news/law-firm sources only; flagged.
- **Exchange charges:** NSE uniform rates from 2024-10-01 (futures 0.00173%, options 0.03503% of premium, cash 0.00297%). Earlier slabs are flagged unverified.
- **Contracts (dated per underlying):** lot size per expiry, tick size, freeze quantity (split orders above it), expiry weekday. Built from the reference-data pull (agent running).
- **Slippage models** (selectable per segment):
  - `ticks`, `pct`, `max(...)`
  - `half_spread` for live quotes
  - `sqrt_impact` (k·σ·√(Q/ADV)) for size
  - multipliers for expiry day and the first 5 minutes
  - a minimum option premium
  - Defaults: NIFTY futures max(2 ticks, 0.003%); ATM options 1 tick + 0.25% of premium; OTM max(1 tick, 1.5%); expiry day ×2.
  - **Calibration source:** the 2026-11-02 Choice TBT depth recording (one-day spread snapshot).
- **Worked check (unit test):** 1 lot NIFTY futures at 25,000 round trip ≈ ₹966 before slippage at 2026-04 rates; 1 lot ATM option at ₹150 ≈ ₹70.

## 10. Data

| Use | Source | Window |
|---|---|---|
| NIFTY futures 1-min, per contract + continuous front | qr `data/raw/futures/fut_contracts_1m.parquet`, `processed/fut_front_1m.parquet` (already cleaned for contract mislabels) | 2022-01 → 2026-09 |
| NIFTY spot 1-min, India VIX daily | qr `data/raw/index`, `raw/vix` | same |
| Stocks 1-min, current F&O list (210) | `~/projects/quant/algo-trading-testing/data/parquet/equity` | 2022-01 → 2026-09 |
| NIFTY options EOD per contract | qr `raw/options/nifty_options_daily.parquet` (bhavcopy) | 2022-01 → 2026-09 |
| Contract specs, lot history, holidays | reference-data pull → `data/ref/*.csv` | 2022 → 2026 |
| Live | Choice TBT WebSocket; JWT refreshed by the existing RPA workflow | from today |

- **Copying:** data files are copied (not linked) into `data/` with a `BUILD_LOG.md` recording source paths and hashes. `data/` is gitignored; the README explains how to rebuild it.
- **Splits:**
  - **Pre-research sandbox:** 2022-01 → 2022-06, used for the step-2 sanity probes only. It's short; the alternative is to drop probes *(call)*.
  - **Research window:** 2022-07 → 2025-06. CPCV, PBO, DSR and patches happen here.
  - **Holdout:** 2025-07 → 2026-09, one touch per strategy, flagged as contaminated by the prior project.
  - **True out-of-sample:** live paper trading.
- **Structural breaks the engine must respect:**
  - SEBI F&O changes in Nov 2024
  - expiry weekday change in Sep 2025
  - closing auction from Aug 2026
  - tick banding from Apr 2025
  - known OI resets
- **Credentials:** env vars only (`TBT_CLIENT_ID`, `TBT_USER_ID`, `TBT_JWT`). The repo never reads the old `.env` or `.env.bak-*` files.

## 11. Paper trading and decay

- **Promotion:** after sign-off, the orchestrator creates `#paper-{id}`. The paper desk subscribes to the TBT feed for the instrument, builds 1-min bars, and runs the **same engine and spec**. Fills use `half_spread` slippage from live best bid/ask, and costs come from the current charge set.
- **Decay monitors** (on paper P&L, posted to the room):
  - CUSUM on per-bet net P&L against the backtest mean (§2.5.2.1)
  - Chu-Stinchcombe-White break test on cumulative P&L (§17.3.2)
  - rolling Lempel-Ziv entropy of the encoded return sign string (§18.8.1): rising entropy means the edge is being arbitraged
  - rolling PSR against the backtest Sharpe
- **Retire rule** *(call)*: two monitors alarm within 10 sessions, or the drawdown passes the backtest 95th-percentile DD. This leads to retirement, a post-mortem, and possibly a fork.
- **Demo fallback:** if the RPA token isn't ready, the desk replays the recorded 2026-11-02 TBT session at speed and is labelled "replay".

## 12. Leaderboard

- **Score** = DSR − penalties (max DD > 20%, TuW > 30% at a 15% vol target, < 50 bets).
- **Columns:**
  - PSR, DSR, PBO (family)
  - CPCV median Sharpe and % positive paths
  - P[failure]
  - beats-null flag
  - holdout result
  - status: research / gate-pass / signed / paper / retired
  - lineage `h12 → h12.1`
- **Reference rows:** the live bot and buy-and-hold, never ranked as leader.
- **Rendering:** a small web page served from the DB, with a summary posted to `#control` each round.

## 13. Tonight's scope (ordered; the Switch loop beats gate coverage)

**User decisions:**
- The Switch loop comes before full gate coverage.
- The demo uses a **flagged demo gate**: `config/gate.demo.yaml`, hashed like the real one. Every verdict, room post, leaderboard row and the video overlay is stamped `DEMO GATE (looser thresholds)`, and the real-gate verdict is shown next to it.
- The old project's 728 + 259 trials are **reported, not counted**: families count only this system's trials, and the README and leaderboard show the prior counts plus the "holdout seen" flag.

**Build order:**
1. Engine + cost config + contract/holiday tables, on NIFTY futures 1-min.
2. DB (append-only, families, trials, cards) + trial runner.
3. **Core gate:**
   - look-ahead probe and determinism
   - purged + embargoed CPCV
   - PSR/DSR with the family trial count
   - PBO
   - exposure-matched null and the reference rows
   - costs and return on execution costs
   - fixture calibration
4. Orchestrator registered as a Switch agent. Room template v2. One full live loop in Switch: propose → room → spec → verify → gate → patch → hard stop → sign-off → paper (replay OK) → fork room. This is the demo video.
5. **Remaining gate checks:** P[failure], concentration and tails, robustness/ablation, synthetic exit flag.
6. Leaderboard page.
7. Landing page (`landing/`, Vite + React, working name "Deflate"). Its job is the opening of the demo speech: the problem statement in the author's voice, then the solution. It is context only; the core product is Switch. The primary CTA redirects to the Switch research floor (`VITE_SWITCH_URL`).
8. README with Switch usage, reproduce steps, limitations, and credits.

**Later:** stocks and option-EOD strategies, the ranker's learned prior (tonight: nearest-neighbour downgrade only), HRP blend, Pine export, real RPA-token paper desk.

## 14. Credits (README acknowledgements)

- Switch (switchagents.ai, sandbox-quantum/switch)
- López de Prado, *Advances in Financial Machine Learning* (Wiley, 2018)
- After42, "Can AI Propose Interesting Research Questions?" (Nie, Wang et al., 2026), after42.org (questions CC BY 4.0)
- IdeaTaste (Zhong, Xu, Ning, Liu, Kumar, Xiong; arXiv:2611.03424, 2026); paper CC BY 4.0, code MIT
- The author's prior research swarm (quant-research-v0), design inspiration

## 15. Decided by the user vs my calls

**User decided:**
- Switch as the backbone
- one room per hypothesis; forks as new rooms with short hierarchical names
- the patch loop with a hard stop
- the AFML rules implemented as scripts
- everything about costs and slippage configurable
- Groww brokerage
- data 2022–2026
- current F&O stocks only
- daily NIFTY options are fine for the demo
- the live feed is Choice TBT via the existing RPA workflow
- paper trading after a pass
- the strategy-language concern (Pine is simple; Python can be buggy)
- credits for After42 and IdeaTaste

**My calls (check these):**
- YAML spec + linted Python fallback
- all thresholds marked *(call)*
- Codex as the judge provider
- the split dates
- patch budget 4 and fork depth 3
- the retire rule
- `build_cap` 2
- SQLite
- Python for engine, gate and orchestrator

**My calls from the builder review:**
- **Where agents run; how specs travel.** All agents run locally through Switch Console on the author's Mac (Claude Code / Codex providers), each in its own git worktree of this repo.
  - The coder writes `specs/{id}.yaml` and commits it on branch `hyp/{id}`.
  - The coder posts `@orchestrator freeze {id}`. The orchestrator reads the file from git, hashes it, and registers it.
  - The orchestrator then (as author) posts the frozen spec as a read-only room doc.
  - Protected paths are enforced twice:
    - Claude Code permission deny rules on every agent's settings
    - the trial runner reading engine/gate/config only from `main` and checking hashes, so edits in a worktree have no effect
- **Live-bot reference row.** This is the qr "180-point" bot (`qr/strategies/bench_live.py`). It is re-implemented as a fallback-function spec (`signal()`), run through the same engine, and is never a candidate.
- **Diagnosis barrier.** Rule specs aren't fitted, so within the research window the protection is accounting, not hiding:
  - Every patch is a counted trial (DSR/PBO tighten).
  - The patch must name its targeted months up front, in the diff rationale.
  - "Win where it wasn't aimed" is computed on CPCV test-path P&L restricted to the non-targeted groups.
  - The holdout and paper data are never visible to agents.
- **TBT recording.** `~/projects/quant/algo-trading-testing/tbt/2026-11-02.bin.zst` (NIFTY index + Nov/Dec/Jan futures, 20-level depth snapshots; decoder in `algo-trading-testing/research/tbt_record.py`) and `2026-11-02-stocks.bin.zst` (best-5, 218 stocks). It is used for slippage calibration and the replay feed. The replay feeder reuses that decoder's frame format (read-only).

**Left to the builder:** module layout, the exact feature registry contents, the embedding model, and leaderboard styling.
