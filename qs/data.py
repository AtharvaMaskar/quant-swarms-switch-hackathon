"""Loaders over the copied data in data/ (see scripts/build_data.py)."""
import csv
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

WINDOWS = {
    "sandbox": ("2022-01-03", "2022-06-30"),
    "research": ("2022-07-01", "2025-06-30"),
    "holdout": ("2025-07-01", "2026-09-23"),
    "all": ("2022-01-03", "2026-09-23"),
}

try:
    from qs.contracts import is_trading_day
except ImportError:  # contracts.py not merged yet

    @lru_cache(maxsize=1)
    def _closed() -> frozenset:
        days = set()
        for name in ("nse_holidays.csv", "special_sessions.csv"):
            with open(ROOT / "ref" / name) as f:
                days |= {date.fromisoformat(r["date"]) for r in csv.DictReader(f)}
        return frozenset(days)

    def is_trading_day(d: date) -> bool:
        return d.weekday() < 5 and d not in _closed()


def window(name: str) -> tuple[str, str]:
    return WINDOWS[name]


def _attach_vix(bars: pd.DataFrame, vix_daily: pd.Series) -> pd.DataFrame:
    """vix = the last VIX close strictly before the bar's date (no look-ahead)."""
    v = pd.DataFrame({"vdate": pd.to_datetime(vix_daily.index).as_unit("ns"), "vix": vix_daily.to_numpy(dtype=float)}).sort_values("vdate")
    d = pd.DataFrame({"date": pd.DatetimeIndex(bars["date"]).as_unit("ns")}).reset_index(names="_i")
    m = pd.merge_asof(d.sort_values("date"), v, left_on="date", right_on="vdate", allow_exact_matches=False)
    out = bars.copy()
    out["vix"] = m.sort_values("_i")["vix"].to_numpy()
    return out


@lru_cache(maxsize=1)
def _vix_daily() -> pd.Series:
    df = pd.concat([pd.read_csv(p) for p in sorted((DATA / "vix").glob("vix-*.csv"))])
    df.columns = df.columns.str.strip()
    s = pd.Series(df["Close"].astype(float).to_numpy(), index=pd.to_datetime(df["Date"].str.strip(), format="%d-%b-%Y"))
    return s[~s.index.duplicated(keep="last")].sort_index()


@lru_cache(maxsize=1)
def _fut() -> pd.DataFrame:
    return pd.read_parquet(DATA / "fut_front_1m.parquet")


@lru_cache(maxsize=1)
def _spot() -> pd.Series:
    s = pd.read_parquet(DATA / "index" / "NIFTY.parquet")["close"]
    if s.index.tz is not None:
        s.index = s.index.tz_convert("Asia/Kolkata").tz_localize(None)
    return s[~s.index.duplicated(keep="last")]


def load_bars(start: str, end: str) -> pd.DataFrame:
    f = _fut()
    ts = pd.to_datetime(f["minute"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    days = ts.dt.normalize()
    keep = (days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))
    hm = ts.dt.strftime("%H:%M")
    keep &= (hm >= "09:15") & (hm <= "15:29")
    b = f.loc[keep, ["open", "high", "low", "close", "volume", "oi", "expiry"]].copy()
    b.index = pd.Index(ts[keep], name="ts")
    b["date"] = days[keep].to_numpy()
    b = b[b["date"].dt.date.map(is_trading_day)].sort_index()
    sp = _spot().reindex(b.index)
    b["spot"] = sp.groupby(b["date"].to_numpy()).ffill()
    b = _attach_vix(b, _vix_daily())
    return b[["open", "high", "low", "close", "volume", "oi", "expiry", "spot", "vix", "date"]]


def load_options_eod(start: str, end: str) -> pd.DataFrame:
    o = pd.read_parquet(DATA / "options" / "nifty_options_daily.parquet")
    return o[(o["date"] >= pd.Timestamp(start)) & (o["date"] <= pd.Timestamp(end))].reset_index(drop=True)
