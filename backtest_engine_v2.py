"""
╔══════════════════════════════════════════════════════════════╗
║     AlgoTrade Backtesting Engine  v2.0  — NSE Stocks        ║
║     INR · Indian Stocks · REST API · Multi-Symbol            ║
╚══════════════════════════════════════════════════════════════╝

Usage:
    python backtest_engine.py              # single run
    python backtest_engine.py --schedule   # daily at 16:00 IST
    python backtest_engine.py --api        # REST API on port 8080
    python backtest_engine.py --symbol RELIANCE.NS
    python backtest_engine.py --all        # all 6 NSE stocks
    python backtest_engine.py --notify     # send Telegram summary

Dependencies:
    pip install numpy pandas requests schedule python-dotenv yfinance
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
from pathlib import Path
from typing  import Optional
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
RESULTS_DIR     = Path("backtest_results")
RESULTS_DIR.mkdir(exist_ok=True)

# NSE commission (realistic intraday)
COMMISSION     = 0.0005   # 0.05% round trip
SCORE_THRESHOLD = 0.40

# NSE stock universe with INR prices
NSE_ASSETS = {
    "RELIANCE.NS":  {"label": "RELIANCE",  "price": 2_855, "vol": 0.014},
    "TCS.NS":       {"label": "TCS",       "price": 3_820, "vol": 0.012},
    "INFY.NS":      {"label": "INFY",      "price": 1_645, "vol": 0.015},
    "HDFCBANK.NS":  {"label": "HDFCBANK",  "price": 1_680, "vol": 0.013},
    "ICICIBANK.NS": {"label": "ICICIBANK", "price":   980, "vol": 0.016},
    "SBIN.NS":      {"label": "SBIN",      "price":   780, "vol": 0.018},
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
    # 15-min bars, NSE session only
    df.index = pd.date_range(
        end=pd.Timestamp.now(tz="Asia/Kolkata"),
        periods=bars, freq="15min"
    )
    return df


def fetch_live_data(symbol: str = "RELIANCE.NS",
                    period: str = "60d",
                    interval: str = "15m") -> pd.DataFrame:
    """
    Fetch real NSE OHLCV from Yahoo Finance.
    NSE symbols need .NS suffix: RELIANCE.NS, TCS.NS etc.
    """
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
#  4. CORE BACKTESTER  (NSE commission applied)
# ═══════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame, signals: np.ndarray,
                 sl_pct: float, tp_pct: float,
                 initial_equity: float = 10_00_000) -> Optional[dict]:
    """
    Simulate intraday trades on NSE stocks.
    Commission: 0.05% round trip (Zerodha/Upstox flat rate approx).
    """
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
                # Apply NSE commission
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

    wins         = [t for t in trades if t["win"]]
    losses       = [t for t in trades if not t["win"]]
    win_rate     = len(wins) / len(trades)
    gross_profit = sum(t["ret"] for t in wins)
    gross_loss   = abs(sum(t["ret"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else gross_profit * 10

    # Max drawdown
    peak, max_dd = equity_curve[0], 0.0
    for e in equity_curve:
        if e > peak: peak = e
        dd = (peak - e) / peak
        if dd > max_dd: max_dd = dd

    # Sharpe (annualised, 15-min bars NSE ~1400 bars/year)
    rets    = [equity_curve[i]/equity_curve[i-1]-1 for i in range(1, len(equity_curve))]
    avg_ret = statistics.mean(rets) if rets else 0
    std_ret = statistics.stdev(rets) if len(rets) > 1 else 1e-9
    sharpe  = (avg_ret / std_ret) * math.sqrt(1400) if std_ret > 0 else 0

    total_return = (equity - initial_equity) / initial_equity * 100

    # Composite score
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
#  5. PARAMETER GRID  (1,656 combos)
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
              verbose: bool = True) -> list:
    grid = build_param_grid()
    if strategy_filter:
        grid = [p for p in grid if p["strategy"] == strategy_filter]

    log.info(f"Sweep: {symbol} | {len(grid)} combinations | {len(df)} bars")
    results = []
    start   = time.time()

    for idx, params in enumerate(grid, 1):
        signals = STRATEGY_FN[params["strategy"]](df, params)
        res     = run_backtest(df, signals, params["sl"], params["tp"])
        if res:
            results.append({**params, **res, "symbol": symbol})
        if verbose and idx % 200 == 0:
            pct = idx / len(grid) * 100
            log.info(f"  [{pct:5.1f}%] {idx}/{len(grid)} valid:{len(results)}")

    results.sort(key=lambda r: r["score"], reverse=True)
    log.info(f"Done {symbol} in {time.time()-start:.1f}s | valid:{len(results)}")
    return results


def run_sweep_all_symbols(live_data: bool = False) -> list:
    """Run backtest across all NSE_ASSETS and merge results."""
    all_results = []
    for symbol, meta in NSE_ASSETS.items():
        log.info(f"{'─'*50}")
        log.info(f"Symbol: {symbol}  ({meta['label']})")
        try:
            df = (fetch_live_data(symbol=symbol)
                  if live_data
                  else generate_ohlcv(
                      start_price=meta["price"],
                      vol=meta["vol"]
                  ))
            results = run_sweep(df, symbol=meta["label"], verbose=False)
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


def print_top_results(results: list, n: int = 10):
    print("\n" + "═"*80)
    print(f"  TOP {n} PARAMETER SETS  (NSE Stocks)")
    print("═"*80)
    hdr = (f"  {'#':>3}  {'STRATEGY':<12}  {'SYMBOL':<10}  {'SL':>5}  {'TP':>5}"
           f"  {'WIN%':>7}  {'PF':>7}  {'DD%':>6}  {'SHARPE':>7}"
           f"  {'RETURN%':>8}  {'TRADES':>7}  {'SCORE':>7}")
    print(hdr)
    print("─"*80)
    for i, r in enumerate(results[:n], 1):
        flag = "★ " if i==1 else f"{i:>2} "
        print(
            f"  {flag}  {r['strategy']:<12}  "
            f"{r.get('symbol',''):<10}  "
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


# ═══════════════════════════════════════════════════════════════
#  8. SAVE RESULTS
# ═══════════════════════════════════════════════════════════════

def save_results(results: list, summary: dict) -> Path:
    ts   = datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"backtest_nse_{ts}.json"

    slim = [{k:v for k,v in r.items() if k != "equity_curve"} for r in results]

    payload = {
        "timestamp":       datetime.datetime.utcnow().isoformat(),
        "market":          "NSE",
        "currency":        "INR",
        "total_tested":    len(results),
        "passed":          sum(1 for r in results if r["score"] > SCORE_THRESHOLD),
        "best_score":      results[0]["score"] if results else 0,
        "strategy_summary": summary,
        "top_100":         slim[:100],
        "deployed_params": slim[0] if slim else None,
    }

    with open(path, "w") as f:
        json.dump(payload, f, indent=2)

    csv_path = RESULTS_DIR / f"backtest_nse_{ts}.csv"
    pd.DataFrame(slim).to_csv(csv_path, index=False)

    log.info(f"Saved → {path}")
    log.info(f"CSV   → {csv_path}")
    return path


# ═══════════════════════════════════════════════════════════════
#  9. SAVE TO SUPABASE
# ═══════════════════════════════════════════════════════════════

def save_to_supabase(results: list, summary: dict) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.warning("Supabase not configured — skipping cloud save")
        return False

    best = results[0] if results else None
    if not best:
        return False

    active   = [s for s,v in summary.items() if v["active"]]
    disabled = [s for s,v in summary.items() if not v["active"]]

    row = {
        "strategy":            best["strategy"],
        "symbol":              best.get("symbol",""),
        "sl_pct":              best["sl"],
        "tp_pct":              best["tp"],
        "win_rate":            best["win_rate"],
        "profit_factor":       best["profit_factor"],
        "max_drawdown":        best["max_drawdown"],
        "sharpe":              best["sharpe"],
        "total_return":        best["total_return"],
        "score":               best["score"],
        "num_trades":          best["num_trades"],
        "active_strategies":   ",".join(active),
        "disabled_strategies": ",".join(disabled),
        "total_tested":        len(results),
        "passed_tests":        sum(1 for r in results if r["score"] > SCORE_THRESHOLD),
        "market":              "NSE",
        "date":                datetime.date.today().isoformat(),
        "created_at":          datetime.datetime.utcnow().isoformat(),
    }

    try:
        res = requests.post(
            f"{SUPABASE_URL}/rest/v1/backtest_results",
            headers={
                "apikey":        SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type":  "application/json",
                "Prefer":        "return=minimal",
            },
            json=row,
            timeout=10,
        )
        if res.ok:
            log.info("Saved to Supabase ✓")
            return True
        log.warning(f"Supabase error: {res.status_code} {res.text}")
    except Exception as e:
        log.warning(f"Supabase save failed: {e}")
    return False


# ═══════════════════════════════════════════════════════════════
#  10. TELEGRAM NOTIFICATION
# ═══════════════════════════════════════════════════════════════

def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATID:
        log.warning("Telegram not configured — skipping")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHATID, "text": text,
                  "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.ok:
            log.info("Telegram sent ✓")
            return True
        log.warning(f"Telegram error: {r.json().get('description')}")
    except Exception as e:
        log.warning(f"Telegram failed: {e}")
    return False


def build_telegram_summary(results: list, summary: dict,
                            elapsed: float, symbol: str = "ALL NSE") -> str:
    if not results:
        return "⚠️ *NSE Backtest Failed* — no valid results."

    best     = results[0]
    active   = [s for s,v in summary.items() if v["active"]]
    disabled = [s for s,v in summary.items() if not v["active"]]
    now_ist_ = now_ist().strftime("%H:%M IST")

    return f"""📊 *NSE Backtest Complete*
_{now_ist_}_

*Symbol:* {symbol}
*Tests:* {len(results):,}
*Passed (score > {SCORE_THRESHOLD}):* {sum(1 for r in results if r["score"] > SCORE_THRESHOLD)}
*Duration:* {elapsed:.0f}s

🏆 *Best Parameters*
Strategy: `{best['strategy'].upper()}`
Symbol: `{best.get('symbol','')}`
SL: `{best['sl']}%` · TP: `{best['tp']}%`
Win Rate: `{best['win_rate']*100:.1f}%`
Profit Factor: `{best['profit_factor']:.2f}`
Sharpe: `{best['sharpe']:.2f}`
Max DD: `{best['max_drawdown']*100:.1f}%`
Score: `{best['score']:.4f}`

✅ *Active:* {', '.join(active) or 'none'}
❌ *Disabled:* {', '.join(disabled) or 'none'}

_Params saved to Supabase ✓_"""


# ═══════════════════════════════════════════════════════════════
#  11. REST API  (called by Activepieces HTTP step)
# ═══════════════════════════════════════════════════════════════

def run_api(port: int = 8080):
    from http.server import HTTPServer, BaseHTTPRequestHandler

    print(f"\n  AlgoTrade Backtest API  |  port {port}")
    print(f"  Market: NSE Stocks (INR)\n")
    print(f"  Endpoints:")
    print(f"    GET  /health              — health check")
    print(f"    GET  /results             — latest saved results")
    print(f"    POST /run                 — run full sweep (all symbols)")
    print(f"    POST /run/symbol          — run single symbol {{symbol}}")
    print(f"    POST /run/strategy        — run one strategy {{strategy}}")
    print(f"    GET  /summary             — strategy status summary\n")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code: int, body: dict):
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(data))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n)) if n else {}

        def _latest_results(self):
            files = sorted(RESULTS_DIR.glob("backtest_nse_*.json"), reverse=True)
            if not files:
                return None
            with open(files[0]) as f:
                return json.load(f)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {
                    "status":   "ok",
                    "market":   "NSE",
                    "currency": "INR",
                    "session":  is_nse_session(),
                    "time_ist": now_ist().strftime("%H:%M:%S IST"),
                })

            elif self.path == "/results":
                data = self._latest_results()
                if data:
                    self._send(200, data)
                else:
                    self._send(404, {"error": "No results yet — run /run first"})

            elif self.path == "/summary":
                data = self._latest_results()
                if data:
                    self._send(200, {
                        "strategy_summary": data.get("strategy_summary", {}),
                        "deployed_params":  data.get("deployed_params", {}),
                        "timestamp":        data.get("timestamp", ""),
                    })
                else:
                    self._send(404, {"error": "No results yet"})

            else:
                self._send(404, {"error": "Not found"})

        def do_POST(self):
            body = self._body()

            # ── POST /run — full sweep all NSE symbols ────────
            if self.path == "/run":
                log.info("[API] /run — full NSE sweep started")
                start     = time.time()
                live      = body.get("live_data", False)

                try:
                    results  = run_sweep_all_symbols(live_data=live)
                    summary  = strategy_summary(results)
                    elapsed  = time.time() - start

                    save_results(results, summary)
                    save_to_supabase(results, summary)

                    slim = [{k:v for k,v in r.items() if k != "equity_curve"}
                            for r in results]
                    payload = {
                        "status":          "ok",
                        "market":          "NSE",
                        "currency":        "INR",
                        "total_tested":    len(results),
                        "passed":          sum(1 for r in results if r["score"] > SCORE_THRESHOLD),
                        "elapsed_seconds": round(elapsed, 1),
                        "deployed_params": slim[0] if slim else None,
                        "strategy_summary": summary,
                        "top_10":          slim[:10],
                    }
                    self._send(200, payload)

                    if TELEGRAM_TOKEN:
                        send_telegram(build_telegram_summary(results, summary, elapsed))

                except Exception as e:
                    log.error(f"Sweep failed: {e}")
                    self._send(500, {"error": str(e)})

            # ── POST /run/symbol — single symbol ─────────────
            elif self.path == "/run/symbol":
                symbol = body.get("symbol", "RELIANCE.NS")
                live   = body.get("live_data", False)
                log.info(f"[API] /run/symbol — {symbol}")
                start  = time.time()

                try:
                    df      = (fetch_live_data(symbol=symbol)
                               if live
                               else generate_ohlcv(
                                   start_price=NSE_ASSETS.get(symbol, {}).get("price", 2855),
                                   vol=NSE_ASSETS.get(symbol, {}).get("vol", 0.014)
                               ))
                    label   = NSE_ASSETS.get(symbol, {}).get("label", symbol)
                    results = run_sweep(df, symbol=label)
                    summary = strategy_summary(results)
                    elapsed = time.time() - start

                    slim = [{k:v for k,v in r.items() if k != "equity_curve"}
                            for r in results]
                    self._send(200, {
                        "status":          "ok",
                        "symbol":          symbol,
                        "total_tested":    len(results),
                        "elapsed_seconds": round(elapsed, 1),
                        "deployed_params": slim[0] if slim else None,
                        "strategy_summary": summary,
                        "top_10":          slim[:10],
                    })
                except Exception as e:
                    self._send(500, {"error": str(e)})

            # ── POST /run/strategy — single strategy ──────────
            elif self.path == "/run/strategy":
                strat  = body.get("strategy", "momentum")
                symbol = body.get("symbol", "RELIANCE.NS")
                live   = body.get("live_data", False)
                log.info(f"[API] /run/strategy — {strat} on {symbol}")
                start  = time.time()

                try:
                    df      = (fetch_live_data(symbol=symbol)
                               if live
                               else generate_ohlcv())
                    results = run_sweep(df, strategy_filter=strat, symbol=symbol)
                    slim    = [{k:v for k,v in r.items() if k != "equity_curve"}
                               for r in results]
                    self._send(200, {
                        "status":          "ok",
                        "strategy":        strat,
                        "symbol":          symbol,
                        "total_tested":    len(results),
                        "elapsed_seconds": round(time.time()-start, 1),
                        "deployed_params": slim[0] if slim else None,
                        "top_10":          slim[:10],
                    })
                except Exception as e:
                    self._send(500, {"error": str(e)})

            else:
                self._send(404, {"error": "Unknown endpoint"})

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


# ═══════════════════════════════════════════════════════════════
#  12. NIGHTLY JOB
# ═══════════════════════════════════════════════════════════════

def nightly_job(live_data: bool = False,
                notify:    bool = True,
                strategy_filter: Optional[str] = None):
    log.info("=" * 60)
    log.info(f"  NIGHTLY NSE BACKTEST  |  {now_ist().strftime('%H:%M IST')}")
    log.info("=" * 60)
    start = time.time()

    results = run_sweep_all_symbols(live_data=live_data)
    summary = strategy_summary(results)
    elapsed = time.time() - start

    print_top_results(results)
    print_strategy_summary(summary)
    save_results(results, summary)
    save_to_supabase(results, summary)

    if notify:
        msg = build_telegram_summary(results, summary, elapsed)
        send_telegram(msg)

    log.info(f"Done in {elapsed:.1f}s  |  next run tomorrow 16:00 IST")


# ═══════════════════════════════════════════════════════════════
#  13. ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="AlgoTrade NSE Backtest Engine")
    ap.add_argument("--schedule",  action="store_true",
                    help="Run on schedule (16:00 IST daily, after NSE close)")
    ap.add_argument("--api",       action="store_true",
                    help="Start REST API on port 8080")
    ap.add_argument("--all",       action="store_true",
                    help="Run sweep on all NSE symbols")
    ap.add_argument("--notify",    action="store_true",
                    help="Send Telegram notification after run")
    ap.add_argument("--live",      action="store_true",
                    help="Fetch live data via yfinance")
    ap.add_argument("--symbol",    default="RELIANCE.NS",
                    help="Single NSE symbol (default: RELIANCE.NS)")
    ap.add_argument("--strategy",  default=None,
                    choices=["momentum","trend","breakout","sweep"])
    ap.add_argument("--port",      type=int, default=8080)
    ap.add_argument("--top",       type=int, default=10)
    args = ap.parse_args()

    # ── API mode ──────────────────────────────────────────────
    if args.api:
        run_api(port=args.port)
        return

    # ── Schedule mode ─────────────────────────────────────────
    if args.schedule:
        # 16:00 IST = 10:30 UTC (after NSE close 15:30 IST)
        schedule.every().day.at("10:30").do(
            nightly_job,
            live_data=args.live,
            notify=args.notify,
        )
        log.info("Scheduler: daily at 16:00 IST (10:30 UTC). Ctrl+C to stop.")
        try:
            while True:
                schedule.run_pending()
                time.sleep(30)
        except KeyboardInterrupt:
            log.info("Scheduler stopped.")
        return

    # ── Single run ────────────────────────────────────────────
    log.info("AlgoTrade NSE Backtest Engine — single run")
    start = time.time()

    if args.all:
        results = run_sweep_all_symbols(live_data=args.live)
    else:
        df      = (fetch_live_data(symbol=args.symbol)
                   if args.live
                   else generate_ohlcv(
                       start_price=NSE_ASSETS.get(args.symbol, {}).get("price", 2855),
                       vol=NSE_ASSETS.get(args.symbol, {}).get("vol", 0.014)
                   ))
        label   = NSE_ASSETS.get(args.symbol, {}).get("label", args.symbol)
        results = run_sweep(df, strategy_filter=args.strategy, symbol=label)

    summary = strategy_summary(results)
    elapsed = time.time() - start

    print_top_results(results, n=args.top)
    print_strategy_summary(summary)
    save_results(results, summary)
    save_to_supabase(results, summary)

    if args.notify:
        send_telegram(build_telegram_summary(results, summary, elapsed))

    log.info(f"Total time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
