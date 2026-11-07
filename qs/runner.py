"""Trial runner: frozen spec + clean engine -> engine -> gate -> append-only trial row.
Also the one-rule patch check and the hard stops (design §7.4-7.5)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
from pathlib import Path

import numpy as np
from scipy import stats as ss

from qs import data, db, ids, refs
from qs.engine import run_spec
from qs.gate import stats
from qs.spec import Spec, load_spec, spec_diff, spec_sha

log = logging.getLogger(__name__)
ENGINE_PATHS = ("qs", "config", "ref")
HOLDOUT_NOTE = "holdout window was seen by a prior project (design §10)"


class TrialError(RuntimeError):
    pass


class PatchError(ValueError):
    pass


# Task 8's gate, imported lazily so this module loads before qs/gate/gate.py exists; tests monkeypatch these.
def load_gate(path):
    from qs.gate.gate import load_gate as f
    return f(path)


def run_gate(*a, **kw):
    from qs.gate.gate import run_gate as f
    return f(*a, **kw)


# ---- engine integrity ------------------------------------------------------------------------
def engine_sha(root=data.ROOT) -> str:
    """sha256 over the qs/, config/, ref/ trees as committed on main. Raises TrialError if the working
    tree differs (or main lacks them), unless QS_ALLOW_DIRTY=1: then a 'dirty-' hash of the working copy."""
    def git(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True)

    ref = os.environ.get("QS_ENGINE_REF", "main")   # the committed ref the engine must match
    trees = git("rev-parse", *(f"{ref}:{p}" for p in ENGINE_PATHS))
    changed = git("diff", "--name-only", ref, "--", *ENGINE_PATHS).stdout.split()
    untracked = git("ls-files", "--others", "--exclude-standard", "--", *ENGINE_PATHS).stdout.split()
    problems = ([] if trees.returncode == 0 else [f"not on {ref}: {trees.stderr.strip()}"]) + changed + untracked
    if not problems:
        return hashlib.sha256(trees.stdout.encode()).hexdigest()
    msg = f"engine differs from {ref}: " + ", ".join(problems[:20])
    if os.environ.get("QS_ALLOW_DIRTY") != "1":
        raise TrialError(msg + " (commit it, or set QS_ALLOW_DIRTY=1 for dev runs)")
    h = hashlib.sha256()
    for f in sorted(git("ls-files", "-co", "--exclude-standard", "--", *ENGINE_PATHS).stdout.split()):
        p = Path(root, f)
        h.update(f.encode() + b"\0" + (p.read_bytes() if p.exists() else b"<deleted>") + b"\0")
    log.warning("!!! ENGINE DIRTY (QS_ALLOW_DIRTY=1): %s. Trial is NOT reproducible from main. !!!", msg)
    return "dirty-" + h.hexdigest()


# ---- patches ---------------------------------------------------------------------------------
_RULE_ROOTS = ("entry", "exit", "sizing", "session", "instrument")


def changed_rules(parent: Spec, child: Spec) -> list[str]:
    """The decision rules a patch changes. A feature, param or grid entry that only serves a changed rule
    is part of that rule ("go long when VIX > its own 250d median" is one rule even though it adds a
    feature and a param); one that serves no changed rule counts as its own change."""
    diff = spec_diff(parent, child)
    rules = {".".join(p.split(".")[:2]) for p in diff if p.split(".")[0] in _RULE_ROOTS}

    def text(spec, rule):           # the expression / value a rule is written in
        node = spec.raw
        for k in rule.split("."):
            node = node.get(k, {}) if isinstance(node, dict) else {}
        return json.dumps(node)

    def users(spec, token):         # rules (transitively through features) that mention token
        hit = {r for r in rules if re.search(rf"\b{re.escape(token)}\b", text(spec, r))}
        for fname, fargs in (spec.raw.get("features") or {}).items():
            if fname != token and re.search(rf"\b{re.escape(token)}\b", json.dumps(fargs)):
                hit |= users(spec, fname)
        return hit

    extra = set()
    for p in diff:
        root, *rest = p.split(".")
        if root in _RULE_ROOTS or not rest:
            continue
        name = rest[0]
        token = name if root == "features" else f"p.{name}"
        if not (users(parent, token) | users(child, token)):
            extra.add(f"{root}.{name}")
    return sorted(rules | extra)


def check_patch(parent: Spec, child: Spec) -> None:
    ch = changed_rules(parent, child)
    if len(ch) != 1:
        raise PatchError(f"a patch must change exactly one rule; {child.id} changes {len(ch)}: {ch} "
                         f"(fields: {spec_diff(parent, child)})")


# ---- helpers over the trials table -------------------------------------------------------------
def _latest(con, hid, col="metrics"):
    r = con.execute(f'SELECT {col} FROM trials WHERE hyp_id=? AND "window"=\'research\' ORDER BY id DESC LIMIT 1',
                    (hid,)).fetchone()
    return json.loads(r[0]) if r else None


def _cpcv(verdict: dict) -> tuple[float | None, list | None]:
    """(median path SR, paths) from the gate's cpcv check; tolerant of a scalar or dict value."""
    v = verdict.get("checks", {}).get("cpcv", {}).get("value")
    if isinstance(v, dict):
        return v.get("median_sr", v.get("median")), v.get("paths")
    return (float(v), None) if isinstance(v, (int, float)) else (None, None)


def _metrics(verdict: dict, seed: int) -> dict:
    m = {k: c.get("value") for k, c in verdict.get("checks", {}).items()
         if isinstance(c.get("value"), (int, float, bool, type(None)))}
    m["cpcv_median"] = _cpcv(verdict)[0]
    return {**m, "n_eff": verdict.get("n_eff"), "n_trials_family": verdict.get("n_trials_family"), "seed": seed}


def _holdout_verdict(daily, cfg, gate_sha) -> dict:
    net, p = float(daily.sum()), stats.psr(daily.to_numpy())
    min_psr = cfg.get("holdout_psr", 0.5)
    checks = {"holdout_net": {"passed": net > 0, "value": net, "threshold": 0.0, "note": HOLDOUT_NOTE},
              "holdout_psr": {"passed": p > min_psr, "value": p, "threshold": min_psr, "note": HOLDOUT_NOTE}}
    passed = all(c["passed"] for c in checks.values())
    demo = bool(cfg.get("demo", False))
    summary = [*(["DEMO GATE (looser thresholds)"] if demo else []),
               f"HOLDOUT {'PASS' if passed else 'FAIL'}: net {net:+.4f} of capital, PSR(0) {p:.3f} (> {min_psr})",
               HOLDOUT_NOTE]
    return {"checks": checks, "passed": passed, "demo": demo, "gate_sha": gate_sha,
            "n_trials_family": None, "n_eff": None, "summary": "\n".join(summary)}


# ---- the trial -------------------------------------------------------------------------------
def run_trial(con, hid, window="research", gate="real", *, bars=None, root=data.ROOT) -> dict:
    row = con.execute("SELECT spec_path, spec_sha, frozen_at, family FROM hypotheses WHERE id=?", (hid,)).fetchone()
    if row is None:
        raise KeyError(hid)
    path, frozen_sha, frozen_at, fam = row
    if not (path and frozen_sha and frozen_at):
        raise TrialError(f"{hid}: spec not frozen")
    if (sha := spec_sha(path)) != frozen_sha:
        raise TrialError(f"{hid}: spec sha {sha[:12]} != frozen {frozen_sha[:12]}; frozen specs are never edited")
    spec = load_spec(path)
    if spec.id != hid:
        raise TrialError(f"{hid}: spec file says id {spec.id!r}")
    eng = engine_sha(root)

    patch_kw = {}
    if ids.kind(hid) == "patch":
        par = ids.parent(hid)
        ppath = con.execute("SELECT spec_path FROM hypotheses WHERE id=?", (par,)).fetchone()
        if not ppath or not ppath[0]:
            raise TrialError(f"{hid}: parent {par} has no spec")
        check_patch(load_spec(ppath[0]), spec)
        pv = _latest(con, par, "verdict")
        patch_kw = dict(targeted_months=spec.card.get("targeted_months"),
                        parent_paths=_cpcv(pv)[1] if pv else None)

    if window == "holdout":
        if db.holdout_used(con, hid):
            raise TrialError(f"{hid}: holdout already used (once per strategy)")
        if not con.execute("SELECT 1 FROM trials WHERE hyp_id=? AND \"window\"='research' AND passed=1",
                           (hid,)).fetchone():
            raise TrialError(f"{hid}: holdout only after a gate pass on the research window")

    if bars is None:
        bars = data.load_bars(*data.window(window))
    daily = run_spec(spec, bars).daily / spec.sizing["capital_inr"]
    cfg, gate_sha = load_gate(Path(root, "config", "gate.demo.yaml" if gate == "demo" else "gate.yaml"))
    seed = secrets.randbelow(2**31)  # hidden from agents: picks CPCV N inside the gate
    if window == "holdout":
        verdict = _holdout_verdict(daily, cfg, gate_sha)
    else:
        verdict = run_gate(spec, bars, db.family_trials(con, fam), cfg, gate_sha,
                           refs=refs.all_refs(bars), seed=seed, **patch_kw)
    tid = db.record_trial(con, hid, window, spec.params, frozen_sha, eng, gate_sha, verdict["demo"],
                          daily, _metrics(verdict, seed), verdict, verdict["passed"])
    return {**verdict, "trial_id": tid}


# ---- hard stop -------------------------------------------------------------------------------
def hard_stop(con, hid, cfg) -> str | None:
    """First hard stop that applies, or None. Spend cap lives in the orchestrator, not here."""
    th = cfg.get("thresholds", cfg)
    if ids.depth(hid) > cfg.get("fork_depth", ids.MAX_FORK_DEPTH):
        return "fork_depth"

    base = re.sub(r"v\d+$", "", hid)
    fam_ids = [r[0] for r in con.execute("SELECT id FROM hypotheses WHERE family=?", (ids.family(hid),))]
    if sum(re.sub(r"v\d+$", "", i) == base and ids.kind(i) == "patch" for i in fam_ids) >= cfg.get("patch_budget", 4):
        return "patch_budget"

    # two most recent patches in this lineage, each without a CPCV-median gain over its parent
    def med(h):
        return (_latest(con, h) or {}).get("cpcv_median")
    chain, h = [], hid
    while ids.kind(h) == "patch" and len(chain) < 2:
        chain.append((h, ids.parent(h)))
        h = ids.parent(h)
    if len(chain) == 2 and all(med(c) is not None and med(p) is not None and med(c) <= med(p) for c, p in chain):
        return "no_improvement_2"

    # DSR > thr needs SR > SR* + z(thr)/sqrt(T-1) (Gaussian approx). Unreachable if that beats a plausible ceiling.
    R = db.family_trials(con, ids.family(hid))
    if R.shape[1] >= 2:
        srs = [stats.sharpe(R[c].dropna()) for c in R]
        sr_star = stats.expected_max_sr(stats.n_eff(R), float(np.var(srs, ddof=1)))
        T = int(R.notna().sum().max())
        need = sr_star + ss.norm.ppf(th.get("dsr", 0.95)) / np.sqrt(max(T - 1, 1))
        # ponytail: fixed ceiling on daily SR; replace with a family-specific bound if it misfires
        if need > cfg.get("max_sr_annual", 3.0) / np.sqrt(252):
            return "dsr_unreachable"
    return None
