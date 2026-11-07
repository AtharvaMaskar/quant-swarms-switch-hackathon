# Deflate

**A quant research swarm on [Switch](https://switchagents.ai) where agents propose, code decides, and a human signs off.**
WeMakeDevs "Flip the Switch", Track 01 (Business Workflow).

---

## The problem

I'm an AI engineer. I build quant and ML trading algorithms for retail and HNI investors in India.

Two things changed. First, alpha decay went from months to days: strategy code is nearly free now, so any edge gets copied and arbitraged away fast. Second, AI swarms make false discoveries cheaper. Every extra backtest raises the best Sharpe you can get by luck alone (López de Prado, AFML §11.3), so a leaderboard of machine-generated strategies mostly ranks noise.

I learned this the hard way. My previous swarm produced **259 candidates and promoted 0**. Train rankings flipped on the holdout (long bias in a rising market looked like skill). The holdout was spent on ten contenders at once. 728 earlier trials were never counted in the deflation. Agents could reach and rewrite the rules that judged them. And the swarm drifted into building statistics tools instead of testing strategies.

Producing ideas is no longer the bottleneck. The bottleneck is deciding honestly and quickly which ideas are real, and noticing when a real one stops working.

## What Deflate does

1. **Agents propose.** Three proposers (literature, post-mortems, contrarian) and a paper reader write five-slot hypothesis cards.
2. **Code decides.** A deterministic gate that agents cannot edit runs AFML checks on a frozen spec. Every run is a counted trial.
3. **Humans sign off.** A gate pass gets one holdout touch, then a human approves or rejects it in the hypothesis's Slack thread.
4. **Paper trading.** Approved strategies run the same engine and spec on a live (replayed) tick feed.
5. **Decay retires.** CUSUM, Chu-Stinchcombe-White, Lempel-Ziv entropy and rolling PSR watch paper P&L. Two alarms in 10 sessions retire the strategy.
6. **Learnings fork.** Dead ends and retired strategies write post-mortems to memory and can fork new hypotheses. Forks inherit the family's trial count, so the bar never resets.

## How Switch is used

Switch is the backbone, not a notification channel. The messaging app is **Slack** (a company workspace, through Switch's Slack bridge). Everything a human sees happens in **one Slack channel, `#deflate-control`**:

- **Each hypothesis is a thread** in that channel, e.g. `🧪 h2 · vix-gated-drift`. Spec, freeze, verify, gate receipt, patches, hard stop and sign-off all happen in the thread.
- **Each paper is a thread** under the human's `deflate implement …` message. The reader brainstorms there.
- **The paper desk reports in the hypothesis thread** (fills, daily P&L, decay alarms, retirement).
- **Forks carry lineage in the thread title** (`🧪 h1.1 · expiry-volgate · forked from h1`).

The hierarchy (families, patches, forks, trial counts) lives in `research.db` and in Switch. It does not live in channel names: hundreds of strategies can't mean hundreds of channels. Thread mode is `QS_THREADS=1` (the default). Per-room channels still exist (`QS_THREADS=0`: a channel per hypothesis, lit and paper room, all prefixed `deflate-`, linked with `link_rooms`), but we don't use them.

| Switch feature | What Deflate does with it |
|---|---|
| **Control room** from a **v2 YAML template** (`templates/control-room.v2.yaml`) | Created on first run as `#deflate-control` on the bridge named by `QS_BRIDGE`. Proposers, judge, reader, coder, verifier, validator, orchestrator and the human are all in it. |
| **Threads** (`post_message` with `thread_id`) | One thread per hypothesis and per paper. The orchestrator's room ids for them are `"<channel>#<thread root>"`, so every post and @-mention lands in the right thread. |
| **One-click agent templates** (`templates/deflate-agents.v2.yaml`) | Creates every Claude Code agent (proposers, judge, reader, coder, verifier, validator) with its brief in `agents/*.md` and the repo as its working directory. |
| **Roles** | `proposer`, `judge`, `coder`, `verifier`, `validator`, `reader`, each with role instructions from the template. |
| **Registered `always_on` agent over HTTP + SSE** | The orchestrator is our own Python process, registered once with `scripts/switch_register.py`. It listens to **all** room messages on `GET /agents/{id}/events` and acts through `POST /agents/{id}/ops/{op}` (post, mention, thread, create room, read context, download media). It is the deterministic state machine Switch itself doesn't have. |
| **@-mention hand-offs** | Every step is an @-mention: `@coder` builds, `@verifier` checks, `@validator` explains, `@judge` compares, `@proposer-lead` writes the post-mortem. |
| **Sign-off in the thread** | The orchestrator @-mentions the humans with the full report. Only names passed in `--humans` (Slack display names) can `approve`, `reject`, `close`, `stop` or `resume`. Each decision is stored as calibration data. |
| **Media store** | SSRN papers arrive as a PDF attached in the thread; the orchestrator pulls it from Switch's media store on `deflate attach <lit_id>`. |
| **Slack bridge** | The only UI. Switch carries every agent and human message between Slack and the agents. |

## What breaks without Switch

Switch is central (judging criterion 1). Take it away and this is what stops:

1. **The agents.** Proposers, judge, reader, coder, verifier and validator are Switch agents created from our template. Without Switch they don't exist as a team: nothing wakes them, nothing gives them a room.
2. **Hand-offs.** Every step (propose → judge → code → freeze → verify → gate → validator → patch or post-mortem) is an @-mention routed by Switch. There is no queue, no RPC and no other channel between agents.
3. **The orchestrator's input.** It has no API of its own. Its only input is Switch's event stream (SSE), and its only output is Switch ops. Without Switch it receives no commands and can post nothing.
4. **Human sign-off.** `approve`, `reject`, `stop` and `resume` are checked against the sender Switch reports. There is no other place to sign off.
5. **Slack.** The Slack channel and its threads exist only through Switch's Slack bridge. We have no Slack bot of our own.
6. **The floor dashboard's live state.** It is meant to read who is online and what is happening from Switch, and to show "Switch offline" when it can't (see Dashboards).

What survives without Switch: the engine, the gate, `research.db` and the leaderboard. You could still run `qs.cli trial` by hand. That's a backtester, not a research floor.

## Architecture

```
  +----------------------- Switch (Slack bridge, QS_BRIDGE) ------------------------+
  |  #deflate-control  (proposers, judge, reader, coder, verifier, validator,       |
  |                     orchestrator, human)                                        |
  |    ├─ thread 🧪 h1 · gap-continuation        spec, gate receipt, patches, stop  |
  |    ├─ thread 🧪 h1.1 · … · forked from h1    lineage in the title               |
  |    ├─ thread under "deflate implement <arXiv>"  paper card, reader brainstorm   |
  |    └─ paper desk posts in the hypothesis thread (fills, P&L, decay, retire)     |
  +---------------^-----------------------------------------^-----------------------+
                  | agent protocol (HTTP + SSE)             |
     +------------+--------------------+          +---------+-------------+
     | Orchestrator (qs/orchestrator)  |          | Paper desk (qs/paper) |
     | state machine, budgets, naming, |          | TBT replay feed, same |
     | hard stops, kickoffs, sign-off  |          | engine, decay monitors|
     +------+---------------+----------+          +-----------+-----------+
            |               |                                 |
     +------v-----+  +------v--------------+  +---------------v---------+
     | Engine     |  | Gate (qs/gate)      |  | SQLite research.db      |
     | spec->bars |  | read-only to agents,|  | append-only (triggers): |
     | ->fills    |  | config sha in every |  | trials, messages, specs |
     | ->net P&L  |  | verdict             |  | frozen, holdout once    |
     +------------+  +---------------------+  +------------+------------+
                                                           |
                               qs.web leaderboard :8765 · floor/ research floor :8766
```

**Deterministic (our code):** orchestrator, engine, gate, cost model, DB, trial runner, paper desk, monitors, leaderboard.
**LLM:** proposing, critiquing, ranking ideas, writing specs, explaining results, post-mortems. An LLM never decides whether a strategy is real.

## The gate

Thresholds live in `config/gate.yaml` (sha256-hashed; the hash is stamped on every verdict). The gate runs on the research window (2022-07 → 2025-06).

| # | Check | Real threshold | AFML |
|---|---|---|---|
| 1 | Look-ahead probe (perturb future bars → identical past positions) and determinism | must hold | §11.2 |
| 2 | Minimum bets | ≥ 50 | §14.3 |
| 3 | CPCV, purged + embargoed (N ∈ {8,10,12} by hidden seed, k = 2, embargo 1%) | median path SR > 0 and ≥ 80% paths positive | §7.4, §12.4 |
| 4 | PSR(0), non-annualised, with skew and kurtosis | > 0.95 | §14.7 |
| 5 | DSR with the family's N_eff and trial-Sharpe variance | > 0.95 | §14.7 |
| 6 | PBO by CSCV (S = 16) over the family's trials | ≤ 0.10 | §11.6 |
| 7 | Exposure-matched null (1,000 circular shifts) | ≥ 95th percentile | qr lesson |
| 8 | Beats buy-and-hold and the live-bot reference | higher Sharpe | §11.2 |
| 9 | Return on execution costs | ≥ 3× | §14.6 |
| 10 | P[strategy failure], target SR 1.0, 2-year horizon | ≤ 0.05 | §15.4 |
| 11 | Concentration: HHI of ± bets; top-5 days share | ≤ 0.30; < 50% | §14.3 |
| 12 | Max drawdown and time under water at 15% vol | ≤ 20%; ≤ 30% | §14.5 |
| 13 | Robustness: ±1 step per parameter | worst neighbour SR > 0 | §11.5 |
| 14 | Ablation: dropping any entry clause must hurt | every clause earns its keep | §8 |
| 15 | Synthetic exit check on AR(1) paths (flag only) | top half of a 20×20 mesh | Ch 13 |
| 16 | Patch "win where it wasn't aimed" | median SR not lower; ≥ 70% untargeted folds held | ours |

**Patch loop and hard stop (§7.4).** A failed hypothesis may file a one-rule patch (`h12v2`; a structured diff enforced by code). The run stops at whichever comes first: 4 patches, 2 non-improving patches, or DSR unreachable at the family's N_eff. **Holdout** (2025-07 → 2026-09) is touched once per strategy, enforced by a unique index in SQLite.

**Calibration (§7.3).** No-skill fixtures go through the full gate on every config change: random entries, pure long exposure, a planted look-ahead leak, and the best of 100 random strategies. The gate must reject at least 95% of each. Run `uv run python -m qs.cli calibrate`; the reject rates are written to the DB and shown in the leaderboard header.

**The DEMO GATE, honestly.** A real-gate pass is rare by design, and one evening of research is unlikely to produce one. To show the whole loop on video (sign-off → paper → decay → retire) we added `config/gate.demo.yaml`. It has the same checks with looser thresholds (PSR/DSR 0.50, PBO 0.50, CPCV 50% positive, 20 bets) and is hashed like the real one. Every verdict, room post and leaderboard row that comes from it is stamped **DEMO GATE (looser thresholds)**, and the leaderboard shows the real-gate verdict next to it. In practice nothing passed the demo gate either (see Results so far).

## Costs and slippage

Everything is in `config/costs.yaml`, as dated charge sets. A trade uses the latest set whose `effective_from` is on or before the trade date.

- **Brokerage: Groww.** ₹20 per executed F&O order. Equity is min(₹20, 0.1%). Flagged `verify: true`, because one Groww page shows ₹50.
- **STT history:**

  | From | Futures (sell) | Options premium (sell) |
  |---|---|---|
  | 2022-01-01 | 0.01% | 0.05% |
  | 2023-04-01 | 0.0125% | 0.0625% |
  | 2024-10-01 | 0.02% | 0.1% |
  | 2026-04-01 | 0.05% | 0.15% (exercise 0.15%) |

  The 2026 rates come from news and law-firm sources only, so they are flagged.
- **Other charges:** NSE exchange charges (uniform from 2024-10-01), SEBI fee, IPFT, stamp duty, and GST.
- **Slippage:** NIFTY futures max(2 ticks, 0.003%); ATM options 1 tick + 0.25% of premium; ×2 on expiry day. Tick banding from 2025-04-15. The paper desk uses the live half-spread instead.
- **Unit test:** 1 lot of NIFTY futures at 25,000, round trip at 2026 rates ≈ ₹966 before slippage.

## Data

| Data | Source | Window |
|---|---|---|
| NIFTY futures 1-min (front contract, rolled) | Choice / Dhan | 2022-01 → 2026-09 |
| NIFTY spot 1-min | Choice / Dhan | same |
| NIFTY options EOD | NSE bhavcopy | same |
| India VIX daily | NSE | same |
| Contract specs, lot sizes, holidays, special sessions | NSE circulars → `ref/*.csv` | 2022 → 2026 |
| Paper-trading feed | Choice TBT recording (2026-11-02, 20-level depth), replayed | one session |

Splits: sandbox 2022-01 → 2022-06 (sanity probes only), research 2022-07 → 2025-06, holdout 2025-07 → 2026-09.

`data/` is gitignored. `scripts/build_data.py` rebuilds it by copying from the author's local research store and writes `data/BUILD_LOG.md` with source paths, sha256 and row counts. To use your own data, put parquet files with the same schema at the same paths.

## Results so far (honest)

- **The real gate rejected everything we tried.**
- **An 8-trial counted search** (one family, `research.demo-search.db`, `specs/demo/`) found nothing that passes even the demo gate. The closest was **h1.2.3, gap continuation in calm regimes**: DSR 0.44, PBO 0.65, 79% of P&L in its top 5 days, and a storytelling clause the ablation caught. Thresholds stay untouched.
- **Calibration:** the no-skill fixtures were rejected 100% on real data.
- **So the demo is "honest no + labelled walkthrough".** The video shows the real FAIL receipts, then runs sign-off → paper → decay on h1.2.3 with every post stamped `REJECTED BY GATE · pipeline demo, not a recommendation` (see `docs/demo-script.md`). A gate that says no to everything an evening of research produced is the product working.

## Quickstart

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 20+. If the shared uv cache is locked, prefix `uv` commands with `UV_CACHE_DIR=/tmp/uv-cache` (any private path).

```bash
# 1. Python env + data
uv sync
uv run python scripts/build_data.py
uv run pytest -q
```

2. **Switch.** Run the Switch Console local server on a free port (3000 is often taken). Console v0.40.1 has no API Keys screen: the registration token is the server's `AGENT_REGISTRATION_TOKEN`, in its `run/switch-core.env`.
3. **Slack.** Create the Slack app from Switch's manifest. For a company workspace, delete the `agent_view` block from the manifest first. Connect it in Console as a bridge; the connection's name is what goes in `QS_BRIDGE`.
4. **`.env`** (gitignored). Key names:

```bash
SWITCH_URL=http://localhost:3100
SWITCH_REG_TOKEN=...                 # the server's AGENT_REGISTRATION_TOKEN
QS_BRIDGE="My Company Slack"         # the Slack connection's name; quote values with spaces
QS_ENGINE_REF=feat/deflate           # the engine-integrity check compares qs/, config/, ref/ to this committed ref
QS_THREADS=1                         # default: one channel, a thread per hypothesis/paper (0 = a channel each)
QS_CHANNEL_PREFIX=deflate-           # default prefix for every channel Deflate creates
```

```bash
set -a; source .env; set +a

# 5. Register the orchestrator as an always_on agent (writes SWITCH_AGENT_ID / SWITCH_API_KEY to .env)
uv run python scripts/switch_register.py
set -a; source .env; set +a
```

6. **Create the agents** in Console → Templates with one click from `templates/deflate-agents.v2.yaml`. If a partial run already created some, use `templates/deflate-agents-rest.v2.yaml` for the rest. Each agent reads its brief from `agents/*.md` and is told never to edit `qs/`, `config/`, `ref/` or any database; the engine-integrity check refuses a trial if those differ from `QS_ENGINE_REF` anyway.

```bash
# 7. Run the orchestrator. It creates #deflate-control on first run.
uv run python -m qs.cli run-orchestrator --humans "<Slack display name>"          # real gate
uv run python -m qs.cli run-orchestrator --humans "<Slack display name>" --demo   # stamped demo gate

# 8. Dashboards
uv run python -m qs.web              # leaderboard, http://127.0.0.1:8765 (refreshes every 5 s)
uv run python -m floor.server        # research floor, http://127.0.0.1:8766 (in progress)

# 9. The landing page (the opening of the demo); its CTA goes to VITE_SWITCH_URL
cd landing && npm install && npm run dev
```

Other commands: `qs.cli calibrate [--demo]`, `qs.cli trial <hid> [--demo]`, `qs.cli leaderboard`, `qs.cli demo-seed` (three sample cards), `qs.cli memory search|show|deadends|features`, `qs.cli paper fetch <url>`.

**Mattermost** also works as a bridge (`QS_BRIDGE` names it), but its container ran out of memory under emulation on Apple Silicon, so we moved to Slack.

## Dashboards

- **Leaderboard** (`qs.web`, :8765): every counted trial, reference rows pinned, DEMO GATE badges beside the real verdict, calibration reject rates in the header.
- **Research floor** (`floor/`, :8766), **in progress.** One live screen: KPIs, a board of hypotheses by stage (one card per patch line), the fork lineage, papers, and a drawer per hypothesis with its card, frozen spec, every trial's receipt and checks, sign-off and paper desk. It reads `research.db` read-only and pushes updates over SSE. Reading live state from Switch (agents online, what's happening in the channel) and a "Switch offline" banner when Switch is down are being added.

## Commands reference

Send these in `#deflate-control` or in any Deflate thread. Every command works as `deflate <cmd> …` (preferred) or `@orchestrator <cmd> …`.

Why `deflate` first: the orchestrator is registered with Switch's bootstrap registration token, so Switch auto-replies "not permitted" to @-mentions of it. The orchestrator listens to every room message and acts on the command anyway, but `deflate …` avoids the noise.

| Message | Who | Effect |
|---|---|---|
| `deflate implement <arXiv/SSRN url or id>` | anyone | open a paper thread under your message. arXiv: LaTeX source is parsed with exact equations. SSRN: the orchestrator asks you to attach the PDF |
| `deflate attach <lit_id>` | anyone | read the latest PDF attached in that paper thread |
| `@reader propose` | human, in a paper thread | the reader posts its cards as `deflate propose {…}` |
| `deflate propose {card JSON}` | proposers, reader | register a card; it gets filtered, judged and ranked |
| `deflate h12 round 3: {"prefer": "A", "reason": "…"}` | judge | pairwise verdict; the winner gets a thread |
| `deflate freeze h12 specs/h12.yaml` | coder | hash and freeze the spec, then ask the verifier (the path must be `specs/<hid>.yaml`) |
| `verified h12 round 3: <note>` | verifier | run the research trial through the gate |
| `deflate approve h12` | humans only | sign off and start the paper desk, which reports in the hypothesis thread |
| `deflate reject h12 <note>` | humans only | reject; the note is stored as calibration |
| `deflate close h12 <note>` | humans only | end a hypothesis that won't be patched further: a hard stop with a post-mortem, which lets the next round start |
| `deflate fork h12 <slug>` | anyone | fork a dead or retired hypothesis into `h12.1` (same family, new thread) |
| `deflate status` | anyone | list hypotheses and statuses |
| `deflate stop` / `resume` | humans only | stop or resume opening threads and rounds |

## Limitations

- **Survivorship.** The stock universe is the current F&O list. Stocks are not in tonight's loop, but the bias is real when they are.
- **Options are EOD only.** There are no intraday option strategies.
- **The holdout is contaminated.** The prior project saw 2025-07 → 2026-09. Every holdout result is flagged "seen by a prior project", and the 987 prior trials are reported, not counted.
- **No pass yet.** Nothing has passed the real gate or the demo gate. The sign-off → paper → decay part of the video is a labelled walkthrough on a rejected strategy, not a recommendation.
- **The floor dashboard is in progress.** It reads `research.db`; live Switch state is being added.
- **Same model family.** The judge and the proposers all run on Claude. The design calls for a different family for the judge (IdeaTaste); we ran out of time.
- **Live feed is a replay.** The paper desk replays a recorded Choice TBT session. The RPA-token live feed is not wired.
- **Unverified rates.** Some costs carry `verify: true` (Groww F&O brokerage, 2026 STT, pre-2024 exchange slabs).

## Acknowledgements

- **Switch** ([switchagents.ai](https://switchagents.ai), sandbox-quantum/switch): rooms, templates, roles and the agent protocol this runs on.
- **Marcos López de Prado**, *Advances in Financial Machine Learning* (Wiley, 2018): the gate, CPCV, PSR/DSR, PBO, P[failure] and the decay monitors.
- **After42**, "Can AI Propose Interesting Research Questions?" (Nie, Wang et al., 2026), after42.org; questions CC BY 4.0. Gave us the pairwise judge tournament and the monotone revision gate.
- **IdeaTaste** (Zhong, Xu, Ning, Liu, Kumar, Xiong; arXiv:2611.03424, 2026); paper CC BY 4.0, code MIT. Gave us five-slot cards, slot-weighted memory matching, and the lesson that off-consensus ideas must earn their prior.
- **quant-research-v0**, my previous research swarm: every failure listed above is a lesson from it. Used for design inspiration only; no code was copied.
