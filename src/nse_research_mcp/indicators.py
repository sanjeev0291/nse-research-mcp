"""Small technical helpers on pandas Series (close prices)."""
from __future__ import annotations

import math

import pandas as pd


def sma(close: pd.Series, n: int):
    if len(close) < n:
        return None
    return float(close.tail(n).mean())


def rsi(close: pd.Series, n: int = 14):
    if len(close) < n + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def max_drawdown(close: pd.Series):
    if close.empty:
        return None
    peak = close.cummax()
    dd = (close / peak - 1.0).min()
    return float(dd * 100)


def pct_return(close: pd.Series, bars: int):
    if len(close) <= bars:
        return None
    a, b = float(close.iloc[-1 - bars]), float(close.iloc[-1])
    if not a or math.isnan(a):
        return None
    return (b / a - 1.0) * 100


def annualized_volatility(close: pd.Series):
    r = close.pct_change().dropna()
    if len(r) < 20:
        return None
    return float(r.std() * math.sqrt(252) * 100)
