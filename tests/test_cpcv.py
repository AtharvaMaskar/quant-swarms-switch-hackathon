import numpy as np
import pandas as pd

from qs.gate.cpcv import cpcv_paths, cpcv_splits, purge_embargo


def test_cpcv_counts():
    splits = cpcv_splits(600, 6, 2)
    assert len(splits) == 15
    for tr, te in splits:
        assert len(np.intersect1d(tr, te)) == 0 and len(tr) + len(te) == 600
    idx = pd.RangeIndex(600)
    rng = np.random.default_rng(0)
    daily = {"a": pd.Series(rng.normal(size=600), index=idx), "b": pd.Series(rng.normal(size=600), index=idx)}
    spans = {"a": [(i, i + 1) for i in range(0, 600, 5)], "b": []}
    paths = cpcv_paths(daily, spans, N=6, k=2)
    assert paths.shape == (5,)
    assert len(cpcv_paths({"a": daily["a"]}, {"a": []}, N=6, k=2)) == 5


def test_purge_drops_overlap():
    tr, te = cpcv_splits(100, 10, 1)[1]  # test = days 10..19
    out = purge_embargo(tr, te, [(8, 11)], embargo=0)
    assert not np.isin([8, 9], out).any()  # bet straddling the boundary is purged from train
    assert 7 in out and 20 in out
    out = purge_embargo(tr, te, [(8, 11)], embargo=3)
    assert not np.isin([20, 21, 22], out).any() and 23 in out
    assert purge_embargo(tr, te, [(30, 35)], embargo=0).tolist() == tr.tolist()
