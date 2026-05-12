"""
╔══════════════════════════════════════════════════════════════╗
║     AlgoTrade Backtesting Engine  v4.0  — NSE 30 Stocks    ║
║     INR · Indian Stocks · REST API · Multi-Symbol          ║
║     30 Stocks · Category Filter · Supabase · Telegram      ║
╚══════════════════════════════════════════════════════════════╝

Usage:
    python backtest_engine.py              # single run
    python backtest_engine.py --schedule   # daily at 16:00 IST
    python backtest_engine.py --api        # REST API on port 8080
    python backtest_engine.py --symbol RELIANCE.NS
    python backtest_engine.py --all        # all 30 NSE stocks
    python backtest_engine.py --notify     # send Telegram summary
    python backtest_engine.py --category "Large Cap"  # filter by category

Dependencies:
    pip install numpy pandas requests schedule python-dotenv yfinance fastapi uvicorn
"""

import os
import sys
import json
import math
import time
import random
import logging
import argparse
import datetime
import schedule
import itertools
import statistics
import threading
from pathlib import Path
from typing  import Optional, List, Dict, Any
from dotenv  import load_dotenv

import numpy as np
import pandas as pd
import requests

load_dotenv()

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt = "%H:%M:%S",
    handlers= [logging.StreamHandler(sys.stdout)],
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

# NSE commission (realistic intraday)
COMMISSION      = 0.0005   # 0.05% round trip
SCORE_THRESHOLD = 0.40

# ═══════════════════════════════════════════════════════════════
#  30 NSE STOCKS WITH CATEGORIES
# ═══════════════════════════════════════════════════════════════

NSE_ASSETS = {
    # Large Cap
    "IDBI.NS":      {"label": "IDBI",       "price": 85,    "vol": 0.025, "category": "Large Cap"},
    "BAJAJHFL.NS":  {"label": "BAJAJHFL",   "price": 165,   "vol": 0.022, "category": "Large Cap"},
    "NHPC.NS":      {"label": "NHPC",       "price": 95,    "vol": 0.018, "category": "Large Cap"},
    "IOB.NS":       {"label": "IOB",        "price": 55,    "vol": 0.028, "category": "Large Cap"},
    "SUZLON.NS":    {"label": "SUZLON",     "price": 72,    "vol": 0.035, "category": "Large Cap"},
    "GMRINFRA.NS":  {"label": "GMRINFRA",   "price": 88,    "vol": 0.024, "category": "Large Cap"},
    "NMDC.NS":      {"label": "NMDC",       "price": 78,    "vol": 0.020, "category": "Large Cap"},
    "UCOBANK.NS":   {"label": "UCOBANK",    "price": 48,    "vol": 0.030, "category": "Large Cap"},
    "MAHABANK.NS":  {"label": "MAHABANK",   "price": 52,    "vol": 0.027, "category": "Large Cap"},
    "CENTRALBK.NS": {"label": "CENTRALBK",  "price": 62,    "vol": 0.026, "category": "Large Cap"},

    # Infra
    "SJVN.NS":      {"label": "SJVN",       "price": 110,   "vol": 0.019, "category": "Infra"},
    "NBCC.NS":      {"label": "NBCC",       "price": 125,   "vol": 0.021, "category": "Infra"},
    "IRB.NS":       {"label": "IRB",        "price": 58,    "vol": 0.023, "category": "Infra"},
    "INOXWIND.NS":  {"label": "INOXWIND",   "price": 185,   "vol": 0.032, "category": "Infra"},
    "RPOWER.NS":    {"label": "RPOWER",     "price": 32,    "vol": 0.040, "category": "Infra"},
    "RINFRA.NS":    {"label": "RINFRA",     "price": 28,    "vol": 0.038, "category": "Infra"},
    "PATELENG.NS":  {"label": "PATELENG",   "price": 45,    "vol": 0.029, "category": "Infra"},

    # Finance
    "IDFCFIRSTB.NS": {"label": "IDFCFIRSTB", "price": 92,   "vol": 0.024, "category": "Finance"},
    "YESBANK.NS":   {"label": "YESBANK",    "price": 28,    "vol": 0.045, "category": "Finance"},
    "IFCI.NS":      {"label": "IFCI",       "price": 85,    "vol": 0.026, "category": "Finance"},
    "MSUMI.NS":     {"label": "MSUMI",      "price": 145,   "vol": 0.017, "category": "Finance"},
    "SBFC.NS":      {"label": "SBFC",       "price": 78,    "vol": 0.022, "category": "Finance"},
    "UJJIVANSFB.NS": {"label": "UJJIVANSFB", "price": 55,   "vol": 0.028, "category": "Finance"},
    "NIVABUPA.NS":  {"label": "NIVABUPA",   "price": 95,    "vol": 0.020, "category": "Finance"},
    "SHRIRAMPRP.NS": {"label": "SHRIRAMPRP", "price": 42,   "vol": 0.031, "category": "Finance"},

    # Industrial
    "TRIDENT.NS":   {"label": "TRIDENT",    "price": 52,    "vol": 0.025, "category": "Industrial"},
    "TTML.NS":      {"label": "TTML",       "price": 125,   "vol": 0.033, "category": "Industrial"},
    "NMDCSTEEL.NS": {"label": "NMDCSTEEL",  "price": 68,    "vol": 0.021, "category": "Industrial"},
    "MOREPEN.NS":   {"label": "MOREPEN",    "price": 95,    "vol": 0.027, "category": "Industrial"},
    "ASIANGRN.NS":  {"label": "ASIANGRN",   "price": 48,    "vol": 0.029, "category": "Industrial"},
}

# ═══════════════════════════════════════════════════════════════
#  CATEGORY HELPERS
# ═══════════════════════════════════════════════════════════════

def get_stocks_by_category(category: str = None) -> dict:
    """Filter stocks by category"""
    if not category:
        return NSE_ASSETS
    return {k: v for k, v in NSE_ASSETS.items() if v["category"] == category}

def get_all_categories() -> List[str]:
    """Get unique categories"""
    return list(set(v["category"] for v in NSE_ASSETS.values()))

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
                   start_price: float = 2_855,
                   drift: float = 0.00005,
                   vol: float = 0.014) -> pd.DataFrame:
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
            "open":   prev,
            "high":   max(prev, price) * (1 + noise),
            "low":    min(prev, price) * (1 - noise),
            "close":  price,
            "volume": 50_000 + rng.random() * 5_00_000,
        })

    df = pd.DataFrame(rows)
    df.index = pd.date_range(
        end=pd.Timestamp.now(tz="Asia/Kolkata"),
        periods=bars, freq="15min"
    )
    return df


def fetch_live_data(symbol: str = "RELIANCE.NS",
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
        asset = NSE_ASSETS.get(symbol, {"price": 2_855, "vol": 0.014})
        return generate_ohlcv(start_price=asset["price"], vol=asset["vol"])
    except Exception as e:
        log.warning(f"Live data failed ({e}) — using synthetic data")
        asset = NSE_ASSETS.get(symbol, {"price": 2_855, "vol": 0.014})
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
        ag = (ag * (period-1) + gains[i-1]) / period
        al = (al * (period-1) + losses[i-1]) / period
        rs = ag / al if al != 0 else 1e9
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
    out[period-1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        out[i] = (out[i-1] * (period-1) + tr[i]) / period
    return out


# ═══════════════════════════════════════════════════════════════
#  3. STRATEGY SIGNALS
# ═══════════════════════════════════════════════════════════════

def signal_momentum(df, period, fast, threshold):
    closes   = df["close"].values
    rsi_vals = rsi(closes, period)
    fast_ema = ema(closes, fast)
    signals  = np.zeros(len(df), dtype=int)
    for i in range(max(period, fast) + 1, len(df)):
        r, rp, e = rsi_vals[i], rsi_vals[i-1], fast_ema[i]
        if np.isnan(r) or np.isnan(e): continue
        if r > threshold and rp <= threshold and closes[i] > e:
            signals[i] = 1
        elif r < (100-threshold) and rp >= (100-threshold) and closes[i] < e:
            signals[i] = -1
    return signals

def signal_trend(df, fast, slow):
    closes   = df["close"].values
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    signals  = np.zeros(len(df), dtype=int)
    for i in range(slow + 1, len(df)):
        if np.isnan(fast_ema[i]) or np.isnan(slow_ema[i]): continue
        if fast_ema[i] > slow_ema[i] and fast_ema[i-1] <= slow_ema[i-1]:
            signals[i] = 1
        elif fast_ema[i] < slow_ema[i] and fast_ema[i-1] >= slow_ema[i-1]:
            signals[i] = -1
    return signals

def signal_breakout(df, period, vol_mult):
    highs   = df["high"].values
    lows    = df["low"].values
    closes  = df["close"].values
    volumes = df["volume"].values
    signals = np.zeros(len(df), dtype=int)
    for i in range(period, len(df)):
        avg_vol  = np.mean(volumes[i-period:i])
        if closes[i] > np.max(highs[i-period:i]) and volumes[i] > avg_vol * vol_mult:
            signals[i] = 1
        elif closes[i] < np.min(lows[i-period:i]) and volumes[i] > avg_vol * vol_mult:
            signals[i] = -1
    return signals

def signal_sweep(df, period):
    closes  = df["close"].values
    highs   = df["high"].values
    lows    = df["low"].values
    atr_v   = atr(df, period)
    signals = np.zeros(len(df), dtype=int)
    for i in range(period + 2, len(df)):
        if np.isnan(atr_v[i]): continue
        prev_low  = min(lows[i-1],  lows[i-2])
        prev_high = max(highs[i-1], highs[i-2])
        if lows[i]  < prev_low  - atr_v[i]*0.1 and closes[i] > prev_low:  signals[i] = 1
        if highs[i] > prev_high + atr_v[i]*0.1 and closes[i] < prev_high: signals[i] = -1
    return signals


STRATEGY_FN = {
    "momentum": lambda df, p: signal_momentum(df, p["period"], p["fast"],    p["threshold"]),
    "trend":    lambda df, p: signal_trend(df, p["fast"], p["slow"]),
    "breakout": lambda df, p: signal_breakout(df, p["period"], p["vol_mult"]),
    "sweep":    lambda df, p: signal_sweep(df, p["period"]),
}


# ═══════════════════════════════════════════════════════════════
#  4. CORE BACKTESTER
# ═══════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame, signals: np.ndarray,
                 sl_pct: float, tp_pct: float,
                 initial_equity: float = 10_00_000) -> Optional[dict]:
    closes = df["close"].values
    equity = initial_equity
    equity_curve = [equity]
    trades = []

    in_trade    = False
    entry_price = 0.0
    direction   = 0
    entry_idx   = 0
    sl          = -sl_pct / 100
    tp          =  tp_pct / 100

    for i in range(1, len(df)):
        if in_trade:
            pct = (
                (closes[i] - entry_price) / entry_price
                if direction == 1
                else (entry_price - closes[i]) / entry_price
            )
            if pct <= sl or pct >= tp:
                ret     = max(sl, min(tp, pct)) * (1 - COMMISSION)
                equity *= (1 + ret)
                trades.append({
                    "ret":  ret,
                    "win":  ret > 0,
                    "bars": i - entry_idx
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

    wins          = [t for t in trades if t["win"]]
    losses        = [t for t in trades if not t["win"]]
    win_rate      = len(wins) / len(trades)
    gross_profit  = sum(t["ret"] for t in wins)
    gross_loss    = abs(sum(t["ret"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else gross_profit * 10

    peak, max_dd = equity_curve[0], 0.0
    for e in equity_curve:
        if e > peak: peak = e
        dd = (peak - e) / peak
        if dd > max_dd: max_dd = dd

    rets    = [equity_curve[i]/equity_curve[i-1]-1 for i in range(1, len(equity_curve))]
    avg_ret = statistics.mean(rets) if rets else 0
    std_ret = statistics.stdev(rets) if len(rets) > 1 else 1e-9
    sharpe  = (avg_ret / std_ret) * math.sqrt(1400) if std_ret > 0 else 0

    total_return = (equity - initial_equity) / initial_equity * 100

    score = (
        min(win_rate, 1.0)           * 0.30 +
        min(profit_factor / 3, 1.0)  * 0.30 +
        (1 - min(max_dd, 1.0))       * 0.20 +
        max(min(sharpe / 3, 1.0), 0) * 0.20
    )

    return {
        "win_rate":      round(win_rate,      4),
        "profit_factor": round(profit_factor, 4),
        "max_drawdown":  round(max_dd,        4),
        "sharpe":        round(sharpe,        4),
        "total_return":  round(total_return,  2),
        "num_trades":    len(trades),
        "final_equity":  round(equity,        2),
        "score":         round(score,         5),
        "equity_curve":  equity_curve[::10],
    }


# ═══════════════════════════════════════════════════════════════
#  5. PARAMETER GRID
# ═══════════════════════════════════════════════════════════════

def build_param_grid() -> list:
    SL = [0.5, 0.8, 1.0, 1.5, 2.0, 2.5]
    TP = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
    grid = []

    for period, thresh, sl, tp in itertools.product(
        [7,10,14,21,28], [45,50,55,60,65], SL, TP
    ):
        grid.append({"strategy":"momentum","sl":sl,"tp":tp,
                     "period":period,"fast":20,"threshold":thresh})

    for fast, slow, sl, tp in itertools.product(
        [5,8,13,21], [20,50,100,200], SL[:4], TP[:4]
    ):
        grid.append({"strategy":"trend","sl":sl,"tp":tp,"fast":fast,"slow":slow})

    for period, vol_mult, sl, tp in itertools.product(
        [10,15,20,30,50], [1.2,1.5,2.0,2.5], SL[:4], TP[:4]
    ):
        grid.append({"strategy":"breakout","sl":sl,"tp":tp,
                     "period":period,"vol_mult":vol_mult})

    for period, sl, tp in itertools.product([5,8,10,14,21], SL, TP):
        grid.append({"strategy":"sweep","sl":sl,"tp":tp,"period":period})

    return grid


# ═══════════════════════════════════════════════════════════════
#  6. SWEEP ENGINE
# ═══════════════════════════════════════════════════════════════

def run_sweep(df: pd.DataFrame,
              strategy_filter: Optional[str] = None,
              symbol: str = "UNKNOWN",
              category: str = "",
              verbose: bool = True) -> list:
    grid = build_param_grid()
    if strategy_filter:
        grid = [p for p in grid if p["strategy"] == strategy_filter]

    log.info(f"Sweep: {symbol} [{category}] | {len(grid)} combinations | {len(df)} bars")
    results = []
    start   = time.time()

    for idx, params in enumerate(grid, 1):
        signals = STRATEGY_FN[params["strategy"]](df, params)
        res     = run_backtest(df, signals, params["sl"], params["tp"])
        if res:
            results.append({**params, **res, "symbol": symbol, "category": category})
        if verbose and idx % 200 == 0:
            pct = idx / len(grid) * 100
            log.info(f"  [{pct:5.1f}%] {idx}/{len(grid)} valid:{len(results)}")

    results.sort(key=lambda r: r["score"], reverse=True)
    log.info(f"Done {symbol} in {time.time()-start:.1f}s | valid:{len(results)}")
    return results


def run_sweep_all_symbols(live_data: bool = False, category: str = None) -> list:
    all_results = []

    # Filter by category if specified
    assets = get_stocks_by_category(category) if category else NSE_ASSETS

    for symbol, meta in assets.items():
        log.info(f"{'─'*50}")
        log.info(f"Symbol: {symbol}  ({meta['label']})  [{meta['category']}]")
        try:
            df = (fetch_live_data(symbol=symbol)
                  if live_data
                  else generate_ohlcv(
                      start_price=meta["price"],
                      vol=meta["vol"]
                  ))
            results = run_sweep(df, symbol=meta["label"], category=meta["category"], verbose=False)
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
    for strat in ["momentum","trend","breakout","sweep"]:
        sr = [r for r in results if r["strategy"] == strat]
        if not sr:
            summary[strat] = {"count":0,"avg_score":0,"best_wr":0,
                               "active":False,"best_params":None}
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
            "active":      avg_score >= SCORE_THRESHOLD,
            "best_params": {k:v for k,v in best.items() if k != "equity_curve"},
        }
    return summary


def category_summary(results: list) -> dict:
    """Summary by stock category"""
    summary = {}
    for cat in get_all_categories():
        cr = [r for r in results if r.get("category") == cat]
        if not cr:
            summary[cat] = {"count": 0, "avg_score": 0, "best_symbol": "N/A"}
            continue
        avg_score = sum(r["score"] for r in cr) / len(cr)
        best = max(cr, key=lambda r: r["score"])
        summary[cat] = {
            "count": len(cr),
            "avg_score": round(avg_score, 4),
            "best_symbol": best["symbol"],
            "best_score": best["score"],
        }
    return summary


def print_top_results(results: list, n: int = 10):
    print("\n" + "═"*80)
    print(f"  TOP {n} PARAMETER SETS  (NSE 30 Stocks)")
    print("═"*80)
    hdr = (f"  {'#':>3}  {'STRATEGY':<12}  {'SYMBOL':<10}  {'CAT':<10}  {'SL':>5}  {'TP':>5}"
           f"  {'WIN%':>7}  {'PF':>7}  {'DD%':>6}  {'SHARPE':>7}"
           f"  {'RETURN%':>8}  {'TRADES':>7}  {'SCORE':>7}")
    print(hdr)
    print("─"*80)
    for i, r in enumerate(results[:n], 1):
        flag = "★ " if i==1 else f"{i:>2} "
        print(
            f"  {flag}  {r['strategy']:<12}  "
            f"{r.get('symbol',''):<10}  "
            f"{r.get('category',''):<10}  "
            f"{r['sl']:>5.1f}  {r['tp']:>5.1f}  "
            f"{r['win_rate']*100:>6.1f}%  "
            f"{r['profit_factor']:>7.3f}  "
            f"{r['max_drawdown']*100:>5.1f}%  "
            f"{r['sharpe']:>7.2f}  "
            f"{r['total_return']:>+7.1f}%  "
            f"{r['num_trades']:>7}  "
            f"{r['score']:>7.4f}"
        )
    print("═"*80)


def print_strategy_summary(summary: dict):
    print("\n  STRATEGY STATUS")
    print("─"*60)
    for strat, s in summary.items():
        status = "✅ ACTIVE  " if s["active"] else "❌ DISABLED"
        if s["count"] == 0:
            print(f"  {strat:<12}  {status}  — no valid results")
        else:
            print(
                f"  {strat:<12}  {status}  "
                f"avg:{s['avg_score']:.3f}  "
                f"WR:{s['best_wr']*100:.1f}%  "
                f"PF:{s['best_pf']:.2f}"
            )
    print()


def print_category_summary(summary: dict):
    print("\n  CATEGORY PERFORMANCE")
    print("─"*60)
    for cat, s in summary.items():
        if s["count"] == 0:
            print(f"  {cat:<12}  — no data")
        else:
            print(
                f"  {cat:<12}  "
                f"tests:{s['count']:>4}  "
                f"avg:{s['avg_score']:.3f}  "
                f"best:{s['best_symbol']}({s['best_score']:.3f})"
            )
    print()


# ═══════════════════════════════════════════════════════════════
#  8. SAVE RESULTS
# ═══════════════════════════════════════════════════════════════

def save_results(results: list, summary: dict, cat_summary: dict = None) -> Path:
    ts   = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"backtest_nse_{ts}.json"

    slim = [{k:v for k,v in r.items() if k != "equity_curve"} for r in results]

    payload = {
        "timestamp":        datetime.datetime.utcnow().isoformat(),
        "market":           "NSE",
        "currency":         "INR",
        "total_tested":     len(results),
        "passed":           sum(1 for r in results if r["score"] > SCORE_THRESHOLD),
        "best_score":       results[0]["score"] if results else 0,
        "strategy_summary": summary,
        "category_summary": cat_summary or {},
        "top_100":          slim[:100],
        "deployed_params":  slim[0] if slim else None,
    }

    with open(path, "w") as f:
        json.dump(payload, f, indent=2)

    csv_path = RESULTS_DIR / f"backtest_nse_{ts}.csv"
    pd.DataFrame(slim).to_csv(csv_path, index=False)

    log.info(f"Saved → {path}")
    log.info(f"CSV   → {csv_path}")
    return path


# ═══════════════════════════════════════════════════════════════
#  9. SUPABASE INTEGRATION
# ═══════════════════════════════════════════════════════════════

def save_to_supabase(results: list, summary: dict, cat_summary: dict = None) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.warning("Supabase not configured — skipping")
        return False

    try:
        slim = [{k:v for k,v in r.items() if k != "equity_curve"} for r in results[:50]]

        payload = {
            "timestamp":        datetime.datetime.utcnow().isoformat(),
            "market":           "NSE",
            "currency":         "INR",
            "total_tested":     len(results),
            "passed":           sum(1 for r in results if r["score"] > SCORE_THRESHOLD),
            "best_score":       results[0]["score"] if results else 0,
            "strategy_summary": json.dumps(summary),
            "category_summary": json.dumps(cat_summary) if cat_summary else None,
            "top_results":      json.dumps(slim[:20]),
            "deployed_params":  json.dumps(slim[0]) if slim else None,
        }

        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }

        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/backtest_results",
            headers=headers,
            json=payload,
            timeout=15
        )

        if resp.status_code in (200, 201):
            log.info("Supabase save OK")
            return True
        else:
            log.warning(f"Supabase error {resp.status_code}: {resp.text[:200]}")
            return False

    except Exception as e:
        log.error(f"Supabase failed: {e}")
        return False


def save_signal_to_supabase(signal_data: dict) -> bool:
    """Save real-time signal from Activepieces/TradingView"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.warning("Supabase not configured — skipping signal save")
        return False

    try:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }

        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/stock_signals",
            headers=headers,
            json=signal_data,
            timeout=15
        )

        if resp.status_code in (200, 201):
            log.info(f"Signal saved to Supabase: {signal_data.get('symbol')}")
            return True
        else:
            log.warning(f"Supabase signal error {resp.status_code}: {resp.text[:200]}")
            return False

    except Exception as e:
        log.error(f"Supabase signal save failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  10. TELEGRAM NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

def telegram_message(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATID:
        log.warning("Telegram not configured")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHATID, "text": text, "parse_mode": "Markdown"}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False


def notify_top_results(results: list, n: int = 5):
    if not results:
        return
    lines = ["🔔 *AlgoTrade Backtest Results*", f"_{now_ist().strftime('%d %b %Y %H:%M')}_", ""]
    for i, r in enumerate(results[:n], 1):
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "▫️"
        lines.append(
            f"{emoji} *{r['symbol']}* | {r['strategy']}\n"
            f"Score: `{r['score']:.4f}` | WR: `{r['win_rate']*100:.1f}%` | PF: `{r['profit_factor']:.2f}`"
        )
    lines.append("")
    lines.append(f"Total tested: `{len(results)}` | Passed: `{sum(1 for r in results if r['score'] > SCORE_THRESHOLD)}`")
    telegram_message("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
#  11. MAIN CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="AlgoTrade NSE Backtest Engine v4.0")
    parser.add_argument("--symbol",    help="Single NSE symbol (e.g. RELIANCE.NS)")
    parser.add_argument("--all",       action="store_true", help="Run all 30 NSE stocks")
    parser.add_argument("--category",  choices=get_all_categories(), help="Filter by category")
    parser.add_argument("--live",      action="store_true", help="Use live Yahoo Finance data")
    parser.add_argument("--strategy",  choices=list(STRATEGY_FN.keys()), help="Filter strategy")
    parser.add_argument("--notify",    action="store_true", help="Send Telegram summary")
    parser.add_argument("--schedule",  action="store_true", help="Schedule daily at 16:00 IST")
    parser.add_argument("--api",       action="store_true", help="Start REST API server")
    args = parser.parse_args()

    if args.api:
        from webhook_server import app
        import uvicorn
        port = int(os.getenv("PORT", 8080))
        uvicorn.run(app, host="0.0.0.0", port=port)
        return

    if args.schedule:
        schedule.every().day.at("16:00").do(lambda: run_sweep_all_symbols(live_data=True))
        log.info("Scheduler started — daily at 16:00 IST")
        while True:
            schedule.run_pending()
            time.sleep(60)
        return

    if args.all or args.category:
        results = run_sweep_all_symbols(live_data=args.live, category=args.category)
        if not results:
            log.warning("No valid results")
            return
        summary = strategy_summary(results)
        cat_summary = category_summary(results)
        print_top_results(results)
        print_strategy_summary(summary)
        print_category_summary(cat_summary)
        save_results(results, summary, cat_summary)
        save_to_supabase(results, summary, cat_summary)
        if args.notify:
            notify_top_results(results)
        return

    if args.symbol:
        symbol = args.symbol.upper()
        meta = NSE_ASSETS.get(symbol, {"label": symbol.replace(".NS",""), "price": 2_855, "vol": 0.014, "category": "Unknown"})
        log.info(f"Single run: {symbol} ({meta['label']}) [{meta['category']}]")
        df = fetch_live_data(symbol=symbol) if args.live else generate_ohlcv(start_price=meta["price"], vol=meta["vol"])
        results = run_sweep(df, strategy_filter=args.strategy, symbol=meta["label"], category=meta["category"])
        if results:
            print_top_results(results, n=5)
            save_results(results, strategy_summary(results))
        return

    # Default: quick demo on top 5 stocks
    log.info("No args — running demo on top 5 stocks")
    top5 = dict(list(NSE_ASSETS.items())[:5])
    for symbol, meta in top5.items():
        df = generate_ohlcv(start_price=meta["price"], vol=meta["vol"])
        results = run_sweep(df, symbol=meta["label"], category=meta["category"], verbose=False)
        if results:
            print_top_results(results, n=3)


if __name__ == "__main__":
    main()
