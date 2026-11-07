"""Feature whitelist. Every feature is causal: its value at bar t uses bars <= t only.

Daily features (realized_vol, atr, prev_day, quantile, gap_pct) are computed on a daily table
built from the intraday bars and shifted one day, so day D only sees days < D (gap_pct uses D's
first open, which is known at D's first bar).
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from functools import cached_property

import numpy as np
import pandas as pd

REGISTRY: dict = {}
DAILY_FIELDS = {"open", "high", "low", "close"}
BAR_FIELDS = {"open", "high", "low", "close", "volume", "oi", "spot", "basis"}
ANCHORS = {"session_open", "prev_close"}
SESSION_OPEN_MIN = 9 * 60 + 15  # minute_of_day counts from 09:15
ANNUAL = np.sqrt(252)


def register(name):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


def deps(args: dict) -> list[str]:
    """Names of other features a feature spec reads (only quantile has any)."""
    return [args["feature"]] if args.get("fn") == "quantile" else []


def implicit_names() -> set[str]:
    """Registry features with no arguments: usable in expressions without declaring them."""
    return {n for n, f in REGISTRY.items() if len(inspect.signature(f).parameters) == 1}


def validate(args: dict) -> None:
    """Raise ValueError if a feature spec {fn: ..., **kw} is not well formed."""
    fn = REGISTRY.get(args.get("fn"))
    if fn is None:
        raise ValueError(f"unknown feature fn {args.get('fn')!r}")
    kw = {k: v for k, v in args.items() if k != "fn"}
    inspect.signature(fn).bind(None, **kw)  # TypeError on missing/extra args
    for k in ("days", "n"):
        if k in kw and not (isinstance(kw[k], int) and not isinstance(kw[k], bool) and kw[k] >= 1):
            raise ValueError(f"{k} must be a positive int")
    if "anchor" in kw and kw["anchor"] not in ANCHORS:
        raise ValueError(f"anchor must be one of {sorted(ANCHORS)}")
    if "field" in kw:
        ok = DAILY_FIELDS if args["fn"] == "prev_day" else BAR_FIELDS
        if kw["field"] not in ok:
            raise ValueError(f"field must be one of {sorted(ok)}")
    if "q" in kw and not (isinstance(kw["q"], (int, float)) and 0 <= kw["q"] <= 1):
        raise ValueError("q must be in [0, 1]")


class Ctx:
    def __init__(self, bars: pd.DataFrame, contracts, specs: dict):
        self.bars, self.contracts, self.specs = bars, contracts, specs
        self.day = bars.index.normalize()
        self._cache: dict = {}
        self._busy: set = set()

    @cached_property
    def daily(self) -> pd.DataFrame:
        g = self.bars.groupby(self.day)
        return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                             "low": g["low"].min(), "close": g["close"].last()})

    def by_day(self, s: pd.Series) -> np.ndarray:
        return s.reindex(self.day).to_numpy(float)

    def field(self, name: str) -> pd.Series:
        return self.bars["close"] - self.bars["spot"] if name == "basis" else self.bars[name].astype(float)

    def get(self, name: str) -> np.ndarray:
        if name not in self._cache:
            if name in self._busy:
                raise ValueError(f"feature cycle at {name!r}")
            self._busy.add(name)
            args = self.specs.get(name, {"fn": name})
            kw = {k: v for k, v in args.items() if k != "fn"}
            self._cache[name] = np.asarray(REGISTRY[args["fn"]](self, **kw), dtype=float)
            self._busy.discard(name)
        return self._cache[name]


def compute(specs: dict, bars: pd.DataFrame, contracts=None, extra=()) -> dict[str, np.ndarray]:
    """Compute declared features `specs` (name -> {fn, **args}) plus implicit names in `extra`."""
    ctx = Ctx(bars, contracts, specs)
    return {n: ctx.get(n) for n in [*specs, *extra]}


@register("ret_since")
def ret_since(ctx, anchor):
    c = ctx.bars["close"].to_numpy(float)
    if anchor == "session_open":
        ref = ctx.by_day(ctx.daily["open"])
    else:
        ref = ctx.by_day(ctx.daily["close"].shift(1))
    return c / ref - 1


@register("realized_vol")
def realized_vol(ctx, days):
    lr = np.log(ctx.daily["close"]).diff()
    return ctx.by_day(lr.rolling(days).std().shift(1) * ANNUAL)


@register("atr")
def atr(ctx, days):
    d = ctx.daily
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    tr[pc.isna()] = np.nan
    return ctx.by_day(tr.rolling(days).mean().shift(1))


@register("prev_day")
def prev_day(ctx, field):
    return ctx.by_day(ctx.daily[field].shift(1))


@register("gap_pct")
def gap_pct(ctx):
    d = ctx.daily
    return ctx.by_day(d["open"] / d["close"].shift(1) - 1)


@register("minute_of_day")
def minute_of_day(ctx):
    idx = ctx.bars.index
    return idx.hour * 60 + idx.minute - SESSION_OPEN_MIN


@register("dte")
def dte(ctx):
    """Trading days in (date, expiry]; 0 on expiry day."""
    exp = pd.to_datetime(ctx.bars["expiry"]).dt.normalize()
    pairs = pd.DataFrame({"d": ctx.day, "e": exp.to_numpy()})
    out = {}
    for d, e in pairs.drop_duplicates().itertuples(index=False):
        n, cur = 0, d.date()
        while cur < e.date():
            cur += timedelta(days=1)
            n += ctx.contracts.is_trading_day(cur)
        out[(d, e)] = n
    return np.array([out[k] for k in zip(pairs["d"], pairs["e"])], dtype=float)


@register("vix")
def vix(ctx):
    return ctx.bars["vix"].to_numpy(float)  # loader already lags VIX to the previous day's close


@register("quantile")
def quantile(ctx, feature, q, days):
    """Rolling q-quantile of `feature`'s end-of-day value over the previous `days` days."""
    eod = pd.Series(ctx.get(feature), index=ctx.bars.index).groupby(ctx.day).last()
    return ctx.by_day(eod.rolling(days).quantile(q).shift(1))


@register("sma")
def sma(ctx, field, n):
    return ctx.field(field).rolling(n).mean().to_numpy()


@register("ema")
def ema(ctx, field, n):
    return ctx.field(field).ewm(span=n, adjust=False).mean().to_numpy()


@register("zscore")
def zscore(ctx, field, n):
    x = ctx.field(field)
    r = x.rolling(n)
    return ((x - r.mean()) / r.std()).to_numpy()


@register("basis")
def basis(ctx):
    return ctx.field("basis").to_numpy()
