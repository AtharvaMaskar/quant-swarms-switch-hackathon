"""No-skill fixtures and gate calibration (design §7.3). The real gate must reject >= 95% of each."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from qs import features
from qs.engine import run_spec
from qs.gate.gate import run_gate
from qs.gate.stats import sharpe
from qs.spec import REQUIRED, Spec, _validate, load_spec

ROOT = Path(__file__).resolve().parents[2]
LEAK = ROOT / "specs/fixtures/leak.yaml"
FIXTURES = ("random_entries", "pure_long", "planted_leak", "best_of_100_random")


@features.register("_fx_uniform")
def _fx_uniform(ctx, seed):
    """Seeded U(0,1) per bar. Independent of prices, so causal (passes the probe)."""
    return np.random.default_rng(seed).random(len(ctx.bars))


def _today_close(ctx):  # the planted leak: the day's final close, visible from 09:15
    return ctx.bars.groupby(ctx.day)["close"].transform("last").to_numpy()


def _spec(raw: dict) -> Spec:
    s = Spec(**{k: raw[k] for k in REQUIRED}, card=raw.get("card", {}), params=raw.get("params", {}),
             grid=raw.get("grid", {}), raw=raw)
    _validate(s)
    return s


_BASE = dict(instrument={"segment": "futures", "underlying": "NIFTY"},
             session={"start": "09:20", "end": "15:15"},
             sizing={"risk_per_trade": 0.01, "capital_inr": 14040000, "max_lots": 10})


def random_entries(seed: int, freq: float = 0.004) -> Spec:
    """Coin-flip entries; per-bar probability `freq` gives ~0.75 bets/day, matching orb_vol's rate."""
    return _spec({**_BASE, "id": f"fx_random_{seed}", "card": {"contribution": "fixture"},
                  "features": {"u": {"fn": "_fx_uniform", "seed": int(seed)}},
                  "entry": {"long": "u < p.f / 2", "short": "u > 1 - p.f / 2", "max_per_day": 1},
                  "exit": {"stop_atr": 1.5, "take_atr": 3.0, "atr_days": 14, "time": "15:15"},
                  "params": {"f": freq}})


def pure_long() -> Spec:
    """Long 1 lot from 09:20 to 15:15 every day; the stop is out of reach. Pure beta."""
    return _spec({**_BASE, "id": "fx_pure_long", "card": {"contribution": "fixture"}, "features": {},
                  "entry": {"long": "minute_of_day >= 5", "max_per_day": 1},
                  "exit": {"stop_atr": 50, "atr_days": 14, "time": "15:15"},
                  "sizing": {"risk_per_trade": 1.0, "capital_inr": 14040000, "max_lots": 1}})


def planted_leak() -> Spec:
    """specs/fixtures/leak.yaml: reads today's close from the first bar. Must fail the probe."""
    features.REGISTRY.setdefault("_today_close", _today_close)  # re-added each call; tests may pop it
    return load_spec(LEAK)


def best_of_100_random(bars: pd.DataFrame, seed: int = 0) -> tuple[Spec, pd.DataFrame]:
    """The luckiest of 100 random-entry specs, plus the other 99 as its family (daily returns)."""
    pool = {}
    for i in range(100):
        s = random_entries(seed * 1000 + i)
        pool[s.id] = (s, run_spec(s, bars).daily / s.sizing["capital_inr"])
    best = max(pool, key=lambda k: sharpe(pool[k][1]))
    fam = pd.DataFrame({k: r for k, (_, r) in pool.items() if k != best})
    return pool[best][0], fam


def _buy_hold(bars: pd.DataFrame) -> pd.Series:
    return bars.groupby(bars.index.normalize())["close"].last().pct_change().fillna(0.0)


def calibrate(bars, cfg, gate_sha, n_seeds: int = 20, *, con=None, refs=None, only=None) -> dict[str, float]:
    """Reject rate per fixture over `n_seeds` gate seeds (CPCV N, probe cuts, nulls vary with the seed).
    Written to meta as `calibration:<gate_sha>` when `con` is given."""
    refs = {"buy_hold": _buy_hold(bars)} if refs is None else refs
    empty = pd.DataFrame()
    rates = {}
    for name in only or FIXTURES:
        rejects = 0
        for seed in range(n_seeds):
            fam = empty
            if name == "random_entries":
                spec = random_entries(seed)
            elif name == "pure_long":
                spec = pure_long()
            elif name == "planted_leak":
                spec = planted_leak()
            else:
                spec, fam = best_of_100_random(bars, seed)
            rejects += not run_gate(spec, bars, fam, cfg, gate_sha, refs=refs, seed=seed)["passed"]
        rates[name] = rejects / n_seeds
    if con is not None:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                    (f"calibration:{gate_sha}", json.dumps({"n_seeds": n_seeds, "reject_rate": rates})))
    return rates
