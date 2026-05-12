"""
╔══════════════════════════════════════════════════════════════╗
║     AlgoTrade Backtesting Engine  v4.0  — NSE Stocks        ║
║     INR · 40 Indian Stocks · ORB+EMA · REST API             ║
╚══════════════════════════════════════════════════════════════╝

Usage:
    python backtest_engine.py              # single run (best stock)
    python backtest_engine.py --schedule   # daily at 16:00 IST
    python backtest_engine.py --api        # REST API on port 8080
    python backtest_engine.py --symbol SUZLON.NS
    python backtest_engine.py --all        # all 40 NSE stocks
    python backtest_engine.py --notify     # send Telegram summary
    python backtest_engine.py --live       # use real Yahoo Finance data

Dependencies:
    pip install numpy pandas requests schedule python-dotenv yfinance fastapi uvicorn
"""

import os
import sys
import json
import math
import time
import logging
import argparse
import datetime
import schedule
import itertools
import statistics
import threading
from pathlib import Path
from typing  import Optional, List
from dotenv  import load_dotenv

import numpy  as np
import pandas as pd
import requests

load_dotenv()

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt  = "%H:%M:%S",
    handlers = [logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("backtest")

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHATID = os.getenv("TELEGRAM_CHAT_ID",   "")
SUPABASE_URL    = os.getenv("SUPABASE_URL",        "")
SUPABASE_KEY    = os.getenv("SUPABASE_ANON_KEY",   "")
RENDER_URL      = os.getenv("RENDER_URL",          "")

RESULTS_DIR     = Path("backtest_results")
RESULTS_DIR.mkdir(exist_ok=True)

COMMISSION      = 0.0005   # 0.05% round-trip NSE intraday
SCORE_THRESHOLD = 0.40

# ═══════════════════════════════════════════════════════════════
#  NSE STOCK UNIVERSE — 40 STOCKS
# ═══════════════════════════════════════════════════════════════

NSE_ASSETS = {
    # ── Large Cap ──────────────────────────────────────────────
    "IDBI.NS":       {"label": "IDBI",       "price":  85,  "vol": 0.018, "sector": "Large Cap"},
    "BAJAJHFL.NS":   {"label": "BAJAJHFL",   "price": 140,  "vol": 0.016, "sector": "Large Cap"},
    "NHPC.NS":       {"label": "NHPC",       "price":  91,  "vol": 0.015, "sector": "Large Cap"},
    "IOB.NS":        {"label": "IOB",        "price":  52,  "vol": 0.020, "sector": "Large Cap"},
    "SUZLON.NS":     {"label": "SUZLON",     "price":  58,  "vol": 0.022, "sector": "Large Cap"},
    "GMRINFRA.NS":   {"label": "GMRINFRA",   "price":  95,  "vol": 0.019, "sector": "Large Cap"},
    "NMDC.NS":       {"label": "NMDC",       "price": 220,  "vol": 0.014, "sector": "Large Cap"},
    "UCOBANK.NS":    {"label": "UCOBANK",    "price":  48,  "vol": 0.021, "sector": "Large Cap"},
    "MAHABANK.NS":   {"label": "MAHABANK",   "price":  55,  "vol": 0.019, "sector": "Large Cap"},
    "CENTRALBK.NS":  {"label": "CENTRALBK",  "price":  58,  "vol": 0.018, "sector": "Large Cap"},
    # ── Infra & Energy ────────────────────────────────────────
    "SJVN.NS":       {"label": "SJVN",       "price": 115,  "vol": 0.016, "sector": "Infra"},
    "NBCC.NS":       {"label": "NBCC",       "price":  98,  "vol": 0.017, "sector": "Infra"},
    "IRB.NS":        {"label": "IRB",        "price":  72,  "vol": 0.018, "sector": "Infra"},
    "INOXWIND.NS":   {"label": "INOXWIND",   "price": 185,  "vol": 0.023, "sector": "Infra"},
    "RPOWER.NS":     {"label": "RPOWER",     "price":  42,  "vol": 0.025, "sector": "Infra"},
    "RINFRA.NS":     {"label": "RINFRA",     "price": 310,  "vol": 0.020, "sector": "Infra"},
    "SONALMERCANTILE.NS": {"label": "SONAL", "price":  95,  "vol": 0.024, "sector": "Infra"},
    "GMRAIRPORTS.NS":{"label": "GMRAIR",     "price":  88,  "vol": 0.019, "sector": "Infra"},
    "PATELENG.NS":   {"label": "PATELENG",   "price":  62,  "vol": 0.019, "sector": "Infra"},
    "OMINFRA.NS":    {"label": "OMINFRA",    "price":  78,  "vol": 0.021, "sector": "Infra"},
    # ── Finance & Specialty ───────────────────────────────────
    "IDFCFIRSTB.NS": {"label": "IDFCFIRSTB", "price":  68,  "vol": 0.017, "sector": "Finance"},
    "YESBANK.NS":    {"label": "YESBANK",    "price":  22,  "vol": 0.024, "sector": "Finance"},
    "IFCI.NS":       {"label": "IFCI",       "price":  28,  "vol": 0.022, "sector": "Finance"},
    "MSUMI.NS":      {"label": "MSUMI",      "price":  55,  "vol": 0.016, "sector": "Finance"},
    "SBFC.NS":       {"label": "SBFC",       "price":  92,  "vol": 0.018, "sector": "Finance"},
    "DOLATALGO.NS":  {"label": "DOLATALGO",  "price": 320,  "vol": 0.015, "sector": "Finance"},
    "MASTERTRUST.NS":{"label": "MASTERTRUST","price": 180,  "vol": 0.017, "sector": "Finance"},
    "UJJIVANSFB.NS": {"label": "UJJIVANSFB", "price":  42,  "vol": 0.020, "sector": "Finance"},
    "NIVABUPA.NS":   {"label": "NIVABUPA",   "price":  78,  "vol": 0.017, "sector": "Finance"},
    "SHRIRAMPRP.NS": {"label": "SHRIRAMPRP", "price": 145,  "vol": 0.019, "sector": "Finance"},
    # ── Industrials, Textiles & Metals ────────────────────────
    "TRIDENT.NS":    {"label": "TRIDENT",    "price":  38,  "vol": 0.018, "sector": "Industrial"},
    "TTML.NS":       {"label": "TTML",       "price":  82,  "vol": 0.021, "sector": "Industrial"},
    "NMDCSTEEL.NS":  {"label": "NMDCSTEEL",  "price":  58,  "vol": 0.020, "sector": "Industrial"},
    "MOREPEN.NS":    {"label": "MOREPEN",    "price":  68,  "vol": 0.019, "sector": "Industrial"},
    "ANDHRASUGAR.NS":{"label": "ANDHRASUGAR","price": 420,  "vol": 0.016, "sector": "Industrial"},
    "ASIANGRN.NS":   {"label": "ASIANGRN",   "price": 142,  "vol": 0.018, "sector": "Industrial"},
    "FILATEX.NS":    {"label": "FILATEX",    "price":  88,  "vol": 0.017, "sector": "Industrial"},
    "BALMERLAWR.NS": {"label": "BALMERLAWR", "price": 185,  "vol": 0.014, "sector": "Industrial"},
    "SPIC.NS":       {"label": "SPIC",       "price":  52,  "vol": 0.020, "sector": "Industrial"},
    "TNPETRO.NS":    {"label": "TNPETRO",    "price": 195,  "vol": 0.016, "sector": "Industrial"},
}

# ═══════════════════════════════════════════════════════════════
#  NSE SESSION HELPERS
# ═══════════════════════════════════════════════════════════════

def now_ist() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)

def is_nse_session() -> bool:
    """NSE trades Mon–Fri 09:15–15:30 IST."""
    t = now_ist()
    if t.weekday() >= 5:
        return False
    open_  = t.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_ = t.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_ <= t <= close_

def fmt_inr(n: float) -> str:
    if n >= 1_00_00_000: return f"Rs.{n/1_00_00_000:.2f}Cr"
    if n >= 1_00_000:    return f"Rs.{n/1_00_000:.2f}L"
    if n >= 1_000:       return f"Rs.{n/1_000:.1f}k"
    return f"Rs.{n:.2f}"

# ═══════════════════════════════════════════════════════════════
#  1. PRICE DATA
# ═══════════════════════════════════════════════════════════════

def generate_ohlcv(bars: int = 400,
                   start_price: float = 100.0,
                   drift: float = 0.00005,
                   vol: float = 0.018) -> pd.DataFrame:
    """Synthetic NSE-like OHLCV via Geometric Brownian Motion."""
    rng    = np.random.default_rng()
    prices = [start_price]
    for _ in range(bars - 1):
        prices.append(prices[-1] * math.exp(drift + vol * rng.standard_normal()))

    rows = []
    for i, price in enumerate(prices):
        prev  = prices[i - 1] if i > 0 else price
        noise = abs(rng.standard_normal()) * 0.002
        rows.append({
            "open":   round(prev, 2),
            "high":   round(max(prev, price) * (1 + noise), 2),
            "low":    round(min(prev, price) * (1 - noise), 2),
            "close":  round(price, 2),
            "volume": int(50_000 + rng.random() * 5_00_000),
        })

    df = pd.DataFrame(rows)
    df.index = pd.date_range(
        end=pd.Timestamp.now(tz="Asia/Kolkata"),
        periods=bars, freq="15min"
    )
    return df


def fetch_live_data(symbol: str = "SUZLON.NS",
                    period: str = "60d",
                    interval: str = "15m") -> pd.DataFrame:
    """Fetch real NSE OHLCV from Yahoo Finance."""
    try:
        import yfinance as yf
        log.info(f"Fetching {symbol} from Yahoo Finance ({period}, {interval})")
        ticker = yf.Ticker(symbol)
        df     = ticker.history(period=period, interval=interval)
        df.columns = df.columns.str.lower()
        df     = df[["open","high","low","close","volume"]].dropna()
        log.info(f"Fetched {len(df)} bars for {symbol}")
        return df
    except ImportError:
        log.warning("yfinance not installed — using synthetic data")
    except Exception as e:
        log.warning(f"Live data failed ({e}) — using synthetic data")

    asset = NSE_ASSETS.get(symbol, {"price": 100.0, "vol": 0.018})
    return generate_ohlcv(start_price=asset["price"], vol=asset["vol"])


# ═══════════════════════════════════════════════════════════════
#  2. TECHNICAL INDICATORS
# ═══════════════════════════════════════════════════════════════

def ema(series: np.ndarray, period: int) -> np.ndarray:
    k   = 2 / (period + 1)
    out = np.full(len(series), np.nan)
    if len(series) < period:
        return out
    out[period - 1] = np.mean(series[:period])
    for i in range(period, len(series)):
        out[i] = series[i] * k + out[i-1] * (1 - k)
    return out


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(closes), np.nan)
    if len(closes) <= period:
        return out
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    ag     = np.mean(gains[:period])
    al     = np.mean(losses[:period])
    for i in range(period, len(closes)):
        ag = (ag * (period - 1) + gains[i - 1]) / period
        al = (al * (period - 1) + losses[i - 1]) / period
        rs  = ag / al if al != 0 else 1e9
        out[i] = 100 - (100 / (1 + rs))
    return out


def atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    tr = np.maximum(
        h[1:] - l[1:],
        np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1]))
    )
    tr  = np.concatenate([[h[0] - l[0]], tr])
    out = np.full(len(tr), np.nan)
    if len(tr) < period:
        return out
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


# ═══════════════════════════════════════════════════════════════
#  3. STRATEGY SIGNALS
# ═══════════════════════════════════════════════════════════════

def signal_orb_ema(df: pd.DataFrame,
                   fast: int = 9,
                   slow: int = 21,
                   orb_period: int = 5) -> np.ndarray:
    """
    ORB + EMA PRO — your core strategy.
    BUY  when fast EMA crosses above slow EMA AND close > ORB high.
    SELL when fast EMA crosses below slow EMA AND close < ORB low.
    """
    closes  = df["close"].values
    highs   = df["high"].values
    lows    = df["low"].values
    fast_e  = ema(closes, fast)
    slow_e  = ema(closes, slow)
    signals = np.zeros(len(df), dtype=int)

    start = max(slow, orb_period) + 1
    for i in range(start, len(df)):
        if np.isnan(fast_e[i]) or np.isnan(slow_e[i]):
            continue

        orb_high = np.max(highs[i - orb_period:i])
        orb_low  = np.min(lows[i  - orb_period:i])

        bull_cross = fast_e[i] >  slow_e[i] and fast_e[i-1] <= slow_e[i-1]
        bear_cross = fast_e[i] <  slow_e[i] and fast_e[i-1] >= slow_e[i-1]

        if bull_cross and closes[i] > orb_high:
            signals[i] =  1
        elif bear_cross and closes[i] < orb_low:
            signals[i] = -1

    return signals


def signal_momentum(df: pd.DataFrame,
                    period: int, fast: int,
                    threshold: float) -> np.ndarray:
    closes   = df["close"].values
    rsi_vals = rsi(closes, period)
    fast_ema = ema(closes, fast)
    signals  = np.zeros(len(df), dtype=int)
    for i in range(max(period, fast) + 1, len(df)):
        r, rp, e = rsi_vals[i], rsi_vals[i-1], fast_ema[i]
        if np.isnan(r) or np.isnan(e):
            continue
        if r > threshold and rp <= threshold and closes[i] > e:
            signals[i] = 1
        elif r < (100 - threshold) and rp >= (100 - threshold) and closes[i] < e:
            signals[i] = -1
    return signals


def signal_trend(df: pd.DataFrame,
                 fast: int, slow: int) -> np.ndarray:
    closes   = df["close"].values
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    signals  = np.zeros(len(df), dtype=int)
    for i in range(slow + 1, len(df)):
        if np.isnan(fast_ema[i]) or np.isnan(slow_ema[i]):
            continue
        if fast_ema[i] > slow_ema[i] and fast_ema[i-1] <= slow_ema[i-1]:
            signals[i] = 1
        elif fast_ema[i] < slow_ema[i] and fast_ema[i-1] >= slow_ema[i-1]:
            signals[i] = -1
    return signals


def signal_breakout(df: pd.DataFrame,
                    period: int, vol_mult: float) -> np.ndarray:
    highs   = df["high"].values
    lows    = df["low"].values
    closes  = df["close"].values
    volumes = df["volume"].values
    signals = np.zeros(len(df), dtype=int)
    for i in range(period, len(df)):
        avg_vol = np.mean(volumes[i - period:i])
        if closes[i] > np.max(highs[i - period:i]) and volumes[i] > avg_vol * vol_mult:
            signals[i] = 1
        elif closes[i] < np.min(lows[i - period:i]) and volumes[i] > avg_vol * vol_mult:
            signals[i] = -1
    return signals


def signal_sweep(df: pd.DataFrame, period: int) -> np.ndarray:
    closes  = df["close"].values
    highs   = df["high"].values
    lows    = df["low"].values
    atr_v   = atr(df, period)
    signals = np.zeros(len(df), dtype=int)
    for i in range(period + 2, len(df)):
        if np.isnan(atr_v[i]):
            continue
        prev_low  = min(lows[i-1],  lows[i-2])
        prev_high = max(highs[i-1], highs[i-2])
        if lows[i]  < prev_low  - atr_v[i] * 0.1 and closes[i] > prev_low:
            signals[i] = 1
        if highs[i] > prev_high + atr_v[i] * 0.1 and closes[i] < prev_high:
            signals[i] = -1
    return signals


STRATEGY_FN = {
    "orb_ema":  lambda df, p: signal_orb_ema(df,  p["fast"], p["slow"], p["orb_period"]),
    "momentum": lambda df, p: signal_momentum(df, p["period"], p["fast"], p["threshold"]),
    "trend":    lambda df, p: signal_trend(df,    p["fast"], p["slow"]),
    "breakout": lambda df, p: signal_breakout(df, p["period"], p["vol_mult"]),
    "sweep":    lambda df, p: signal_sweep(df,    p["period"]),
}


# ═══════════════════════════════════════════════════════════════
#  4. CORE BACKTESTER
# ═══════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame,
                 signals: np.ndarray,
                 sl_pct: float,
                 tp_pct: float,
                 initial_equity: float = 10_00_000) -> Optional[dict]:
    """
    Simulate trades on price data.
    Returns performance dict or None if < 5 trades.
    """
    closes = df["close"].values
    equity = initial_equity
    equity_curve = [equity]
    trades = []

    in_trade    = False
    entry_price = 0.0
    direction   = 0
    entry_idx   = 0
    sl_frac     = -sl_pct / 100
    tp_frac     =  tp_pct / 100

    for i in range(1, len(df)):
        if in_trade:
            pct = (
                (closes[i] - entry_price) / entry_price
                if direction == 1
                else (entry_price - closes[i]) / entry_price
            )
            if pct <= sl_frac or pct >= tp_frac:
                ret     = max(sl_frac, min(tp_frac, pct)) * (1 - COMMISSION)
                equity *= (1 + ret)
                trades.append({
                    "entry":     round(entry_price, 2),
                    "exit":      round(closes[i], 2),
                    "direction": direction,
                    "ret":       ret,
                    "win":       ret > 0,
                    "bars":      i - entry_idx,
                    "exit_type": "TP" if pct >= tp_frac else "SL",
                })
                in_trade = False

        elif signals[i] != 0:
            in_trade    = True
            entry_price = closes[i]
            direction   = int(signals[i])
            entry_idx   = i

        equity_curve.append(equity)

    if len(trades) < 5:
        return None

    wins         = [t for t in trades if t["win"]]
    losses       = [t for t in trades if not t["win"]]
    win_rate     = len(wins) / len(trades)
    gross_profit = sum(t["ret"] for t in wins)
    gross_loss   = abs(sum(t["ret"] for t in losses))
    profit_factor= (gross_profit / gross_loss) if gross_loss > 0 else gross_profit * 10

    # Max Drawdown
    peak, max_dd = equity_curve[0], 0.0
    for e in equity_curve:
        if e > peak: peak = e
        dd = (peak - e) / peak
        if dd > max_dd: max_dd = dd

    # Sharpe Ratio
    rets    = [equity_curve[i] / equity_curve[i-1] - 1 for i in range(1, len(equity_curve))]
    avg_ret = statistics.mean(rets) if rets else 0
    std_ret = statistics.stdev(rets) if len(rets) > 1 else 1e-9
    sharpe  = (avg_ret / std_ret) * math.sqrt(1400) if std_ret > 0 else 0

    total_return = (equity - initial_equity) / initial_equity * 100

    # Composite Score
    score = (
        min(win_rate, 1.0)            * 0.30 +
        min(profit_factor / 3, 1.0)   * 0.30 +
        (1 - min(max_dd, 1.0))        * 0.20 +
        max(min(sharpe / 3, 1.0), 0)  * 0.20
    )

    return {
        "win_rate":      round(win_rate,      4),
        "profit_factor": round(profit_factor, 4),
        "max_drawdown":  round(max_dd,        4),
        "sharpe":        round(sharpe,        4),
        "total_return":  round(total_return,  2),
        "num_trades":    len(trades),
        "num_wins":      len(wins),
        "num_losses":    len(losses),
        "final_equity":  round(equity,        2),
        "score":         round(score,         5),
        "equity_curve":  equity_curve[::10],
        "last_trades":   trades[-5:],
    }


# ═══════════════════════════════════════════════════════════════
#  5. PARAMETER GRID
# ═══════════════════════════════════════════════════════════════

def build_param_grid() -> list:
    SL   = [0.5, 0.8, 1.0, 1.5, 2.0, 2.5]
    TP   = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
    grid = []

    # ── ORB + EMA (Primary strategy) ──────────────────────────
    for fast, slow, orb, sl, tp in itertools.product(
        [5, 9, 14, 21],
        [21, 50, 100],
        [3, 5, 8, 10],
        SL, TP
    ):
        if fast >= slow:
            continue
        grid.append({
            "strategy":   "orb_ema",
            "sl":          sl,
            "tp":          tp,
            "fast":        fast,
            "slow":        slow,
            "orb_period":  orb,
        })

    # ── Momentum ──────────────────────────────────────────────
    for period, thresh, sl, tp in itertools.product(
        [7, 10, 14, 21, 28],
        [45, 50, 55, 60, 65],
        SL, TP
    ):
        grid.append({
            "strategy":  "momentum",
            "sl":         sl,
            "tp":         tp,
            "period":     period,
            "fast":       20,
            "threshold":  thresh,
        })

    # ── Trend EMA Cross ───────────────────────────────────────
    for fast, slow, sl, tp in itertools.product(
        [5, 8, 13, 21],
        [20, 50, 100, 200],
        SL[:4], TP[:4]
    ):
        if fast >= slow:
            continue
        grid.append({
            "strategy": "trend",
            "sl":        sl,
            "tp":        tp,
            "fast":      fast,
            "slow":      slow,
        })

    # ── Breakout ──────────────────────────────────────────────
    for period, vol_mult, sl, tp in itertools.product(
        [10, 15, 20, 30, 50],
        [1.2, 1.5, 2.0, 2.5],
        SL[:4], TP[:4]
    ):
        grid.append({
            "strategy":  "breakout",
            "sl":         sl,
            "tp":         tp,
            "period":     period,
            "vol_mult":   vol_mult,
        })

    # ── Liquidity Sweep ───────────────────────────────────────
    for period, sl, tp in itertools.product([5, 8, 10, 14, 21], SL, TP):
        grid.append({
            "strategy": "sweep",
            "sl":        sl,
            "tp":        tp,
            "period":    period,
        })

    return grid


# ═══════════════════════════════════════════════════════════════
#  6. SWEEP ENGINE
# ═══════════════════════════════════════════════════════════════

def run_sweep(df: pd.DataFrame,
              strategy_filter: Optional[str] = None,
              symbol: str = "UNKNOWN",
              verbose: bool = True) -> list:
    grid = build_param_grid()
    if strategy_filter:
        grid = [p for p in grid if p["strategy"] == strategy_filter]

    log.info(f"Sweep: {symbol} | {len(grid)} combinations | {len(df)} bars")
    results = []
    start   = time.time()

    for idx, params in enumerate(grid, 1):
        try:
            signals = STRATEGY_FN[params["strategy"]](df, params)
            res     = run_backtest(df, signals, params["sl"], params["tp"])
            if res:
                results.append({**params, **res, "symbol": symbol})
        except Exception as e:
            pass

        if verbose and idx % 500 == 0:
            pct = idx / len(grid) * 100
            log.info(f"  [{pct:5.1f}%] {idx}/{len(grid)} valid:{len(results)}")

    results.sort(key=lambda r: r["score"], reverse=True)
    elapsed = time.time() - start
    log.info(f"Done {symbol} in {elapsed:.1f}s | valid:{len(results)} / {len(grid)}")
    return results


def run_sweep_all_symbols(live_data: bool = False,
                          strategy_filter: Optional[str] = None) -> list:
    all_results = []
    total = len(NSE_ASSETS)
    for i, (symbol, meta) in enumerate(NSE_ASSETS.items(), 1):
        log.info(f"{'─'*55}")
        log.info(f"[{i}/{total}] {symbol}  ({meta['label']})  [{meta['sector']}]")
        try:
            df = (fetch_live_data(symbol=symbol)
                  if live_data
                  else generate_ohlcv(
                      start_price=meta["price"],
                      vol=meta["vol"]
                  ))
            results = run_sweep(df, strategy_filter=strategy_filter,
                                symbol=meta["label"], verbose=False)
            all_results.extend(results)
        except Exception as e:
            log.warning(f"Failed {symbol}: {e}")

    all_results.sort(key=lambda r: r["score"], reverse=True)
    return all_results


# ═══════════════════════════════════════════════════════════════
#  7. ANALYSIS & REPORTING
# ═══════════════════════════════════════════════════════════════

def strategy_summary(results: list) -> dict:
    summary = {}
    for strat in ["orb_ema", "momentum", "trend", "breakout", "sweep"]:
        sr = [r for r in results if r["strategy"] == strat]
        if not sr:
            summary[strat] = {
                "count": 0, "avg_score": 0,
                "best_wr": 0, "active": False, "best_params": None
            }
            continue
        avg_score = sum(r["score"] for r in sr) / len(sr)
        best      = max(sr, key=lambda r: r["score"])
        summary[strat] = {
            "count":       len(sr),
            "avg_score":   round(avg_score, 4),
            "best_score":  best["score"],
            "best_wr":     best["win_rate"],
            "best_pf":     best["profit_factor"],
            "best_sharpe": best["sharpe"],
            "best_return": best["total_return"],
            "active":      avg_score >= SCORE_THRESHOLD,
            "best_params": {k: v for k, v in best.items()
                            if k not in ("equity_curve", "last_trades")},
        }
    return summary


def print_top_results(results: list, n: int = 10):
    print("\n" + "═"*90)
    print(f"  TOP {n} PARAMETER SETS  ─  NSE AlgoTrade  (v4.0)")
    print("═"*90)
    hdr = (f"  {'#':>3}  {'STRATEGY':<12}  {'SYMBOL':<12}  {'SL':>5}  {'TP':>5}"
           f"  {'WIN%':>7}  {'PF':>6}  {'DD%':>6}  {'SHARPE':>7}"
           f"  {'RETURN%':>8}  {'TRADES':>7}  {'SCORE':>7}")
    print(hdr)
    print("─"*90)
    for i, r in enumerate(results[:n], 1):
        flag = "★ " if i == 1 else f"{i:>2} "
        print(
            f"  {flag}  {r['strategy']:<12}  "
            f"{r.get('symbol',''):<12}  "
            f"{r['sl']:>5.1f}  {r['tp']:>5.1f}  "
            f"{r['win_rate']*100:>6.1f}%  "
            f"{r['profit_factor']:>6.3f}  "
            f"{r['max_drawdown']*100:>5.1f}%  "
            f"{r['sharpe']:>7.2f}  "
            f"{r['total_return']:>+7.1f}%  "
            f"{r['num_trades']:>7}  "
            f"{r['score']:>7.4f}"
        )
    print("═"*90)


def print_strategy_summary(summary: dict):
    print("\n  STRATEGY RANKING")
    print("─"*65)
    sorted_strats = sorted(summary.items(), key=lambda x: x[1]["avg_score"], reverse=True)
    for strat, s in sorted_strats:
        status = "✅ ACTIVE  " if s["active"] else "❌ DISABLED"
        if s["count"] == 0:
            print(f"  {strat:<14}  {status}  ─ no valid results")
        else:
            print(
                f"  {strat:<14}  {status}  "
                f"avg:{s['avg_score']:.3f}  "
                f"WR:{s['best_wr']*100:.1f}%  "
                f"PF:{s['best_pf']:.2f}  "
                f"Ret:{s['best_return']:+.1f}%"
            )
    print()


def print_sector_summary(results: list):
    sectors = {}
    for r in results:
        sym    = r.get("symbol", "")
        sector = next(
            (m["sector"] for m in NSE_ASSETS.values() if m["label"] == sym),
            "Unknown"
        )
        if sector not in sectors:
            sectors[sector] = []
        sectors[sector].append(r["score"])

    print("\n  SECTOR PERFORMANCE")
    print("─"*40)
    for sec, scores in sorted(sectors.items(), key=lambda x: -sum(x[1])/len(x[1])):
        avg = sum(scores) / len(scores)
        bar = "█" * int(avg * 20)
        print(f"  {sec:<14}  {avg:.3f}  {bar}")
    print()


# ═══════════════════════════════════════════════════════════════
#  8. TELEGRAM NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATID:
        log.warning("Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r   = requests.post(url, json={
            "chat_id":    TELEGRAM_CHATID,
            "text":       message,
            "parse_mode": "HTML"
        }, timeout=10)
        log.info(f"Telegram sent: {r.status_code}")
        return r.ok
    except Exception as e:
        log.warning(f"Telegram failed: {e}")
        return False


def notify_top_results(results: list, summary: dict):
    if not results:
        return

    top   = results[0]
    ts    = now_ist().strftime("%d %b %Y  %H:%M IST")
    passed= sum(1 for r in results if r["score"] > SCORE_THRESHOLD)

    # Best per strategy
    strat_lines = ""
    for strat, s in summary.items():
        if s["count"] > 0 and s["active"]:
            strat_lines += (
                f"\n  <b>{strat.upper()}</b>  "
                f"WR:{s['best_wr']*100:.1f}%  "
                f"PF:{s['best_pf']:.2f}  "
                f"Score:{s['best_score']:.3f}"
            )

    msg = (
        f"<b>🔔 AlgoTrade Backtest Complete</b>\n"
        f"<i>{ts}</i>\n\n"
        f"<b>📊 Summary</b>\n"
        f"Total tested: {len(results)}\n"
        f"Passed threshold: {passed}\n\n"
        f"<b>🏆 Best Setup</b>\n"
        f"Strategy : {top['strategy'].upper()}\n"
        f"Symbol   : {top.get('symbol', 'N/A')}\n"
        f"SL / TP  : {top['sl']}% / {top['tp']}%\n"
        f"Win Rate : {top['win_rate']*100:.1f}%\n"
        f"Prof. Fac: {top['profit_factor']:.2f}\n"
        f"Return   : {top['total_return']:+.1f}%\n"
        f"Max DD   : {top['max_drawdown']*100:.1f}%\n"
        f"Sharpe   : {top['sharpe']:.2f}\n"
        f"Score    : {top['score']:.4f}\n\n"
        f"<b>✅ Active Strategies</b>{strat_lines}"
    )
    send_telegram(msg)


def notify_trade_signal(stock: str, signal: str, price: float,
                        sl: float, tp: float, reason: str):
    """Send a single trade signal alert to Telegram."""
    emoji  = "🟢 BUY" if signal.upper() == "BUY" else "🔴 SELL"
    ts     = now_ist().strftime("%H:%M IST")
    msg    = (
        f"<b>🔔 AlgoTrade Signal</b>  <i>{ts}</i>\n\n"
        f"<b>Stock   :</b> {stock}\n"
        f"<b>Signal  :</b> {emoji}\n"
        f"<b>Price   :</b> ₹{price:.2f}\n"
        f"<b>SL      :</b> ₹{sl:.2f}\n"
        f"<b>Target  :</b> ₹{tp:.2f}\n"
        f"<b>Reason  :</b> {reason}"
    )
    send_telegram(msg)


# ═══════════════════════════════════════════════════════════════
#  9. SAVE RESULTS
# ═══════════════════════════════════════════════════════════════

def save_results(results: list, summary: dict) -> Path:
    ts   = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"backtest_nse_{ts}.json"

    slim = [
        {k: v for k, v in r.items() if k not in ("equity_curve", "last_trades")}
        for r in results
    ]

    payload = {
        "timestamp":        datetime.datetime.utcnow().isoformat(),
        "market":           "NSE",
        "currency":         "INR",
        "total_stocks":     len(NSE_ASSETS),
        "total_tested":     len(results),
        "passed":           sum(1 for r in results if r["score"] > SCORE_THRESHOLD),
        "best_score":       results[0]["score"] if results else 0,
        "strategy_summary": summary,
        "top_100":          slim[:100],
        "deployed_params":  slim[0] if slim else None,
    }

    with open(path, "w") as f:
        json.dump(payload, f, indent=2)

    csv_path = RESULTS_DIR / f"backtest_nse_{ts}.csv"
    pd.DataFrame(slim).to_csv(csv_path, index=False)

    log.info(f"Saved JSON → {path}")
    log.info(f"Saved CSV  → {csv_path}")
    return path


# ═══════════════════════════════════════════════════════════════
#  10. SUPABASE UPLOAD
# ═══════════════════════════════════════════════════════════════

def save_to_supabase(results: list, summary: dict) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.warning("Supabase not configured — skipping")
        return False
    try:
        slim = [
            {k: v for k, v in r.items() if k not in ("equity_curve", "last_trades")}
            for r in results[:50]
        ]
        payload = {
            "timestamp":        datetime.datetime.utcnow().isoformat(),
            "market":           "NSE",
            "currency":         "INR",
            "best_score":       results[0]["score"] if results else 0,
            "strategy_summary": json.dumps(summary),
            "top_results":      json.dumps(slim),
        }
        url = f"{SUPABASE_URL}/rest/v1/backtest_results"
        r   = requests.post(url, json=payload, headers={
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        }, timeout=15)
        if r.ok:
            log.info("Supabase upload success")
            return True
        else:
            log.warning(f"Supabase error {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        log.warning(f"Supabase upload failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  11. REST API (FastAPI)
# ═══════════════════════════════════════════════════════════════

def start_api(host: str = "0.0.0.0", port: int = 8080):
    try:
        from fastapi import FastAPI
        import uvicorn
    except ImportError:
        log.error("Run: pip install fastapi uvicorn")
        return

    app = FastAPI(title="AlgoTrade Backtest API", version="4.0")

    @app.get("/")
    def root():
        return {
            "service": "AlgoTrade NSE Backtesting Engine v4.0",
            "stocks":  len(NSE_ASSETS),
            "market":  "NSE",
            "currency":"INR",
        }

    @app.get("/stocks")
    def list_stocks():
        return [
            {"symbol": k, "label": v["label"],
             "price": v["price"], "sector": v["sector"]}
            for k, v in NSE_ASSETS.items()
        ]

    @app.get("/backtest/{symbol}")
    def backtest_symbol(symbol: str, strategy: str = "orb_ema",
                        sl: float = 1.5, tp: float = 3.0,
                        live: bool = False):
        key  = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
        meta = NSE_ASSETS.get(key)
        if not meta:
            return {"error": f"Unknown symbol: {key}"}
        df  = fetch_live_data(key) if live else generate_ohlcv(
            start_price=meta["price"], vol=meta["vol"]
        )
        results = run_sweep(df, strategy_filter=strategy,
                            symbol=meta["label"], verbose=False)
        if not results:
            return {"error": "No valid results"}
        top = results[0]
        return {k: v for k, v in top.items() if k not in ("equity_curve", "last_trades")}

    @app.get("/sweep/all")
    def sweep_all(strategy: str = "orb_ema", live: bool = False):
        results = run_sweep_all_symbols(live_data=live, strategy_filter=strategy)
        summary = strategy_summary(results)
        slim    = [
            {k: v for k, v in r.items() if k not in ("equity_curve", "last_trades")}
            for r in results[:20]
        ]
        return {"top_20": slim, "summary": summary}

    @app.get("/status")
    def status():
        return {
            "nse_open":     is_nse_session(),
            "ist_time":     now_ist().strftime("%H:%M:%S"),
            "total_stocks": len(NSE_ASSETS),
            "telegram_ok":  bool(TELEGRAM_TOKEN and TELEGRAM_CHATID),
            "supabase_ok":  bool(SUPABASE_URL and SUPABASE_KEY),
        }

    log.info(f"Starting API on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


# ═══════════════════════════════════════════════════════════════
#  12. SCHEDULER
# ═══════════════════════════════════════════════════════════════

def scheduled_run(live_data: bool = False, notify: bool = True):
    log.info("=" * 60)
    log.info("SCHEDULED BACKTEST RUN — NSE 40 Stocks")
    log.info("=" * 60)
    results = run_sweep_all_symbols(live_data=live_data)
    if not results:
        log.warning("No results — skipping save")
        return
    summary = strategy_summary(results)
    print_top_results(results, n=10)
    print_strategy_summary(summary)
    print_sector_summary(results)
    save_results(results, summary)
    save_to_supabase(results, summary)
    if notify:
        notify_top_results(results, summary)
    log.info("Scheduled run complete.")


def start_scheduler(live_data: bool = False, notify: bool = True):
    log.info("Scheduler started — daily at 16:00 IST (NSE close)")
    schedule.every().day.at("16:00").do(
        scheduled_run, live_data=live_data, notify=notify
    )
    while True:
        schedule.run_pending()
        time.sleep(30)


# ═══════════════════════════════════════════════════════════════
#  13. CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="AlgoTrade NSE Backtesting Engine v4.0"
    )
    parser.add_argument("--symbol",   type=str,  default=None,
                        help="Single symbol e.g. SUZLON.NS")
    parser.add_argument("--all",      action="store_true",
                        help="Run sweep on all 40 NSE stocks")
    parser.add_argument("--strategy", type=str,  default=None,
                        choices=["orb_ema","momentum","trend","breakout","sweep"],
                        help="Filter to one strategy")
    parser.add_argument("--live",     action="store_true",
                        help="Use real Yahoo Finance data")
    parser.add_argument("--notify",   action="store_true",
                        help="Send Telegram notification")
    parser.add_argument("--schedule", action="store_true",
                        help="Run daily at 16:00 IST")
    parser.add_argument("--api",      action="store_true",
                        help="Start REST API on port 8080")
    parser.add_argument("--top",      type=int,  default=10,
                        help="Number of top results to show")
    args = parser.parse_args()

    # ── API Mode ────────────────────────────────────────────────
    if args.api:
        start_api()
        return

    # ── Scheduler Mode ──────────────────────────────────────────
    if args.schedule:
        start_scheduler(live_data=args.live, notify=args.notify)
        return

    # ── Single Symbol ───────────────────────────────────────────
    if args.symbol:
        key  = args.symbol if args.symbol.endswith(".NS") else f"{args.symbol}.NS"
        meta = NSE_ASSETS.get(key)
        if not meta:
            log.error(f"Unknown symbol: {key}")
            log.info(f"Available: {', '.join(NSE_ASSETS.keys())}")
            return
        log.info(f"Single sweep: {key}  [{meta['sector']}]")
        df      = fetch_live_data(key) if args.live else generate_ohlcv(
            start_price=meta["price"], vol=meta["vol"]
        )
        results = run_sweep(df, strategy_filter=args.strategy,
                            symbol=meta["label"], verbose=True)
        if not results:
            log.warning("No valid results found.")
            return
        summary = strategy_summary(results)
        print_top_results(results, n=args.top)
        print_strategy_summary(summary)
        save_results(results, summary)
        if args.notify:
            notify_top_results(results, summary)
        return

    # ── All Stocks ──────────────────────────────────────────────
    if args.all:
        log.info(f"Full sweep: {len(NSE_ASSETS)} stocks")
        results = run_sweep_all_symbols(
            live_data=args.live, strategy_filter=args.strategy
        )
        if not results:
            log.warning("No valid results found.")
            return
        summary = strategy_summary(results)
        print_top_results(results, n=args.top)
        print_strategy_summary(summary)
        print_sector_summary(results)
        save_results(results, summary)
        save_to_supabase(results, summary)
        if args.notify:
            notify_top_results(results, summary)
        return

    # ── Default: best single stock (SUZLON) ─────────────────────
    log.info("Default run — SUZLON.NS  (use --all for all 40 stocks)")
    meta    = NSE_ASSETS["SUZLON.NS"]
    df      = fetch_live_data("SUZLON.NS") if args.live else generate_ohlcv(
        start_price=meta["price"], vol=meta["vol"]
    )
    results = run_sweep(df, strategy_filter=args.strategy,
                        symbol=meta["label"], verbose=True)
    if not results:
        log.warning("No valid results found.")
        return
    summary = strategy_summary(results)
    print_top_results(results, n=args.top)
    print_strategy_summary(summary)
    save_results(results, summary)
    if args.notify:
        notify_top_results(results, summary)


if __name__ == "__main__":
    main()
