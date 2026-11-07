"""Combinatorial purged CV (AFML §12.4) with purge + embargo (§7.4). Indices are day positions."""
from itertools import combinations
from math import ceil

import numpy as np
import pandas as pd

from qs.gate.stats import sharpe


def cpcv_splits(n_days: int, N: int, k: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """C(N,k) (train, test) splits over N contiguous groups, in itertools.combinations order."""
    groups = np.array_split(np.arange(n_days), N)
    out = []
    for combo in combinations(range(N), k):
        test = np.concatenate([groups[g] for g in combo])
        out.append((np.setdiff1d(np.arange(n_days), test), test))
    return out


def purge_embargo(train_idx, test_idx, bet_spans: list[tuple[int, int]], embargo: int) -> np.ndarray:
    """Drop train days inside any bet span [t0,t1] that touches a test day, and the `embargo`
    days after each contiguous test block."""
    train, test = np.asarray(train_idx), np.sort(np.asarray(test_idx))
    drop = set()
    test_set = set(test.tolist())
    for t0, t1 in bet_spans:
        days = range(t0, t1 + 1)
        if any(d in test_set for d in days):
            drop.update(days)
    if embargo > 0 and len(test):
        block_ends = test[np.r_[np.diff(test) > 1, True]]
        for e in block_ends:
            drop.update(range(e + 1, e + 1 + embargo))
    return train[~np.isin(train, list(drop))]


def cpcv_paths(daily_by_param: dict[str, pd.Series], bet_spans_by_param: dict[str, list], N: int,
               k: int = 2, embargo_frac: float = 0.01) -> np.ndarray:
    """Per split pick the param with the best purged train Sharpe; assemble φ = k/N·C(N,k) test
    paths (path j takes group g from the j-th split testing g); return path Sharpes."""
    df = pd.DataFrame(daily_by_param).fillna(0.0)
    T = len(df)
    X = df.to_numpy(float)
    keys = list(df.columns)
    embargo = ceil(embargo_frac * T)
    groups = np.array_split(np.arange(T), N)
    combos = list(combinations(range(N), k))
    per_group: dict[int, list[np.ndarray]] = {g: [] for g in range(N)}
    for combo, (train, test) in zip(combos, cpcv_splits(T, N, k)):
        scores = [sharpe(X[purge_embargo(train, test, bet_spans_by_param.get(key, []), embargo), j])
                  for j, key in enumerate(keys)]
        best = int(np.argmax(scores))
        for g in combo:
            per_group[g].append(X[groups[g], best])
    n_paths = len(per_group[0])  # = C(N-1,k-1) = k/N·C(N,k)
    return np.array([sharpe(np.concatenate([per_group[g][j] for g in range(N)])) for j in range(n_paths)])
