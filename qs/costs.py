"""Dated transaction costs and slippage, all numbers from config/costs.yaml."""
import math
from datetime import date, datetime, time, timedelta

import yaml

SEGMENTS = ("futures", "options", "equity_intraday", "equity_delivery")


def load_costs(path="config/costs.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _resolve(by_date: dict, s: dict) -> dict:
    if "inherits" not in s:
        return s
    parent = by_date[_as_date(s["inherits"])]
    return _merge(_resolve(by_date, parent), {k: v for k, v in s.items() if k != "inherits"})


def _as_date(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def charge_set(costs: dict, d: date) -> dict:
    sets = costs["charge_sets"]
    by_date = {_as_date(s["effective_from"]): s for s in sets}
    live = [k for k in by_date if k <= d]
    if not live:
        raise ValueError(f"no charge set in force on {d}")
    return _resolve(by_date, by_date[max(live)])


def _rupee(x: float) -> float:
    return math.floor(x + 0.5)  # half-up, as on contract notes


def order_cost_inr(costs, d: date, segment: str, side: int, units: int, price: float, exchange="NSE") -> float:
    """All charges for one executed order. side +1 buy / -1 sell; options price = premium."""
    if segment not in SEGMENTS:
        raise ValueError(f"unknown segment {segment!r}")
    if side not in (1, -1):
        raise ValueError(f"side must be +1 or -1, got {side!r}")
    if units == 0:
        return 0.0
    cs = charge_set(costs, d)
    seg = cs[segment]
    turnover = abs(units) * price

    b = seg["brokerage"]
    brokerage = b["flat"] if "pct" not in b else min(b["flat"], b["pct"] * turnover)
    exch = seg["exchange"][exchange] * turnover
    sebi = cs["sebi_per_cr"] * turnover / 1e7
    ipft = seg["ipft_per_cr"] * turnover / 1e7
    gst = cs["gst"] * (brokerage + exch + sebi + ipft)

    stt_side = seg["stt"]["side"]
    stt_due = stt_side == "both" or (stt_side == "sell") == (side == -1)
    stt = _rupee(seg["stt"]["rate"] * turnover) if stt_due else 0.0
    stamp = _rupee(seg["stamp"] * turnover) if side == 1 else 0.0
    return brokerage + exch + sebi + ipft + gst + stt + stamp


def _val(x):
    return x["value"] if isinstance(x, dict) else x


def slippage_pts(costs, segment: str, price: float, tick: float, *, minute: time, expiry_day: bool,
                 half_spread: float | None = None) -> float:
    sl = costs["slippage"]
    if half_spread is not None:
        pts = half_spread
    else:
        m = sl["segments"][segment]
        parts = []
        if "ticks" in m:
            parts.append(m["ticks"] * tick)
        if "pct" in m:
            parts.append(m["pct"] * price)
        if "pct_premium" in m:
            parts.append(m["pct_premium"] * price)
        pts = max(parts) if m["combine"] == "max" else sum(parts)

    mult = sl["multipliers"]
    if expiry_day:
        pts *= _val(mult["expiry_day"])
    open_t = time.fromisoformat(sl["session_open"])
    open_end = (datetime.combine(date.min, open_t) + timedelta(minutes=5)).time()
    if open_t <= minute < open_end:
        pts *= _val(mult["open_5min"])
    return pts
