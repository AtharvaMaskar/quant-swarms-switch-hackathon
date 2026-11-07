import json
from datetime import date

import numpy as np
import pandas as pd

from qs import db
from qs.costs import load_costs
from qs.paper.desk import PaperDesk
from qs.paper.monitors import rolling_psr
from qs.spec import load_spec

EXPIRY = date(2026, 11, 24)
SPEC = """
id: h7
instrument: {segment: futures, underlying: NIFTY}
session: {start: "09:20", end: "15:15"}
features:
  ret_open: {fn: ret_since, anchor: session_open}
entry:
  long: "ret_open > p.thr"
  short: "ret_open < -p.thr"
  max_per_day: 1
exit: {stop_atr: 1.5, atr_days: 3, time: "15:15"}
sizing: {risk_per_trade: 0.01, capital_inr: 14040000, max_lots: 10}
params: {thr: 0.001}
"""


class FakeAgent:
    def __init__(self):
        self.posts, self.archived = [], []

    def post(self, room_id, content):
        self.posts.append((room_id, content))
        return {}

    def archive(self, room_id):
        self.archived.append(room_id)
        return {}


def _bars(day, n, start=24000.0, drift=0.0, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range(f"{day} 09:15", periods=n, freq="min")
    close = start + np.cumsum(drift + rng.normal(0, 3, n))
    op = np.r_[start, close[:-1]]
    return pd.DataFrame({"open": op, "high": np.maximum(op, close) + 2, "low": np.minimum(op, close) - 2,
                         "close": close, "volume": 0.0, "oi": 0.0, "expiry": pd.Timestamp(EXPIRY),
                         "spot": np.nan, "vix": 12.0, "date": pd.Timestamp(day)}, index=pd.Index(ts, name="ts"))


def _desk(tmp_path, feed, ref=None):
    p = tmp_path / "h7.yaml"
    p.write_text(SPEC)
    hist = pd.concat([_bars(d, 375, seed=i) for i, d in enumerate(["2026-10-27", "2026-10-28", "2026-10-29",
                                                                   "2026-10-30"])])
    con = db.connect(":memory:")
    db.add_hypothesis(con, "h7", "test", {})
    agent = FakeAgent()
    ref = ref or dict(mean=1000.0, sd=5000.0, sr=0.2, lz=0.9, dd95=0.5, bets=200)
    desk = PaperDesk(con, agent, "h7", load_spec(p), feed, load_costs(), history=hist, ref=ref,
                     expiry=EXPIRY, room="room-h7", control_room="control")
    return con, agent, desk


def test_desk_trades_on_replay(tmp_path):
    live = _bars("2026-11-02", 120, drift=1.5, seed=9)
    feed = [dict(ts=t, open=r.open, high=r.high, low=r.low, close=r.close, bid=r.close - 0.5, ask=r.close + 0.5)
            for t, r in live.iterrows()]
    con, agent, desk = _desk(tmp_path, iter(feed))
    desk.run()
    bets = [json.loads(p) for (p,) in con.execute("SELECT payload FROM paper WHERE kind='bet'")]
    assert len(bets) >= 1
    assert all(pd.Timestamp(b["t0"]).date() == date(2026, 11, 2) for b in bets)
    # live half-spread 0.5 pts each side, not the config ticks model
    lot = bets[0]["units"] / bets[0]["lots"]
    assert abs(bets[0]["spread_inr"] - 2 * 0.5 * bets[0]["units"]) < 1e-6 and lot > 1
    texts = [c for _, c in agent.posts]
    assert all("h7" in t for t in texts)
    assert any("LONG" in t or "SHORT" in t for t in texts)  # entry fill
    assert any("EXIT" in t for t in texts)
    assert "paper day 2026-11-02" in texts[-1]  # daily summary closes the session
    assert {r for r, _ in agent.posts} == {"room-h7"}


def test_desk_retires_on_alarm(tmp_path):
    con, agent, desk = _desk(tmp_path, iter([]))
    t = pd.Timestamp("2026-11-02 10:00")
    for i in range(12):
        desk.book(dict(t0=t + pd.Timedelta(minutes=2 * i), t1=t + pd.Timedelta(minutes=2 * i + 1), side=1, lots=1,
                       entry_px=24000.0, exit_px=23900.0, gross_inr=-6000.0, cost_inr=200.0, net_inr=-6200.0,
                       reason="stop"))
        if desk.retired:
            break
    texts = [c for _, c in agent.posts]
    assert any("DECAY ALARM" in c for c in texts)
    assert any("RETIRED" in c and "h7" in c for c in texts)
    assert ("control", "@proposer-lead post-mortem h7") in [(r, c.split(":")[0]) for r, c in agent.posts]
    assert agent.archived == ["room-h7"]
    assert con.execute("SELECT status FROM hypotheses WHERE id='h7'").fetchone()[0] == "retired"
    assert desk.step(dict(ts=t, open=1, high=1, low=1, close=1, bid=None, ask=None)) == []


def test_rolling_psr():
    rng = np.random.default_rng(4)
    ok = rolling_psr(rng.normal(0.3, 1, 60), window=30, sr_backtest=0.3)
    assert np.isnan(ok[:29]).all() and 0.05 < ok[-1] < 0.95
    bad = rolling_psr(rng.normal(-0.5, 1, 60), window=30, sr_backtest=0.3)
    assert bad[-1] < 0.05
