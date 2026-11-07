import numpy as np
import pandas as pd
import pytest

from qs.gate.stats import (dsr, expected_max_sr, exposure_null_pctile, hhi, n_eff, p_failure, psr,
                           sharpe, drawdown_stats, top_k_share)
from qs.gate.pbo import pbo_cscv
from qs.gate.stats import _implied_precision


def test_psr_gaussian():
    r = np.random.default_rng(1).normal(0.05, 1, 5000)  # n=2000 gives z≈2.2, too near 0.95 on unlucky seeds
    assert psr(r, 0.0) > 0.95


def test_dsr_falls_with_trials():
    rng = np.random.default_rng(2)
    r = rng.normal(0.08, 1, 1000)
    srs = rng.normal(0, 0.03, 50)
    d1, d10, d100 = dsr(r, srs, 1), dsr(r, srs, 10), dsr(r, srs, 100)
    assert d1 > d10 > d100


@pytest.mark.parametrize("n,expected", [(10, 1.57), (100, 2.53), (1000, 3.26)])
def test_expected_max_sr_values(n, expected):
    assert abs(expected_max_sr(n, 1.0) - expected) <= 0.01


def test_n_eff_bounds():
    rng = np.random.default_rng(3)
    x = rng.normal(size=500)
    same = pd.DataFrame({f"t{i}": x for i in range(10)})
    assert n_eff(same) == pytest.approx(1.0, abs=1e-6)
    indep = pd.DataFrame(rng.normal(size=(5000, 10)))
    assert n_eff(indep) == pytest.approx(10, abs=0.5)
    assert n_eff(pd.DataFrame({"a": x})) == 1.0


def test_pbo_noise_high():
    M = pd.DataFrame(np.random.default_rng(4).normal(size=(1000, 100)))
    assert 0.3 <= pbo_cscv(M, S=16) <= 0.7


def test_pbo_skill_low():
    rng = np.random.default_rng(5)
    M = pd.DataFrame(rng.normal(size=(1000, 100)))
    M[0] += 0.3
    assert pbo_cscv(M, S=16) < 0.2
    assert np.isnan(pbo_cscv(M[[0]]))


def test_pbo_fast():
    import time
    M = pd.DataFrame(np.random.default_rng(6).normal(size=(750, 60)))
    t = time.perf_counter()
    pbo_cscv(M, S=16)
    assert time.perf_counter() - t < 2.0


def test_zero_variance_safe():
    z = np.zeros(300)
    assert sharpe(z) == 0.0
    assert psr(z) in (0.0, 0.5)
    assert dsr(z, np.zeros(5), 5) in (0.0, 0.5)
    assert sharpe(np.array([])) == 0.0 and psr(np.array([1.0])) in (0.0, 0.5)
    dd = drawdown_stats(pd.Series(z), 1e6)
    assert dd["max_dd"] == 0.0
    assert hhi(np.array([1.0])) == 0.0
    assert p_failure(np.array([]), 1.0) == 1.0
    assert top_k_share(pd.Series(z)) == 1.0


def test_hhi_extremes():
    assert hhi(np.ones(10)) == pytest.approx(0.0)
    assert hhi(np.array([1.0, 0, 0, 0])) == pytest.approx(1.0)


def test_p_failure_direction():
    rng = np.random.default_rng(7)
    good = np.where(rng.random(500) < 0.6, 1.0, -1.0)  # ~250 bets/yr, SR well above 1
    coin = np.tile([1.0, -1.0], 250)  # exact p=0.5; a random draw can land above p*≈0.532 (SR 1 at 250 bets/yr)
    assert p_failure(good, 2.0) < 0.05
    assert p_failure(coin, 2.0) > 0.9  # ≈0.92: 500 eval bets leave ~1.4σ between 0.5 and p*
    assert _implied_precision(-1.0, 1.0, 250, 1.0) == pytest.approx(0.5316, abs=1e-3)


def test_exposure_null_kills_beta():
    rng = np.random.default_rng(8)
    idx = pd.RangeIndex(1000)
    mkt = pd.Series(rng.normal(0.001, 0.01, 1000), index=idx)
    pos = pd.Series(1.0, index=idx)
    assert exposure_null_pctile(pos, mkt, pos * mkt) < 0.95
    # timing skill: long on up days, flat otherwise, beats its shifted exposure
    timed = pd.Series((mkt > 0).astype(float), index=idx)
    assert exposure_null_pctile(timed, mkt, timed * mkt) >= 0.95
