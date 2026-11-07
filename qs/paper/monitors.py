"""Decay monitors on paper P&L (spec §11): CUSUM, Chu-Stinchcombe-White, Lempel-Ziv entropy, retire rule."""
import numpy as np

CSW_B = 4.6  # AFML §17.3.2, b_0.05
RETIRE_WINDOW = 10  # sessions


def cusum_alarm(x: np.ndarray, mean0: float, h: float, k: float = 0.0) -> int | None:
    """One-sided CUSUM for a downward shift from mean0 (AFML §2.5.2.1). Index of the first alarm, or None."""
    s = 0.0
    for i, v in enumerate(np.asarray(x, float)):
        s = max(0.0, s + (mean0 - v) - k)
        if s > h:
            return i
    return None


def csw_stat(cum: np.ndarray) -> tuple[float, float]:
    """Chu-Stinchcombe-White CUSUM break test on cumulative P&L, AFML §17.3.2, one-sided for a fall.

    For the latest point t and each reference n < t: S = (y_n - y_t) / (sigma * sqrt(t - n)),
    critical c = sqrt(b + ln(t - n)). Returns (S, c) at the n with the largest S - c; S > c is a break."""
    y = np.asarray(cum, float)
    t = len(y) - 1
    if t < 2:
        return 0.0, np.inf
    sigma = np.sqrt(np.sum(np.diff(y) ** 2) / (t - 1))
    if sigma == 0:
        return 0.0, np.inf
    gap = t - np.arange(t)
    s = (y[:t] - y[t]) / (sigma * np.sqrt(gap))
    c = np.sqrt(CSW_B + np.log(gap))
    j = int(np.argmax(s - c))
    return float(s[j]), float(c[j])


def lz_entropy(bits: np.ndarray) -> float:
    """Lempel-Ziv (LZ76) complexity of a binary string, normalised by n/log2(n): ~0 constant, ~1 random."""
    s = "".join("1" if b else "0" for b in np.asarray(bits).ravel())
    n = len(s)
    if n < 2:
        return 0.0
    c, i = 0, 0
    while i < n:  # new phrase = shortest substring starting at i not seen in s[:i + len - 1]
        L = 1
        while i + L <= n and s[i:i + L] in s[:i + L - 1]:
            L += 1
        c, i = c + 1, i + L
    return float(min(1.0, c * np.log2(n) / n))


def rolling_entropy(signs: np.ndarray, window: int = 50) -> np.ndarray:
    """LZ entropy of the return-sign string (positive -> 1) over a trailing window; NaN until full."""
    bits = np.asarray(signs, float) > 0
    out = np.full(len(bits), np.nan)
    for t in range(window - 1, len(bits)):
        out[t] = lz_entropy(bits[t - window + 1:t + 1])
    return out


def retire_decision(alarms: list[tuple[str, int]], session_idx: int, dd: float, dd95_backtest: float) -> str | None:
    """Retire if the drawdown passes the backtest 95th-percentile DD, or two different monitors alarmed in the
    last RETIRE_WINDOW sessions. alarms = [(monitor, session_idx)]; dd and dd95 are positive fractions."""
    if dd > dd95_backtest:
        return f"drawdown {dd:.1%} > backtest p95 {dd95_backtest:.1%}"
    recent = sorted({m for m, s in alarms if 0 <= session_idx - s < RETIRE_WINDOW})
    if len(recent) >= 2:
        return f"{' + '.join(recent)} alarmed within {RETIRE_WINDOW} sessions"
    return None


def rolling_psr(bet_net: np.ndarray, window: int = 30, *, sr_backtest: float) -> np.ndarray:
    """PSR of the trailing `window` bets against the backtest per-bet Sharpe; NaN until full.
    A low value (< 0.05) means paper Sharpe is confidently below the backtest's."""
    from qs.gate.stats import psr

    x = np.asarray(bet_net, float)
    out = np.full(len(x), np.nan)
    for t in range(window - 1, len(x)):
        out[t] = psr(x[t - window + 1:t + 1], sr_backtest)
    return out
