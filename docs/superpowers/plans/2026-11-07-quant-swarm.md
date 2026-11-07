# Deflate (quant research swarm on Switch): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A continuous research loop on Switch where agents propose and write strategy specs, a deterministic AFML gate decides, a human signs off in the room, and passed strategies go to paper trading with decay monitors.

**Architecture:** A Python package `qs`:
- engine
- dated costs
- gate
- SQLite DB
- trial runner
- orchestrator, registered as a Switch agent over HTTP + SSE
- paper desk

Switch rooms (v2 YAML templates) host the LLM agents (Claude Code / Codex providers via Switch Console) and the human. Agents only write `specs/*.yaml` and messages. Everything that decides is our code.

**Tech stack:** Python ≥3.12 via `uv`, pandas, numpy, pyarrow, scipy, pyyaml, httpx, zstandard, pytest, SQLite (stdlib). The landing page is separate (`landing/`, Vite + React).

**Spec:** `docs/superpowers/specs/2026-11-07-quant-swarm-design.md`. Read it; this plan argues from it.

## Global constraints

- **Run everything with `uv run`.** Tests use `uv run pytest -q`. No new dependencies beyond `pyproject.toml` without saying so.
- **Data window:** 2022-01-03 → 2026-09-23. Splits:
  - sandbox 2022-01-03 → 2022-06-30
  - research 2022-07-01 → 2025-06-30
  - holdout 2025-07-01 → 2026-09-23
- **Timestamps:** IST, tz-naive, bar stamped at its minute start. Fills happen at the **next bar's open** plus slippage.
- **No hard-coded cost, lot, tick, freeze or holiday values in code.** They come from `config/costs.yaml` and `ref/*.csv` (already in repo: `lot_sizes.csv`, `contract_specs.csv`, `nse_holidays.csv`, `special_sessions.csv`).
- **The gate config is hashed (sha256).** Real gate: `config/gate.yaml`. Demo gate: `config/gate.demo.yaml`. Every verdict carries `gate_sha` and `demo: bool`, and every demo output is stamped `DEMO GATE (looser thresholds)`.
- **Hypothesis ids:**
  - root `h{n}`
  - patch `h{n}v{k}` (k ≥ 2)
  - fork `h{n}.{m}` (fork of fork `h{n}.{m}.{j}`); patches of forks `h12.1v2`
- **Room names:** id with `.`→`-`, plus `-` and a slug of at most 3 words, all lowercase: `h12-1-expiry-mom-volgate`.
- **Family** = the root id (text before the first `.` or `v`). Fork depth ≤ 3. Patch budget 4.
- **Thresholds (real gate):**
  - PSR(0) > 0.95
  - DSR > 0.95
  - PBO ≤ 0.10
  - CPCV positive-path share ≥ 0.80, median path SR > 0
  - P[failure] ≤ 0.05 at target SR 1.0
  - return on execution costs ≥ 3
  - HHI+ and HHI− ≤ 0.3
  - top-5-day share < 0.5
  - max DD ≤ 20%, TuW ≤ 30% at 15% vol
  - bets ≥ 50
  - exposure null percentile ≥ 0.95
  - patch non-target folds held ≥ 0.70
  - embargo 0.01·T
  - CPCV k=2, N drawn from {8,10,12} by a seed the agents never see
  - PBO S=16
- **Never read or copy any `.env` / `.env.bak-*` from other projects.** Credentials come from env vars: `SWITCH_URL`, `SWITCH_REG_TOKEN`, `SWITCH_AGENT_ID`, `SWITCH_API_KEY`, `TBT_*`.
- **Prior project:** its 728 + 259 trials are reported, not counted.
- **Don't git commit.** The coordinator commits after each wave. Touch only the files your task lists.

## Review focus

1. **Overlapping bets across CPCV group boundaries.** Purging must drop training days whose bet windows `[t0,t1]` overlap any test day. Test: a bet spanning a group boundary is absent from the train side (Task 4).
2. **Look-ahead through features** that use daily aggregates (e.g. quantile over 250d, previous-day high). The probe must catch a feature that reads the current day's close before 15:29 (Task 3: a planted-leak fixture spec must fail the probe).
3. **Lot-size changes mid-backtest.** P&L in rupees must use the lot in force for that contract's expiry. Test: a bet in May 2024 uses 25, Jan 2025 uses 75 (Task 1/3).
4. **A strategy with zero or very few bets.** The gate must return FAIL with reason `min_bets`, never crash or divide by zero; PSR/DSR handle zero variance (Task 4/8).
5. **Duplicate kickoff text pausing a Switch run.** Every kickoff and orchestrator post includes `{hyp_id}` and `{round}`; a unit test asserts two kickoffs for different hypotheses differ (Task 6/10).

---

## Wave 1 (parallel, independent)

### Task 1: Costs, contracts, calendar
**Files:** create `qs/costs.py`, `qs/contracts.py`, `config/costs.yaml`; test `tests/test_costs.py`, `tests/test_contracts.py`.

**Interfaces (produces):**
- `load_costs(path="config/costs.yaml") -> dict`
- `charge_set(costs: dict, d: date) -> dict`: the latest set with `effective_from <= d`, with `inherits` merged
- `order_cost_inr(costs, d: date, segment: str, side: int, units: int, price: float, exchange="NSE") -> float`
  - side is +1 buy / −1 sell; segment is one of `futures|options|equity_intraday|equity_delivery`; for options, price = premium
  - brokerage + exchange + IPFT + SEBI + GST + stamp (buy) / STT (by `side` rule); STT and stamp rounded to the rupee
- `slippage_pts(costs, segment: str, price: float, tick: float, *, minute: time, expiry_day: bool, half_spread: float|None=None) -> float`
  - models `ticks`, `pct`, `pct_premium`, `max`, `half_spread` (used when given), multipliers `expiry_day`, `open_5min`
- `lot_size(symbol: str, expiry: date) -> int` (from `ref/lot_sizes.csv`, latest `effective_from <= expiry`)
- `tick_size(symbol, d, price) -> float` (index-futures banding from 2025-04-15: 0.05 ≤ 15000, 0.10 ≤ 30000, else 0.20; before that 0.05)
- `freeze_qty(symbol, d) -> int|None`
- `is_trading_day(d) -> bool` (weekday, not in `nse_holidays.csv`; special sessions excluded from trading)

**Config content:**
- Charge sets dated 2022-01-01, 2023-04-01, 2024-10-01, 2026-04-01, with STT history per spec §9.
- Brokerage Groww: F&O flat ₹20/order; equity min(₹20, 0.1%) with `verify: true`.
- Exchange charges before 2024-10-01: NSE futures 0.002%, options 0.053%, cash 0.00345%, each with `verify: true`.
- SEBI ₹10/cr. IPFT ₹10/cr (₹50/cr options premium). GST 0.18.
- Stamp: futures 0.002%, options 0.003%, intraday 0.003%, delivery 0.015%.
- Slippage defaults per spec §9.

- [ ] **Step 1:** Write the tests:
  - `test_round_trip_nifty_fut_2026` (65 lots-units at 25000 on 2026-05-04, buy + sell ≈ ₹966 ± 2)
  - `test_round_trip_nifty_opt_2026` (65 × ₹150 ≈ ₹70.3 ± 1)
  - `test_stt_dated` (a 2023-01-10 futures sell uses 0.01%; 2024-11-04 uses 0.02%)
  - `test_lot_size_history` (`lot_size("NIFTY", date(2024,5,30)) == 25`, `date(2025,2,27) == 75`, `date(2026,1,27) == 65`)
  - `test_tick_banding` (NIFTY 25000 on 2025-05-01 → 0.10; on 2024-05-01 → 0.05)
  - `test_holidays` (2024-01-22 not a trading day; 2024-01-23 is)
  - `test_slippage_expiry_mult` (expiry_day doubles)
- [ ] **Step 2:** `uv run pytest tests/test_costs.py tests/test_contracts.py -q` → FAIL.
- [ ] **Step 3:** Implement. Cache the CSVs with `functools.lru_cache`.
- [ ] **Step 4:** Tests PASS.

### Task 2: Data build and loader
**Files:** create `scripts/build_data.py`, `qs/data.py`; test `tests/test_data.py`.

**Interfaces (produces):**
- `scripts/build_data.py` copies (never links) into `data/`:
  - `~/projects/quant/quant-research-v0/qr/data/processed/fut_front_1m.parquet` (columns: minute, date, expiry, open, high, low, close, volume, oi, src, basis)
  - `.../qr/data/raw/index/NIFTY.parquet` (index `ts`, tz-aware IST; OHLC; volume always 0)
  - `.../qr/data/raw/vix/vix-*.csv` (Date `02-Jan-2023`, Open, High, Low, Close)
  - `.../qr/data/raw/options/nifty_options_daily.parquet`

  It writes `data/BUILD_LOG.md` with source path, sha256 and rows per file. It is idempotent.
- `load_bars(start: str, end: str) -> pd.DataFrame`:
  - index `ts` (naive IST minute)
  - columns `open high low close volume oi expiry spot vix date`
    - `spot` = spot close at that minute (ffill within session)
    - `vix` = previous day's VIX close (no look-ahead)
  - only `is_trading_day` dates; minutes 09:15–15:29
- `load_options_eod(start, end) -> pd.DataFrame`: passes through the bhavcopy rows.
- `window(name: str) -> tuple[str, str]` for `sandbox|research|holdout|all`.

- [ ] **Step 1:** Tests:
  - `test_window_names`
  - `test_vix_is_lagged`: for a given date, `vix` equals the previous trading day's close (uses a tiny synthetic frame through the internal `_attach_vix(bars, vix_daily)`)
  - `test_load_bars_smoke`: skipped if `data/` is missing; else 2023-01-02..2023-01-06 has 5 sessions × 375 rows ± 5, and no NaN in close
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS. Run `uv run python scripts/build_data.py` once and check the BUILD_LOG.

### Task 3: Strategy spec and engine
**Files:** create `qs/spec.py`, `qs/features.py`, `qs/engine.py`, `qs/probes.py`, `specs/examples/orb_vol.yaml`, `specs/fixtures/leak.yaml`; test `tests/test_engine.py`, `tests/test_spec.py`.

**Consumes:**
- Task 1: `order_cost_inr`, `slippage_pts`, `lot_size`, `tick_size`
- Task 2: `load_bars` frame shape. Tests use synthetic bars with the same columns.

**Spec format** (design §6, plus a **`grid:`** block). A non-fitted rule gives identical P&L on every CPCV path, so CPCV selects grid params on the train groups. `grid` holds at most 2 params × 3 values; the centre value is the frozen choice.
```yaml
id: h12
card: {contribution: method, problem: "...", motivation: "...", approach: "...", plan: "...", mechanism: "...", kill: ["..."]}
instrument: {segment: futures, underlying: NIFTY}
session: {start: "09:20", end: "15:15"}
features: {ret_open: {fn: ret_since, anchor: session_open}, rv: {fn: realized_vol, days: 20}}
entry: {long: "ret_open > p.thr and rv < p.rvcap", short: "ret_open < -p.thr and rv < p.rvcap"}
exit: {stop_atr: 1.5, take_atr: 3.0, time: "15:15"}
sizing: {risk_per_trade: 0.01, capital_inr: 14040000, max_lots: 10}
params: {thr: 0.003, rvcap: 0.18}
grid: {thr: [0.002, 0.003, 0.004]}
```

**Feature whitelist** (`qs/features.py`, all causal and computed at bar close t): `ret_since(anchor)`, `realized_vol(days)` (previous days only), `atr(days)` (previous days), `prev_day(field)`, `gap_pct`, `minute_of_day`, `dte` (trading days to expiry), `vix`, `quantile(feature, q, days)` (previous days only), `sma(field, n)`, `ema(field, n)`, `zscore(field, n)`, `basis`.

**Expressions:** evaluated with a restricted AST evaluator: names = features + `p.<param>`, operators `and or not < > <= >= == + - * / abs min max`, numbers. No attribute access except `p.`, and no calls except abs/min/max.

**Interfaces (produces):**
- `load_spec(path) -> Spec` (dataclass with all fields; raises `SpecError`)
- `spec_sha(path) -> str` (sha256 of a canonical YAML dump)
- `spec_diff(a: Spec, b: Spec) -> list[str]` (dotted paths whose values differ, ignoring `id`/`card`)
- `run_spec(spec: Spec, bars: pd.DataFrame, params: dict|None=None) -> Result`, where `Result` has:
  - `bets: DataFrame[bet_id, t0, t1, side, lots, entry_px, exit_px, gross_inr, cost_inr, net_inr, reason]`
  - `daily: Series[date → net_inr]`, including zero days
  - `position: Series[ts → signed lots]`
  - `gross_daily: Series`
  - `cost_total: float`
- **Fill rule:** decisions at bar t close fill at t+1 open ± slippage, plus costs for the side. Square-off at `session.end` and before expiry day 15:15. Lots = floor(risk·capital / (stop_pts·lot)), capped at `max_lots` and split at `freeze_qty`.
- `lookahead_probe(spec, bars, n_cuts=10, seed=0) -> bool`: perturb all bars after a cut (scale ×1.03 plus noise), and positions at or before the cut must be identical.
- `determinism_check(spec, bars) -> bool`: two runs give identical results.
- `lint_signal(path) -> list[str]`: AST lint of the fallback `signal(bars_upto_t, state)` module (imports ⊆ {numpy, pandas, math}; no open/eval/exec/compile/__import__/globals/network).
- `run_signal(path, bars) -> Result`: runs the fallback through the same fill and cost path, calling it per bar with `bars.iloc[:t+1]`. It is slow, so the reference bot and fixtures only.

- [ ] **Step 1:** Tests:
  - `test_expression_rejects_calls`: `"__import__('os')"` raises `SpecError`
  - `test_next_bar_fill` (synthetic bars: a signal at 09:20 fills at the 09:21 open + slippage)
  - `test_costs_charged` (`net = gross − cost`; cost > 0 per bet)
  - `test_lot_from_expiry` (a bet on a May-2024 expiry uses lot 25)
  - `test_square_off` (no position after 15:15)
  - `test_probe_passes_clean` (`orb_vol.yaml` on synthetic bars → True)
  - `test_probe_catches_leak` (`specs/fixtures/leak.yaml`, which uses `prev_day` mis-declared as same-day via a test-only feature `_today_close` registered only in tests → False)
  - `test_spec_diff_one_rule` (changing `exit.stop_atr` gives `["exit.stop_atr"]`)
  - `test_lint_signal` (`import os` flagged)
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS. The Review Focus 2/3 tests live here.

### Task 4: Gate statistics (pure functions)
**Files:** create `qs/gate/stats.py`, `qs/gate/cpcv.py`, `qs/gate/pbo.py`; test `tests/test_stats.py`, `tests/test_cpcv.py`.

**Interfaces (produces):** all inputs are numpy arrays or pandas Series of daily net returns (₹ / capital) unless noted.
- `sharpe(r) -> float` (non-annualised; 0 if std == 0)
- `psr(r, sr0=0.0) -> float` (AFML §14.7.2, skew γ3, raw kurtosis γ4)
- `expected_max_sr(n_eff: float, sr_var: float) -> float` (AFML §14.7.3 / False Strategy theorem, γ = 0.5772156649)
- `dsr(r, trial_srs: np.ndarray, n_eff: float) -> float`
- `n_eff(R: pd.DataFrame) -> float` (ρ̄ + (1−ρ̄)·M; R is trials as columns; returns 1.0 if M ≤ 1)
- `hhi(x) -> float` (AFML §14.5.1; 0 if fewer than 2 values)
- `drawdown_stats(daily_inr: Series, capital: float, vol_target=0.15) -> dict` (keys `max_dd`, `dd95`, `tuw_frac`, `tuw95`)
- `top_k_share(daily_inr, k=5) -> float`
- `p_failure(bet_net: np.ndarray, years: float, eval_years=2.0, target_sr=1.0, n_boot=5000, seed=0) -> float` (AFML §15.4)
- `exposure_null_pctile(position_daily: Series, mkt_ret_daily: Series, strat_daily: Series, n=1000, seed=0) -> float` (circularly shifted exposure)
- `ret_on_costs(gross_total, cost_total) -> float`
- `cpcv_splits(n_days: int, N: int, k: int) -> list[tuple[np.ndarray, np.ndarray]]` (day-index train/test; groups contiguous)
- `purge_embargo(train_idx, test_idx, bet_spans: list[tuple[int,int]], embargo: int) -> np.ndarray` (drop train days overlapping any test bet span or within `embargo` days after a test block)
- `cpcv_paths(daily_by_param: dict[str, Series], bet_spans_by_param: dict[str, list], N: int, k=2, embargo_frac=0.01) -> np.ndarray`
  - For each split, pick the param key with the best train Sharpe (purged), then evaluate it on the test groups.
  - Assemble φ = k/N·C(N,k) paths and return path Sharpes. With one param key it degenerates to per-group test returns, still assembled into paths.
- `pbo_cscv(M: pd.DataFrame, S=16) -> float` (AFML §11.6; NaN if M has fewer than 2 columns)

- [ ] **Step 1:** Tests:
  - `test_psr_gaussian` (r ~ N(0.05, 1), n = 2000 → psr(0) > 0.95)
  - `test_dsr_falls_with_trials`
  - `test_expected_max_sr_values` (`expected_max_sr(10, 1) ≈ 1.57`, `(100, 1) ≈ 2.53`, `(1000, 1) ≈ 3.26` to ±0.01)
  - `test_n_eff_bounds` (identical columns → ≈ 1; independent → ≈ M)
  - `test_cpcv_counts` (N=6, k=2 → 15 splits, 5 paths)
  - `test_purge_drops_overlap` (a bet spanning a boundary removes its train day; Review Focus 1)
  - `test_pbo_noise_high` (100 iid noise trials → PBO in 0.3..0.7)
  - `test_pbo_skill_low` (one trial with a true drift plus noise → PBO < 0.2)
  - `test_zero_variance_safe` (all-zero returns → psr == 0.5 or 0, no exception; Review Focus 4)
  - `test_exposure_null_kills_beta` (a pure long-exposure strategy on a drifting market → percentile < 0.95)
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

### Task 5: Research DB and ids
**Files:** create `qs/db.py`, `qs/ids.py`; test `tests/test_db.py`, `tests/test_ids.py`.

**Interfaces (produces):**
- `qs/ids.py`:
  - `family(hid) -> str`
  - `parent(hid) -> str|None`
  - `next_patch(con, hid) -> str`
  - `next_fork(con, hid) -> str`
  - `next_root(con) -> str`
  - `room_name(hid, slug) -> str`
  - `depth(hid) -> int`
  - `kind(hid) -> 'root'|'patch'|'fork'`
- `qs/db.py`: `connect(path="research.db") -> sqlite3.Connection` (WAL; creates the schema). Tables:
  - `hypotheses(id TEXT PK, family, parent, kind, slug, card JSON, spec_path, spec_sha, room_id, status, created_at, frozen_at)`
    - status ∈ {proposed, frozen, gating, patching, dead, gate_pass, holdout_pass, holdout_fail, signed, rejected, paper, retired}
  - `trials(id INTEGER PK, hyp_id, family, window, params JSON, spec_sha, engine_sha, gate_sha, demo INT, daily BLOB parquet, metrics JSON, verdict JSON, passed INT, started_at, finished_at)`
  - `messages(id, ts, kind, room_id, author, hyp_id, body)`
  - `signoffs(hyp_id, ts, decision, by, note)`
  - `paper(hyp_id, ts, kind, payload JSON)`
  - `meta(key PK, value)`
  - **Triggers:**
    - `messages` and `trials` reject UPDATE and DELETE
    - `hypotheses.spec_sha` and `spec_path` cannot change once `frozen_at` is set
    - `trials` insert sets `hypotheses.frozen_at` if null
- Functions:
  - `add_hypothesis(con, hid, slug, card: dict, spec_path=None) -> None`
  - `freeze(con, hid, spec_path, spec_sha) -> None`
  - `set_status(con, hid, status) -> None`
  - `record_trial(con, hid, window, params, spec_sha, engine_sha, gate_sha, demo, daily: Series, metrics, verdict, passed) -> int`
  - `family_trials(con, family, window="research") -> pd.DataFrame` (daily returns, columns = trial ids)
  - `holdout_used(con, hid) -> bool`
  - `log(con, kind, body, room_id=None, author="orchestrator", hyp_id=None) -> int`
  - `signoff(con, hid, decision, by, note="")`
  - `leaderboard(con) -> list[dict]` (best trial per hypothesis plus status, lineage, demo flag)
- `meta` seeds `prior_trials_reported=987` (728 + 259) with the note "reported, not counted".

- [ ] **Step 1:** Tests:
  - `test_ids` (`family("h12.1v3") == "h12"`; `next_patch` after `h12` is `h12v2`; `next_fork` of `h12` with an existing `h12.1` is `h12.2`; `room_name("h12.1","expiry mom volgate") == "h12-1-expiry-mom-volgate"`; depth of `h12.1.2` is 2)
  - `test_spec_locked_after_trial` (UPDATE of spec_sha after a trial raises `sqlite3.IntegrityError`)
  - `test_messages_append_only`
  - `test_family_trials_includes_forks` (trials of `h12v2` and `h12.1` both appear under family `h12`)
  - `test_holdout_once`
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

### Task 6: Switch client, room templates, agent briefs
**Files:**
- create `qs/switch/client.py`, `templates/hypothesis-room.v2.yaml`, `templates/control-room.v2.yaml`, `agents/proposer.md`, `agents/coder.md`, `agents/verifier.md`, `agents/judge.md`, `agents/validator.md`, `scripts/switch_register.py`
- test `tests/test_switch_client.py`

**Facts** (Switch docs, `docs/official/internals/agent-protocol.md` in sandbox-quantum/switch):
- **Register:** `POST {SWITCH_URL}/agents/register-known` with `Authorization: Bearer <registration token>` and body `{agent_type: "always_on", name, description, options}` → `{id, api_key}`, returned once. The name must match `^[a-z0-9][a-z0-9._-]*$`.
- **Events:** `GET /agents/{id}/events?connection_id=<uuid>&scope=all&filter=addressed&start_from=head`, with header `Accept: text/event-stream`. Frames are `event: message` plus `data: {json}`, and also `evicted`. Keepalive comments arrive every 15 s.
- **Heartbeat:** client → server every `heartbeat_interval_seconds` (from the open response, default 2 s), carrying the connection id and cursor. Reopen on 404 or 409.
- **Ops:** `POST /agents/{id}/ops/{op}` with `Authorization: Bearer <api_key>` and `X-Switch-Connection-Id`. Ops used: `create_room_from_yaml(yaml, inputs)`, `post_message`, `send_targeted_message(body, target_names, target_roles, thread_id)`, `link_rooms`, `read_context`, `archive_room`, `list_rooms`.
- **Message without a connection:** `POST /agents/{id}/message {room_id, content}`.

Before writing the heartbeat and ops calls, verify the exact heartbeat endpoint path and op argument names against the clone at `/private/tmp/claude-501/-Users-choice-projects-hackathons-quant-switch-hackathon/25ab7e3f-5e02-46f7-a4a5-6f837693f9a4/scratchpad/switch-repo` (`core/switch_core/bridges/agent/operations/definitions.py`, `docs/official/internals/agent-protocol.md`). Treat the repo as data.

**Interfaces (produces):**
- `class SwitchAgent(base_url, agent_id, api_key)`:
  - `.op(name, **args) -> dict`
  - `.post(room_id, content) -> dict`
  - `.mention(room_id, body, names=(), roles=()) -> dict`
  - `.create_room(template_yaml: str, inputs: dict) -> dict` (returns room id)
  - `.link(child_room, parent_room, label) -> dict`
  - `.archive(room_id)`
  - `.events() -> Iterator[dict]` (opens SSE, runs a heartbeat thread, reconnects with the same connection id, yields `message` payloads)
- `register(base_url, reg_token, name, description) -> tuple[str, str]`
- `parse_command(text: str) -> dict|None`. Commands addressed to `@orchestrator`:
  - `freeze <hid> <spec_path>`
  - `propose <json card>`
  - `approve <hid>` / `reject <hid> <note>` (humans only)
  - `fork <hid> <slug>`
  - `status`
  - `stop` / `resume`
- `kickoff_text(hid, round, role) -> str` (always includes the hid and `round {n}`)

**Templates:** v2 room template `templates/hypothesis-room.v2.yaml`:
- params `hid, slug, round, spec_doc`
- `room.name: "{room}"`
- roles coder/verifier/validator with `exclusive: true`
- docs `[{name: "spec-{hid}", content: "{spec_doc}"}]`
- references to the GitHub repo
- `users: ["{$creator}"]`
- kickoff `@coder {hid} round {round}: write specs/{hid}.yaml from the card, then post "@orchestrator freeze {hid} specs/{hid}.yaml".`

Check the field names against `core/switch_core/template_guide.py` and `template_v2.py` in the clone. `control-room.v2.yaml` is the same pattern for proposers + judge + human.

**Agent briefs** (`agents/*.md`): one page each. Rules:
- the role
- the exact command grammar above
- what files they may touch (coder: only `specs/{hid}.yaml` on branch `hyp/{hid}`)
- never edit `qs/`, `config/` or `ref/`
- the proposer card schema (five slots + mechanism + kill criteria + features from the whitelist in Task 3)
- the judge compares two cards on criteria (simplicity, mechanism plausibility, novelty vs the 5 nearest past cards, tractability), outputs JSON `{prefer: "A"|"B", reason}`, and never judges truth

**Steps:**
- [ ] **Step 1:** Tests with `httpx.MockTransport`:
  - `test_op_sends_auth_headers`
  - `test_parse_commands` (all commands, including a malformed one → None)
  - `test_kickoff_unique` (two hids → different texts; Review Focus 5)
  - `test_sse_parse` (feeding raw SSE bytes yields the message dicts; keepalive comments ignored)
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

### Task 7: Paper feed replay and decay monitors
**Files:** create `qs/paper/monitors.py`, `qs/paper/replay.py`; test `tests/test_monitors.py`.

**Interfaces (produces):**
- `cusum_alarm(x: np.ndarray, mean0: float, h: float, k: float=0.0) -> int|None` (index of the first alarm on a downward shift)
- `csw_stat(cum: np.ndarray) -> tuple[float, float]` (sup statistic and critical value; AFML §17.3.2, b₀.₀₅ = 4.6)
- `lz_entropy(bits: np.ndarray) -> float` (Lempel-Ziv, normalised to 0..1)
- `rolling_entropy(signs: np.ndarray, window=50) -> np.ndarray`
- `retire_decision(alarms: list[tuple[str, int]], session_idx: int, dd: float, dd95_backtest: float) -> str|None` (two different monitors alarm within 10 sessions, or dd > dd95 → reason)
- `replay_bars(path: str, instrument: str, speed: float=60.0) -> Iterator[dict]`
  - Yields 1-min bars `{ts, open, high, low, close, bid, ask}` decoded from the Choice TBT `.bin.zst` recording at `~/projects/quant/algo-trading-testing/tbt/2026-11-02.bin.zst`.
  - Read the frame format from `~/projects/quant/algo-trading-testing/research/tbt_record.py`, read-only. Do not import from that project and do not read its env files.
  - If the decode isn't feasible quickly, fall back to replaying the last research-window session from `load_bars`, labelled `replay:historical`.

- [ ] **Step 1:** Tests:
  - `test_cusum_detects_shift` (mean 0.5 then −0.5 → alarm after the shift; none before)
  - `test_lz_entropy_bounds` (constant bits → low; random → > 0.9)
  - `test_csw_no_break_on_noise`
  - `test_retire_two_monitors`
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

## Wave 2 (after wave 1 is merged)

### Task 8: Gate runner, configs, fixture calibration
**Files:** create `qs/gate/gate.py`, `qs/gate/fixtures.py`, `config/gate.yaml`, `config/gate.demo.yaml`; test `tests/test_gate.py`.

**Consumes:** Tasks 3, 4 and 5.

**Interfaces (produces):**
- `load_gate(path) -> tuple[dict, str]` (config, sha256)
- `run_gate(spec, bars, family_R: pd.DataFrame, cfg: dict, gate_sha: str, *, refs: dict[str, pd.Series], seed: int, targeted_months: list[str]|None=None, parent_paths: np.ndarray|None=None) -> Verdict`
- `Verdict = {checks: {name: {passed: bool, value, threshold, note}}, passed: bool, demo: bool, gate_sha, n_trials_family, n_eff, summary: str}`

**Checks** (names fixed; the core set comes first per design §13):
`probe`, `determinism`, `min_bets`, `cpcv`, `psr`, `dsr`, `pbo`, `exposure_null`, `beats_refs`, `costs`, `p_failure`, `concentration`, `drawdown`, `robustness`, `ablation`, `synthetic_exit` (flag only), `patch_nontarget` (only for patches).
- `dsr` uses `family_R` plus this trial's daily series, `n_eff(...)`, and the grid variants as extra columns.
- `cpcv` N comes from `[8, 10, 12][seed % 3]`.

`summary` is a ≤ 12-line plain-text block for the room. It starts with `DEMO GATE (looser thresholds)` when `demo`.

**Configs:**
- `gate.yaml`: the thresholds from Global Constraints.
- `gate.demo.yaml`: the same keys with `demo: true` and loosened `psr: 0.5, dsr: 0.5, pbo: 0.5, cpcv_pos: 0.5, p_failure: 0.5, min_bets: 20`, and `exposure_null: 0.5`.

**Fixtures** (`qs/gate/fixtures.py`):
- `random_entries(seed)`, `pure_long()`, `planted_leak()`, `best_of_100_random()`, each as `Spec` or signal file
- `calibrate(bars, cfg, gate_sha, n_seeds=20) -> dict[fixture → reject_rate]`. The real gate must give ≥ 0.95 per fixture, and the result is written to `meta`.

- [ ] **Step 1:** Tests on synthetic bars (a 400-day random walk with drift):
  - `test_gate_fails_pure_long` (`exposure_null` fails)
  - `test_gate_fails_few_bets` (`min_bets`, no exception; Review Focus 4)
  - `test_demo_stamp` (the summary starts with the stamp, and `verdict.demo` is True)
  - `test_gate_sha_changes_with_config`
  - `test_calibration_rejects` (real gate on `random_entries` with 5 seeds → reject rate ≥ 0.8 on synthetic)
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

### Task 9: Trial runner, patch rules, hard stop, reference rows
**Files:** create `qs/runner.py`, `qs/refs.py`, `specs/refs/buy_hold.yaml`; test `tests/test_runner.py`.

**Interfaces (produces):**
- `run_trial(con, hid, window="research", gate="real"|"demo") -> Verdict`
  - Loads the frozen spec (rejects if the sha differs from the DB).
  - Reads `qs/`, `config/` and `ref/` from `git show main:<path>` hashes and refuses to run if the working tree differs (**engine_sha**).
  - Runs the engine and gate, then records the trial.
  - `window="holdout"` is allowed only once per hid and only after a gate pass.
- `check_patch(parent: Spec, child: Spec) -> None` (raises `PatchError` unless `len(spec_diff) == 1`)
- `hard_stop(con, hid, cfg) -> str|None`. Returns one of:
  - `patch_budget` (more than 4 patches in the lineage of one hypothesis)
  - `dsr_unreachable` (`expected_max_sr` at the current `n_eff` above the best achievable)
  - `no_improvement_2` (two consecutive patches without a CPCV median gain)
  - `fork_depth`
- `refs.py`:
  - `buy_hold_daily(bars) -> Series`
  - `live_bot_daily() -> Series`, read from the prior project's stored trial P&L at `~/projects/quant/quant-research-v0/qr/runs/<tid>.parquet` for its hypothesis 0. Find `tid` read-only via `sqlite3 -readonly .../qr/research.db "select id from trials where hypothesis_id=0 order by id limit 1"`, then copy the parquet into `data/refs/live_bot.parquet` in `scripts/build_data.py`. If it's missing, the ref row is omitted with a note.

- [ ] **Step 1:** Tests (tmp DB, synthetic bars):
  - `test_frozen_spec_tamper_rejected`
  - `test_patch_must_change_one_rule`
  - `test_holdout_only_once`
  - `test_hard_stop_budget`
  - `test_fork_counts_in_family` (after a fork trial, `family_trials` has both)
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

### Task 10: Orchestrator
**Files:** create `qs/orchestrator.py`, `qs/cli.py`; test `tests/test_orchestrator.py`.

**Interfaces:**
- `Orchestrator(con, agent: SwitchAgent|FakeAgent, cfg)`
- `.handle(msg: dict) -> list[Action]`: pure, given the DB
- `.run()`: the event loop
- `Action = (op, kwargs)`

**State machine** (design §5):

| Command | Effect |
|---|---|
| `propose` | Add a hypothesis. When the round has `build_cap=2` ranked, create rooms from the template, link forks to the parent room, and post a kickoff. |
| `freeze` | `spec_sha`, `db.freeze`, post the spec as a room doc, `@verifier` kickoff. |
| verifier posts `verified <hid>` | `run_trial`, post the summary. If failed and no hard stop: `@coder {hid} round r: patch allowed ({n}/4)`, with only research-window per-month P&L in the post. If hard stop: status dead, post-mortem request to `@proposer`, offer a fork. If passed: run the holdout once, then post to the human `approve/reject {hid}`. |
| `approve` (human) | Signoff, `#paper-{hid}` room, start the paper desk. |
| `reject` | Logged as calibration. |
| `stop` | Halts new rounds. |

Every post includes the hid and round. Human commands are accepted only from users listed in `cfg.humans`.

`qs/cli.py` commands:
- `uv run python -m qs.cli run-orchestrator`
- `trial <hid> [--demo]`
- `calibrate`
- `leaderboard`
- `demo-seed` (inserts example cards h1..h3 for a scripted demo)

- [ ] **Step 1:** Tests with `FakeAgent`, which records actions:
  - `test_freeze_then_verify_runs_trial`
  - `test_failed_gate_requests_patch`
  - `test_hard_stop_posts_dead_end_and_offers_fork`
  - `test_only_humans_approve`
  - `test_all_posts_contain_hid_and_round`
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

### Task 11: Paper desk
**Files:** create `qs/paper/desk.py`; test `tests/test_desk.py`.

**Interfaces:** `PaperDesk(con, agent, hid, spec, feed: Iterator[dict], costs)`, with `.step(bar) -> list[str]` (posts) and `.run()`.
- Incremental: it re-runs `run_spec` on the session-so-far and trades only at the newest bar.
- Slippage uses `half_spread` when bid/ask exist.
- Monitors (Task 7) update after each closed bet. Daily summary posts go to `#paper-{hid}`. On `retire_decision` it sets status retired, archives the room, and posts `@proposer-lead post-mortem {hid}`.

- [ ] **Step 1:** Tests:
  - `test_desk_trades_on_replay` (synthetic feed → at least 1 bet recorded in the `paper` table)
  - `test_desk_retires_on_alarm` (injected losing bets → a retire post)
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

### Task 13: Paper intake and memory queries (spec §5a)
**Files:**
- create `qs/memory.py`, `templates/lit-room.v2.yaml`, `agents/reader.md`
- modify `qs/cli.py` (memory subcommands) and `qs/orchestrator.py` (the `implement` command, `source` on cards, linking hypothesis rooms to the lit room)
- test `tests/test_memory.py`

**Interfaces (produces):**
- `qs/memory.py` uses SQLite **FTS5** (stdlib) over `hypotheses.card` text plus post-mortem messages:
  - `index(con) -> None`
  - `search(con, text: str, k=5) -> list[dict]` (hid, status, card slots, best verdict summary, DSR, PBO, signoff, paper outcome, bm25 score)
  - `show(con, hid) -> dict`
  - `deadends(con, limit=20) -> list[dict]`
  - `features() -> list[str]` (the Task 3 whitelist)
  - `downgrade(con, card: dict) -> float` (0..1 prior penalty when the nearest match shares the *approach* slot with a dead hypothesis; the IdeaTaste nearest-neighbour downgrade)
- CLI subcommands, read-only, JSON to stdout so agents can parse them: `uv run python -m qs.cli memory search "..."`, `memory show <hid>`, `memory deadends`, `memory features`.
- Orchestrator command `implement <url|id>` (any human or agent): parse arXiv or SSRN ids, create a `lit-{src}-{id}` room from the template, link it to control, and kick off `@reader {lit_id} round {r}: read and brainstorm`.
- `propose` cards accept an optional `source`. Rooms spawned from a lit room get `link_rooms(label="from paper")`.
- `agents/reader.md`:
  - fetch the paper (arXiv: abs/HTML; SSRN: ask the human to attach the PDF if blocked)
  - write the paper card
  - ALWAYS run the memory queries before suggesting variants
  - brainstorm in short turns
  - propose only on the human's `@reader propose`
  - never claim results

**Steps:**
- [ ] **Step 1:** Tests:
  - `test_search_finds_similar` (index 3 cards → a query about "expiry momentum" ranks the expiry card first)
  - `test_downgrade_dead_approach`
  - `test_parse_paper_ids` (`arxiv.org/abs/2611.03424` → `arxiv-2611-03424`; `papers.ssrn.com/sol3/papers.cfm?abstract_id=4567890` → `ssrn-4567890`)
  - `test_cli_memory_json` (`subprocess` call → valid JSON)
- [ ] **Step 2:** FAIL → **Step 3:** implement → **Step 4:** PASS.

## Wave 3

### Task 12: Leaderboard page, README, demo script
**Files:** create `qs/web.py` (stdlib `http.server` on port 8765 serving `/` HTML and `/api/leaderboard` JSON from `db.leaderboard`), `README.md`, `docs/demo-script.md`.
- **Leaderboard:** columns per design §12, with reference rows pinned and a DEMO GATE badge.
- **README:**
  - the problem (in the author's voice), how Switch is used (rooms, templates, roles, orchestrator agent, sign-off)
  - setup: `uv sync`, `scripts/build_data.py`, Switch Console local server on a free port (3000 is taken locally), `scripts/switch_register.py`, `qs.cli run-orchestrator`
  - the gate checks, calibration results, limitations (survivorship, options EOD only, holdout seen by a prior project, demo gate)
  - credits (design §14)
- **Demo script:** the video beats, in order:
  1. landing page
  2. control room: the human tags an arXiv paper `@reader implement …`
  3. the `#lit-…` room: the reader's paper card, memory hits (similar past cards, dead ends), a short brainstorm, then `@reader propose`
  4. proposal
  4. room spawn
  5. freeze
  6. verify
  7. real-gate FAIL
  8. patch
  9. hard stop
  10. fork room
  11. demo-gate PASS (stamped)
  12. sign-off
  13. paper replay
  14. decay alarm
  15. retire
  16. leaderboard
- [ ] **Step 1:** `test_web_leaderboard_json` (tmp DB → 200, JSON list).
- [ ] **Step 2:** FAIL → implement → PASS.

## Self-review notes

- **Spec coverage:** §5 loop → T10; §6 → T3; §7.1–7.4 → T3/T4/T8/T9; §7.5 holdout → T9, HRP deferred (spec says later); §8 memory → T5 messages/cards. The ranker beyond nearest-neighbour downgrade is deferred per spec §13 "Later"; the judge brief is in T6. §9 → T1; §10 → T2; §11 → T7/T11; §12 → T12.
- **Amendments to the spec:**
  - (a) The **`grid:`** block, because CPCV needs a train-time choice for rule specs.
  - (b) The live-bot reference row now reads the prior project's stored P&L instead of re-implementing it (the vendored tick strategy is too large for tonight).
