# Demo video plan (target 4:30, hard cap 5:00)

**The story in one line:** AI makes it cheap to try ideas, so it also makes it cheap to fool yourself. Deflate lets agents generate ideas freely, but on Switch, code decides what's real and a human signs off.

**What a judge should know by the end:**
1. The problem is real: more backtests raise the Sharpe ratio you can get by luck.
2. Switch is what makes this a team, not a script. It provides the rooms, roles, agent identities, hand-offs, the Slack bridge and sign-off.
3. The flow is deterministic. Agents talk, but every verdict, budget and stop is code.
4. It ran for real, and it says no honestly, including to our own live bot.

## Pacing rules

- **About 150 words a minute** of voiceover. That is ~650 words for 4:30. Every line below has its word count; keep to it.
- **One idea per beat.** If a screen needs explaining twice, cut the screen, not the explanation.
- **Hold every receipt for 2 seconds** after the line ends: gate FAIL grids, the patch refusal, the luck line. Zoom in (Screen Studio or a crop) so the numbers can be read.
- **Show the real wait once, then time-lapse.** Put a "time-lapse ×8" caption on screen the first time and never fake a post.
- **Record voice separately** over the cut picture. Record the screens first.
- **Blur the company.** Hide the Slack sidebar (View → hide sidebar, or crop to the channel pane). Blur the workspace name and any company channels or DMs. In Console, blur the bridge name "Choice Slack".

## Structure at a glance

| Act | Time | Purpose | Main screen |
|---|---|---|---|
| 0 · Cold open | 0:00–0:20 | Hook | Floor: h0 row |
| 1 · The problem | 0:20–1:00 | Why this matters, each half answered on screen | Landing 01 Alpha decay → board; 02 Tangent trap → Slack |
| 2 · Switch Console | 1:00–1:55 | How the floor is built on Switch | Console, then terminal |
| 3 · Deterministic flow | 1:55–2:35 | Agents propose, code decides | Landing loop clock, gate.yaml, a refusal |
| 4 · A real run | 2:35–3:45 | The loop working end to end | Slack `#deflate-control` threads |
| 5 · The floor | 3:45–4:15 | What a research lead sees | Floor dashboard |
| 6 · Close | 4:15–4:30 | The claim and the links | Landing hero |

---

## Act 0 · Cold open (0:00–0:20)

**Screen:** the floor's strategies table, zoomed on the **h0 · reference audit** row: FAIL 6/15, DSR 0.017, net −₹38.1L.

> "This is a strategy we actually traded. We ran it through Deflate. It failed six of six checks. Its Sharpe was luck, and the losses weren't." *(30 words)*

Cut on the last word to the hero, "MOST BACKTESTS ARE LUCK."

## Act 1 · The problem, each half answered on screen (0:20–1:00)

Two problem chapters, each followed by a short look at the product. These are teasers of 8–10 seconds; Acts 4 and 5 go deep.

| Time | Screen | Say |
|---|---|---|
| 0:20–0:32 | Landing → **01 Alpha decay**. Scroll until the counter hits DAYS and "By the time you find an edge, it's gone." lands. | "I build quant algorithms for retail investors. Edges used to last months. Now it's weeks, or days. So you have to test more ideas, faster." *(26)* |
| 0:32–0:40 | Cut to the **floor**: the strategies-tested table, scrolling down the list. | "This is one afternoon of Deflate: every strategy tested, and every one counted." *(13)* |
| 0:40–0:50 | Landing → **02 The tangent trap**. Let "One idea. Ten tangents. Nine dead ends." animate in. | "But every idea spawns ten tangents. Chase them by hand and you lose months on nine dead ends." *(18)* |
| 0:50–1:00 | Cut to **Slack `#deflate-control`**: scroll past the `🧪 h…` thread roots. | "Here, each tangent is a thread with its own agents, tested in hours. And more tests bring a new problem: luck. A referee has to decide what's real." *(28)* |

Then 03 "The problem, multiplied" carries the luck line into Act 3; you can show it briefly as a bridge, or cut straight to Act 2.

## Act 2 · Built on Switch Console (1:00–1:55)

The point of this act: **Switch is the floor.** Show the setup in the order you'd build it.

| Time | Screen | Say |
|---|---|---|
| 1:00–1:15 | Console → the template you created from `templates/control-room.v2.yaml`. Point at the `bridge`, `human` and agent-typed params. | "Everything starts as a Switch v2 template. One control room, on Slack, with its roles: proposers, a judge, a reader, a coder, a verifier, a validator." *(27)* |
| 1:15–1:30 | Console → Your Agents: eight agents with their LED avatars, all live. | "The agents are Claude Code sessions created from one agent template, each with its own brief. Switch gives each one an identity, a role, and a way to be woken up." *(31)* |
| 1:30–1:42 | Console → connectivity: the Slack bridge, connected (blur the name). | "Switch's Slack bridge is our only UI. We wrote no Slack bot." *(11)* |
| 1:42–1:55 | Terminal: the orchestrator starting. It opens the event stream, pins `#deflate-control` and replays the backlog. Optionally flash `qs/switch/client.py` and its ops list. | "The referee is our own process, registered on Switch as an always-on agent. It listens to every message on Switch's event stream and acts only through Switch ops. Without Switch it hears nothing and can say nothing." *(37)* |

## Act 3 · Deterministic flow (1:55–2:35)

The point of this act: **LLMs talk, code decides.** Switch's templates provision rooms; they don't run workflows. The workflow is a state machine in the orchestrator, driven by Switch events.

| Time | Screen | Say |
|---|---|---|
| 1:55–2:10 | Landing → the loop (stepped clock): propose → judge → freeze → verify → gate → patch or stop → sign-off → paper → retire → fork. | "The loop is a state machine. Agents can propose, critique and explain. They can't decide. Every transition is code reacting to a Switch message." *(25)* |
| 2:10–2:22 | `config/gate.yaml` in the editor (CPCV, deflated Sharpe, PBO, null, costs), then a gate receipt showing the config sha. | "The gate is fifteen-plus checks from López de Prado's book. It's hashed and read-only to agents. Change a threshold and the next trial refuses to run." *(28)* |
| 2:22–2:35 | Slack: the orchestrator refusing a patch that changes two rules: "freeze refused: a patch must change exactly one rule". | "Patches change exactly one rule. Four patches, then a hard stop. Only a named human can approve. None of that is a prompt; it's code." *(26)* |

**Prep for 2:22:** produce a real refusal before recording. Copy `specs/h2v2.yaml` to `specs/h2v3.yaml` and change two rules, e.g. the entry filter and the exit. Then post `deflate freeze h2v3 specs/h2v3.yaml` in the h2 thread. The orchestrator replies `@coder h2 … freeze refused: a patch must change exactly one rule; h2v3 changes 2: […]`. Delete that spec afterwards. Don't use the old "7 fields" refusal; it came from a bug that's since fixed.

## Act 4 · A real run (2:35–3:45)

The point of this act: **the whole loop, for real, in one Slack channel.** Each hypothesis and paper is a thread.

| Time | Screen | Say |
|---|---|---|
| 2:35–2:55 | The Bouchaud paper thread: `deflate implement https://arxiv.org/abs/2103.09692`, the parsed card with exact LaTeX equations, then the reader's brainstorm with memory hits. | "A researcher drops a paper. The orchestrator parses it, equations included, and opens a thread. The reader checks memory, what we tried and what died, then brainstorms with me before proposing anything." *(33)* |
| 2:55–3:10 | Control: three proposer cards, the judge's pairwise JSON verdict, then the thread root `🧪 h2 · vix-gated-drift`. | "Proposers write cards; the judge compares them in pairs. The winner gets a thread: one Switch room per hypothesis, shown as a thread, so hundreds of strategies don't mean hundreds of channels." *(32)* |
| 3:10–3:25 | In the h2 thread: the coder freezes `specs/h2.yaml` (sha), the verifier's `verified`, then the **FAIL 11/15** receipt. Hold on it. Then the validator's plain-English explanation. | "The coder freezes a spec. The verifier checks it matches the card. The gate runs once, and it fails: deflated Sharpe 0.43, overfitting probability 0.87. The validator explains it. It can't change it." *(34)* |
| 3:25–3:45 | Same thread: `patch allowed (1/4)`, the coder freezes h2v2 (VIX above its 250-day median), and h2v2 also FAILs 9/16. If h2 reached its hard stop before recording, end on `hard stop · dead end` and the post-mortem request. | "One patch, one rule: only trade when volatility is above its yearly median. It's a new trial, so the bar goes up. Still a fail. The family's trial count never resets, even when it forks." *(37)* |

Time-lapse the coder writing specs and the trial runs.

## Act 5 · The research floor (3:45–4:15)

**Screen:** the floor, live on :8766 (or the Pages snapshot). Pan the KPI band, the pipeline, then the strategies table with its luck line. Open the h2 drawer, then show the agents table ("9 of 9 live", read from Switch).

> "This is what a research lead sees. Every trial is counted, failures included. The dashed line is the Sharpe that luck alone reaches at this many trials; nothing is above it yet. Agent status comes straight from Switch. And 987 old trials are shown, but not counted." *(48)*

## Act 6 · Close (4:15–4:30)

**Screen:** back to the landing hero, then the end card: the GitHub repo URL and the Pages URL.

> "Agents propose. Code decides. Humans sign off. Switch is where all of it happens. Deflate: so the backtests you keep are the ones that weren't luck." *(26)*

Total voiceover: about 570 words, which leaves room to breathe.

---

## Optional beat, if time allows (+0:25, after Act 4)

The paper desk on a **rejected** strategy, labelled on every post: daily health, a decay alarm, then retirement. It shows the tail of the loop. Skip it if the cut is over 4:45.

```bash
uv run python -m qs.paper.desk --hid h2v2 --spec specs/h2v2.yaml --replay \
  --label "REJECTED BY GATE · pipeline demo, not a recommendation"
```

## Before recording (about 30 minutes)

```bash
set -a; source .env; set +a
export QS_ENGINE_REF=feat/deflate QS_THREADS=1 UV_CACHE_DIR=$HOME/.cache/uv-qs
uv run python -m qs.cli run-orchestrator --humans "Atharva Maskar,atharva.maskar,atharva,AtharvaMaskar"   # if it isn't already running
uv run python -m floor.server                    # http://127.0.0.1:8766
cd landing && npm run dev                        # or the Pages URL
```

1. **Console:** all eight agents show live. The Slack bridge shows connected.
2. **Slack:** hide the sidebar. Open `#deflate-control` with the h2 thread and the Bouchaud thread ready.
3. **The refusal for Act 3:** produce it now (see the prep note) so it already sits in the thread.
4. **The floor:** refresh it, open the h2 drawer once to warm it up, and close it.
5. **Browser:** a clean profile with no bookmarks bar, zoom at 110%, dark theme on both sites.
6. **Tabs, in order:** floor (h0 row), landing, Console, terminal, editor (`config/gate.yaml`), Slack.

## Recording order (not the same as the video order)

Record by screen so you set each one up only once:
1. Landing: Acts 1 (chapters 01 and 02), 3a and 6.
2. Console: Act 2.
3. Editor and terminal: Acts 2d and 3b.
4. Slack: the Act 1 scroll past the thread roots, then Acts 3c and 4. Scroll the threads slowly, top to bottom.
5. Floor: Acts 0, 1 (strategies-table scroll) and 5.
6. Voiceover: read the lines above in one take per act, then cut the picture to the voice.

## If something stalls

- **An agent is slow:** cut. Never fake a post; every message on screen must come from a real run.
- **The orchestrator doesn't react:** run `deflate status`. A "not permitted" auto-reply to `@orchestrator` is expected; use the `deflate` prefix.
- **Running out of time:** drop the optional beat. Then shorten Act 4 to the h2 thread only (the FAIL receipt and the patch). The 3:30 cut is Acts 0–6 without 2:55–3:10 and without the editor shot in Act 3.
