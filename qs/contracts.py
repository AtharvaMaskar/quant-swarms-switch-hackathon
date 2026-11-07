"""Dated contract facts: lot, tick, freeze qty, trading calendar (from ref/*.csv + config/costs.yaml)."""
import csv
import re
from bisect import bisect_right
from datetime import date
from functools import lru_cache
from pathlib import Path

from qs.costs import _as_date, load_costs

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "ref"


def _rows(name):
    with open(REF / name, newline="") as f:
        return list(csv.DictReader(f))


def _latest(series: list[tuple[date, object]], d: date):
    """series sorted by date; value of the last entry with date <= d, else None."""
    i = bisect_right([k for k, _ in series], d)
    return series[i - 1][1] if i else None


@lru_cache(maxsize=None)
def _lots() -> dict[str, list]:
    out: dict[str, list] = {}
    for r in _rows("lot_sizes.csv"):
        out.setdefault(r["symbol"], []).append((date.fromisoformat(r["effective_from"]), int(r["lot_size"])))
    return {k: sorted(v) for k, v in out.items()}


def lot_size(symbol: str, expiry: date) -> int:
    lot = _latest(_lots().get(symbol, []), expiry)
    if lot is None:
        raise KeyError(f"no lot size for {symbol} expiring {expiry}")
    return lot


@lru_cache(maxsize=None)
def _ticks() -> dict:
    return load_costs(ROOT / "config" / "costs.yaml")["tick_size"]


def tick_size(symbol, d, price, kind: str = "FUT") -> float:
    """kind="OPT" for options (index options are not banded)."""
    t = _ticks()
    if kind != "FUT" or symbol not in t["index_symbols"]:
        return t["default"]
    regime = _latest(sorted((_as_date(b["effective_from"]), b["bands"]) for b in t["index_futures_bands"]), d)
    if regime is None:
        return t["default"]
    for upper, tick in regime:
        if upper is None or price <= upper:
            return tick
    raise ValueError("tick bands must end with a null upper bound")


@lru_cache(maxsize=None)
def _freeze() -> dict[str, list]:
    out: dict[str, list] = {}
    for r in _rows("contract_specs.csv"):
        if r["field"] == "freeze_qty":
            qty = int(re.match(r"\s*(\d+)", r["value"]).group(1))
            out.setdefault(r["symbol"], []).append((date.fromisoformat(r["effective_from"]), qty))
    return {k: sorted(v) for k, v in out.items()}


def freeze_qty(symbol, d) -> int | None:
    return _latest(_freeze().get(symbol, []), d)


@lru_cache(maxsize=None)
def _closed_days() -> frozenset:
    days = {date.fromisoformat(r["date"]) for r in _rows("nse_holidays.csv") if "FO" in r["segment"]}
    days |= {date.fromisoformat(r["date"]) for r in _rows("special_sessions.csv")}
    return frozenset(days)


def is_trading_day(d) -> bool:
    """Regular session day: weekday, not a holiday; special sessions (muhurat, Saturday drills) excluded."""
    return d.weekday() < 5 and d not in _closed_days()
