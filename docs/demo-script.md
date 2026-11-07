# Demo video script (3:30–4:00)

## Before recording

```bash
set -a; source .env; set +a
uv run python -m qs.cli calibrate                 # fills the calibration stat on the leaderboard
uv run python -m qs.cli run-orchestrator --humans <you>          # real gate, for beats 2–10
uv run python -m qs.web                           # http://127.0.0.1:8765
cd landing && npm run dev                         # http://localhost:5173
```

- Switch Console is open, and the agents in `agents/` are registered and online.
- **Choose the paper beforehand** (an intraday momentum or expiry-flow paper on arXiv), and dry-run beats 2–4 once so the reader's answers are fast.
- Open tabs: landing, Switch (`#deflate-control`), leaderboard.
- Between beat 10 and beat 11, restart the orchestrator with `--demo`. Cut the restart. The overlay `DEMO GATE (looser thresholds)` stays on screen from beat 11 to the end.
- Long waits (coder writing a spec, trial runs, paper replay) are cut or sped up. Say so once on screen: "time-lapse".

## Beats

| # | Time | On screen | Type / do | Say |
|---|---|---|---|---|
| 1 | 0:00–0:30 | Landing page, scroll from hero to the problem section | — | "I build quant and ML algorithms for retail and HNI investors. Alpha decay went from months to days, and AI swarms make false discoveries cheaper: every extra backtest raises the best Sharpe you can get by luck. My last swarm produced 259 candidates and promoted zero. Deflate is the fix: agents propose, code decides, a human signs off. It runs entirely on Switch." |
| 2 | 0:30–0:45 | Switch, `#deflate-control` | `@reader implement https://arxiv.org/abs/<id>` | "A researcher sees a paper and tags the reader. The orchestrator is our own process, registered on Switch as an always-on agent. It opens a literature room from a YAML template and links it to control." |
| 3 | 0:45–1:10 | `#lit-arxiv-…`: the reader's paper card, then its memory hits (similar past cards, dead ends) | Reply to one question, e.g. `Map it to NIFTY futures, expiry days only. Skip the stock version.` Then `@reader propose` | "The reader writes a paper card: the claimed effect, the mechanism, and the threats. Before suggesting anything, it checks memory: what we tried, what died, and why. I steer; it proposes only when I ask." |
| 4 | 1:10–1:20 | `#deflate-control`: `@orchestrator propose {…}` and `registered card …` | — | "The card goes to the orchestrator. Code checks it against the feature registry and the dead ends. Then the judge compares it with another card." |
| 5 | 1:20–1:30 | Room list: the new `#h1-…` room, its link "from paper", the roles and the spec doc | — | "The winner gets its own room from a template: coder, verifier and validator roles, the card as a room doc, the repo as a reference." |
| 6 | 1:30–1:45 | `#h1-…`: the coder posts `@orchestrator freeze h1 specs/h1.yaml`; the orchestrator replies `spec frozen … sha …` | — | "The coder writes a YAML spec. Freezing hashes it, and after the first trial it can never change. Agents can't touch the engine, the gate or the database." |
| 7 | 1:45–1:55 | The verifier posts `verified h1 round 1: …` | — | "The verifier checks the spec matches the card. Then the trial runner runs it once, and that trial is counted forever." |
| 8 | 1:55–2:15 | The orchestrator posts `GATE FAIL` with the check grid; the validator explains it in plain language | — | "Real gate: fail. CPCV, deflated Sharpe with the family's trial count, PBO, an exposure-matched null. The validator explains the result; it can't change it." |
| 9 | 2:15–2:30 | `patch allowed (1/4)` with monthly P&L; the coder freezes `h1v2`; another FAIL | (coder) `@orchestrator freeze h1v2 specs/h1v2.yaml` | "One patch, exactly one rule changed, enforced by a structured diff. It's a new trial, so the bar goes up." |
| 10 | 2:30–2:40 | `hard stop (no_improvement_2); dead end` and the post-mortem request in control | — | "Code stops it, not an agent. The post-mortem goes to memory." |
| 11 | 2:40–2:55 | Control: fork; the new `#h1-1-…` room linked "forked from h1" | `@orchestrator fork h1 expiry-volgate` | "What we learned forks a new hypothesis. The fork inherits the family's trial count; forking never resets the bar." |
| 12 | 2:55–3:05 | `#h1-1-…`: **DEMO GATE** stamped `GATE PASS`, then the holdout result; cut to the leaderboard row showing `real gate: FAIL` beside it | Off camera: `uv run python -m qs.cli trial h1.1` (records the real-gate verdict as one more counted trial) | "For the video, this pass is on the demo gate: looser thresholds, stamped everywhere. The real verdict sits right next to it. The holdout is touched exactly once." |
| 13 | 3:05–3:12 | The sign-off request mentioning you | `@orchestrator approve h1.1` | "Only a human can approve, in the room." |
| 14 | 3:12–3:25 | `#paper-h1-1`: replayed fills, daily P&L summary (time-lapse) | — | "Approval opens a paper room. The same engine and spec run on a replayed Choice tick feed, with the live half-spread as slippage." |
| 15 | 3:25–3:35 | Decay alarm posts (CUSUM, entropy) | — | "Decay monitors watch paper P&L: CUSUM, a structural-break test, Lempel-Ziv entropy." |
| 16 | 3:35–3:45 | Retirement post; the room archives; the post-mortem request in control | — | "Two alarms within ten sessions, and it retires. The learning feeds the next round." |
| 17 | 3:45–4:00 | Leaderboard at `127.0.0.1:8765`: reference rows pinned, DEMO GATE badge, 987 prior trials "reported, not counted", calibration reject rates | — | "Every trial is counted, failures included. Buy-and-hold and the live bot are pinned as the bar to beat. Deflate: agents propose, code decides, humans sign off, and Switch is where all of it happens." |

## If something stalls

- An agent is slow: cut. Never fake a post. Every message on screen must come from a real run.
- The orchestrator didn't react: `@orchestrator status` shows the state. Check the orchestrator's terminal for the parse error.
- The paper replay is too slow: start the desk by hand with a higher speed: `uv run python -m qs.paper.desk --hid h1.1 --spec specs/h1.1.yaml --replay --speed 0 --room <paper room id> --db research.db`.

## Decision (2026-11-07): honest no + labelled walkthrough

Nothing passed the real gate, and nothing passed the demo gate either. The search made 8 counted trials in one family; see `research.demo-search.db` and `specs/demo/`. The closest was h1.2.3 (gap continuation in calm regimes): DSR 0.44, PBO 0.65, 79% of P&L in its top 5 days, and a storytelling clause. Thresholds stay untouched.

- **Beats 11–12 change.** Show h1.2.3's demo-gate FAIL receipt. Say: "Even the loose demo gate says no. That's the product working."
- **Beats 13–15 (sign-off → paper → decay).** Run as a walkthrough on h1.2.3, stamped on every post:
  `uv run python -m qs.paper.desk --hid h1.2.3 --spec specs/demo/h1.2.3.yaml --replay --label "REJECTED BY GATE · pipeline demo, not a recommendation"`
  - h1.2.3 enters at 09:20, and the 2026-11-02 recording starts at 09:47, so the replay makes no trades. Show the daily health post. For the decay alarm and retire beat, use the alarm/retire posts from the injected-losses run (`tests/test_desk.py::test_desk_retires_on_alarm`), labelled as a simulation.
