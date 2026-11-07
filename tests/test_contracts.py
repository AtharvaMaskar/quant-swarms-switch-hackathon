from datetime import date

from qs.contracts import freeze_qty, is_trading_day, lot_size, tick_size


def test_lot_size_history():
    assert lot_size("NIFTY", date(2024, 5, 30)) == 25
    assert lot_size("NIFTY", date(2025, 2, 27)) == 75
    assert lot_size("NIFTY", date(2026, 1, 27)) == 65


def test_tick_banding():
    assert tick_size("NIFTY", date(2025, 5, 1), 25000) == 0.10
    assert tick_size("NIFTY", date(2024, 5, 1), 25000) == 0.05
    assert tick_size("BANKNIFTY", date(2025, 5, 1), 55000) == 0.20
    assert tick_size("NIFTY", date(2025, 5, 1), 25000, kind="OPT") == 0.05


def test_freeze_qty():
    assert freeze_qty("NIFTY", date(2024, 1, 2)) == 1800
    assert freeze_qty("NIFTY", date(2026, 10, 10)) == 3510
    assert freeze_qty("NIFTY", date(2026, 11, 3)) == 1800
    assert freeze_qty("NIFTY", date(2022, 6, 1)) is None
    assert freeze_qty("RELIANCE", date(2024, 1, 2)) is None


def test_holidays():
    assert not is_trading_day(date(2024, 1, 22))
    assert is_trading_day(date(2024, 1, 23))
    assert not is_trading_day(date(2024, 1, 20))  # Saturday special session excluded
    assert not is_trading_day(date(2022, 10, 24))  # Muhurat-only weekday
    assert not is_trading_day(date(2024, 1, 21))  # Sunday
