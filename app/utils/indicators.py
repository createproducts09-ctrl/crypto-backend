from __future__ import annotations

from typing import Any

import numpy as np


def _series(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=float)


def sma(values: list[float], window: int) -> list[float | None]:
    arr = _series(values)
    out: list[float | None] = [None] * len(arr)
    if len(arr) < window:
        return out
    cumsum = np.cumsum(arr)
    for i in range(window - 1, len(arr)):
        total = cumsum[i] if i == window - 1 else cumsum[i] - cumsum[i - window]
        out[i] = float(total / window)
    return out


def ema(values: list[float], window: int) -> list[float | None]:
    arr = _series(values)
    out: list[float | None] = [None] * len(arr)
    if len(arr) < window:
        return out
    alpha = 2 / (window + 1)
    seed = float(np.mean(arr[:window]))
    out[window - 1] = seed
    prev = seed
    for i in range(window, len(arr)):
        prev = alpha * float(arr[i]) + (1 - alpha) * prev
        out[i] = prev
    return out


def rsi(values: list[float], window: int = 14) -> list[float | None]:
    arr = _series(values)
    out: list[float | None] = [None] * len(arr)
    if len(arr) <= window:
        return out
    deltas = np.diff(arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:window])
    avg_loss = np.mean(losses[:window])
    for i in range(window, len(deltas)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        rs = avg_gain / avg_loss if avg_loss else np.inf
        out[i + 1] = float(100 - (100 / (1 + rs)))
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, list[float | None]]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    line: list[float | None] = []
    for a, b in zip(ema_fast, ema_slow):
        line.append(None if a is None or b is None else a - b)
    compact = [x for x in line if x is not None]
    signal_compact = ema(compact, signal)
    signal_full: list[float | None] = [None] * len(line)
    idx = 0
    for i, v in enumerate(line):
        if v is None:
            continue
        signal_full[i] = signal_compact[idx]
        idx += 1
    hist: list[float | None] = []
    for a, b in zip(line, signal_full):
        hist.append(None if a is None or b is None else a - b)
    return {"macd": line, "signal": signal_full, "histogram": hist}


def bollinger(values: list[float], window: int = 20, num_std: float = 2.0) -> dict[str, list[float | None]]:
    mid = sma(values, window)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    arr = _series(values)
    for i in range(window - 1, len(arr)):
        slice_ = arr[i - window + 1 : i + 1]
        std = float(np.std(slice_))
        m = mid[i]
        if m is None:
            continue
        upper[i] = m + num_std * std
        lower[i] = m - num_std * std
    return {"middle": mid, "upper": upper, "lower": lower}


def support_resistance(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"support": None, "resistance": None}
    arr = _series(values[-60:])
    return {"support": float(np.min(arr)), "resistance": float(np.max(arr))}


def summarize_ta(closes: list[float]) -> dict[str, Any]:
    if len(closes) < 30:
        return {"trend": "insufficient_data", "rsi": None, "macd_signal": "n/a"}
    rsi_vals = [v for v in rsi(closes) if v is not None]
    macd_data = macd(closes)
    hist = [v for v in macd_data["histogram"] if v is not None]
    ema20 = [v for v in ema(closes, 20) if v is not None]
    ema50 = [v for v in ema(closes, 50) if v is not None]
    last_rsi = rsi_vals[-1] if rsi_vals else None
    trend = "neutral"
    if ema20 and ema50:
        trend = "bullish" if ema20[-1] > ema50[-1] else "bearish"
    macd_signal = "bullish" if hist and hist[-1] > 0 else "bearish"
    levels = support_resistance(closes)
    return {
        "trend": trend,
        "rsi": last_rsi,
        "rsi_interpretation": (
            "overbought" if last_rsi and last_rsi > 70 else "oversold" if last_rsi and last_rsi < 30 else "neutral"
        ),
        "macd_signal": macd_signal,
        "ema_crossover": "bullish" if trend == "bullish" else "bearish",
        "support": levels["support"],
        "resistance": levels["resistance"],
    }
