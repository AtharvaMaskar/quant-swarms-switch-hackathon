"""Replay a recorded Choice TBT session (.bin.zst) as 1-min bars with best bid/ask.

Record format (algo-trading-testing/research/tbt_record.py): repeated [int64 receive_ns][uint32 len][frame].
Full frame (v1/v2): byte0 = 1, byte1 in (1, 2), then a 580-byte payload p with, little-endian:
  p[0:240] 20 bid levels (int32 px, qty, orders), p[240:480] 20 ask levels, p[508] int32 ltp,
  p[520] int64 exchange update ms (epoch), p[572] uint32 price divisor, p[576] int32 token.
The file is untrusted: short, torn, non-full or nonsensical frames are skipped, never trusted.
"""
import struct
import time
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import zstandard

DEFAULT_PATH = Path.home() / "projects/quant/algo-trading-testing/data/tbt/2026-11-02.bin.zst"
NEAR_FUT = "2@61471"  # NIFTY NOV-2026 future on the 2026-11-02 recording
REC = struct.Struct("<qI")
PAYLOAD = 580
MAX_FRAME = 1 << 20  # a larger length means the stream is corrupt
SESSION = (9 * 60 + 15, 15 * 60 + 30)  # IST minute-of-day kept: open <= m < close
IST_MIN = 330  # IST = UTC + 5:30


def _ticks(path: str, token: int) -> Iterator[tuple[int, float, float, float]]:
    """(upd_ms, ltp, best bid, best ask) for one token, streamed."""
    with open(path, "rb") as fh:
        r = zstandard.ZstdDecompressor().stream_reader(fh)
        buf, i = b"", 0
        while True:
            chunk = r.read(1 << 24)
            buf, i = buf[i:] + chunk, 0
            while i + REC.size <= len(buf):
                _, n = REC.unpack_from(buf, i)
                if n > MAX_FRAME:
                    return
                if i + REC.size + n > len(buf):
                    break
                f = buf[i + REC.size:i + REC.size + n]
                i += REC.size + n
                if n < 4 + PAYLOAD or f[0] != 1 or f[1] not in (1, 2):
                    continue
                if struct.unpack_from("<i", f, 4 + 576)[0] != token:
                    continue
                div = struct.unpack_from("<I", f, 4 + 572)[0]
                ltp = struct.unpack_from("<i", f, 4 + 508)[0]
                if div == 0 or ltp <= 0:
                    continue
                bid, ask = struct.unpack_from("<i", f, 4)[0], struct.unpack_from("<i", f, 4 + 240)[0]
                yield struct.unpack_from("<q", f, 4 + 520)[0], ltp / div, bid / div, ask / div
            if not chunk:
                return


def replay_bars(path: str = str(DEFAULT_PATH), instrument: str = NEAR_FUT, speed: float = 60.0) -> Iterator[dict]:
    """1-min bars {ts, open, high, low, close, bid, ask} (IST, tz-naive, minute start; bid/ask at bar close).

    instrument is "segment@token" or a bare token. speed = market minutes per wall-clock minute (0 = no pacing)."""
    token = int(str(instrument).split("@")[-1])
    bar = None
    for ms, ltp, bid, ask in _ticks(path, token):
        m = ms // 60_000 + IST_MIN  # IST minute since epoch
        if not SESSION[0] <= m % 1440 < SESSION[1]:
            continue
        if bar and m < cur:
            continue  # out-of-order frame from an earlier minute: that bar is already out
        if bar and m > cur:
            yield bar
            if speed > 0:
                time.sleep(60.0 / speed)
            bar = None
        if bar is None:
            cur = m
            bar = dict(ts=pd.Timestamp(m * 60, unit="s"), open=ltp, high=ltp, low=ltp, close=ltp, bid=None, ask=None)
        bar.update(high=max(bar["high"], ltp), low=min(bar["low"], ltp), close=ltp)
        if 0 < bid < ask:  # keep the last uncrossed book
            bar.update(bid=bid, ask=ask)
    if bar:
        yield bar
