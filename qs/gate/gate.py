"""The gate (design §7): runs every check on one frozen spec and returns a Verdict dict.

Verdict = {checks: {name: {passed, value, threshold, note}}, passed, demo, gate_sha, n_trials_family,
           n_eff, failed, metrics, paths, summary}
- Sharpe ratios shown in checks/summary are annualised (x sqrt 252); the stats run non-annualised.
- checks.cpcv.value = {median_sr, paths} (annualised path Sharpes). `verdict.paths` is the CPCV OOS
  path *return* matrix (paths x days). `parent_paths` accepts either: the 2-D matrix enables the
  per-fold "win where it wasn't aimed" check; a 1-D Sharpe list gives the median rule only.
- cfg: procedure keys at top level, thresholds under `thresholds:` (flattened here).
- probe / determinism / min_bets short-circuit: the remaining checks are not run.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import itertools
from itertools import combinations
from math import ceil
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from qs import contracts
from qs.engine import Result, run_spec
from qs.gate import stats
from qs.gate.cpcv import cpcv_splits, purge_embargo
from qs.gate.pbo import pbo_cscv
from qs.probes import determinism_check, lookahead_probe
from qs.spec import Spec, parse_hhmm

ANN = np.sqrt(252)
DEMO_STAMP = "DEMO GATE (looser thresholds)"
FLAG_ONLY = {"synthetic_exit"}
ORDER = ["probe", "determinism", "min_bets", "cpcv", "psr", "dsr", "pbo", "exposure_null", "beats_refs",
         "costs", "p_failure", "concentration", "drawdown", "robustness", "ablation", "synthetic_exit",
         "patch_nontarget"]


def load_gate(path) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    return yaml.safe_load(raw), hashlib.sha256(raw).hexdigest()


def _chk(passed, value, threshold, note=""):
    if value is None or isinstance(value, (str, int)) and not isinstance(value, bool):
        v = value
    else:
        v = float(value) if np.isfinite(value) else str(value)
    return {"passed": bool(passed), "value": v, "threshold": threshold, "note": note}


class _Runs:
    """run_spec cache keyed by (param overrides, ablated clause)."""

    def __init__(self, spec, bars):
        self.spec, self.bars, self.c = spec, bars, {}

    def get(self, params: dict | None = None, drop: int | None = None) -> Result:
        key = (tuple(sorted((params or {}).items())), drop)
        if key not in self.c:
            s = self.spec if drop is None else _drop_clause(self.spec, drop)
            self.c[key] = run_spec(s, self.bars, params or None)
        return self.c[key]


def _grid(spec: Spec) -> list[dict]:
    keys = list(spec.grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(spec.grid[k] for k in keys))]


def _clauses(src: str) -> list[ast.expr]:
    body = ast.parse(src, mode="eval").body
    return list(body.values) if isinstance(body, ast.BoolOp) and isinstance(body.op, ast.And) else [body]


def _n_clauses(spec: Spec) -> int:
    return max(len(_clauses(spec.entry[s])) for s in ("long", "short") if s in spec.entry)


def _drop_clause(spec: Spec, i: int) -> Spec:
    """Drop the i-th top-level `and` clause from each side that has more than i (and > 1) clauses."""
    entry = dict(spec.entry)
    for side in ("long", "short"):
        if side in entry:
            cl = _clauses(entry[side])
            if len(cl) > 1 and i < len(cl):
                rest = [c for j, c in enumerate(cl) if j != i]
                entry[side] = ast.unparse(rest[0] if len(rest) == 1 else ast.BoolOp(ast.And(), rest))
    return dataclasses.replace(spec, entry=entry)


def _spans(res: Result, days: pd.DatetimeIndex) -> list[tuple[int, int]]:
    if res.bets.empty:
        return []
    t0 = days.get_indexer(pd.DatetimeIndex(res.bets["t0"]).normalize())
    t1 = days.get_indexer(pd.DatetimeIndex(res.bets["t1"]).normalize())
    return list(zip(t0.tolist(), t1.tolist()))


def _cpcv_matrix(X: np.ndarray, spans: list, N: int, k: int, embargo_frac: float) -> np.ndarray:
    """Same selection/assembly as qs.gate.cpcv.cpcv_paths, but returns the path return matrix (paths x T)."""
    T = len(X)
    embargo = ceil(embargo_frac * T)
    groups = np.array_split(np.arange(T), N)
    per_group: dict[int, list] = {g: [] for g in range(N)}
    for combo, (train, test) in zip(combinations(range(N), k), cpcv_splits(T, N, k)):
        scores = [stats.sharpe(X[purge_embargo(train, test, spans[j], embargo), j]) for j in range(X.shape[1])]
        best = int(np.argmax(scores))
        for g in combo:
            per_group[g].append(X[groups[g], best])
    return np.array([np.concatenate([per_group[g][j] for g in range(N)]) for j in range(len(per_group[0]))])


def _cpcv(runs: _Runs, grid: list[dict], drop, days, capital, N, cfg) -> np.ndarray:
    res = [runs.get(p, drop) for p in grid]
    X = np.column_stack([r.daily.reindex(days, fill_value=0.0).to_numpy(float) / capital for r in res])
    return _cpcv_matrix(X, [_spans(r, days) for r in res], N, cfg["cpcv_k"], cfg["embargo_frac"])


def _exposure_daily(res: Result, bars: pd.DataFrame, underlying: str) -> pd.Series:
    exp = pd.to_datetime(bars["expiry"]).dt.date
    lots = {e: contracts.lot_size(underlying, e) for e in exp.unique()}
    units = res.position.to_numpy(float) * exp.map(lots).to_numpy(float)
    return pd.Series(units, index=bars.index).groupby(bars.index.normalize()).mean()


def _synthetic_exit(bars, spec, cfg, seed) -> tuple[bool, float, str]:
    """Design §7.2 #14: AR(1) fit to intraday log-price deviation from the day's open (in ATR units),
    simulated paths, long entry at 0; rank the spec's stop/take on synthetic Sharpe in a mesh x mesh grid."""
    day = bars.index.normalize()
    lc = np.log(bars["close"].to_numpy(float))
    y = lc - np.log(bars.groupby(day)["open"].transform("first").to_numpy(float))
    same = day[1:] == day[:-1]
    y0, y1 = y[:-1][same], y[1:][same]
    phi = float(np.clip(y0 @ y1 / max(y0 @ y0, 1e-18), -1, 1))
    sig = float(np.std(y1 - phi * y0))
    d = bars.groupby(day).agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    atr = float(np.nanmedian(((d.h - d.l) / d.c).to_numpy()))
    if not (atr > 0 and sig > 0):
        return True, float("nan"), "n/a (flat prices)"
    end = min(parse_hhmm(spec.session["end"]), parse_hhmm(spec.exit.get("time", spec.session["end"])))
    st = parse_hhmm(spec.session["start"])
    L = max((end.hour * 60 + end.minute) - (st.hour * 60 + st.minute), 2)
    rng = np.random.default_rng(seed)
    n = int(cfg["synthetic_paths"])
    eps = rng.normal(0, sig / atr, (n, L))
    paths = np.empty((n, L))
    acc = np.zeros(n)
    for t in range(L):  # ponytail: python loop over minutes (<= 375); vectorise with lfilter if it matters
        acc = phi * acc + eps[:, t]
        paths[:, t] = acc
    hi, lo, last = np.maximum.accumulate(paths, 1), np.minimum.accumulate(paths, 1), paths[:, -1]

    def first_hit(mask):  # first minute a barrier is crossed, L if never
        return np.where(mask.any(1), mask.argmax(1), L)

    def sr(stop, take):
        ts = first_hit(lo <= -stop)
        tt = first_hit(hi >= take) if np.isfinite(take) else np.full(n, L)
        pnl = np.where((tt < ts) & (tt < L), take, np.where(ts < L, -stop, last))
        return stats.sharpe(pnl)

    mesh = np.linspace(0.25, 5.0, int(cfg["synthetic_mesh"]))
    srs = np.array([sr(s, t) for s in mesh for t in mesh])
    own = sr(float(spec.exit["stop_atr"]), float(spec.exit.get("take_atr") or np.inf))
    pct = float((srs < own).mean())
    return pct >= 0.5, pct, f"AR(1) phi={phi:.4f}; exit beats {pct:.0%} of {srs.size} synthetic exits"


def run_gate(spec: Spec, bars: pd.DataFrame, family_R: pd.DataFrame, cfg: dict, gate_sha: str, *,
             refs: dict[str, pd.Series], seed: int, targeted_months: list[str] | None = None,
             parent_paths: np.ndarray | None = None) -> dict:
    cfg = {**cfg, **cfg.get("thresholds", {})}  # thresholds block flattened over procedure keys
    demo = bool(cfg.get("demo", False))
    capital = float(spec.sizing["capital_inr"])
    N = int(cfg["cpcv_n"][seed % len(cfg["cpcv_n"])])
    checks: dict = {}
    family_R = family_R if family_R is not None else pd.DataFrame()
    n_family = int(family_R.shape[1]) + 1
    out = dict(checks=checks, passed=False, demo=demo, gate_sha=gate_sha, n_trials_family=n_family,
               n_eff=None, failed=[], metrics={"cpcv_N": N}, paths=None, summary="", hyp_id=spec.id)

    def done(stopped=None):
        out["failed"] = [k for k, c in checks.items() if not c["passed"] and k not in FLAG_ONLY]
        out["passed"] = not out["failed"] and stopped is None
        out["summary"] = summarize(out, stopped)
        return out

    runs = _Runs(spec, bars)
    base = runs.get()
    ok = lookahead_probe(spec, bars, n_cuts=int(cfg["probe_cuts"]), seed=seed)
    checks["probe"] = _chk(ok, None, "clean", "" if ok else "positions move when future bars change")
    if not ok:
        return done("probe")
    ok = determinism_check(spec, bars)
    checks["determinism"] = _chk(ok, None, "identical", "" if ok else "two runs differ")
    if not ok:
        return done("determinism")
    nb = len(base.bets)
    checks["min_bets"] = _chk(nb >= cfg["min_bets"], nb, f">={cfg['min_bets']}", f"{nb} bets")
    out["metrics"].update(n_bets=nb, net_inr=float(base.daily.sum()), gross_inr=float(base.gross_daily.sum()),
                          cost_inr=base.cost_total)
    if nb < cfg["min_bets"]:
        return done("min_bets")

    days = base.daily.index
    r = base.daily.to_numpy(float) / capital
    grid = _grid(spec)

    # cpcv
    P = _cpcv(runs, grid, None, days, capital, N, cfg)
    psr_ = np.array([stats.sharpe(p) for p in P]) * ANN
    med, pos = float(np.median(psr_)), float((P.sum(1) > 0).mean())
    out["paths"] = P.tolist()
    checks["cpcv"] = _chk(med > cfg["cpcv_median_sr"] and pos >= cfg["cpcv_pos"], None,
                          f">{cfg['cpcv_median_sr']}, pos>={cfg['cpcv_pos']}",
                          f"N={N} k={cfg['cpcv_k']} {len(P)} paths, {pos:.0%} positive, SR sd {psr_.std():.2f}")
    checks["cpcv"]["value"] = {"median_sr": med, "paths": [float(x) for x in psr_]}  # runner contract
    out["metrics"].update(cpcv_median_sr=med, cpcv_pos=pos, sr=stats.sharpe(r) * ANN)

    # psr
    v = stats.psr(r)
    checks["psr"] = _chk(v > cfg["psr"], v, f">{cfg['psr']}", f"SR {stats.sharpe(r) * ANN:.2f} ann.")

    # dsr + pbo over family + this trial + grid variants
    cols = {f"f{c}": family_R[c] for c in family_R.columns}
    cols["this"] = base.daily / capital
    for i, p in enumerate(grid):
        if p != {k: spec.params[k] for k in p}:
            cols[f"g{i}"] = runs.get(p).daily / capital
    M = pd.DataFrame({k: pd.Series(s).reindex(days) for k, s in cols.items()}).fillna(0.0)
    ne = stats.n_eff(M)
    srs = np.array([stats.sharpe(M[c]) for c in M])
    v = stats.dsr(r, srs, ne)
    sr_star = stats.expected_max_sr(ne, float(srs.var(ddof=1)) if len(srs) > 1 else 0.0) * ANN
    out["n_eff"] = float(ne)
    checks["dsr"] = _chk(v > cfg["dsr"], v, f">{cfg['dsr']}",
                         f"{M.shape[1]} series ({n_family} trials + {M.shape[1] - n_family} grid), "
                         f"n_eff {ne:.1f}, SR* {sr_star:.2f}")
    if M.shape[1] < 2:
        checks["pbo"] = _chk(True, None, f"<={cfg['pbo']}", "n/a (1 series); DSR alone")
    else:
        v = pbo_cscv(M, S=int(cfg["pbo_S"]))
        checks["pbo"] = _chk(v <= cfg["pbo"], v, f"<={cfg['pbo']}", f"CSCV S={cfg['pbo_S']} over {M.shape[1]}")
    out["metrics"].update(psr=checks["psr"]["value"], dsr=checks["dsr"]["value"], pbo=checks["pbo"]["value"])

    # exposure-matched null: mean daily signed units, circularly shifted, x the day's open->close move
    g = bars.groupby(bars.index.normalize())
    mkt = (g["close"].last() - g["open"].first()).reindex(days)
    expo = _exposure_daily(base, bars, spec.instrument["underlying"]).reindex(days)
    v = stats.exposure_null_pctile(expo, mkt, base.daily, n=int(cfg["exposure_null_n"]), seed=seed)
    checks["exposure_null"] = _chk(v >= cfg["exposure_null"], v, f">={cfg['exposure_null']}",
                                   f"vs {cfg['exposure_null_n']} shifted-exposure nulls")

    # reference rows
    sr_ann = stats.sharpe(r) * ANN
    notes, beat = [], True
    for name in ("buy_hold", "live_bot"):
        s = (refs or {}).get(name)
        if s is None or len(s) == 0:
            notes.append(f"{name} missing, skipped")
            continue
        s = pd.Series(np.asarray(s, float), index=pd.to_datetime(s.index).normalize())
        common = days.intersection(s.index)
        if len(common) < 3:
            notes.append(f"{name} no overlap, skipped")
            continue
        mine, ref = stats.sharpe(r[days.get_indexer(common)]) * ANN, stats.sharpe(s.loc[common]) * ANN
        beat &= mine > ref
        notes.append(f"{name} {ref:.2f}")
    checks["beats_refs"] = _chk(beat, sr_ann, "SR > refs", "; ".join(notes))

    # costs
    gross, cost = float(base.gross_daily.sum()), float(base.cost_total)
    v = stats.ret_on_costs(gross, cost)
    checks["costs"] = _chk(v >= cfg["cost_ratio"], v, f">={cfg['cost_ratio']}",
                           f"gross {gross / 1e5:.1f}L net {(gross - cost) / 1e5:.1f}L cost {cost / 1e5:.1f}L INR")

    # probability of failure
    years = len(days) / 252
    v = stats.p_failure(base.bets["net_inr"].to_numpy(float) / capital, years, cfg["p_failure_eval_years"],
                        cfg["p_failure_target_sr"], seed=seed)
    checks["p_failure"] = _chk(v <= cfg["p_failure"], v, f"<={cfg['p_failure']}",
                               f"target SR {cfg['p_failure_target_sr']}, {cfg['p_failure_eval_years']}y horizon")

    # concentration
    b = base.bets["net_inr"].to_numpy(float)
    hp, hn, top = stats.hhi(b[b > 0]), stats.hhi(-b[b < 0]), stats.top_k_share(base.daily, 5)
    checks["concentration"] = _chk(hp <= cfg["hhi"] and hn <= cfg["hhi"] and top < cfg["top5_share"], top,
                                   f"<{cfg['top5_share']}, HHI<={cfg['hhi']}",
                                   f"top-5-day share {top:.2f}, HHI+ {hp:.3f} HHI- {hn:.3f}")

    # drawdown at vol target
    dd = stats.drawdown_stats(base.daily, capital, cfg["vol_target"])
    checks["drawdown"] = _chk(dd["max_dd"] <= cfg["max_dd"] and dd["tuw_frac"] <= cfg["tuw"], dd["max_dd"],
                              f"<={cfg['max_dd']}, TuW<={cfg['tuw']}",
                              f"at {cfg['vol_target']:.0%} vol: DD95 {dd['dd95']:.2f}, TuW {dd['tuw_frac']:.2f}")
    out["metrics"].update(max_dd=dd["max_dd"], tuw=dd["tuw_frac"])

    # robustness: +-1 grid step per grid param, +-robust_frac on other params; full-sample SR > 0
    # (with one fixed variant, every CPCV path is the same series, so median path SR == full-sample SR)
    neigh = []
    for k, vals in spec.grid.items():
        neigh += [{k: vals[0]}, {k: vals[2]}]
    for k, v0 in spec.params.items():
        if k not in spec.grid:
            step = 1 if isinstance(v0, int) else abs(v0) * cfg["robust_frac"]
            neigh += [{k: v0 - step}, {k: v0 + step}]
    if neigh:
        nsr = {f"{k}={v:g}": stats.sharpe(runs.get(p).daily / capital) * ANN
               for p in neigh for k, v in p.items()}
        worst = min(nsr, key=nsr.get)
        checks["robustness"] = _chk(nsr[worst] > 0, nsr[worst], ">0",
                                    f"{len(nsr)} neighbours, worst {worst}")
    else:
        checks["robustness"] = _chk(True, None, ">0", "n/a (no params)")

    # ablation: dropping each entry clause must lower the CPCV median path SR
    nc = _n_clauses(spec)
    if nc > 1:
        story = []
        for i in range(nc):
            Pi = _cpcv(runs, grid, i, days, capital, N, cfg)
            mi = float(np.median([stats.sharpe(p) for p in Pi])) * ANN
            if not mi < med:
                story.append(f"#{i + 1} ({mi:.2f})")
        checks["ablation"] = _chk(not story, f"{nc - len(story)}/{nc}", "all clauses hurt",
                                  "storytelling: " + ", ".join(story) if story else "every clause earns its keep")
    else:
        checks["ablation"] = _chk(True, None, "-", "n/a (single clause)")

    ok, v, note = _synthetic_exit(bars, spec, cfg, seed)
    checks["synthetic_exit"] = _chk(ok, v, ">=0.5 (flag)", note)

    # patch: must not lower the CPCV median and must hold in >= patch_nontarget of untargeted groups
    if parent_paths is not None:
        pp = np.asarray(parent_paths, float)
        pmed = float(np.median(pp)) if pp.ndim == 1 else float(np.median([stats.sharpe(p) for p in pp])) * ANN
        if pp.ndim == 2 and pp.shape == P.shape:
            months = set(targeted_months or [])
            dm = days.strftime("%Y-%m")
            groups = [gr for gr in np.array_split(np.arange(len(days)), N) if not months & set(dm[gr])]
            held = [np.mean([stats.sharpe(p[gr]) for p in P]) >= np.mean([stats.sharpe(p[gr]) for p in pp]) - 1e-9
                    for gr in groups]
            share = float(np.mean(held)) if held else 1.0
            okf, fnote = share >= cfg["patch_nontarget"], f"{sum(held)}/{len(held)} untargeted groups held"
        else:
            share, okf, fnote = None, True, f"fold check n/a (parent paths shape {pp.shape} != {P.shape})"
        checks["patch_nontarget"] = _chk(med >= pmed - 1e-9 and okf, share, f">={cfg['patch_nontarget']}",
                                         f"median {pmed:.2f} -> {med:.2f}; {fnote}")
    return done()


def _fmt(c) -> str:
    v = c["value"]
    if isinstance(v, dict):
        v = v["median_sr"]
    if v is None:
        return "-"
    if isinstance(v, str):
        return v
    return f"{v:d}" if isinstance(v, int) else f"{v:.0f}" if abs(v) >= 100 else f"{v:.2f}"


def summarize(v: dict, stopped: str | None = None) -> str:
    """<= 12 plain-text lines for the room: stamp, verdict, context, then two checks per line."""
    lines = [DEMO_STAMP] if v["demo"] else []
    status = "PASS" if v["passed"] else "FAIL"
    n = len([k for k in v["checks"] if k not in FLAG_ONLY])
    why = (f"stopped at {stopped}" if stopped else
           f"{len(v['failed'])} of {n} checks failed" if v["failed"] else f"all {n} checks passed")
    lines.append(f"GATE {status}  {v.get('hyp_id', '')}  {why}")
    m = v["metrics"]
    ctx = f"gate {v['gate_sha'][:8]}  CPCV N={m.get('cpcv_N')}  family trials {v['n_trials_family']}"
    if v["n_eff"] is not None:
        ctx += f"  n_eff {v['n_eff']:.1f}"
    if "n_bets" in m:
        ctx += f"  bets {m['n_bets']}  net {m['net_inr'] / 1e5:+.1f}L"
    lines.append(ctx)
    cells = []
    for name in ORDER:
        if name in v["checks"]:
            c = v["checks"][name]
            st = ("PASS" if c["passed"] else "FLAG") if name in FLAG_ONLY else ("PASS" if c["passed"] else "FAIL")
            cells.append(f"{name:<15}{st:<5}{_fmt(c):>7} {c['threshold']}"[:44])
    for i in range(0, len(cells), 2):
        lines.append("  ".join(f"{c:<44}" for c in cells[i:i + 2]).rstrip())
    if stopped:
        lines.append(f"{stopped}: {v['checks'][stopped]['note'] or 'failed'}; remaining checks not run")
    return "\n".join(lines[:12])
