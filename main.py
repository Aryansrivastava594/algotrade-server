"""
╔══════════════════════════════════════════════════════════════╗
║   AlgoTrade Pro  —  API Server  v4.0                        ║
║   Full Workflow:                                            ║
║   TradingView → Webhook → Groq → Risk Mgr → Render          ║
║              → Dhan Execute → Telegram                      ║
╚══════════════════════════════════════════════════════════════╝

Endpoints:
    GET  /              → API info
    GET  /health        → health check
    GET  /status        → system status
    GET  /symbols       → all 40 NSE stocks
    GET  /sectors       → stocks by sector
    GET  /positions     → open Dhan positions
    GET  /orders        → today orders
    GET  /portfolio     → portfolio summary with P&L
    GET  /telegram/test → test Telegram
    GET  /backtest/{symbol} → quick backtest
    POST /run           → full backtest sweep
    POST /signal        → TradingView webhook + process
    POST /signal/raw    → fallback raw webhook
    POST /order         → manual order placement
    POST /squareoff     → square off all positions
    POST /cancel        → cancel all orders (emergency)
"""

import os
import sys
import time
import logging
import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Backtest engine ───────────────────────────────────────────
from backtest import (
    fetch_live_data,
    generate_ohlcv,
    run_sweep,
    run_sweep_all_symbols,
    strategy_summary,
    save_to_supabase,
    send_telegram,
    notify_top_results,
    NSE_ASSETS,
    SCORE_THRESHOLD,
    now_ist,
    is_nse_session,
)

# ── Dhan execution ────────────────────────────────────────────
from dhan_executor import (
    place_order,
    get_positions,
    get_all_orders,
    get_order_status,
    get_portfolio_summary,
    cancel_all_orders,
    square_off_all,
    DHAN_CONFIGURED,
)

# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt  = "%H:%M:%S",
    handlers = [logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("api")

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

API_KEY         = os.getenv("API_KEY",           "")
PORT            = int(os.getenv("PORT",          "8080"))
ENVIRONMENT     = os.getenv("ENVIRONMENT",       "render")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN","")
TELEGRAM_CHATID = os.getenv("TELEGRAM_CHAT_ID",  "")
SUPABASE_URL    = os.getenv("SUPABASE_URL",       "")
SUPABASE_KEY    = os.getenv("SUPABASE_ANON_KEY",  "")

# ═══════════════════════════════════════════════════════════════
#  APP
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title       = "AlgoTrade Pro API",
    version     = "4.0.0",
    description = (
        "NSE Algo Trading — 40 Stocks · ORB+EMA · "
        "Groq AI · Risk Manager · Dhan Execution · Telegram"
    ),
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

class SignalRequest(BaseModel):
    # Core signal
    stock:         Optional[str]   = None
    symbol:        Optional[str]   = None
    signal:        Optional[str]   = None
    price:         Optional[float] = None
    sl:            Optional[float] = None
    tp:            Optional[float] = None
    reason:        Optional[str]   = None
    strategy:      Optional[str]   = "ORB EMA PRO v9"
    exchange:      Optional[str]   = "NSE"
    timeframe:     Optional[str]   = "15"
    # AI analysis (from Groq via Activepieces)
    approved:      Optional[bool]  = None
    action:        Optional[str]   = None
    quality_score: Optional[int]   = None
    risk:          Optional[str]   = None
    # Risk manager output (from Code step)
    quantity:      Optional[int]   = None
    position_value:Optional[float] = None
    rr_ratio:      Optional[float] = None
    # Controls
    auto_execute:  bool            = True
    save_supabase: bool            = True
    order_type:    str             = "MARKET"


class SignalResponse(BaseModel):
    status:          str
    stock:           str
    signal:          str
    price:           Optional[float]
    sl:              Optional[float]
    tp:              Optional[float]
    reason:          Optional[str]
    approved:        Optional[bool]
    action:          Optional[str]
    quality_score:   Optional[int]
    quantity:        Optional[int]
    order_status:    Optional[str]  = None
    order_id:        Optional[str]  = None
    order_error:     Optional[str]  = None
    telegram_sent:   bool           = False
    supabase_saved:  bool           = False
    rr_ratio:        Optional[float]= None


class OrderRequest(BaseModel):
    stock:      str
    signal:     str
    price:      float
    sl:         Optional[float] = None
    tp:         Optional[float] = None
    quantity:   Optional[int]   = None
    order_type: str             = "MARKET"


class RunRequest(BaseModel):
    symbol:          Optional[str]  = None
    strategy:        Optional[str]  = None
    live_data:       bool           = False
    top_n:           int            = 10
    save_supabase:   bool           = False
    notify_telegram: bool           = False


# ═══════════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════════

def _check_auth(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ═══════════════════════════════════════════════════════════════
#  SUPABASE SIGNAL SAVE
# ═══════════════════════════════════════════════════════════════

def save_signal_to_supabase(data: dict) -> bool:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        import requests as req
        r = req.post(
            f"{SUPABASE_URL}/rest/v1/trade_signals",
            json=data,
            headers={
                "apikey":        SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type":  "application/json",
                "Prefer":        "return=minimal",
            },
            timeout=10,
        )
        return r.ok
    except Exception as e:
        log.warning(f"Supabase save failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def on_startup():
    log.info("═" * 55)
    log.info("  AlgoTrade Pro API  v4.0  starting …")
    log.info(f"  Environment  : {ENVIRONMENT}")
    log.info(f"  Stocks       : {len(NSE_ASSETS)}")
    log.info(f"  Telegram     : {'✅' if TELEGRAM_TOKEN else '❌'}")
    log.info(f"  API Key      : {'✅ set' if API_KEY else '⚠️  open'}")
    log.info(f"  Supabase     : {'✅' if SUPABASE_URL else '❌'}")
    log.info(f"  Dhan Broker  : {'✅ configured' if DHAN_CONFIGURED else '❌ not set'}")
    log.info("═" * 55)


# ═══════════════════════════════════════════════════════════════
#  BASIC ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {
        "service":     "AlgoTrade Pro",
        "version":     "4.0.0",
        "market":      "NSE",
        "stocks":      len(NSE_ASSETS),
        "environment": ENVIRONMENT,
        "dhan":        DHAN_CONFIGURED,
        "nse_open":    is_nse_session(),
        "ist_time":    now_ist().strftime("%H:%M:%S"),
        "workflow": {
            "step_1": "TradingView → Activepieces Webhook",
            "step_2": "Groq AI → approve/reject",
            "step_3": "Risk Manager → position size",
            "step_4": "POST /signal → save + process",
            "step_5": "POST /order → Dhan execution",
            "step_6": "Telegram → alert with order status",
        },
    }


@app.get("/health")
def health():
    return {
        "status":   "ok",
        "nse_open": is_nse_session(),
        "ist_time": now_ist().strftime("%H:%M:%S"),
    }


@app.get("/status")
def status():
    return {
        "nse_open":      is_nse_session(),
        "ist_time":      now_ist().strftime("%H:%M:%S"),
        "ist_date":      now_ist().strftime("%d %b %Y"),
        "total_stocks":  len(NSE_ASSETS),
        "telegram_ok":   bool(TELEGRAM_TOKEN and TELEGRAM_CHATID),
        "supabase_ok":   bool(SUPABASE_URL and SUPABASE_KEY),
        "dhan_ok":       DHAN_CONFIGURED,
        "api_key_set":   bool(API_KEY),
        "environment":   ENVIRONMENT,
    }


@app.get("/symbols")
def symbols():
    return {
        "total": len(NSE_ASSETS),
        "symbols": [
            {"symbol": k, "label": v["label"],
             "price": v["price"], "sector": v["sector"]}
            for k, v in NSE_ASSETS.items()
        ],
    }


@app.get("/sectors")
def sectors():
    result = {}
    for sym, meta in NSE_ASSETS.items():
        sec = meta["sector"]
        if sec not in result:
            result[sec] = []
        result[sec].append({
            "symbol": sym, "label": meta["label"], "price": meta["price"]
        })
    return {"sectors": result, "counts": {k: len(v) for k, v in result.items()}}


# ═══════════════════════════════════════════════════════════════
#  SIGNAL ENDPOINT — Step 4 in workflow
# ═══════════════════════════════════════════════════════════════

@app.post("/signal", response_model=SignalResponse)
async def signal_endpoint(
    body:      SignalRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """
    Step 4 in workflow — receives processed signal from Activepieces.
    At this point signal is already:
      ✅ Approved by Groq AI
      ✅ Position sized by Risk Manager
    
    This endpoint:
      1. Saves to Supabase
      2. Returns processed signal for Step 5 (Dhan order)
      3. Logs everything
    """
    _check_auth(x_api_key)

    stock  = (body.stock or body.symbol or "UNKNOWN").upper().replace(".NS", "")
    signal = (body.signal or "UNKNOWN").upper()
    price  = body.price
    reason = body.reason or "ORB EMA Crossover"

    # Auto SL/TP if not provided
    sl = body.sl or (round(price * 0.985, 2) if price else None)
    tp = body.tp or (round(price * 1.030, 2) if price else None)

    log.info(
        f"/signal  {stock}  {signal}  ₹{price}  "
        f"approved:{body.approved}  score:{body.quality_score}  "
        f"qty:{body.quantity}"
    )

    # Save to Supabase
    supabase_saved = False
    if body.save_supabase:
        supabase_saved = save_signal_to_supabase({
            "stock":         stock,
            "signal":        signal,
            "price":         price,
            "sl":            sl,
            "tp":            tp,
            "reason":        reason,
            "strategy":      body.strategy,
            "approved":      body.approved,
            "action":        body.action,
            "quality_score": body.quality_score,
            "quantity":      body.quantity,
            "rr_ratio":      body.rr_ratio,
            "risk_level":    body.risk,
            "ist_time":      now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        })

    return SignalResponse(
        status         = "ok",
        stock          = stock,
        signal         = signal,
        price          = price,
        sl             = sl,
        tp             = tp,
        reason         = reason,
        approved       = body.approved,
        action         = body.action,
        quality_score  = body.quality_score,
        quantity       = body.quantity,
        rr_ratio       = body.rr_ratio,
        telegram_sent  = False,
        supabase_saved = supabase_saved,
    )


# ═══════════════════════════════════════════════════════════════
#  ORDER ENDPOINT — Step 5 in workflow (Dhan Execution)
# ═══════════════════════════════════════════════════════════════

@app.post("/order")
def order_endpoint(
    body:      OrderRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    """
    Step 5 in workflow — places order on Dhan broker.
    Called by Activepieces after /signal returns ok.
    
    Activepieces Step 5 body:
    {
      "stock":    {{step_4.stock}},
      "signal":   {{step_4.signal}},
      "price":    {{step_4.price}},
      "sl":       {{step_4.sl}},
      "tp":       {{step_4.tp}},
      "quantity": {{step_3.quantity}}
    }
    """
    _check_auth(x_api_key)

    if not DHAN_CONFIGURED:
        return {
            "status":  "skipped",
            "message": "Dhan not configured",
            "stock":   body.stock,
            "signal":  body.signal,
        }

    if not is_nse_session():
        return {
            "status":  "skipped",
            "message": "Market is closed",
            "stock":   body.stock,
            "signal":  body.signal,
        }

    log.info(f"/order  {body.stock}  {body.signal}  qty:{body.quantity}")

    result = place_order(
        stock      = body.stock,
        signal     = body.signal,
        price      = body.price,
        sl         = body.sl,
        tp         = body.tp,
        quantity   = body.quantity,
        order_type = body.order_type,
    )

    # Send Telegram with order status
    if TELEGRAM_TOKEN and TELEGRAM_CHATID:
        emoji = "🟢" if body.signal.upper() == "BUY" else "🔴"
        ts    = now_ist().strftime("%d %b  %H:%M IST")

        if result.get("status") == "success":
            order_line = f"✅ Order Placed\nOrder ID : {result.get('order_id')}"
        else:
            order_line = f"❌ Order Failed\nReason   : {result.get('error')}"

        msg = (
            f"<b>{emoji} AlgoTrade Signal</b>  <i>{ts}</i>\n\n"
            f"<b>Stock    :</b> {body.stock}\n"
            f"<b>Signal   :</b> {body.signal}\n"
            f"<b>Price    :</b> ₹{body.price}\n"
            f"<b>SL       :</b> ₹{body.sl}\n"
            f"<b>Target   :</b> ₹{body.tp}\n"
            f"<b>Quantity :</b> {result.get('qty', body.quantity)}\n"
            f"<b>Value    :</b> ₹{result.get('order_value', 0)}\n"
            f"<b>R:R      :</b> 1:{result.get('rr_ratio', 0)}\n\n"
            f"<b>🏦 Dhan</b>\n{order_line}"
        )
        send_telegram(msg)

    return result


# ═══════════════════════════════════════════════════════════════
#  SIGNAL/RAW — Fallback for direct TradingView
# ═══════════════════════════════════════════════════════════════

@app.post("/signal/raw")
async def signal_raw(request: Request):
    """Fallback — accepts ANY JSON from TradingView directly."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    log.info(f"/signal/raw  payload={data}")

    stock  = str(
        data.get("stock") or data.get("symbol") or
        data.get("ticker") or "UNKNOWN"
    ).upper().replace(".NS", "")
    signal = str(data.get("signal") or data.get("action") or "UNKNOWN").upper()
    price  = data.get("price") or data.get("close")
    sl     = data.get("sl") or (round(float(price) * 0.985, 2) if price else None)
    tp     = data.get("tp") or (round(float(price) * 1.030, 2) if price else None)
    reason = data.get("reason") or "ORB EMA Crossover"

    # Place order if valid signal
    order_result = None
    if signal in ["BUY", "SELL"] and price and DHAN_CONFIGURED and is_nse_session():
        order_result = place_order(
            stock  = stock,
            signal = signal,
            price  = float(price),
            sl     = sl,
            tp     = tp,
        )

    # Telegram
    if TELEGRAM_TOKEN and TELEGRAM_CHATID:
        emoji = "🟢" if signal == "BUY" else "🔴"
        ts    = now_ist().strftime("%d %b  %H:%M IST")
        order_line = ""
        if order_result:
            order_line = (
                f"\n✅ Order: {order_result.get('order_id')}"
                if order_result.get("status") == "success"
                else f"\n❌ Order Failed: {order_result.get('error')}"
            )
        send_telegram(
            f"<b>{emoji} AlgoTrade Signal</b>  <i>{ts}</i>\n\n"
            f"<b>Stock  :</b> {stock}\n"
            f"<b>Signal :</b> {signal}\n"
            f"<b>Price  :</b> ₹{price}\n"
            f"<b>SL     :</b> ₹{sl}\n"
            f"<b>Target :</b> ₹{tp}\n"
            f"<b>Reason :</b> {reason}"
            f"{order_line}"
        )

    return {
        "status":       "ok",
        "parsed":       {"stock": stock, "signal": signal, "price": price},
        "order_result": order_result,
    }


# ═══════════════════════════════════════════════════════════════
#  BROKER ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/positions")
def positions(x_api_key: Optional[str] = Header(default=None)):
    _check_auth(x_api_key)
    if not DHAN_CONFIGURED:
        raise HTTPException(status_code=503, detail="Dhan not configured")
    data = get_positions()
    return {"status": "ok", "count": len(data), "positions": data,
            "ist_time": now_ist().strftime("%H:%M:%S")}


@app.get("/orders")
def orders(x_api_key: Optional[str] = Header(default=None)):
    _check_auth(x_api_key)
    if not DHAN_CONFIGURED:
        raise HTTPException(status_code=503, detail="Dhan not configured")
    data = get_all_orders()
    return {"status": "ok", "count": len(data), "orders": data}


@app.get("/portfolio")
def portfolio(x_api_key: Optional[str] = Header(default=None)):
    _check_auth(x_api_key)
    if not DHAN_CONFIGURED:
        raise HTTPException(status_code=503, detail="Dhan not configured")
    return get_portfolio_summary()


@app.post("/squareoff")
def squareoff(x_api_key: Optional[str] = Header(default=None)):
    """Close all open positions at market price."""
    _check_auth(x_api_key)
    if not DHAN_CONFIGURED:
        raise HTTPException(status_code=503, detail="Dhan not configured")
    result = square_off_all()
    if TELEGRAM_TOKEN:
        send_telegram(
            f"🔴 <b>Square Off All</b>\n"
            f"Closed: {result.get('closed', 0)} positions\n"
            f"Failed: {result.get('failed', 0)}\n"
            f"Time: {now_ist().strftime('%H:%M IST')}"
        )
    return result


@app.post("/cancel")
def cancel(x_api_key: Optional[str] = Header(default=None)):
    """🚨 Emergency — cancel ALL pending orders."""
    _check_auth(x_api_key)
    if not DHAN_CONFIGURED:
        raise HTTPException(status_code=503, detail="Dhan not configured")
    ok = cancel_all_orders()
    if TELEGRAM_TOKEN:
        send_telegram("🚨 <b>EMERGENCY</b> — All orders cancelled via API")
    return {"status": "ok" if ok else "failed",
            "ist_time": now_ist().strftime("%H:%M:%S")}


# ═══════════════════════════════════════════════════════════════
#  BACKTEST ROUTES
# ═══════════════════════════════════════════════════════════════

@app.post("/run")
def run_endpoint(
    body:      RunRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    _check_auth(x_api_key)
    t0 = time.time()

    if body.symbol:
        sym = body.symbol if body.symbol.endswith(".NS") else f"{body.symbol}.NS"
        if sym not in NSE_ASSETS:
            raise HTTPException(status_code=404, detail=f"Symbol '{sym}' not found.")
        meta    = NSE_ASSETS[sym]
        df      = (fetch_live_data(sym) if body.live_data
                   else generate_ohlcv(start_price=meta["price"], vol=meta["vol"]))
        results = run_sweep(df, strategy_filter=body.strategy,
                            symbol=meta["label"], verbose=False)
        label   = sym
    else:
        results = run_sweep_all_symbols(
            live_data=body.live_data, strategy_filter=body.strategy)
        label = "ALL_NSE_40"

    if not results:
        raise HTTPException(status_code=422, detail="No valid results")

    summary = strategy_summary(results)
    top     = [{k: v for k, v in r.items()
                if k not in ("equity_curve", "last_trades")}
               for r in results[:body.top_n]]
    best    = top[0]
    passed  = sum(1 for r in results if r["score"] > SCORE_THRESHOLD)

    if body.save_supabase:
        try: save_to_supabase(results, summary)
        except Exception as e: log.warning(f"Supabase: {e}")

    if body.notify_telegram:
        try: notify_top_results(results, summary)
        except Exception as e: log.warning(f"Telegram: {e}")

    return {
        "status":           "ok",
        "symbol":           label,
        "elapsed_sec":      round(time.time() - t0, 2),
        "total_tested":     len(results),
        "passed":           passed,
        "best_score":       best["score"],
        "strategy_summary": summary,
        "top_results":      top,
        "deployed_params":  best,
    }


@app.get("/backtest/{symbol}")
def quick_backtest(
    symbol:    str,
    strategy:  str   = "orb_ema",
    live:      bool  = False,
    x_api_key: Optional[str] = Header(default=None),
):
    _check_auth(x_api_key)
    key = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    if key not in NSE_ASSETS:
        raise HTTPException(status_code=404, detail=f"Unknown: {key}")
    meta    = NSE_ASSETS[key]
    df      = (fetch_live_data(key) if live
               else generate_ohlcv(start_price=meta["price"], vol=meta["vol"]))
    results = run_sweep(df, strategy_filter=strategy,
                        symbol=meta["label"], verbose=False)
    if not results:
        raise HTTPException(status_code=422, detail="No valid results")
    top = {k: v for k, v in results[0].items()
           if k not in ("equity_curve", "last_trades")}
    return {"status": "ok", "symbol": key, "sector": meta["sector"], "best": top}


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM TEST
# ═══════════════════════════════════════════════════════════════

@app.get("/telegram/test")
def telegram_test(x_api_key: Optional[str] = Header(default=None)):
    _check_auth(x_api_key)
    ts = now_ist().strftime("%d %b %Y  %H:%M IST")
    ok = send_telegram(
        f"✅ <b>AlgoTrade Pro v4.0</b> is live!\n"
        f"<i>{ts}</i>\n\n"
        f"Stocks  : {len(NSE_ASSETS)}\n"
        f"Dhan    : {'✅ Ready' if DHAN_CONFIGURED else '❌ Not configured'}\n"
        f"Market  : {'🟢 Open' if is_nse_session() else '🔴 Closed'}\n"
        f"Env     : {ENVIRONMENT}\n\n"
        f"<b>Workflow:</b>\n"
        f"TV → Webhook → Groq → Risk → Render → Dhan → Telegram"
    )
    return {"telegram_sent": ok}


# ═══════════════════════════════════════════════════════════════
#  ERROR HANDLER
# ═══════════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(f"Error on {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": str(exc)},
    )
