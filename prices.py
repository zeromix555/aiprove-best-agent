"""Price fetching + a small, deterministic forecasting model.

Prices come from Yahoo Finance's public chart API (no key). Binance is
geo-blocked from this machine, so every symbol — BTC-USD, ETH-USD, SPY,
GLD, NVDA — goes through Yahoo. A browser User-Agent is required.

Model (documented, deterministic, no randomness):
  closes = last 7 daily closes (most recent last)
  rets   = daily returns
  e      = 0.5 * mean(last 5 daily returns)          (short-term momentum)
           + 0.3 * |last-day return|  if last day moved >3% down  (oversold bounce)
           - 0.3 * last-day return    if last day moved >3% up    (overbought fade)
  e is clamped to [-3%, +3%]; multi-day totals are clamped to [-10%, +10%].
  - Brier yes/no: "yes" iff expected total return > 0; confidence =
    0.5 + min(|total| * 4, 0.30), clamped to [0.52, 0.80].
  - Close predictions: last_close * (1 + total), rounded sensibly.
  - Signal: "long" iff expected return > 0 else "short".
"""

from __future__ import annotations

import json
import subprocess


def _curl(url: str) -> str:
    p = subprocess.run(
        ["curl", "-sSL", "--max-time", "30", "-A", "Mozilla/5.0", url],
        capture_output=True, text=True, timeout=40,
    )
    if p.returncode != 0:
        raise RuntimeError(f"curl {url} failed ({p.returncode}): {p.stderr[:200]}")
    return p.stdout


def fetch_closes(symbol: str, days: int = 7) -> list[float]:
    """Daily closes for a Yahoo symbol, oldest first, most recent last."""
    raw = _curl(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range={days}d&interval=1d"
    )
    data = json.loads(raw)
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise RuntimeError(f"no chart result for {symbol}: {raw[:200]}")
    closes = [c for c in result[0]["indicators"]["quote"][0]["close"] if c is not None]
    if len(closes) < 3:
        raise RuntimeError(f"too few closes for {symbol}: {closes}")
    return closes


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def expected_return(closes: list[float], horizon_days: int = 1) -> float:
    """Expected total return over horizon_days, from the model above."""
    rets = [(closes[i + 1] - closes[i]) / closes[i] for i in range(len(closes) - 1)]
    m = sum(rets[-5:]) / len(rets[-5:])
    e = 0.5 * m
    r1 = rets[-1]
    if r1 < -0.03:
        e += 0.3 * abs(r1)
    elif r1 > 0.03:
        e -= 0.3 * r1
    e = _clamp(e, -0.03, 0.03)
    return _clamp(e * horizon_days, -0.10, 0.10)


def brier_call(closes: list[float], horizon_days: int = 1) -> dict:
    total = expected_return(closes, horizon_days)
    conf = _clamp(0.5 + min(abs(total) * 4, 0.30), 0.52, 0.80)
    return {
        "answer": "yes" if total > 0 else "no",
        "confidence": round(conf, 2),
        "expected_return": round(total, 4),
        "last_close": closes[-1],
    }


def close_call(closes: list[float], horizon_days: int = 1, ndigits: int = 2) -> dict:
    total = expected_return(closes, horizon_days)
    pred = closes[-1] * (1 + total)
    return {
        "prediction": round(pred, ndigits),
        "expected_return": round(total, 4),
        "last_close": closes[-1],
    }


def signal_call(closes: list[float]) -> dict:
    total = expected_return(closes, 1)
    return {
        "answer": "long" if total > 0 else "short",
        "expected_return": round(total, 4),
        "last_close": closes[-1],
    }


# Bounty slug -> (yahoo symbol, kind, horizon_days, round digits)
FORECASTS = {
    "btc-tomorrow": ("BTC-USD", "brier", 1, 2),
    "forecast-eth-24h": ("ETH-USD", "brier", 1, 2),
    "forecast-btc-7d": ("BTC-USD", "brier", 7, 2),
    "spy-close-1dollar": ("SPY", "close", 1, 2),
    "forecast-gold-tomorrow": ("GLD", "close", 1, 2),
    "forecast-nvda-3d": ("NVDA", "close", 3, 2),
    "signal-eth-pnl-24h": ("ETH-USD", "signal", 1, 2),
}


def predict(slug: str) -> dict:
    """Full prediction payload + reasoning for one forecast bounty."""
    symbol, kind, horizon, ndigits = FORECASTS[slug]
    closes = fetch_closes(symbol)
    if kind == "brier":
        out = brier_call(closes, horizon)
        payload = {"answer": out["answer"], "confidence": out["confidence"]}
    elif kind == "close":
        out = close_call(closes, horizon, ndigits)
        payload = out["prediction"]
    else:  # signal
        out = signal_call(closes)
        payload = {"answer": out["answer"]}
    reason = (
        f"{symbol} last close {out['last_close']}, 7d closes "
        f"{[round(c, 2) for c in closes]}, expected {horizon}d return "
        f"{out['expected_return']:+.2%}"
    )
    return {"payload": payload, "reason": reason, "detail": out}
