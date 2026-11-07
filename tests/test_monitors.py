import struct

import numpy as np
import zstandard

from qs.paper.monitors import csw_stat, cusum_alarm, lz_entropy, retire_decision, rolling_entropy
from qs.paper.replay import replay_bars


def test_cusum_detects_shift():
    rng = np.random.default_rng(0)
    x = np.r_[rng.normal(0.5, 0.3, 200), rng.normal(-0.5, 0.3, 200)]
    i = cusum_alarm(x, mean0=0.5, h=5.0)
    assert i is not None and 200 <= i < 220
    assert cusum_alarm(x[:200], mean0=0.5, h=5.0) is None
    assert cusum_alarm(np.array([]), 0.5, 5.0) is None


def test_lz_entropy_bounds():
    assert lz_entropy(np.zeros(1000, dtype=int)) < 0.1
    assert lz_entropy(np.random.default_rng(1).integers(0, 2, 1000)) > 0.9
    assert lz_entropy(np.array([], dtype=int)) == 0.0
    r = rolling_entropy(np.random.default_rng(2).normal(size=200), window=50)
    assert r.shape == (200,) and np.isnan(r[:49]).all() and np.isfinite(r[49:]).all()
    assert ((r[49:] >= 0) & (r[49:] <= 1)).all()


def test_csw_no_break_on_noise():
    rng = np.random.default_rng(3)
    breaks = [s > cv for s, cv in (csw_stat(np.cumsum(rng.normal(0, 1, 500))) for _ in range(200))]
    assert np.mean(breaks) <= 0.10  # b = 4.6 is a 5% test: noise breaks about 1 time in 20
    base = np.cumsum(rng.normal(0.2, 1, 300))
    cum = np.r_[base, base[-1] - np.cumsum(np.full(100, 3.0))]
    s, cv = csw_stat(cum)
    assert s > cv  # a crash in cumulative P&L is a break
    assert csw_stat(np.zeros(3))[0] == 0.0  # flat P&L: no variance, no break


def test_retire_two_monitors():
    assert retire_decision([("cusum", 3)], 5, 0.05, 0.10) is None
    assert retire_decision([("cusum", 3), ("cusum", 5)], 5, 0.05, 0.10) is None  # same monitor twice
    assert "cusum" in retire_decision([("cusum", 3), ("csw", 9)], 9, 0.05, 0.10)
    assert retire_decision([("cusum", 3), ("csw", 20)], 20, 0.05, 0.10) is None  # 17 sessions apart
    assert "drawdown" in retire_decision([], 1, 0.12, 0.10)


def _frame(token, ltp, upd_ms, bid, ask, div=100):
    p = bytearray(580)
    struct.pack_into("<iii", p, 0, int(bid * div), 75, 1)
    struct.pack_into("<iii", p, 240, int(ask * div), 75, 1)
    struct.pack_into("<i", p, 508, int(ltp * div))
    struct.pack_into("<q", p, 520, upd_ms)
    struct.pack_into("<I", p, 572, div)
    struct.pack_into("<i", p, 576, token)
    return bytes([1, 2, 0, 0]) + bytes(p)


def test_replay_decodes_frames(tmp_path):
    t0 = 1793591100000  # 2026-11-02 09:15:00 IST in epoch ms
    frames = [_frame(61471, 100, t0, 99.9, 100.1), _frame(26000, 5, t0, 0, 0),
              _frame(61471, 103, t0 + 20_000, 102.9, 103.1), _frame(61471, 98, t0 + 40_000, 97.9, 98.1),
              _frame(61471, 101, t0 + 61_000, 100.9, 101.2), b'{"status":"ok"}', b"\x01\x02junk"]
    raw = b"".join(struct.pack("<qI", 0, len(f)) + f for f in frames) + struct.pack("<qI", 0, 999)  # torn tail
    path = tmp_path / "d.bin.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress(raw))
    bars = list(replay_bars(str(path), "2@61471", speed=0))
    assert [b["ts"].strftime("%H:%M") for b in bars] == ["09:15", "09:16"]
    b = bars[0]
    assert (b["open"], b["high"], b["low"], b["close"]) == (100, 103, 98, 98)
    assert (b["bid"], b["ask"]) == (97.9, 98.1)
