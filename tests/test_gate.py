import dataclasses
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qs.gate import fixtures
from qs.gate.cpcv import cpcv_paths
from qs.gate.gate import _cpcv_matrix, load_gate, run_gate
from qs.gate.stats import sharpe
from qs.spec import load_spec

ROOT = Path(__file__).resolve().parents[1]
REAL, DEMO = load_gate(ROOT / "config/gate.yaml"), load_gate(ROOT / "config/gate.demo.yaml")


def make_bars(days=400, drift=0.0004, seed=0, start="2023-01-02"):
    """Random walk with drift: 375 one-minute bars per weekday, weekly Thursday expiry."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=days)
    gaps = rng.normal(drift, 0.004, days)
    r = rng.normal(0, 0.0004, (days, 375))
    lvl = 22000 * np.exp(np.cumsum(gaps + r.sum(1)) - r.sum(1))  # day open levels
    close = lvl[:, None] * np.exp(np.cumsum(r, 1))
    open_ = np.c_[lvl, close[:, :-1]]
    ts = (dates.values[:, None] + pd.to_timedelta(np.arange(375) + 555, "min").values).ravel()
    exp = np.repeat((dates + pd.to_timedelta((3 - dates.day_of_week) % 7, "D")).values, 375)
    c, o = close.ravel(), open_.ravel()
    return pd.DataFrame({
        "open": o, "high": np.maximum(o, c) * 1.0001, "low": np.minimum(o, c) * 0.9999, "close": c,
        "volume": 500.0, "oi": 1e6, "expiry": exp, "spot": c - 20, "vix": 14.0,
        "date": np.repeat(dates.values, 375)}, index=pd.DatetimeIndex(ts, name="ts"))


@pytest.fixture(scope="module")
def bars():
    return make_bars()


def fast(cfg_sha):  # fewer probe cuts and synthetic paths keep the suite quick; thresholds unchanged
    cfg, sha = cfg_sha
    return {**cfg, "probe_cuts": 3, "synthetic_paths": 2000}, sha


def gate(spec, bars, which=REAL, seed=0, **kw):
    cfg, sha = fast(which)
    return run_gate(spec, bars, pd.DataFrame(), cfg, sha, refs={}, seed=seed, **kw)


def test_gate_fails_pure_long(bars):
    v = gate(fixtures.pure_long(), bars)
    assert v["checks"]["probe"]["passed"] and v["checks"]["min_bets"]["passed"]
    assert not v["checks"]["exposure_null"]["passed"]
    assert not v["passed"]


def test_gate_fails_few_bets(bars):
    spec = fixtures.random_entries(0)
    few = dataclasses.replace(spec, entry={**spec.entry, "long": "u < 0.00002", "short": "u > 0.99998"})
    for s in (few, dataclasses.replace(spec, entry={"long": "u > 2"})):  # few bets, zero bets
        v = gate(s, bars)
        assert not v["passed"] and not v["checks"]["min_bets"]["passed"]
        assert "min_bets" in v["summary"] and len(v["summary"].splitlines()) <= 12


def test_demo_stamp(bars):
    v = gate(load_spec(ROOT / "specs/examples/orb_vol.yaml"), bars, DEMO)
    assert v["demo"] is True and v["gate_sha"] == DEMO[1]
    assert v["summary"].startswith("DEMO GATE (looser thresholds)")
    assert len(v["summary"].splitlines()) <= 12
    real = gate(load_spec(ROOT / "specs/examples/orb_vol.yaml"), bars)
    assert real["demo"] is False and not real["summary"].startswith("DEMO")
    assert {"probe", "determinism", "min_bets", "cpcv", "psr", "dsr", "pbo", "exposure_null", "beats_refs",
            "costs", "p_failure", "concentration", "drawdown", "robustness", "ablation",
            "synthetic_exit"} <= set(real["checks"])


def test_gate_sha_changes_with_config(tmp_path):
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    text = (ROOT / "config/gate.yaml").read_text()
    a.write_text(text)
    b.write_text(text.replace("psr: 0.95", "psr: 0.96"))
    (ca, sa), (cb, sb) = load_gate(a), load_gate(b)
    assert sa != sb and len(sa) == 64 and ca["thresholds"]["psr"] == 0.95 and cb["thresholds"]["psr"] == 0.96
    for k in ("patch_budget", "fork_depth", "dsr", "max_sr_annual", "holdout_psr"):  # runner contract
        assert k in REAL[0]["thresholds"] and k in DEMO[0]["thresholds"]
    assert sa == REAL[1] and REAL[1] != DEMO[1]


def test_calibration_rejects(bars):
    cfg, sha = fast(REAL)
    rates = fixtures.calibrate(bars, cfg, sha, n_seeds=5, only=["random_entries"])
    assert rates["random_entries"] >= 0.8


def test_planted_leak_rejected_by_probe(bars):
    v = gate(fixtures.planted_leak(), bars.iloc[: 375 * 60])
    assert not v["checks"]["probe"]["passed"] and not v["passed"]


def test_cpcv_matrix_matches_cpcv_paths():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2023-01-02", periods=300)
    d = {f"p{i}": pd.Series(rng.normal(0.001 * i, 0.01, 300), index=idx) for i in range(3)}
    spans = {k: [(i, i) for i in range(0, 300, 7)] for k in d}
    X = pd.DataFrame(d).to_numpy()
    m = _cpcv_matrix(X, [spans[k] for k in d], N=10, k=2, embargo_frac=0.01)
    np.testing.assert_allclose([sharpe(p) for p in m], cpcv_paths(d, spans, N=10, k=2))


def test_patch_nontarget(bars):
    spec = load_spec(ROOT / "specs/examples/orb_vol.yaml")
    base = gate(spec, bars)
    parent = np.asarray(base["paths"])
    same = gate(spec, bars, parent_paths=parent, targeted_months=["2023-03"])
    assert same["checks"]["patch_nontarget"]["passed"]  # identical child holds every fold
    worse = gate(spec, bars, parent_paths=parent + 0.01, targeted_months=[])
    assert not worse["checks"]["patch_nontarget"]["passed"]
    cv = base["checks"]["cpcv"]["value"]  # runner contract: {median_sr, paths}
    assert set(cv) == {"median_sr", "paths"} and len(cv["paths"]) == len(parent)
    one_d = gate(spec, bars, parent_paths=np.array(cv["paths"]) + 1.0)  # 1-D: median rule only
    assert not one_d["checks"]["patch_nontarget"]["passed"]
