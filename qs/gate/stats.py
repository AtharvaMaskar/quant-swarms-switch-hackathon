"""Gate statistics (AFML). Pure functions on daily net returns (rupees / capital), non-annualised."""
import numpy as np
import pandas as pd
from scipy import stats as ss

EULER_GAMMA = 0.5772156649


def sharpe(r) -> float:
    r = np.asarray(r, float)
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    return 0.0 if sd == 0 or not np.isfinite(sd) else float(r.mean() / sd)


def psr(r, sr0: float = 0.0) -> float:
    """AFML §14.7.2. Zero variance or fewer than 3 obs: 0.0 (no evidence of skill)."""
    r = np.asarray(r, float)
    if len(r) < 3 or r.std(ddof=1) == 0:
        return 0.0
    sr = sharpe(r)
    g3 = ss.skew(r)
    g4 = ss.kurtosis(r, fisher=False)  # raw kurtosis, 3 for a Gaussian
    den = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    den = max(den, 1e-12)
    return float(ss.norm.cdf((sr - sr0) * np.sqrt(len(r) - 1) / np.sqrt(den)))


def expected_max_sr(n_eff: float, sr_var: float) -> float:
    """AFML §14.7.3 (False Strategy theorem): E[max SR] over n_eff independent trials."""
    if n_eff <= 1 or sr_var <= 0:
        return 0.0
    z = ss.norm.ppf
    return float(np.sqrt(sr_var) * ((1 - EULER_GAMMA) * z(1 - 1 / n_eff) + EULER_GAMMA * z(1 - 1 / (n_eff * np.e))))


def dsr(r, trial_srs: np.ndarray, n_eff: float) -> float:
    """PSR against SR* = expected max SR under the family's trials (non-annualised trial Sharpes)."""
    srs = np.asarray(trial_srs, float)
    var = float(srs.var(ddof=1)) if len(srs) > 1 else 0.0
    return psr(r, expected_max_sr(n_eff, var))


def n_eff(R: pd.DataFrame) -> float:
    """ρ̄ + (1−ρ̄)·M with ρ̄ the mean off-diagonal correlation, clipped to [0, 1]."""
    M = R.shape[1]
    if M <= 1:
        return 1.0
    c = R.corr().to_numpy()
    rho = np.nanmean(c[~np.eye(M, dtype=bool)])
    rho = float(np.clip(0.0 if np.isnan(rho) else rho, 0.0, 1.0))
    return rho + (1 - rho) * M


def hhi(x) -> float:
    """AFML §14.5.1 normalised HHI of a set of returns (pass r[r>0] or r[r<0]). 0 if < 2 values."""
    x = np.asarray(x, float)
    if len(x) < 2 or x.sum() == 0:
        return 0.0
    w = x / x.sum()
    return float(((w**2).sum() - 1 / len(x)) / (1 - 1 / len(x)))


def drawdown_stats(daily_inr: pd.Series, capital: float, vol_target: float = 0.15) -> dict:
    """Returns rescaled to `vol_target` annualised vol (252 d), equity = 1 + cumsum (fixed capital).
    max_dd / dd95: max and 95th pct of per-episode drawdown (fraction of HWM).
    tuw_frac: longest time-under-water episode / total days. tuw95: 95th pct episode length in days."""
    r = np.asarray(daily_inr, float) / capital
    zero = dict(max_dd=0.0, dd95=0.0, tuw_frac=0.0, tuw95=0.0)
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    if sd == 0 or not np.isfinite(sd):
        return zero
    eq = 1 + np.cumsum(r * vol_target / (sd * np.sqrt(252)))
    hwm = np.maximum.accumulate(np.maximum(eq, 1.0))
    dd = 1 - eq / hwm
    under = dd > 0
    if not under.any():
        return zero
    # episodes = runs of consecutive under-water days
    edges = np.diff(np.r_[0, under.astype(int), 0])
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    ep_dd = np.array([dd[s:e].max() for s, e in zip(starts, ends)])
    ep_len = ends - starts
    return dict(max_dd=float(dd.max()), dd95=float(np.percentile(ep_dd, 95)),
                tuw_frac=float(ep_len.max() / len(r)), tuw95=float(np.percentile(ep_len, 95)))


def top_k_share(daily_inr, k: int = 5) -> float:
    """Share of total net P&L from the best k days. Total ≤ 0 returns 1.0 (fails any < 1 threshold)."""
    x = np.asarray(daily_inr, float)
    total = x.sum()
    if total <= 0:
        return 1.0
    return float(np.sort(x)[::-1][:k].sum() / total)


def _implied_precision(sl: float, pt: float, freq: float, tsr: float) -> float:
    """AFML snippet 15.4 binHR: precision needed for annualised SR tsr given payoffs pt>0, sl<0."""
    a = (freq + tsr**2) * (pt - sl) ** 2
    b = (2 * freq * sl - tsr**2 * (pt - sl)) * (pt - sl)
    c = freq * sl**2
    disc = b**2 - 4 * a * c
    return np.nan if disc < 0 else (-b + np.sqrt(disc)) / (2 * a)


def p_failure(bet_net: np.ndarray, years: float, eval_years: float = 2.0, target_sr: float = 1.0,
              n_boot: int = 5000, seed: int = 0) -> float:
    """AFML §15.4: P[precision over an eval_years horizon < implied precision for target_sr]."""
    b = np.asarray(bet_net, float)
    if len(b) < 2 or years <= 0 or not (b > 0).any():
        return 1.0
    if not (b < 0).any():
        return 0.0
    pt, sl, p = b[b > 0].mean(), b[b <= 0].mean(), (b > 0).mean()
    if sl == 0:
        return 0.0
    freq = len(b) / years
    p_star = _implied_precision(sl, pt, freq, target_sr)
    if not np.isfinite(p_star) or p_star >= 1:
        return 1.0
    n_eval = max(int(round(freq * eval_years)), 1)
    boot = np.random.default_rng(seed).binomial(n_eval, p, n_boot) / n_eval
    return float((boot < p_star).mean())


def exposure_null_pctile(position_daily: pd.Series, mkt_ret_daily: pd.Series, strat_daily: pd.Series,
                         n: int = 1000, seed: int = 0) -> float:
    """Share of circularly-shifted-exposure nulls whose Sharpe is strictly below the strategy's.
    Constant exposure shifts to itself, so pure beta scores 0."""
    df = pd.concat([position_daily, mkt_ret_daily, strat_daily], axis=1, join="inner").fillna(0.0)
    pos, mkt, strat = (df.iloc[:, i].to_numpy(float) for i in range(3))
    T = len(df)
    if T < 3:
        return 0.0
    target = sharpe(strat)
    shifts = np.random.default_rng(seed).integers(1, T, n)
    null = np.array([sharpe(np.roll(pos, s) * mkt) for s in shifts])
    return float((null < target).mean())


def ret_on_costs(gross_total: float, cost_total: float) -> float:
    if cost_total <= 0:
        return float("inf") if gross_total > 0 else 0.0
    return float(gross_total / cost_total)
