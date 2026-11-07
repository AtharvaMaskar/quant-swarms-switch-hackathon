# Demo video script (3:30–4:00)

The plan is **honest no + labelled walkthrough**. Nothing has passed the real gate or the demo gate, so the video shows real FAIL receipts, then walks sign-off → paper → decay on a rejected strategy, stamped on every post.

## Before recording

```bash
set -a; source .env; set +a          # QS_BRIDGE, QS_ENGINE_REF=feat/deflate, QS_THREADS=1
uv run python -m qs.cli calibrate                                        # fills the calibration stat
uv run python -m qs.cli run-orchestrator --humans "<Slack display name>" # real gate
uv run python -m qs.web                # leaderboard, http://127.0.0.1:8765
uv run python -m floor.server          # research floor, http://127.0.0.1:8766
cd landing && npm run dev              # http://localhost:5173
```

- Switch Console local server is up; the agents from `templates/deflate-agents.v2.yaml` are online.
- Slack is open on `#deflate-control`. Everything happens in this one channel: each hypothesis and each paper is a thread.
- Use `deflate <cmd>` rather than `@orchestrator <cmd>`: Switch auto-replies "not permitted" to @-mentions of the orchestrator (it's registered with the bootstrap token). The orchestrator acts either way.
- Dry-run the paper beat once so the reader's answers are fast.
- Open tabs: landing, Slack `#deflate-control`, floor (:8766), leaderboard (:8765).
- Long waits (coder writing a spec, trial runs, paper replay) are cut or sped up. Say so once on screen: "time-lapse".

## Beats

| # | Time | On screen | Type / do | Say |
|---|---|---|---|---|
| 1 | 0:00–0:25 | Landing page, hero to the problem section | — | "I build quant and ML algorithms for retail and HNI investors. Alpha decay went from months to days, and AI swarms make false discoveries cheaper: every extra backtest raises the best Sharpe you can get by luck. My last swarm produced 259 candidates and promoted zero. Deflate is the fix: agents propose, code decides, a human signs off. It runs entirely on Switch." |
| 2 | 0:25–0:40 | Slack `#deflate-control` | `deflate implement https://arxiv.org/abs/2103.09692` | "A researcher drops a paper. The orchestrator is our own process, registered on Switch as an always-on agent. It opens a paper thread right under my message." |
| 3 | 0:40–1:05 | The paper thread: the parsed card (title, abstract, exact LaTeX equations), then the reader's memory hits and what's testable | Reply, e.g. `Which parts can we actually test on NIFTY?` Then `@reader propose` | "Bouchaud's 'Radical Complexity' is a review, not a strategy, so the reader says what's testable. Rough volatility becomes a vol-forecast filter. Crowded trades only partly: we only have open interest. It checks memory first: what we tried, what died, and why. It proposes only when I ask." |
| 4 | 1:05–1:20 | Control: `deflate propose {…}`, `registered card …`, then the judge's pairwise verdict | — | "Cards go to the orchestrator. Code checks them against the feature registry and the dead ends. Then the judge compares two cards; the winner gets a thread." |
| 5 | 1:20–1:30 | The new thread `🧪 h1 · …` in `#deflate-control` | — | "One Switch room per hypothesis, shown as a thread in one Slack channel. Hundreds of strategies can't mean hundreds of channels; the family tree lives in the database." |
| 6 | 1:30–1:45 | In the thread: the coder posts `deflate freeze h1 specs/h1.yaml`; the orchestrator replies `spec frozen … sha …` | — | "The coder writes a YAML spec. Freezing hashes it, and after the first trial it can never change. The engine, gate and config are checked against a committed ref before every trial, so an agent's edit has no effect." |
| 7 | 1:45–1:55 | The verifier posts `verified h1 round 1: …` | — | "The verifier checks the spec matches the card. Then the trial runs once, and that trial is counted forever." |
| 8 | 1:55–2:15 | `research trial FAIL` with the check grid; the validator explains it | — | "Real gate: fail. CPCV, deflated Sharpe with the family's trial count, PBO, an exposure-matched null. The validator explains the result; it can't change it." |
| 9 | 2:15–2:30 | `patch allowed (1/4)` with monthly P&L; the coder freezes `h1v2`; another FAIL | (coder) `deflate freeze h1v2 specs/h1v2.yaml` | "One patch, exactly one rule changed, enforced by a structured diff. It's a new trial, so the bar goes up." |
| 10 | 2:30–2:40 | `hard stop (…); dead end` and the post-mortem request | — | "Code stops it, not an agent. The post-mortem goes to memory." |
| 11 | 2:40–2:55 | Control: fork; new thread `🧪 h1.1 · … · forked from h1` | `deflate fork h1 <slug>` | "What we learned forks a new hypothesis, with its lineage in the thread title. The fork inherits the family's trial count; forking never resets the bar." |
| 12 | 2:55–3:10 | Floor dashboard (:8766): board by stage, lineage, a hypothesis drawer with its receipts | — | "The research floor: every hypothesis, every patch line, every receipt, live from the research database." (If the Switch live-state panel is in by recording, show it; it reads "Switch offline" when Switch is down.) |
| 13 | 3:10–3:20 | h1.2.3's demo-gate FAIL receipt (from the 8-trial search) | — | "We searched eight counted trials for anything to show. The closest: gap continuation in calm regimes. Deflated Sharpe 0.44, PBO 0.65, 79% of its P&L from five days. Even the loose demo gate says no. That's the product working." |
| 14 | 3:20–3:40 | Labelled walkthrough on h1.2.3: every post stamped `REJECTED BY GATE · pipeline demo, not a recommendation`; the daily health post; the decay alarm and retire posts | See below | "To show the rest of the pipeline, here's sign-off to paper to decay on that rejected strategy, labelled on every post. Same engine and spec on a replayed tick feed; CUSUM and entropy watch the P&L; two alarms and it retires." |
| 15 | 3:40–4:00 | Leaderboard at `127.0.0.1:8765`: reference rows pinned, every FAIL counted, 987 prior trials "reported, not counted", calibration reject rates (100% on no-skill fixtures) | — | "Every trial is counted, failures included. The gate rejects every no-skill fixture. Deflate: agents propose, code decides, humans sign off, and Switch is where all of it happens." |

## The labelled walkthrough (beat 14)

```bash
uv run python -m qs.paper.desk --hid h1.2.3 --spec specs/demo/h1.2.3.yaml --replay \
  --label "REJECTED BY GATE · pipeline demo, not a recommendation"
```

- Add `--room "<control channel id>#<h1.2.3 thread root>"` to post in the hypothesis thread, as the desk does after a real approval.
- h1.2.3 enters at 09:20 and the 2026-11-02 recording starts at 09:47, so the replay makes no trades. Show the daily health post.
- For the decay alarm and retire posts, use the injected-losses run (`tests/test_desk.py::test_desk_retires_on_alarm`), labelled as a simulation.
- Thresholds stay untouched. The search is in `research.demo-search.db` and `specs/demo/`.

## If something stalls

- An agent is slow: cut. Never fake a post. Every message on screen must come from a real run.
- The orchestrator didn't react: `deflate status` shows the state. Check the orchestrator's terminal for the parse error. A "not permitted" auto-reply to an `@orchestrator` mention is expected; it still acts.
- uv complains the cache is locked: prefix with `UV_CACHE_DIR=/tmp/uv-cache`.
