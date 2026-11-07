"""Probability of backtest overfitting by CSCV (AFML §11.6)."""
from itertools import combinations

import numpy as np
import pandas as pd


def pbo_cscv(M: pd.DataFrame, S: int = 16) -> float:
    """M: rows = days, columns = trials. All C(S, S/2) IS/OOS block combinations, vectorised
    from per-block sums (12,870 combos × 60 trials runs in well under a second; no subsampling).
    PBO = share of combinations where the IS-best trial's OOS logit rank λ ≤ 0."""
    X = np.asarray(M, float)
    if X.ndim != 2 or X.shape[1] < 2:
        return float("nan")
    X = np.nan_to_num(X)
    blocks = np.array_split(np.arange(len(X)), S)
    s1 = np.array([X[b].sum(0) for b in blocks])        # S × trials
    s2 = np.array([(X[b] ** 2).sum(0) for b in blocks])
    cnt = np.array([len(b) for b in blocks], float)
    combos = np.array(list(combinations(range(S), S // 2)))
    mask = np.zeros((len(combos), S))
    mask[np.arange(len(combos))[:, None], combos] = 1.0

    def sr(m):
        n = (m @ cnt)[:, None]
        a, b = m @ s1, m @ s2
        var = (b - a**2 / n) / (n - 1)
        sd = np.sqrt(np.maximum(var, 0))
        return np.divide(a / n, sd, out=np.zeros_like(a), where=sd > 0)

    is_sr, oos_sr = sr(mask), sr(1 - mask)
    best = is_sr.argmax(1)
    oos_best = oos_sr[np.arange(len(mask)), best][:, None]
    rank = (oos_sr < oos_best).sum(1) + 0.5 * ((oos_sr == oos_best).sum(1) - 1) + 1  # 1..N, ties averaged
    w = rank / (X.shape[1] + 1)
    return float((np.log(w / (1 - w)) <= 0).mean())
