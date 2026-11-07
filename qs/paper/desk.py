"""Paper desk (spec §11): runs the frozen spec bar by bar on a live/replayed feed and posts to #paper-{hid}.

Each closed bar re-runs `run_spec` on history + session-so-far + one phantom bar (so the engine does not
treat the newest bar as the day's last and force a flat), and acts only on fills at the newest bar.
Slippage is the live half-spread; monitors update after every closed bet; the retire rule archives the room.

    uv run python -m qs.paper.desk --hid h0 --spec specs/examples/orb_vol.yaml --replay --speed 0 --dry-run
"""
from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

from qs import contracts, db
from qs import costs as qcosts
from qs.engine import run_spec
from qs.gate.stats import drawdown_stats
from qs.paper import monitors as mon
from qs.spec import load_spec

CUSUM_H, CUSUM_K = 4.0, 0.5   # in backtest per-bet sd units
PSR_WINDOW, PSR_ALARM = 30, 0.05
LZ_WINDOW, LZ_RISE = 50, 0.15  # alarm when sign entropy beats the backtest's by this much
ALARM_TEXT = {
    "CUSUM": "per-bet net has drifted below the backtest mean",
    "CSW": "structural break in cumulative paper P&L",
    "PSR": f"rolling {PSR_WINDOW}-bet PSR: paper Sharpe is below the backtest's",
    "LZ": "return-sign entropy is rising: the edge is being arbitraged",
}


def _inr(x: float, sign=True) -> str:
    s = f"₹{abs(x):,.0f}"
    return (("+" if x >= 0 else "−") + s) if sign else s


class _SpreadCosts:
    """qs.costs with the live half-spread swapped in for the slippage model, keyed by fill minute."""
    # ponytail: keyed by time-of-day only, so warm-up history bars at the same minute get it too; their
    # bets are never booked, so it only matters if history bets ever get reported

    order_cost_inr = staticmethod(qcosts.order_cost_inr)

    def __init__(self, tbl):
        self.tbl, self.half = tbl, {}

    def load_costs(self):
        return self.tbl

    def slippage_pts(self, tbl, segment, price, tick, *, minute, expiry_day):
        return qcosts.slippage_pts(tbl, segment, price, tick, minute=minute, expiry_day=expiry_day,
                                   half_spread=self.half.get(minute))


class PaperDesk:
    def __init__(self, con, agent, hid, spec, feed, costs, *, history: pd.DataFrame, ref: dict, expiry: date,
                 room: str | None = None, control_room: str | None = None, label: str = "replay"):
        self.con, self.agent, self.hid, self.spec, self.feed = con, agent, hid, spec, feed
        self.cost = _SpreadCosts(costs)
        self.history, self.ref, self.expiry, self.label = history, ref, pd.Timestamp(expiry), label
        self.room, self.control_room = room or f"#paper-{hid}", control_room or room or f"#paper-{hid}"
        self.vix = float(history["vix"].iloc[-1]) if len(history) else np.nan
        self.cap = float(spec.sizing["capital_inr"])
        self.lot = contracts.lot_size(spec.instrument["underlying"], self.expiry.date())
        self.session: list[dict] = []
        self.booked: list[dict] = []
        self.day_start = 0           # index into booked where the current session starts
        self.session_idx = 0
        self.alarms: list[tuple[str, int]] = []
        self.retired = False

    # ---- feed --------------------------------------------------------------------------------
    def run(self) -> None:
        for bar in self.feed:
            self.step(bar)
            if self.retired:
                return
        self.end_session()

    def step(self, bar: dict) -> list[str]:
        if self.retired:
            return []
        ts = pd.Timestamp(bar["ts"])
        posts = []
        if self.session and ts.normalize() != self.session[-1]["ts"].normalize():
            posts += self.end_session()
        book = self.session[-1] if self.session else bar  # the book at this bar's open ~ last close
        if book.get("bid") and book.get("ask"):
            self.cost.half[ts.time()] = (book["ask"] - book["bid"]) / 2
        else:
            self.cost.half.pop(ts.time(), None)
        self.session.append(dict(ts=ts, open=bar["open"], high=bar["high"], low=bar["low"], close=bar["close"],
                                 bid=bar.get("bid"), ask=bar.get("ask")))
        for b in self._live_bets(phantom=True):
            if b["t0"] == ts:
                posts.append(self._post(self._entry_text(b), "fill", b))
            elif b["t1"] == ts:
                posts += self.book(b)
        return posts

    def end_session(self) -> list[str]:
        if not self.session:
            return []
        done = {b["t0"] for b in self.booked}
        posts = []
        for b in self._live_bets(phantom=False):  # the engine's own end-of-day flat
            if b["t0"] not in done and not self.retired:
                posts += self.book(b)
        if not self.retired:
            posts.append(self._post(self._summary_text(), "summary", self._day_stats()))
        self.history = pd.concat([self.history, self._frame(self.session)])
        self.session, self.day_start = [], len(self.booked)
        self.session_idx += 1
        return posts

    def _frame(self, rows):
        df = pd.DataFrame(rows).set_index("ts")[["open", "high", "low", "close"]]
        df.index.name = "ts"
        return df.assign(volume=0.0, oi=0.0, expiry=self.expiry, spot=np.nan, vix=self.vix,
                         date=df.index.normalize())

    def _live_bets(self, phantom: bool) -> list[dict]:
        rows = list(self.session)
        if phantom:
            rows.append({**rows[-1], "ts": rows[-1]["ts"] + pd.Timedelta(minutes=1)})
        bars = pd.concat([self.history, self._frame(rows)]) if len(self.history) else self._frame(rows)
        bets = run_spec(self.spec, bars, costs=self.cost).bets
        bets = bets[pd.DatetimeIndex(bets["t0"]) >= self.session[0]["ts"]]
        return bets.to_dict("records")

    # ---- bets and monitors -------------------------------------------------------------------
    def book(self, b: dict) -> list[str]:
        """Record one closed bet, post its exit fill, update monitors, maybe retire."""
        units = int(b["lots"]) * self.lot
        b = {**b, "units": units, "spread_inr": float(b["gross_inr"] - b["side"] * (b["exit_px"] - b["entry_px"]) * units)}
        self.booked.append(b)
        posts = [self._post(self._exit_text(b), "bet", b)]
        status = self.monitors()
        for name, (alarm, _) in status.items():
            if alarm and name not in {m for m, _ in self.alarms}:
                self.alarms.append((name, self.session_idx))
                posts.append(self._post(self._alarm_text(name, status), "alarm", {"monitor": name}))
        reason = mon.retire_decision(self.alarms, self.session_idx, self.drawdown(), self.ref["dd95"])
        if reason:
            posts += self.retire(reason)
        return posts

    def nets(self) -> np.ndarray:
        return np.array([b["net_inr"] for b in self.booked], float)

    def drawdown(self) -> float:
        eq = self.cap + np.cumsum(self.nets())
        return float((1 - eq / np.maximum.accumulate(np.maximum(eq, self.cap))).max()) if len(eq) else 0.0

    def monitors(self) -> dict[str, tuple[bool, str]]:
        """name -> (alarm, short status)."""
        x, r, n = self.nets(), self.ref, len(self.booked)
        out = {}
        i = mon.cusum_alarm(x / r["sd"], r["mean"] / r["sd"], CUSUM_H, CUSUM_K)
        out["CUSUM"] = (i is not None, "ALARM" if i is not None else "ok")
        s, c = mon.csw_stat(np.cumsum(x))
        out["CSW"] = (s > c, "ALARM" if s > c else "ok")
        if n >= PSR_WINDOW:
            p = mon.rolling_psr(x, PSR_WINDOW, sr_backtest=r["sr"])[-1]
            out["PSR"] = (p < PSR_ALARM, f"{p:.2f}" + (" ALARM" if p < PSR_ALARM else ""))
        else:
            out["PSR"] = (False, f"{n}/{PSR_WINDOW}")
        if n >= LZ_WINDOW:
            e = mon.rolling_entropy(x, LZ_WINDOW)[-1]
            out["LZ"] = (e > r["lz"] + LZ_RISE, f"{e:.2f}" + (" ALARM" if e > r["lz"] + LZ_RISE else ""))
        else:
            out["LZ"] = (False, f"{n}/{LZ_WINDOW}")
        return out

    def retire(self, reason: str) -> list[str]:
        self.retired = True
        with contextlib.suppress(KeyError):  # a dry-run hid need not be in the DB
            db.set_status(self.con, self.hid, "retired")
        x = self.nets()
        posts = [self._post(f"{self.hid} ■ RETIRED · {reason}\n"
                            f"paper record: {len(x)} bets · net {_inr(x.sum())} · DD {self.drawdown():.1%} "
                            f"over {self.session_idx + 1} session(s) · room archived", "retire", {"reason": reason})]
        posts.append(self._post(f"@proposer-lead post-mortem {self.hid}: {reason}", "postmortem", {},
                                room=self.control_room))
        self.agent.archive(self.room)
        return posts

    # ---- posts -------------------------------------------------------------------------------
    def _post(self, text, kind, payload, room=None):
        room = room or self.room
        ts = payload.get("t1") or payload.get("t0") or (self.session[-1]["ts"] if self.session else pd.Timestamp.now())
        self.con.execute("INSERT INTO paper(hyp_id, ts, kind, payload) VALUES (?,?,?,?)",
                         (self.hid, str(ts), kind, json.dumps(payload, default=str)))
        db.log(self.con, "paper", text, room_id=room, author="paper-desk", hyp_id=self.hid)
        self.agent.post(room, text)
        return text

    def _tag(self):
        return f" [{self.label}]" if self.label else ""

    def _entry_text(self, b):
        side = "▲ LONG" if b["side"] > 0 else "▼ SHORT"
        hs = self.cost.half.get(b["t0"].time())
        spread = f" · half-spread {hs:.2f} pts" if hs is not None else ""
        return (f"{self.hid} {side} {b['lots']} lot{'s' * (b['lots'] != 1)} {self.spec.instrument['underlying']} FUT "
                f"@ {b['entry_px']:,.2f} · {b['t0']:%H:%M}{spread}{self._tag()}")

    def _exit_text(self, b):
        side = "LONG" if b["side"] > 0 else "SHORT"
        return (f"{self.hid} ■ EXIT {side} {b['lots']} lot{'s' * (b['lots'] != 1)} @ {b['exit_px']:,.2f} · {pd.Timestamp(b['t1']):%H:%M} "
                f"{b['reason']} · net {_inr(b['net_inr'])} (gross {_inr(b['gross_inr'])}, "
                f"costs {_inr(b['cost_inr'], False)})")

    def _day_stats(self):
        day = self.booked[self.day_start:]
        f = lambda k: float(sum(b[k] for b in day))  # noqa: E731
        return dict(day=str(self.session[0]["ts"].date()), bets=len(day), won=sum(b["net_inr"] > 0 for b in day),
                    net=f("net_inr"), gross=f("gross_inr"), cost=f("cost_inr"), spread=f("spread_inr"),
                    cum=float(self.nets().sum()), dd=self.drawdown())

    def _summary_text(self):
        s = self._day_stats()
        mons = " · ".join(f"{k} {v}" for k, (_, v) in self.monitors().items())
        return (f"{self.hid} · paper day {s['day']}{self._tag()}\n"
                f"P&L     {_inr(s['net'])} net · gross {_inr(s['gross'])} · costs {_inr(s['cost'], False)} "
                f"(spread {_inr(s['spread'], False)})\n"
                f"Bets    {s['bets']} ({s['won']} won) · cumulative {_inr(s['cum'])} over "
                f"{self.session_idx + 1} session(s)\n"
                f"Health  {mons} · DD {s['dd']:.1%} of p95 {self.ref['dd95']:.1%}")

    def _alarm_text(self, name, status):
        live = sum(a for a, _ in status.values())
        return (f"{self.hid} ▼ DECAY ALARM · {name}: {ALARM_TEXT[name]}\n"
                f"bet {len(self.booked)}, session {self.session_idx + 1} · {live} monitor(s) alarming; "
                f"two within {mon.RETIRE_WINDOW} sessions retires")


# ---- CLI ---------------------------------------------------------------------------------------
def backtest_ref(spec, bars: pd.DataFrame) -> dict:
    """Backtest per-bet and drawdown references the monitors compare paper against."""
    res = run_spec(spec, bars)
    net = res.bets["net_inr"].to_numpy(float)
    cap = float(spec.sizing["capital_inr"])
    vol = float((res.daily / cap).std(ddof=1) * np.sqrt(252))
    # unscaled (vol_target = own vol) so dd95 is a fraction of capital, like the paper drawdown
    dd95 = drawdown_stats(res.daily, cap, vol_target=vol)["dd95"] if vol > 0 else 0.0
    mean, sd = float(net.mean()), float(net.std(ddof=1))
    return dict(mean=mean, sd=sd, sr=mean / sd, lz=mon.lz_entropy(net > 0), dd95=dd95, bets=len(net))


def monthly_expiry(d: date) -> date:
    """NIFTY monthly expiry: last Tuesday of d's month, moved earlier over a non-trading day."""
    e = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    while e.weekday() != 1 or not contracts.is_trading_day(e):
        e -= timedelta(days=1)
    return e


class _Printer:
    def post(self, room_id, content):
        print(f"── {room_id}\n{content}\n", flush=True)
        return {}

    def archive(self, room_id):
        print(f"── archive {room_id}\n", flush=True)
        return {}


def main(argv=None):
    from qs.data import load_bars, window
    from qs.paper import replay

    ap = argparse.ArgumentParser(prog="python -m qs.paper.desk")
    ap.add_argument("--hid", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--replay", action="store_true", help="replay the recorded TBT session")
    ap.add_argument("--path", default=str(replay.DEFAULT_PATH))
    ap.add_argument("--instrument", default=replay.NEAR_FUT)
    ap.add_argument("--speed", type=float, default=120.0, help="market minutes per wall minute (0 = no pacing)")
    ap.add_argument("--dry-run", action="store_true", help="print posts instead of sending")
    ap.add_argument("--room", help="paper room id (default #paper-{hid})")
    ap.add_argument("--control-room", help="room for the post-mortem request (default: the paper room)")
    ap.add_argument("--db", default="research.db")
    ap.add_argument("--warmup-days", type=int, default=60)
    ap.add_argument("--label", default="replay", help="stamp appended to every post, e.g. 'REJECTED BY GATE - pipeline demo only'")
    a = ap.parse_args(argv)
    if not a.replay:
        ap.error("live TBT feed is not wired yet; use --replay")

    spec = load_spec(a.spec)
    ref = backtest_ref(spec, load_bars(*window("research")))
    hist = load_bars(*window("holdout"))
    hist = hist[hist["date"] >= hist["date"].unique()[-a.warmup_days]]
    feed = replay.replay_bars(a.path, a.instrument, a.speed)
    first = next(feed)
    feed = itertools.chain([first], feed)
    con = db.connect(":memory:" if a.dry_run else a.db)
    if a.dry_run:
        agent = _Printer()
    else:
        from qs.switch.client import SwitchAgent
        agent = SwitchAgent(os.environ["SWITCH_URL"], os.environ["SWITCH_AGENT_ID"], os.environ["SWITCH_API_KEY"])
    print(f"{a.hid}: backtest ref {ref['bets']} bets · mean {_inr(ref['mean'])} · sd {_inr(ref['sd'], False)} · "
          f"SR/bet {ref['sr']:.3f} · dd95 {ref['dd95']:.1%}\n", flush=True)
    PaperDesk(con, agent, a.hid, spec, feed, qcosts.load_costs(), history=hist, ref=ref,
              expiry=monthly_expiry(pd.Timestamp(first["ts"]).date()), room=a.room,
              control_room=a.control_room, label=a.label).run()


if __name__ == "__main__":
    main()
