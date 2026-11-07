"""Bar engine. Decisions at bar t close fill at bar t+1 open +- slippage, plus charges per order.

Positions are intraday only: a flat order is forced so it fills at min(exit.time, session.end)
(15:15 at the latest on the contract's expiry day), and always by the last bar of the day.
"""
from __future__ import annotations

import importlib
import importlib.util
import math
from dataclasses import dataclass
from datetime import time

import numpy as np
import pandas as pd

from qs import features
from qs.spec import Spec, SpecError, compile_expr, eval_expr, expr_names, parse_hhmm

EXPIRY_SQUARE_OFF = time(15, 15)
BET_COLS = ["bet_id", "t0", "t1", "side", "lots", "entry_px", "exit_px", "gross_inr", "cost_inr", "net_inr", "reason"]


@dataclass
class Result:
    bets: pd.DataFrame       # entry_px/exit_px are fills incl. slippage; gross at raw opens; cost = charges + slippage
    daily: pd.Series         # net INR by exit day (normalized Timestamp), zero days included
    position: pd.Series      # signed lots held during each bar
    gross_daily: pd.Series
    cost_total: float


def _providers(costs, contracts):
    return (costs or importlib.import_module("qs.costs"),
            contracts or importlib.import_module("qs.contracts"))


def _mins(t: time) -> int:
    return t.hour * 60 + t.minute


def _simulate(bars, decide, *, underlying, segment, exit_time: time, costs, contracts) -> Result:
    costs, contracts = _providers(costs, contracts)
    tbl = costs.load_costs()
    n = len(bars)
    idx = bars.index
    if n and not idx.is_monotonic_increasing:
        raise ValueError("bars must be sorted by ts")
    day = idx.normalize()
    dates = idx.date
    tod = idx.time
    mins = idx.hour * 60 + idx.minute
    op = bars["open"].to_numpy(float)
    exp = pd.to_datetime(bars["expiry"]).dt.date.to_numpy()
    is_exp = dates == exp
    cutoff = np.where(is_exp, min(_mins(exit_time), _mins(EXPIRY_SQUARE_OFF)), _mins(exit_time))
    last = np.r_[day[1:] != day[:-1], True] if n else np.zeros(0, bool)
    # decision at i may leave a position open after the fill at i+1
    can_hold = np.zeros(n, bool)
    if n > 1:
        can_hold[:-1] = ~last[:-1] & ~last[1:] & (mins[1:] < cutoff[1:])

    lots_cache: dict = {}

    def lot_at(j):
        e = exp[j]
        if e not in lots_cache:
            lots_cache[e] = int(contracts.lot_size(underlying, e))
        return lots_cache[e]

    def fill(j, side, lots, lot):
        raw = op[j]
        tick = contracts.tick_size(underlying, dates[j], raw)
        slip = costs.slippage_pts(tbl, segment, raw, tick, minute=tod[j], expiry_day=bool(is_exp[j]))
        px = raw + side * slip
        units = lots * lot
        fq = contracts.freeze_qty(underlying, dates[j])
        chunks = [units] if not fq else [fq] * (units // fq) + ([units % fq] if units % fq else [])
        charges = sum(costs.order_cost_inr(tbl, dates[j], segment, side, u, px) for u in chunks)
        return px, charges + slip * units

    position = np.zeros(n)
    rows, pos, bet = [], 0, None
    for i in range(n - 1):
        position[i] = pos
        target, reason = decide(i, pos, lot_at) if can_hold[i] else (0, "time")
        if target == pos:
            continue
        j = i + 1
        if pos:
            px, c = fill(j, -bet["side"], bet["lots"], bet["lot"])
            gross = bet["side"] * (op[j] - bet["raw"]) * bet["lots"] * bet["lot"]
            cost = bet["cost"] + c
            rows.append([len(rows), bet["t0"], idx[j], bet["side"], bet["lots"], bet["px"], px,
                         gross, cost, gross - cost, reason])
            bet = None
        if target:
            side, lots, lot = (1 if target > 0 else -1), abs(int(target)), lot_at(j)
            px, c = fill(j, side, lots, lot)
            bet = dict(t0=idx[j], side=side, lots=lots, lot=lot, raw=op[j], px=px, cost=c)
        pos = target
    if n:
        position[n - 1] = pos  # always 0: the last bar of a day is never held into

    bets = pd.DataFrame(rows, columns=BET_COLS)
    bets = bets.astype({"side": int, "lots": int, "entry_px": float, "exit_px": float,
                        "gross_inr": float, "cost_inr": float, "net_inr": float})
    days = pd.DatetimeIndex(day.unique(), name="date")
    t1d = pd.DatetimeIndex(bets["t1"]).normalize()
    daily = bets.groupby(t1d)["net_inr"].sum().reindex(days, fill_value=0.0)
    gross_daily = bets.groupby(t1d)["gross_inr"].sum().reindex(days, fill_value=0.0)
    return Result(bets=bets, daily=daily.rename("net_inr"), position=pd.Series(position, index=idx, name="lots"),
                  gross_daily=gross_daily.rename("gross_inr"), cost_total=float(bets["cost_inr"].sum()))


def _exit_time(session: dict, ex: dict) -> time:
    end = parse_hhmm(session["end"], "session.end")
    return min(end, parse_hhmm(ex["time"], "exit.time")) if "time" in ex else end


def run_spec(spec: Spec, bars: pd.DataFrame, params: dict | None = None, *, costs=None, contracts=None) -> Result:
    if spec.instrument["segment"] != "futures":
        raise SpecError("engine: only futures specs run intraday (options legs are EOD, not built)")
    p = dict(spec.params)
    for k, v in (params or {}).items():
        if k not in p:
            raise SpecError(f"unknown param {k!r}")
        p[k] = v
    _, contracts = _providers(costs, contracts)

    trees = {s: compile_expr(spec.entry[s], spec.names, set(p)) for s in ("long", "short") if s in spec.entry}
    used = set().union(*(expr_names(t) for t in trees.values()))
    atr_name = "__atr"
    fspecs = {**spec.features, atr_name: {"fn": "atr", "days": spec.exit.get("atr_days", 14)}}
    env = features.compute(fspecs, bars, contracts, extra=sorted(used - set(spec.features)))
    n = len(bars)
    sig = {s: (np.broadcast_to(np.asarray(eval_expr(t, env, p), bool), (n,)) if n else np.zeros(0, bool))
           for s, t in trees.items()}
    go_long = sig.get("long", np.zeros(n, bool)) & ~sig.get("short", np.zeros(n, bool))
    go_short = sig.get("short", np.zeros(n, bool)) & ~sig.get("long", np.zeros(n, bool))
    go_long, go_short = go_long.tolist(), go_short.tolist()
    atr = env[atr_name].tolist()
    close = bars["close"].to_numpy(float).tolist()
    op = bars["open"].to_numpy(float).tolist()
    mins = (bars.index.hour * 60 + bars.index.minute).tolist()
    day = bars.index.normalize()
    start = _mins(parse_hhmm(spec.session["start"]))
    stop_atr, take_atr = spec.exit["stop_atr"], spec.exit.get("take_atr")
    risk = spec.sizing["risk_per_trade"] * spec.sizing["capital_inr"]
    max_lots, max_per_day = int(spec.sizing["max_lots"]), spec.entry.get("max_per_day")
    st = {"day": None, "count": 0}

    def decide(i, pos, lot_at):
        if pos:
            move = (1 if pos > 0 else -1) * (close[i] - op[st["j"]])
            if move <= -st["stop"]:
                return 0, "stop"
            if st["take"] is not None and move >= st["take"]:
                return 0, "take"
            return pos, None
        if st["day"] != day[i]:
            st["day"], st["count"] = day[i], 0
        if mins[i] < start or (max_per_day and st["count"] >= max_per_day):
            return 0, None
        side = 1 if go_long[i] else -1 if go_short[i] else 0
        a = atr[i]
        if not side or not (a > 0):  # also rejects NaN warm-up
            return 0, None
        stop = stop_atr * a
        lots = min(math.floor(risk / (stop * lot_at(i + 1))), max_lots)
        if lots < 1:
            return 0, None
        st.update(j=i + 1, stop=stop, take=take_atr * a if take_atr else None, count=st["count"] + 1)
        return side * lots, "entry"

    return _simulate(bars, decide, underlying=spec.instrument["underlying"], segment="futures",
                     exit_time=_exit_time(spec.session, spec.exit), costs=costs, contracts=contracts)


def run_signal(path, bars: pd.DataFrame, *, underlying="NIFTY", session_end="15:15",
               costs=None, contracts=None) -> Result:
    """Fallback `signal(bars_upto_t, state) -> target signed lots`, through the same fill/cost path.
    `state` is a dict kept across calls; state['pos'] is the position held during bar t. Slow (O(n^2))."""
    from qs.probes import lint_signal

    issues = lint_signal(path)
    if issues:
        raise SpecError("signal lint failed: " + "; ".join(issues))
    # ponytail: the AST lint is the guard, not a sandbox; run untrusted code in a subprocess/container if needed
    mod_spec = importlib.util.spec_from_file_location("qs_user_signal", path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    state: dict = {}

    def decide(i, pos, lot_at):
        state["pos"] = pos
        t = mod.signal(bars.iloc[: i + 1], state)
        if not isinstance(t, (int, np.integer, float, np.floating)) or not math.isfinite(t) or t != int(t):
            raise SpecError(f"signal returned {t!r}; expected an integer lot count")
        return int(t), "signal"

    return _simulate(bars, decide, underlying=underlying, segment="futures",
                     exit_time=parse_hhmm(session_end), costs=costs, contracts=contracts)
