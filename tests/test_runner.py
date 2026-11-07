import logging
import subprocess

import numpy as np
import pandas as pd
import pytest
import yaml

from qs import db, refs, runner
from qs.runner import PatchError, TrialError, check_patch, hard_stop, run_trial
from qs.spec import load_spec, spec_sha
from test_engine import ORB, make_bars

BARS = make_bars(days=30)


def _verdict(passed=False, median=0.0, demo=False):
    return {"checks": {"cpcv": {"passed": passed, "value": {"median_sr": median, "paths": [median]},
                                "threshold": 0.0, "note": ""},
                       "dsr": {"passed": passed, "value": 0.5, "threshold": 0.95, "note": ""}},
            "passed": passed, "demo": demo, "gate_sha": "g", "n_trials_family": 1, "n_eff": 1.0,
            "summary": "stub"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QS_ALLOW_DIRTY", "1")
    calls, outcome = [], {"passed": False, "median": 0.0}

    def fake_run_gate(spec, bars, family_R, cfg, gate_sha, *, refs, seed, targeted_months=None, parent_paths=None):
        calls.append(dict(spec=spec, family_R=family_R, refs=refs, seed=seed, parent_paths=parent_paths))
        return _verdict(outcome["passed"], outcome["median"], cfg.get("demo", False))

    monkeypatch.setattr(runner, "load_gate", lambda path: ({"demo": "demo" in str(path)}, "gsha"))
    monkeypatch.setattr(runner, "run_gate", fake_run_gate)
    con = db.connect(tmp_path / "r.db")
    return con, tmp_path, calls, outcome


def _add(con, tmp_path, hid, freeze=True, **over):
    d = yaml.safe_load(ORB.read_text())
    d["id"] = hid
    d.update(over)
    p = tmp_path / f"{hid}.yaml"
    p.write_text(yaml.safe_dump(d))
    db.add_hypothesis(con, hid, "orb vol", d["card"], str(p))
    if freeze:
        db.freeze(con, hid, str(p), spec_sha(p))
    return p


def test_frozen_spec_tamper_rejected(env):
    con, tmp, calls, _ = env
    _add(con, tmp, "h2", freeze=False)
    with pytest.raises(TrialError, match="not frozen"):
        run_trial(con, "h2", bars=BARS)
    p = _add(con, tmp, "h1")
    v = run_trial(con, "h1", bars=BARS)
    assert v["trial_id"] and "buy_hold" in calls[0]["refs"]
    d = yaml.safe_load(p.read_text())
    d["params"]["thr"] = 0.009
    p.write_text(yaml.safe_dump(d))
    with pytest.raises(TrialError, match="sha"):
        run_trial(con, "h1", bars=BARS)
    assert len(calls) == 1


def test_patch_must_change_one_rule(env, tmp_path):
    con, tmp, calls, _ = env
    parent = load_spec(_add(con, tmp, "h1"))
    two = load_spec(_add(con, tmp, "h1v2", params={"thr": 0.004, "rvcap": 0.18}, grid={}))
    with pytest.raises(PatchError, match="params.thr"):  # params.thr and the grid both changed
        check_patch(parent, two)
    run_trial(con, "h1", bars=BARS)
    with pytest.raises(PatchError):
        run_trial(con, "h1v2", bars=BARS)
    assert con.execute("SELECT count(*) FROM trials WHERE hyp_id='h1v2'").fetchone()[0] == 0
    _add(con, tmp, "h2")
    run_trial(con, "h2", bars=BARS)
    ok = _add(con, tmp, "h2v2", exit={"stop_atr": 2.0, "take_atr": 3.0, "atr_days": 14, "time": "15:15"})
    check_patch(parent, load_spec(ok))
    run_trial(con, "h2v2", bars=BARS)
    assert calls[-1]["parent_paths"] == [0.0]  # parent's CPCV paths handed to the gate
    with pytest.raises(PatchError, match="changes 0"):
        check_patch(parent, parent)


def test_holdout_only_once(env):
    con, tmp, _, outcome = env
    _add(con, tmp, "h1")
    with pytest.raises(TrialError, match="gate pass"):
        run_trial(con, "h1", window="holdout", bars=BARS)
    run_trial(con, "h1", bars=BARS)  # failed research trial
    with pytest.raises(TrialError, match="gate pass"):
        run_trial(con, "h1", window="holdout", bars=BARS)
    outcome["passed"] = True
    run_trial(con, "h1", bars=BARS)
    v = run_trial(con, "h1", window="holdout", bars=BARS)
    assert set(v["checks"]) == {"holdout_net", "holdout_psr"}
    with pytest.raises(TrialError, match="once"):
        run_trial(con, "h1", window="holdout", bars=BARS)


def test_demo_gate_flag(env):
    con, tmp, _, _ = env
    _add(con, tmp, "h1")
    assert run_trial(con, "h1", gate="demo", bars=BARS)["demo"] is True
    assert con.execute("SELECT demo FROM trials").fetchone()[0] == 1


def _trial(con, hid, median=None, daily=None):
    daily = daily if daily is not None else pd.Series(0.0, index=pd.date_range("2023-01-02", periods=500))
    db.record_trial(con, hid, "research", {}, "s", "e", "g", False, daily,
                    {"cpcv_median": median}, {}, False)


def test_hard_stop_budget(env):
    con, *_ = env
    db.add_hypothesis(con, "h1", "a", {})
    for k in (2, 3, 4):
        db.add_hypothesis(con, f"h1v{k}", "a", {})
    assert hard_stop(con, "h1v4", {}) is None  # 3 of 4 patches used
    db.add_hypothesis(con, "h1v5", "a", {})
    assert hard_stop(con, "h1v5", {}) == "patch_budget"
    assert hard_stop(con, "h1v5", {"patch_budget": 6}) is None


def test_hard_stop_no_improvement_and_fork_depth(env):
    con, *_ = env
    for hid, m in [("h1", 0.10), ("h1v2", 0.12), ("h1v3", 0.11)]:
        db.add_hypothesis(con, hid, "a", {})
        _trial(con, hid, m)
    assert hard_stop(con, "h1v3", {}) is None  # v2 improved
    db.add_hypothesis(con, "h1v4", "a", {})
    _trial(con, "h1v4", 0.11)
    assert hard_stop(con, "h1v4", {}) == "no_improvement_2"
    db.add_hypothesis(con, "h1.1.1.1.1", "a", {})
    assert hard_stop(con, "h1.1.1.1.1", {}) == "fork_depth"


def test_hard_stop_dsr_unreachable(env):
    con, *_ = env
    db.add_hypothesis(con, "h1", "a", {})
    rng = np.random.default_rng(0)
    idx = pd.date_range("2023-01-02", periods=200, freq="B")
    for mu in np.linspace(-0.5, 0.5, 20):  # wildly different trial Sharpes, independent noise
        _trial(con, "h1", 0.0, pd.Series(rng.normal(mu, 1.0, 200), index=idx))
    assert hard_stop(con, "h1", {}) == "dsr_unreachable"


def test_fork_counts_in_family(env):
    con, tmp, calls, _ = env
    _add(con, tmp, "h1")
    _add(con, tmp, "h1.1", exit={"stop_atr": 2.0, "take_atr": 3.0, "atr_days": 14, "time": "15:15"})
    run_trial(con, "h1", bars=BARS)
    run_trial(con, "h1.1", bars=BARS)
    assert calls[1]["family_R"].shape[1] == 1  # the root's trial feeds the fork's gate
    assert db.family_trials(con, "h1").shape[1] == 2


# ---- engine_sha ---------------------------------------------------------------------------------
def _git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    for p in ("qs/a.py", "config/c.yaml", "ref/r.csv"):
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text(p)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    return tmp_path


def test_engine_sha_clean_and_dirty(repo, monkeypatch, caplog):
    monkeypatch.delenv("QS_ALLOW_DIRTY", raising=False)
    s = runner.engine_sha(repo)
    assert len(s) == 64 and runner.engine_sha(repo) == s
    (repo / "qs/a.py").write_text("changed")
    with pytest.raises(TrialError, match="qs/a.py"):
        runner.engine_sha(repo)
    (repo / "qs/a.py").write_text("qs/a.py")
    (repo / "ref/new.csv").write_text("x")  # untracked counts too
    with pytest.raises(TrialError, match="ref/new.csv"):
        runner.engine_sha(repo)
    monkeypatch.setenv("QS_ALLOW_DIRTY", "1")
    with caplog.at_level(logging.WARNING):
        d = runner.engine_sha(repo)
    assert d.startswith("dirty-") and "ENGINE DIRTY" in caplog.text


def test_engine_sha_not_on_main(tmp_path, monkeypatch):
    monkeypatch.delenv("QS_ALLOW_DIRTY", raising=False)
    (tmp_path / "qs").mkdir()
    (tmp_path / "qs/a.py").write_text("x")
    _git(tmp_path, "init", "-q", "-b", "main")
    with pytest.raises(TrialError):
        runner.engine_sha(tmp_path)


# ---- reference rows -------------------------------------------------------------------------------
def test_live_bot_daily():
    if not refs.LIVE_BOT.exists():
        pytest.skip("data/refs/live_bot.parquet not built")
    r = refs.live_bot_daily()
    eq = pd.read_parquet(refs.LIVE_BOT)["equity_inr"].to_numpy()
    assert np.allclose(14_040_000 * (1 + r.cumsum().to_numpy()), eq)
    assert r.index.is_unique and r.index.is_monotonic_increasing


def test_buy_hold_daily():
    r = refs.buy_hold_daily(BARS)
    assert len(r) == BARS.index.normalize().nunique()
    assert (r != 0).sum() >= 10  # long most days once ATR has warmed up


def test_refs_omit_missing_live_bot(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(refs, "LIVE_BOT", tmp_path / "nope.parquet")
    with caplog.at_level(logging.WARNING):
        out = refs.all_refs(BARS)
    assert set(out) == {"buy_hold"} and "live_bot" in caplog.text


def test_patch_counts_rules_not_fields(tmp_path):
    """Swapping a fixed VIX threshold for VIX above its own trailing quantile is ONE rule change,
    even though it adds a feature and changes a param and its grid; touching an exit as well is two."""
    import yaml
    from qs.spec import load_spec
    from qs.runner import changed_rules, check_patch, PatchError
    base = yaml.safe_load(open("specs/h2.yaml"))
    p = tmp_path / "h2.yaml"; p.write_text(yaml.safe_dump(base))
    child = yaml.safe_load(yaml.safe_dump(base)); child["id"] = "h2v2"
    child["features"]["vq"] = {"fn": "quantile", "feature": "v", "q": 0.5, "days": 250}
    child["entry"]["long"] = child["entry"]["long"].replace("v > p.vthr", "v > vq * p.vthr")
    child["params"]["vthr"] = 1.0
    child["grid"]["vthr"] = [0.9, 1.0, 1.1]
    c = tmp_path / "h2v2.yaml"; c.write_text(yaml.safe_dump(child))
    assert changed_rules(load_spec(p), load_spec(c)) == ["entry.long"]
    check_patch(load_spec(p), load_spec(c))
    child["exit"]["stop_atr"] = 40
    c.write_text(yaml.safe_dump(child))
    with pytest.raises(PatchError):
        check_patch(load_spec(p), load_spec(c))
