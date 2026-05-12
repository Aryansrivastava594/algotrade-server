"""
╔══════════════════════════════════════════════════════════════╗
║   AlgoTrade Pro  —  API Server  v2.0                        ║
║   Railway + Render compatible                               ║
║   POST /run        →  backtest sweep                        ║
║   POST /signal     →  TradingView webhook handler           ║
║   GET  /health     →  uptime ping                           ║
║   GET  /symbols    →  list all 40 NSE stocks                ║
║   GET  /sectors    →  sector summary                        ║
║   GET  /status     →  system status                         ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, sys, time, logging, json, datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Import backtest engine ────────────────────────────────────
# Make sure backtest_engine.py is renamed to backtest.py in same folder
from backtest import (
    fetch_live_data,
    generate_ohlcv,
    run_sweep,
    run_sweep_all_symbols,
    strategy_summary,
    save_to_supabase,
    send_telegram,
    notify_trade_signal,
    notify_top_results,
    print_top_results,
    print_strategy_summary,
    NSE_ASSETS,
    SCORE_THRESHOLD,
    now_ist,
    is_nse_session,
    fmt_inr,
)

# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt = "%H:%M:%S",
    handlers= [logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("api")

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

API_KEY         = os.getenv("API_KEY",          "")
PORT            = int(os.getenv("PORT",          "8080"))
ENVIRONMENT     = os.getenv("ENVIRONMENT",       "production")   # railway | render | local
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHATID = os.getenv("TELEGRAM_CHAT_ID",  "")

# ═══════════════════════════════════════════════════════════════
#  APP
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title       = "AlgoTrade Pro API",
    version     = "2.0.0",
    description = "NSE Backtesting + Signal Handler — 40 Stocks · ORB+EMA · Telegram",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

# ═══════════════════════════════════════════════════════════════
#  MODELS
# ═══════════════════════════════════════════════════════════════

class RunRequest(BaseModel):
    symbol:             Optional[str]  = None   # e.g. "SUZLON.NS" — None = all 40
    strategy:           Optional[str]  = None   # orb_ema | momentum | trend | breakout | sweep
    live_data:          bool           = False
    top_n:              int            = 10
    save_supabase:      bool           = False
    notify_telegram:    bool           = False
    tradingview_signal: Optional[dict] = None


class RunResponse(BaseModel):
    status:            str
    symbol:            str
    elapsed_sec:       float
    total_tested:      int
    passed:            int
    best_score:        float
    strategy_summary:  dict
    top_results:       list
    deployed_params:   Optional[dict]
    tradingview_echo:  Optional[dict]  = None


class SignalRequest(BaseModel):
    """TradingView / Activepieces webhook payload."""
    stock:    Optional[str]  = None
    symbol:   Optional[str]  = None   # fallback alias
    signal:   Optional[str]  = None   # BUY | SELL
    price:    Optional[float]= None
    sl:       Optional[float]= None
    tp:       Optional[float]= None
    reason:   Optional[str]  = None
    insight:  Optional[str]  = None
    strategy: Optional[str]  = "ORB EMA PRO"
    # auto-backtest on signal
    run_backtest: bool        = False
    live_data:    bool        = False


class SignalResponse(BaseModel):
    status:          str
    stock:           str
    signal:          str
    price:           Optional[float]
    sl:              Optional[float]
    tp:              Optional[float]
    reason:          Optional[str]
    telegram_sent:   bool
    backtest_score:  Optional[float] = None
    backtest_params: Optional[dict]  = None


# ═══════════════════════════════════════════════════════════════
#  AUTH HELPER
# ═══════════════════════════════════════════════════════════════

def _check_auth(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ═══════════════════════════════════════════════════════════════
#  STARTUP EVENT
# ═══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def on_startup():
    log.info("═" * 55)
    log.info("  AlgoTrade Pro API  v2.0  starting …")
    log.info(f"  Environment  : {ENVIRONMENT}")
    log.info(f"  Stocks       : {len(NSE_ASSETS)}")
    log.info(f"  Telegram     : {'✅' if TELEGRAM_TOKEN else '❌ not configured'}")
    log.info(f"  API Key      : {'✅ set' if API_KEY else '⚠️  not set (open)'}")
    log.info("═" * 55)


# ═══════════════════════════════════════════════════════════════
#  ROUTES — BASIC
# ═══════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {
        "service":     "AlgoTrade Pro",
        "version":     "2.0.0",
        "market":      "NSE",
        "currency":    "INR",
        "stocks":      len(NSE_ASSETS),
        "environment": ENVIRONMENT,
        "endpoints": {
            "POST /run":     "Run backtest sweep",
            "POST /signal":  "Handle TradingView / Activepieces signal",
            "GET  /health":  "Health check",
            "GET  /symbols": "List all 40 NSE stocks",
            "GET  /sectors": "Sector performance summary",
            "GET  /status":  "System status",
        },
    }


@app.get("/health")
def health():
    """Railway + Render health check endpoint."""
    return {
        "status":   "ok",
        "market":   "NSE",
        "currency": "INR",
        "ist_time": now_ist().strftime("%H:%M:%S"),
        "nse_open": is_nse_session(),
    }


@app.get("/status")
def status():
    return {
        "nse_open":      is_nse_session(),
        "ist_time":      now_ist().strftime("%H:%M:%S"),
        "total_stocks":  len(NSE_ASSETS),
        "telegram_ok":   bool(TELEGRAM_TOKEN and TELEGRAM_CHATID),
        "api_key_set":   bool(API_KEY),
        "environment":   ENVIRONMENT,
        "score_threshold": SCORE_THRESHOLD,
    }


@app.get("/symbols")
def symbols():
    """Return all 40 NSE stocks with metadata."""
    return {
        "total": len(NSE_ASSETS),
        "symbols": [
            {
                "symbol":  k,
                "label":   v["label"],
                "price":   v["price"],
                "sector":  v["sector"],
                "vol":     v["vol"],
            }
            for k, v in NSE_ASSETS.items()
        ],
    }


@app.get("/sectors")
def sectors():
    """Return stocks grouped by sector."""
    result = {}
    for sym, meta in NSE_ASSETS.items():
        sec = meta["sector"]
        if sec not in result:
            result[sec] = []
        result[sec].append({"symbol": sym, "label": meta["label"], "price": meta["price"]})
    return {
        "sectors": result,
        "counts":  {k: len(v) for k, v in result.items()},
    }


# ═══════════════════════════════════════════════════════════════
#  ROUTES — BACKTEST
# ═══════════════════════════════════════════════════════════════

@app.post("/run", response_model=RunResponse)
def run_endpoint(
    body: RunRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """
    Run a backtest sweep.
    - symbol=None  → all 40 stocks
    - strategy=None → all strategies (orb_ema, momentum, trend, breakout, sweep)
    """
    _check_auth(x_api_key)
    t0 = time.time()

    log.info(
        f"/run  symbol={body.symbol}  strategy={body.strategy}"
        f"  live={body.live_data}  notify={body.notify_telegram}"
    )

    # ── Fetch data ──────────────────────────────────────────────
    if body.symbol:
        sym = body.symbol
        if not sym.endswith(".NS"):
            sym = sym + ".NS"
        if sym not in NSE_ASSETS:
            raise HTTPException(
                status_code=404,
                detail=f"Symbol '{sym}' not found. Use GET /symbols to list all."
            )
        meta = NSE_ASSETS[sym]
        try:
            df = (fetch_live_data(symbol=sym)
                  if body.live_data
                  else generate_ohlcv(
                      start_price=meta["price"],
                      vol=meta["vol"],
                  ))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Data fetch failed: {e}")

        results = run_sweep(df, strategy_filter=body.strategy,
                            symbol=meta["label"], verbose=False)
        label = sym
    else:
        # All 40 stocks
        results = run_sweep_all_symbols(
            live_data=body.live_data,
            strategy_filter=body.strategy,
        )
        label = "ALL_NSE_40"

    if not results:
        raise HTTPException(
            status_code=422,
            detail="No valid backtest results — try different parameters"
        )

    # ── Analysis ────────────────────────────────────────────────
    summary = strategy_summary(results)
    top     = [
        {k: v for k, v in r.items() if k not in ("equity_curve", "last_trades")}
        for r in results[:body.top_n]
    ]
    best   = top[0]
    passed = sum(1 for r in results if r["score"] > SCORE_THRESHOLD)

    # ── Supabase ────────────────────────────────────────────────
    if body.save_supabase:
        try:
            save_to_supabase(results, summary)
        except Exception as e:
            log.warning(f"Supabase save failed: {e}")

    # ── Telegram ────────────────────────────────────────────────
    if body.notify_telegram:
        try:
            notify_top_results(results, summary)
        except Exception as e:
            log.warning(f"Telegram notify failed: {e}")

    elapsed = round(time.time() - t0, 2)
    log.info(f"Done in {elapsed}s | valid={len(results)} | best_score={best['score']}")

    return RunResponse(
        status           = "ok",
        symbol           = label,
        elapsed_sec      = elapsed,
        total_tested     = len(results),
        passed           = passed,
        best_score       = best["score"],
        strategy_summary = summary,
        top_results      = top,
        deployed_params  = best,
        tradingview_echo = body.tradingview_signal,
    )


@app.get("/backtest/{symbol}")
def quick_backtest(
    symbol:   str,
    strategy: str  = "orb_ema",
    sl:       float = 1.5,
    tp:       float = 3.0,
    live:     bool  = False,
    x_api_key: Optional[str] = Header(default=None),
):
    """
    Quick single-symbol backtest via GET.
    Example: GET /backtest/SUZLON.NS?strategy=orb_ema&sl=1.5&tp=3.0
    """
    _check_auth(x_api_key)

    key = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    if key not in NSE_ASSETS:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {key}")

    meta = NSE_ASSETS[key]
    df   = (fetch_live_data(key)
            if live
            else generate_ohlcv(start_price=meta["price"], vol=meta["vol"]))

    results = run_sweep(df, strategy_filter=strategy,
                        symbol=meta["label"], verbose=False)
    if not results:
        raise HTTPException(status_code=422, detail="No valid results")

    top = {k: v for k, v in results[0].items()
           if k not in ("equity_curve", "last_trades")}
    return {"status": "ok", "symbol": key, "sector": meta["sector"], "best": top}


# ═══════════════════════════════════════════════════════════════
#  ROUTES — TRADINGVIEW / ACTIVEPIECES SIGNAL WEBHOOK
# ═══════════════════════════════════════════════════════════════

@app.post("/signal", response_model=SignalResponse)
async def signal_endpoint(
    body: SignalRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """
    Receives signal from TradingView alert or Activepieces.

    TradingView alert message (paste this in TV alert):
    {
      "stock":   "{{ticker}}",
      "signal":  "{{strategy.order.action}}",
      "price":   {{close}},
      "reason":  "ORB EMA Crossover"
    }

    Activepieces → HTTP module → POST this endpoint.
    """
    _check_auth(x_api_key)

    # Normalize stock name (tv sends "SUZLON" or "SUZLON.NS")
    stock  = (body.stock or body.symbol or "UNKNOWN").upper().replace(".NS", "")
    signal = (body.signal or "UNKNOWN").upper()
    price  = body.price
    reason = body.reason or "ORB EMA Crossover"

    # Auto-calculate SL / TP if not provided
    sl = body.sl
    tp = body.tp
    if price and not sl:
        sl = round(price * 0.985, 2)   # 1.5% SL
    if price and not tp:
        tp = round(price * 1.030, 2)   # 3.0% TP

    log.info(f"/signal  {stock}  {signal}  ₹{price}  SL:{sl}  TP:{tp}")

    # ── Optional auto-backtest ──────────────────────────────────
    backtest_score  = None
    backtest_params = None
    sym_key         = f"{stock}.NS"

    if body.run_backtest and sym_key in NSE_ASSETS:
        try:
            meta    = NSE_ASSETS[sym_key]
            df      = (fetch_live_data(sym_key)
                       if body.live_data
                       else generate_ohlcv(
                           start_price=meta["price"], vol=meta["vol"]
                       ))
            results = run_sweep(df, strategy_filter="orb_ema",
                                symbol=stock, verbose=False)
            if results:
                backtest_score  = results[0]["score"]
                backtest_params = {
                    k: v for k, v in results[0].items()
                    if k not in ("equity_curve", "last_trades")
                }
        except Exception as e:
            log.warning(f"Auto-backtest failed: {e}")

    # ── Send Telegram ───────────────────────────────────────────
    telegram_sent = False
    if TELEGRAM_TOKEN and TELEGRAM_CHATID:
        try:
            emoji    = "🟢" if signal == "BUY" else "🔴"
            ts       = now_ist().strftime("%d %b  %H:%M IST")
            bt_line  = ""
            if backtest_score is not None:
                rating   = "✅ STRONG" if backtest_score >= 0.55 else "⚠️ MODERATE" if backtest_score >= 0.40 else "❌ WEAK"
                bt_line  = f"\n<b>Backtest Score:</b> {backtest_score:.3f}  {rating}"

            msg = (
                f"<b>{emoji} AlgoTrade Signal</b>  <i>{ts}</i>\n\n"
                f"<b>Stock   :</b> {stock}\n"
                f"<b>Signal  :</b> {signal}\n"
                f"<b>Price   :</b> ₹{price}\n"
                f"<b>SL      :</b> ₹{sl}\n"
                f"<b>Target  :</b> ₹{tp}\n"
                f"<b>Reason  :</b> {reason}"
                f"{bt_line}"
            )
            telegram_sent = send_telegram(msg)
        except Exception as e:
            log.warning(f"Telegram failed: {e}")

    return SignalResponse(
        status          = "ok",
        stock           = stock,
        signal          = signal,
        price           = price,
        sl              = sl,
        tp              = tp,
        reason          = reason,
        telegram_sent   = telegram_sent,
        backtest_score  = backtest_score,
        backtest_params = backtest_params,
    )


@app.post("/signal/raw")
async def signal_raw(request: Request):
    """
    Fallback endpoint — accepts ANY JSON from TradingView
    even if fields don't match the schema.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    log.info(f"/signal/raw  payload={data}")

    stock  = str(data.get("stock") or data.get("symbol") or "UNKNOWN").upper()
    signal = str(data.get("signal") or data.get("action") or "UNKNOWN").upper()
    price  = data.get("price") or data.get("close")
    reason = data.get("reason") or "ORB EMA Signal"

    telegram_sent = False
    if TELEGRAM_TOKEN and TELEGRAM_CHATID:
        emoji = "🟢" if signal == "BUY" else "🔴"
        ts    = now_ist().strftime("%d %b  %H:%M IST")
        msg   = (
            f"<b>{emoji} AlgoTrade Signal</b>  <i>{ts}</i>\n\n"
            f"<b>Stock  :</b> {stock}\n"
            f"<b>Signal :</b> {signal}\n"
            f"<b>Price  :</b> ₹{price}\n"
            f"<b>Reason :</b> {reason}\n\n"
            f"<i>Raw payload received</i>"
        )
        telegram_sent = send_telegram(msg)

    return {
        "status":       "ok",
        "received":     data,
        "parsed":       {"stock": stock, "signal": signal, "price": price},
        "telegram_sent": telegram_sent,
    }


# ═══════════════════════════════════════════════════════════════
#  ROUTES — TELEGRAM TEST
# ═══════════════════════════════════════════════════════════════

@app.get("/telegram/test")
def telegram_test(x_api_key: Optional[str] = Header(default=None)):
    """Send a test message to your Telegram bot."""
    _check_auth(x_api_key)
    ts  = now_ist().strftime("%d %b %Y  %H:%M IST")
    ok  = send_telegram(
        f"✅ <b>AlgoTrade Pro API</b> is live!\n"
        f"<i>{ts}</i>\n\n"
        f"Stocks loaded: {len(NSE_ASSETS)}\n"
        f"Environment: {ENVIRONMENT}"
    )
    return {"telegram_sent": ok, "configured": bool(TELEGRAM_TOKEN and TELEGRAM_CHATID)}


# ═══════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(f"Unhandled error on {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": str(exc)},
    )
