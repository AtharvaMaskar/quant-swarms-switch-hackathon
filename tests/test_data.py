from pathlib import Path

import pandas as pd
import pytest

from qs import data


def test_window_names():
    assert data.window("sandbox") == ("2022-01-03", "2022-06-30")
    assert data.window("research") == ("2022-07-01", "2025-06-30")
    assert data.window("holdout") == ("2025-07-01", "2026-09-23")
    assert data.window("all") == ("2022-01-03", "2026-09-23")
    with pytest.raises(KeyError):
        data.window("nope")


def test_vix_is_lagged():
    ts = pd.to_datetime(["2023-01-03 09:15", "2023-01-03 15:29", "2023-01-04 09:15", "2023-01-05 09:15"])
    bars = pd.DataFrame({"close": [1.0, 2, 3, 4]}, index=pd.Index(ts, name="ts"))
    bars["date"] = bars.index.normalize()
    vix = pd.Series([14.0, 15.0, 16.0, 17.0], index=pd.to_datetime(["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"]))
    out = data._attach_vix(bars, vix)
    assert out["vix"].tolist() == [14.0, 14.0, 15.0, 16.0]


@pytest.mark.skipif(not (Path(__file__).parents[1] / "data" / "fut_front_1m.parquet").exists(), reason="data/ not built")
def test_load_bars_smoke():
    b = data.load_bars("2023-01-02", "2023-01-06")
    assert b["date"].nunique() == 5
    assert abs(len(b) - 5 * 375) <= 5
    assert not b["close"].isna().any()
    assert list(b.columns) == ["open", "high", "low", "close", "volume", "oi", "expiry", "spot", "vix", "date"]
    assert b.index.tz is None and b.index.name == "ts"
    assert b.index.time.min().strftime("%H:%M") == "09:15" and b.index.time.max().strftime("%H:%M") == "15:29"
