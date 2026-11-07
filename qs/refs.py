"""Reference rows every strategy must beat: intraday buy-and-hold and the prior project's live bot.
Both are daily net returns (rupees / capital), the unit the gate works in."""
import logging

import pandas as pd

from qs.data import DATA, ROOT
from qs.engine import run_spec
from qs.spec import load_spec

log = logging.getLogger(__name__)
BUY_HOLD = ROOT / "specs/refs/buy_hold.yaml"
LIVE_BOT = DATA / "refs/live_bot.parquet"
LIVE_BOT_CAPITAL = 14_040_000


def buy_hold_daily(bars: pd.DataFrame) -> pd.Series:
    spec = load_spec(BUY_HOLD)
    return (run_spec(spec, bars).daily / spec.sizing["capital_inr"]).rename("buy_hold")


def live_bot_daily() -> pd.Series:
    """Prior project's hypothesis-0 bot (copied by scripts/build_data.py). FileNotFoundError if not built."""
    d = pd.read_parquet(LIVE_BOT).sort_values("date")
    eq = d["equity_inr"].to_numpy()
    pnl = pd.Series(eq, index=pd.DatetimeIndex(d["date"]).as_unit("ns"), name="live_bot").diff()
    pnl.iloc[0] = eq[0] - LIVE_BOT_CAPITAL
    return pnl / LIVE_BOT_CAPITAL


def all_refs(bars: pd.DataFrame) -> dict[str, pd.Series]:
    out = {"buy_hold": buy_hold_daily(bars)}
    try:
        out["live_bot"] = live_bot_daily()
    except FileNotFoundError:
        log.warning("live_bot reference row omitted: %s not found (run scripts/build_data.py)", LIVE_BOT)
    return out
