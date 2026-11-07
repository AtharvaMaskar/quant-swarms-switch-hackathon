from datetime import date, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

from qs import features
from qs.engine import run_spec, run_signal
from qs.probes import determinism_check, lookahead_probe
from qs.spec import load_spec

ROOT = Path(__file__).resolve().parents[1]
ORB = ROOT / "specs/examples/orb_vol.yaml"
LEAK = ROOT / "specs/fixtures/leak.yaml"


# ---- stubs for Task 1 (costs/contracts) so these tests stand alone ----------------------------
def _order_cost(costs, d, segment, side, units, price, exchange="NSE"):
    return 20.0 + 0.0001 * units * price


def _slip(costs, segment, price, tick, *, minute, expiry_day, half_spread=None):
    return 2 * tick * (2 if expiry_day else 1)


COSTS = SimpleNamespace(load_costs=lambda: {}, order_cost_inr=_order_cost, slippage_pts=_slip)


class Contracts:
    def __init__(self):
        self.lot_calls = []

    def lot_size(self, symbol, expiry):
        self.lot_calls.append((symbol, expiry))
        for start, lot in [(date(2026, 1, 6), 65), (date(2025, 1, 2), 75), (date(2024, 5, 2), 25)]:
            if expiry >= start:
                return lot
        return 50

    def tick_size(self, symbol, d, price):
        return 0.05

    def freeze_qty(self, symbol, d):
        return None

    def is_trading_day(self, d):
        return d.weekday() < 5


def make_bars(start="2024-04-01", days=40, seed=0, expiry=None):
    rng = np.random.default_rng(seed)
    out, px, d = [], 22000.0, pd.Timestamp(start)
    while len(out) < days:
        if d.weekday() < 5:
            ts = pd.date_range(d + pd.Timedelta("09:15:00"), periods=375, freq="min")
            px *= 1 + rng.normal(0, 0.004)  # overnight gap
            r = rng.normal(0, 0.0004, 375)  # ~14% annualised vol
            close = px * np.exp(np.cumsum(r))
            open_ = np.r_[px, close[:-1]]
            hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0002, 375)))
            lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0002, 375)))
            exp = expiry or (d + pd.Timedelta(days=(3 - d.weekday()) % 7)).date()
            out.append(pd.DataFrame({
                "open": open_, "high": hi, "low": lo, "close": close,
                "volume": rng.integers(100, 1000, 375), "oi": 1e6, "expiry": exp,
                "spot": close - 20, "vix": 14.0, "date": d.date()}, index=pd.Index(ts, name="ts")))
            px = close[-1]
        d += pd.Timedelta(days=1)
    return pd.concat(out)


def _spec(tmp_path, **over):
    d = yaml.safe_load(ORB.read_text())
    for k, v in over.items():
        d[k] = v
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump(d))
    return load_spec(p)


KW = dict(costs=COSTS)


@pytest.fixture
def contracts():
    return Contracts()


def test_next_bar_fill(tmp_path, contracts):
    bars = make_bars(days=16)
    spec = _spec(tmp_path, features={"m": {"fn": "minute_of_day"}},
                 entry={"long": "m == 5", "max_per_day": 1})  # 09:20 bar
    res = run_spec(spec, bars, contracts=contracts, **KW)
    day = bars.index.normalize().unique()[15]  # first day with 14d of prior ATR
    b = res.bets[res.bets.t0.dt.normalize() == day].iloc[0]
    t21 = day + pd.Timedelta("09:21:00")
    assert b.t0 == t21 and b.side == 1
    assert b.entry_px == pytest.approx(bars.loc[t21, "open"] + 0.1)  # 2 ticks of 0.05
    assert res.position[day + pd.Timedelta("09:20:00")] == 0
    assert res.position[t21] == b.lots > 0


def test_costs_charged(contracts):
    bars = make_bars(days=60)
    res = run_spec(load_spec(ORB), bars, contracts=contracts, **KW)
    bets = res.bets
    assert len(bets) > 0
    assert np.allclose(bets.net_inr, bets.gross_inr - bets.cost_inr)
    assert (bets.cost_inr > 0).all()
    assert res.cost_total == pytest.approx(bets.cost_inr.sum())
    assert res.daily.sum() == pytest.approx(bets.net_inr.sum())
    assert len(res.daily) == bars.index.normalize().nunique()  # includes zero days
    assert (res.daily.reindex(res.gross_daily.index) <= res.gross_daily + 1e-9).all()


def _check_gross(res, bars, lot):
    for b in res.bets.itertuples():
        raw = bars.loc[b.t1, "open"] - bars.loc[b.t0, "open"]
        assert b.gross_inr == pytest.approx(b.side * raw * b.lots * lot)


def test_lot_from_expiry(contracts):
    bars = make_bars(start="2024-04-15", days=40, expiry=date(2024, 5, 30))
    res = run_spec(load_spec(ORB), bars, contracts=contracts, **KW)
    assert len(res.bets) > 0
    assert set(contracts.lot_calls) == {("NIFTY", date(2024, 5, 30))}
    _check_gross(res, bars, 25)


def test_lot_from_expiry_real_contracts():
    """Review focus 3 against the real Task 1 module (skipped until it lands)."""
    try:
        from qs import contracts as real
        real.lot_size("NIFTY", date(2024, 5, 30))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"qs.contracts not ready: {e}")
    for start, exp, lot in [("2024-04-15", date(2024, 5, 30), 25), ("2024-12-02", date(2025, 2, 27), 75)]:
        bars = make_bars(start=start, days=40, expiry=exp)
        res = run_spec(load_spec(ORB), bars, contracts=real, **KW)
        assert len(res.bets) > 0
        _check_gross(res, bars, lot)


def test_square_off(tmp_path, contracts):
    bars = make_bars(days=30)
    spec = _spec(tmp_path, features={"m": {"fn": "minute_of_day"}}, entry={"long": "m >= 0"},
                 session={"start": "09:20", "end": "15:25"},
                 exit={"stop_atr": 50.0, "atr_days": 3, "time": "15:25"},
                 sizing={"risk_per_trade": 1.0, "capital_inr": 1e9, "max_lots": 1})
    res = run_spec(spec, bars, contracts=contracts, **KW)
    pos, tod = res.position, res.position.index.time
    exp_day = np.array([d.date() == e for d, e in zip(res.position.index, bars.expiry)])
    assert (pos[tod >= time(15, 25)] == 0).all()
    assert (pos[exp_day & (tod >= time(15, 15))] == 0).all()           # expiry day: out by 15:15
    assert (pos[~exp_day & (tod == time(15, 20))] != 0).any()          # other days hold to 15:25
    # the day boundary never carries a position
    last = pos.groupby(pos.index.normalize()).tail(1)
    assert (last == 0).all()


def test_probe_passes_clean(contracts):
    bars = make_bars(days=60)
    spec = load_spec(ORB)
    assert (run_spec(spec, bars, contracts=contracts, **KW).position != 0).any()
    assert lookahead_probe(spec, bars, contracts=contracts, **KW) is True
    assert determinism_check(spec, bars, contracts=contracts, **KW) is True


@pytest.fixture
def today_close():
    @features.register("_today_close")
    def _today_close(ctx):  # LEAK: the day's final close, visible from 09:15
        return ctx.bars.groupby(ctx.day)["close"].transform("last").to_numpy()
    yield
    features.REGISTRY.pop("_today_close")


def test_probe_catches_leak(today_close, contracts):
    bars = make_bars(days=30)
    spec = load_spec(LEAK)
    assert (run_spec(spec, bars, contracts=contracts, **KW).position != 0).any()
    assert lookahead_probe(spec, bars, contracts=contracts, **KW) is False


ALL_FEATURES = {
    "ro": {"fn": "ret_since", "anchor": "session_open"},
    "rp": {"fn": "ret_since", "anchor": "prev_close"},
    "rv": {"fn": "realized_vol", "days": 5},
    "at": {"fn": "atr", "days": 5},
    "ph": {"fn": "prev_day", "field": "high"},
    "q": {"fn": "quantile", "feature": "rv", "q": 0.6, "days": 5},
    "qi": {"fn": "quantile", "feature": "ro", "q": 0.5, "days": 5},
    "s": {"fn": "sma", "field": "close", "n": 30},
    "e": {"fn": "ema", "field": "close", "n": 30},
    "z": {"fn": "zscore", "field": "close", "n": 30},
    "g": {"fn": "gap_pct"}, "m": {"fn": "minute_of_day"}, "dte": {"fn": "dte"},
    "v": {"fn": "vix"}, "b": {"fn": "basis"},
}


def test_every_feature_is_causal(contracts):
    bars = make_bars(days=25)
    base = features.compute(ALL_FEATURES, bars, contracts)
    rng = np.random.default_rng(1)
    for cut in rng.integers(375 * 6, len(bars) - 1, 8):
        pert = bars.copy()
        cols = ["open", "high", "low", "close", "spot"]
        pert.iloc[cut + 1:, [pert.columns.get_loc(c) for c in cols]] *= 1.03
        got = features.compute(ALL_FEATURES, pert, contracts)
        for k in ALL_FEATURES:
            np.testing.assert_array_equal(base[k][:cut + 1], got[k][:cut + 1], err_msg=k)
    # sanity: daily features are previous-day values
    days = bars.index.normalize()
    d1 = days.unique()[10]
    prev = bars[days == days.unique()[9]]
    assert np.all(base["ph"][days == d1] == prev["high"].max())
    assert base["dte"][bars.index.get_loc(pd.Timestamp("2024-04-01 10:00"))] == 3  # Mon -> Thu


def test_run_signal_same_path(tmp_path, contracts):
    p = tmp_path / "sig.py"
    p.write_text("def signal(bars, state):\n"
                 "    t = bars.index[-1]\n"
                 "    return 1 if (t.hour, t.minute) == (9, 30) else state.get('pos', 0) if t.hour < 11 else 0\n")
    bars = make_bars(days=3)
    res = run_signal(p, bars, contracts=contracts, **KW)
    assert len(res.bets) == 3
    b = res.bets.iloc[0]
    assert b.t0.time() == time(9, 31) and b.t1.time() == time(11, 1)
    assert np.allclose(res.bets.net_inr, res.bets.gross_inr - res.bets.cost_inr)
    bad = tmp_path / "bad.py"
    bad.write_text("import os\ndef signal(b, s):\n    return 0\n")
    with pytest.raises(Exception):
        run_signal(bad, bars, contracts=contracts, **KW)
