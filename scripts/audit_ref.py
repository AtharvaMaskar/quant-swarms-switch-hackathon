"""Reference audit: the live 180-pt NIFTY bot through every gate check that needs only daily returns.

Stored P&L (data/refs/live_bot.parquet) over the research-window overlap, real-gate thresholds.
Checks that need trades/positions/spec are reported "not run", never PASS.
    uv run python scripts/audit_ref.py [--record] [--db research.db] [--force-new-trial]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qs import data, db, ids, refs  # noqa: E402
from qs.gate import stats  # noqa: E402
from qs.gate.cpcv import cpcv_paths  # noqa: E402
from qs.gate.gate import ANN, ORDER, _fmt, load_gate  # noqa: E402

HID, SLUG = "h0", "live-180pt-bot"
CARD = {"lens": "reference", "contribution": "audit",
        "problem": "Is the live production bot's edge real by the gate's standard?",
        "source": {"type": "prod", "id": "nifty-180-points"}}
ENGINE_SHA = "audit-stored-pnl"
OLD_N = 987
END = "2024-10-31"  # live bot data ends here
NOT_RUN = {
    "probe": "needs the strategy's code/positions to perturb future bars",
    "determinism": "needs the strategy's code to re-run",
    "min_bets": "needs the trade list ({active} active days as a proxy)",
    "pbo": "single strategy, n/a",
    "exposure_null": "needs daily positions",
    "costs": "needs gross P&L and per-trade costs",
    "p_failure": "needs per-bet returns",
    "robustness": "needs the strategy's params to perturb",
    "ablation": "needs the strategy's entry clauses",
    "synthetic_exit": "needs the strategy's stop/take",
}


def _chk(passed, value, threshold, note=""):
    return {"passed": bool(passed), "value": None if value is None else float(value), "threshold": threshold,
            "note": note, "run": True}


def audit(r: pd.Series, bh: pd.Series | None, cfg: dict, gate_sha: str, seed: int = 0,
          capital: float = refs.LIVE_BOT_CAPITAL) -> dict:
    """r, bh: daily returns (fraction of capital) over the audit window. Returns a gate-shaped verdict."""
    cfg = {**cfg, **cfg.get("thresholds", {})}
    x = r.to_numpy(float)
    sr = stats.sharpe(x)
    active = int((x != 0).sum())
    checks = {}

    p = stats.psr(x)
    checks["psr"] = _chk(p > cfg["psr"], p, f">{cfg['psr']}", f"SR {sr * ANN:.2f} ann.")

    # N_eff = 1: pre-existing strategy chosen before this run, not selected from a search here.
    d = stats.dsr(x, np.array([sr]), 1.0)
    # context: SR* at the old project's 987 trials, null SR variance 1/(T-1) (their trial SRs aren't stored)
    sr_star = stats.expected_max_sr(OLD_N, 1 / (len(x) - 1))
    d_old = stats.psr(x, sr_star)
    checks["dsr"] = _chk(d > cfg["dsr"], d, f">{cfg['dsr']}",
                         f"N_eff=1: pre-existing bot, not selected from a search here, so not deflated by this "
                         f"run's trials; at old N={OLD_N}: DSR {d_old:.2f} (SR* {sr_star * ANN:.2f} ann.)")

    N = int(np.random.default_rng(seed).choice(cfg["cpcv_n"]))
    path_sr = cpcv_paths({"bot": r.reset_index(drop=True)}, {}, N, int(cfg["cpcv_k"]), cfg["embargo_frac"]) * ANN
    med, pos = float(np.median(path_sr)), float((path_sr > 0).mean())
    checks["cpcv"] = _chk(med > cfg["cpcv_median_sr"] and pos >= cfg["cpcv_pos"], None,
                          f">{cfg['cpcv_median_sr']}, pos>={cfg['cpcv_pos']}",
                          f"N={N} k={cfg['cpcv_k']} {len(path_sr)} paths, {pos:.0%} positive "
                          "(one series: every path is the full sample)")
    checks["cpcv"]["value"] = {"median_sr": med, "paths": [float(v) for v in path_sr]}

    notes, beat = [], True
    if bh is None or len(bh) == 0:
        notes.append("buy_hold missing, skipped")
    else:
        common = r.index.intersection(bh.index)
        mine, ref = stats.sharpe(r.loc[common]) * ANN, stats.sharpe(bh.loc[common]) * ANN
        beat = mine > ref
        notes.append(f"buy_hold {ref:.2f} over {len(common)} days")
    checks["beats_refs"] = _chk(beat, sr * ANN, "SR > refs", "; ".join(notes))

    # bets aren't stored: HHI over active days' returns as the proxy
    hp, hn, top = stats.hhi(x[x > 0]), stats.hhi(-x[x < 0]), stats.top_k_share(x, 5)
    checks["concentration"] = _chk(hp <= cfg["hhi"] and hn <= cfg["hhi"] and top < cfg["top5_share"], top,
                                   f"<{cfg['top5_share']}, HHI<={cfg['hhi']}",
                                   f"top-5-day share {top:.2f}, HHI+ {hp:.3f} HHI- {hn:.3f} (days, not bets)")

    dd = stats.drawdown_stats(r, 1.0, cfg["vol_target"])
    checks["drawdown"] = _chk(dd["max_dd"] <= cfg["max_dd"] and dd["tuw_frac"] <= cfg["tuw"], dd["max_dd"],
                              f"<={cfg['max_dd']}, TuW<={cfg['tuw']}",
                              f"at {cfg['vol_target']:.0%} vol: DD95 {dd['dd95']:.2f}, TuW {dd['tuw_frac']:.2f}")

    run = list(checks)
    for k, why in NOT_RUN.items():
        thr = {"probe": "clean", "determinism": "identical", "min_bets": f">={cfg['min_bets']}",
               "pbo": f"<={cfg['pbo']}", "exposure_null": f">={cfg['exposure_null']}",
               "costs": f">={cfg['cost_ratio']}", "p_failure": f"<={cfg['p_failure']}", "robustness": ">0",
               "ablation": "all clauses hurt", "synthetic_exit": ">=0.5 (flag)"}[k]
        checks[k] = {"passed": False, "value": None, "threshold": thr, "run": False,
                     "note": "not run (needs trades/positions): " + why.format(active=active)}
    failed = [k for k in run if not checks[k]["passed"]]
    metrics = dict(cpcv_N=N, sr=sr * ANN, psr=p, dsr=d, dsr_old_n=d_old, cpcv_median_sr=med, cpcv_pos=pos,
                   net_inr=float(x.sum() * capital), max_dd=dd["max_dd"], tuw=dd["tuw_frac"], active_days=active,
                   days=len(x))
    v = dict(checks=checks, passed=not failed, demo=False, gate_sha=gate_sha, n_trials_family=1, n_eff=1.0,
             failed=failed, not_run=list(NOT_RUN), metrics=metrics, paths=None, hyp_id=HID, partial=True,
             window=[str(r.index[0].date()), str(r.index[-1].date())])
    v["summary"] = summarize(v)
    return v


def summarize(v: dict) -> str:
    run = [k for k, c in v["checks"].items() if c.get("run")]
    m, (a, b) = v["metrics"], v["window"]
    lines = [f"REFERENCE AUDIT · live 180-pt bot · stored P&L · partial gate ({len(run)} of 15 checks run)",
             f"AUDIT {'PASS' if v['passed'] else 'FAIL'}  {v['hyp_id']}  "
             + (f"{len(v['failed'])} of {len(run)} run checks failed" if v["failed"] else f"all {len(run)} run checks passed"),
             f"gate {v['gate_sha'][:8]}  CPCV N={m['cpcv_N']}  {a} → {b}  days {m['days']} (active {m['active_days']})"
             f"  net {m['net_inr'] / 1e5:+.1f}L"]
    cells = []
    for name in ORDER:
        if name in v["checks"]:
            c = v["checks"][name]
            st = ("PASS" if c["passed"] else "FAIL") if c.get("run") else "N/R"
            cells.append(f"{name:<15}{st:<5}{_fmt(c):>7} {c['threshold']}"[:44])
    for i in range(0, len(cells), 2):
        lines.append("  ".join(f"{c:<44}" for c in cells[i:i + 2]).rstrip())
    lines += [f"dsr: {v['checks']['dsr']['note']}", f"beats_refs: {v['checks']['beats_refs']['note']}",
              "N/R = not run (needs trades/positions): " + ", ".join(v["not_run"]),
              f"stored P&L uses the old flat cost model (STT 0.02%, not dated rates); window ends {b} (bot data ends)"]
    return "\n".join(lines)


def record(con, v: dict, r: pd.Series, force_new_trial: bool = False) -> int:
    exists = con.execute("SELECT 1 FROM hypotheses WHERE id=?", (HID,)).fetchone()
    if exists and not force_new_trial:
        raise SystemExit(f"{HID} already exists; pass --force-new-trial to append another trial")
    before = ids.next_root(con)
    con.execute("BEGIN IMMEDIATE")
    try:
        if not exists:
            db.add_hypothesis(con, HID, SLUG, CARD)
        metrics = {k: v["metrics"][k] for k in ("dsr", "psr", "sr", "cpcv_pos", "net_inr", "max_dd", "tuw",
                                                "active_days", "dsr_old_n")}
        metrics.update(cpcv_median=v["metrics"]["cpcv_median_sr"], pbo=None, n_eff=1.0, n_trials_family=1)
        tid = db.record_trial(con, HID, "research", {}, None, ENGINE_SHA, v["gate_sha"], False, r, metrics, v,
                              v["passed"])
        # FAIL -> dead. On PASS leave 'proposed'?: no - the orchestrator's judge pool takes proposed roots,
        # so park it at 'frozen' (a trial exists) instead.
        db.set_status(con, HID, "dead" if not v["passed"] else "frozen")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    after = ids.next_root(con)
    assert after == before, f"audit moved next_root {before} -> {after}"
    return tid


def load(start: str, end: str) -> tuple[pd.Series, pd.Series]:
    r = refs.live_bot_daily().loc[start:end]
    bh = refs.buy_hold_daily(data.load_bars(start, end))
    bh.index = pd.to_datetime(bh.index).normalize()
    return r, bh


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--db", default=str(ROOT / "research.db"))
    ap.add_argument("--force-new-trial", action="store_true")
    ap.add_argument("--seed", type=int, default=0, help="picks CPCV N from cpcv_n")
    a = ap.parse_args(argv)
    cfg, sha = load_gate(ROOT / "config/gate.yaml")
    start = data.window("research")[0]
    r, bh = load(start, END)
    v = audit(r, bh, cfg, sha, a.seed)
    print(v["summary"])
    if a.record:
        con = db.connect(a.db)
        tid = record(con, v, r, a.force_new_trial)
        print(f"recorded {HID} trial {tid} in {a.db} (next root still {ids.next_root(con)})")


if __name__ == "__main__":
    main()
