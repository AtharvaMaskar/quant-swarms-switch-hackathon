from datetime import date, time

import pytest

from qs.costs import charge_set, load_costs, order_cost_inr, slippage_pts

C = load_costs()
D26 = date(2026, 5, 4)


def round_trip(seg, units, price, d=D26):
    return order_cost_inr(C, d, seg, +1, units, price) + order_cost_inr(C, d, seg, -1, units, price)


def test_round_trip_nifty_fut_2026():
    assert round_trip("futures", 65, 25000) == pytest.approx(966, abs=2)


def test_round_trip_nifty_opt_2026():
    assert round_trip("options", 65, 150) == pytest.approx(70.3, abs=1)


def test_stt_dated():
    assert charge_set(C, date(2023, 1, 10))["futures"]["stt"]["rate"] == pytest.approx(0.0001)
    assert charge_set(C, date(2024, 11, 4))["futures"]["stt"]["rate"] == pytest.approx(0.0002)
    # STT is charged on the sell side only for futures: sell - buy = STT - stamp
    d = date(2023, 1, 10)
    sell = order_cost_inr(C, d, "futures", -1, 50, 18000)
    buy = order_cost_inr(C, d, "futures", +1, 50, 18000)
    assert sell - buy == pytest.approx(90 - 18)  # STT 0.01% of 9L, stamp 0.002%


def test_inherits_merged():
    s = charge_set(C, D26)
    assert s["gst"] == pytest.approx(0.18)  # restated only in the first set
    assert s["options"]["stt"]["rate"] == pytest.approx(0.0015)
    with pytest.raises(ValueError):
        charge_set(C, date(2021, 12, 31))


def test_zero_units_is_free():
    assert order_cost_inr(C, D26, "futures", -1, 0, 25000) == 0


def test_slippage_expiry_mult():
    kw = dict(minute=time(11, 0))
    base = slippage_pts(C, "futures", 25000, 0.10, expiry_day=False, **kw)
    assert base == pytest.approx(max(2 * 0.10, 25000 * 0.00003))
    assert slippage_pts(C, "futures", 25000, 0.10, expiry_day=True, **kw) == pytest.approx(2 * base)


def test_slippage_half_spread_and_open():
    hs = slippage_pts(C, "options", 150, 0.05, minute=time(11, 0), expiry_day=False, half_spread=0.4)
    assert hs == pytest.approx(0.4)
    late = slippage_pts(C, "options", 150, 0.05, minute=time(11, 0), expiry_day=False)
    assert late == pytest.approx(0.05 + 0.0025 * 150)
    early = slippage_pts(C, "options", 150, 0.05, minute=time(9, 16), expiry_day=False)
    assert early > late
