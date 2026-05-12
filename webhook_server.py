"""
╔══════════════════════════════════════════════════════════════╗
║     AlgoTrade Webhook Server  v2.1  — FastAPI               ║
║     Rendersafe · HEAD handlers · Supabase · Telegram       ║
╚══════════════════════════════════════════════════════════════╝

Usage:
    uvicorn webhook_server:app --host 0.0.0.0 --port $PORT

Environment:
    SUPABASE_URL=https://your-project.supabase.co
    SUPABASE_ANON_KEY=your-anon-key
    TELEGRAM_BOT_TOKEN=your-bot-token
    TELEGRAM_CHAT_ID=your-chat-id
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
import requests

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHATID = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("webhook")

# ═══════════════════════════════════════════════════════════════
#  FASTAPI APP
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="AlgoTrade API",
    description="Webhook receiver for NSE trading signals",
    version="2.1"
)

# ═══════════════════════════════════════════════════════════════
#  DATA MODELS
# ═══════════════════════════════════════════════════════════════

class TradingSignal(BaseModel):
    """Signal from TradingView/Activepieces"""
    symbol: str
    price: float
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_range: Optional[float] = None
    ema_setup: Optional[str] = "9 EMA above 21 EMA"
    adx: Optional[float] = None
    rsi: Optional[float] = None
    volume_ratio: Optional[float] = None
    supertrend: Optional[str] = None
    macd: Optional[str] = None
    nifty_trend: Optional[str] = None
    signal: str = Field(..., pattern="^(buy|sell)$")
    sl: float
    tp: float
    reason: Optional[str] = ""
    insight: Optional[str] = ""
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    category: Optional[str] = None

class SignalResponse(BaseModel):
    """Response after processing signal"""
    status: str
    signal_id: Optional[int] = None
    symbol: str
    decision: str
    confidence: str
    score: float
    telegram_sent: bool
    supabase_saved: bool

# ═══════════════════════════════════════════════════════════════
#  SUPABASE HELPERS
# ═══════════════════════════════════════════════════════════════

def supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

def save_signal_to_supabase(data: dict) -> bool:
    """Save signal to stock_signals table"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        log.warning("Supabase not configured")
        return False

    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/stock_signals",
            headers=supabase_headers(),
            json=data,
            timeout=15
        )
        if resp.status_code in (200, 201):
            log.info(f"Signal saved to Supabase: {data.get('symbol')}")
            return True
        else:
            log.warning(f"Supabase error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Supabase save failed: {e}")
        return False

def get_stock_universe(category: Optional[str] = None) -> List[dict]:
    """Fetch stocks from stock_universe table"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    try:
        url = f"{SUPABASE_URL}/rest/v1/stock_universe"
        params = {}
        if category:
            params["category"] = f"eq.{category}"

        resp = requests.get(url, headers=supabase_headers(), params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception as e:
        log.error(f"Fetch universe failed: {e}")
        return []

# ═══════════════════════════════════════════════════════════════
#  TELEGRAM HELPERS
# ═══════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    """Send message to Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATID:
        log.warning("Telegram not configured")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHATID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            },
            timeout=10
        )
        if resp.status_code == 200:
            log.info("Telegram message sent")
            return True
        else:
            log.warning(f"Telegram error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

def format_telegram_message(data: dict, decision: str, confidence: str, score: float) -> str:
    """Format rich Telegram message"""
    emoji = "🟢" if decision == "approved" else "🟡" if decision == "approved_caution" else "🔴"
    conf_emoji = "🔥" if confidence == "high" else "⚡" if confidence == "medium" else "❄️"

    # Calculate R:R
    price = float(data.get('price', 0))
    sl = float(data.get('sl', 0))
    tp = float(data.get('tp', 0))
    risk = abs(price - sl) if price and sl else 0
    reward = abs(tp - price) if tp and price else 0
    rr = round(reward / risk, 2) if risk > 0 else 0

    return f"""{emoji} *AlgoTrade Signal*

📊 *Trade Setup*
*Stock:* {data.get('symbol', 'N/A')}
*Category:* {data.get('category', 'N/A')}
*Signal:* {data.get('signal', 'N/A').upper()}
*Price:* ₹{price:.2f}

✅ *Decision:* {decision.upper().replace('_', ' ')}
*Confidence:* {conf_emoji} {confidence.upper()}
*Score:* {score:.2f}/1.00
*R:R Ratio:* 1:{rr}

📝 *Analysis*
*Reason:* {data.get('reason', 'N/A')}
*AI Insight:* {data.get('insight', 'N/A')}

🎯 *Levels*
*Entry:* ₹{price:.2f}
*Stop Loss:* ₹{sl:.2f}
*Target:* ₹{tp:.2f}

📉 *Indicators*
*ADX:* {data.get('adx', 'N/A')} | *RSI:* {data.get('rsi', 'N/A')}
*Volume:* {data.get('volume_ratio', 'N/A')}x | *EMA:* {data.get('ema_setup', 'N/A')}
*Supertrend:* {data.get('supertrend', 'N/A')} | *MACD:* {data.get('macd', 'N/A')}
*Nifty:* {data.get('nifty_trend', 'N/A')}

⏰ *Time:* {datetime.now().strftime('%H:%M:%S')} IST
"""

# ═══════════════════════════════════════════════════════════════
#  SIGNAL EVALUATION ENGINE
# ═══════════════════════════════════════════════════════════════

def evaluate_signal(data: dict) -> tuple:
    """
    Evaluate signal quality
    Returns: (decision, confidence, score, checks)
    """
    score = 0.0
    checks = []

    # Check ADX
    adx = data.get('adx', 0)
    if adx and adx > 25:
        score += 0.25
        checks.append("ADX strong")
    elif adx and adx > 20:
        score += 0.15
        checks.append("ADX moderate")

    # Check Volume
    vol = data.get('volume_ratio', 0)
    if vol and vol > 1.5:
        score += 0.25
        checks.append("Volume spike")
    elif vol and vol > 1.2:
        score += 0.15
        checks.append("Volume elevated")

    # Check RSI
    rsi = data.get('rsi', 50)
    signal = data.get('signal', 'buy')
    if signal == 'buy' and rsi and rsi > 55:
        score += 0.20
        checks.append("RSI bullish")
    elif signal == 'sell' and rsi and rsi < 45:
        score += 0.20
        checks.append("RSI bearish")

    # Check Supertrend
    st = data.get('supertrend', '')
    if signal == 'buy' and st == 'green':
        score += 0.15
        checks.append("Supertrend bullish")
    elif signal == 'sell' and st == 'red':
        score += 0.15
        checks.append("Supertrend bearish")

    # Check MACD
    macd = data.get('macd', '')
    if signal == 'buy' and macd == 'bullish':
        score += 0.15
        checks.append("MACD bullish")
    elif signal == 'sell' and macd == 'bearish':
        score += 0.15
        checks.append("MACD bearish")

    # Determine decision
    if score >= 0.70:
        return "approved", "high", score, checks
    elif score >= 0.50:
        return "approved_caution", "medium", score, checks
    else:
        return "rejected", "low", score, checks

# ═══════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """Health check with full status"""
    return {
        "status": "AlgoTrade Server Running",
        "version": "2.1",
        "time": datetime.now().isoformat(),
        "supabase_connected": bool(SUPABASE_URL and SUPABASE_KEY),
        "telegram_connected": bool(TELEGRAM_TOKEN and TELEGRAM_CHATID)
    }

@app.head("/")
async def head_root():
    """HEAD handler for Render health checks"""
    return PlainTextResponse("", status_code=200)

@app.get("/health")
async def health():
    """Simple health check"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "algotrade-webhook"
    }

@app.head("/health")
async def head_health():
    """HEAD handler for health endpoint"""
    return PlainTextResponse("", status_code=200)

@app.post("/webhook", response_model=SignalResponse)
async def receive_webhook(signal: TradingSignal, background_tasks: BackgroundTasks):
    """
    Receive trading signal from TradingView/Activepieces
    """
    log.info(f"Received signal: {signal.symbol} @ {signal.price} ({signal.signal})")

    # Get category from stock_universe if not provided
    category = signal.category
    if not category:
        universe = get_stock_universe()
        for stock in universe:
            if stock.get('symbol') == signal.symbol:
                category = stock.get('category')
                break

    # Evaluate signal
    data = signal.dict()
    data['category'] = category or 'Unknown'
    decision, confidence, score, checks = evaluate_signal(data)

    log.info(f"Signal evaluation: {decision} (score: {score:.2f}, checks: {checks})")

    # Prepare database record
    db_record = {
        "symbol": signal.symbol,
        "category": category or 'Unknown',
        "price": float(signal.price),
        "signal_type": signal.signal,
        "decision": decision,
        "confidence": confidence,
        "score": round(score, 4),
        "entry_price": float(signal.price),
        "stop_loss": float(signal.sl),
        "target": float(signal.tp),
        "rr_ratio": round(abs(signal.tp - signal.price) / abs(signal.price - signal.sl), 2) if abs(signal.price - signal.sl) > 0 else 0,
        "reason": signal.reason or f"ORB breakout: {', '.join(checks)}",
        "ai_insight": signal.insight or f"Score: {score:.2f}/1.00. Checks passed: {', '.join(checks)}",
        "adx": signal.adx,
        "rsi": signal.rsi,
        "volume_ratio": signal.volume_ratio,
        "ema_setup": signal.ema_setup,
        "supertrend": signal.supertrend,
        "macd": signal.macd,
        "nifty_trend": signal.nifty_trend,
        "market_status": "Open",
        "telegram_sent": False,
        "executed": False
    }

    # Save to Supabase
    supabase_ok = save_signal_to_supabase(db_record)

    # Send Telegram notification
    telegram_msg = format_telegram_message(data, decision, confidence, score)
    telegram_ok = send_telegram(telegram_msg)

    if telegram_ok:
        db_record["telegram_sent"] = True
        # Update Supabase with telegram status
        try:
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/stock_signals?symbol=eq.{signal.symbol}&order=timestamp.desc&limit=1",
                headers=supabase_headers(),
                json={"telegram_sent": True},
                timeout=10
            )
        except:
            pass

    log.info(f"Signal processed: {decision} | Telegram: {telegram_ok} | Supabase: {supabase_ok}")

    return SignalResponse(
        status="success",
        symbol=signal.symbol,
        decision=decision,
        confidence=confidence,
        score=score,
        telegram_sent=telegram_ok,
        supabase_saved=supabase_ok
    )

@app.get("/signals")
async def get_signals(limit: int = 20, symbol: Optional[str] = None):
    """Get recent signals from database"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(500, "Supabase not configured")

    try:
        url = f"{SUPABASE_URL}/rest/v1/stock_signals"
        params = {"order": "timestamp.desc", "limit": limit}
        if symbol:
            params["symbol"] = f"eq.{symbol}"

        resp = requests.get(url, headers=supabase_headers(), params=params, timeout=15)
        if resp.status_code == 200:
            signals = resp.json()
            return {"count": len(signals), "signals": signals}
        raise HTTPException(500, f"Supabase error: {resp.status_code}")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/universe")
async def get_universe(category: Optional[str] = None):
    """Get stock universe from database"""
    stocks = get_stock_universe(category)
    return {"count": len(stocks), "stocks": stocks}

@app.get("/stats")
async def get_stats():
    """Get trading statistics"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"error": "Supabase not configured"}

    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = f"{SUPABASE_URL}/rest/v1/stock_signals"

        resp = requests.get(
            url,
            headers=supabase_headers(),
            params={"timestamp": f"gte.{today}T00:00:00"},
            timeout=15
        )

        signals = resp.json() if resp.status_code == 200 else []

        approved = sum(1 for s in signals if s.get('decision') == 'approved')
        caution = sum(1 for s in signals if s.get('decision') == 'approved_caution')
        rejected = sum(1 for s in signals if s.get('decision') == 'rejected')

        return {
            "today_signals": len(signals),
            "approved": approved,
            "approved_caution": caution,
            "rejected": rejected,
            "win_rate": round(approved / len(signals) * 100, 1) if signals else 0,
            "supabase_connected": True,
            "telegram_connected": bool(TELEGRAM_TOKEN and TELEGRAM_CHATID)
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/categories")
async def get_categories():
    """Get all stock categories"""
    universe = get_stock_universe()
    categories = {}
    for stock in universe:
        cat = stock.get('category', 'Unknown')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(stock)

    return {
        "categories": list(categories.keys()),
        "counts": {k: len(v) for k, v in categories.items()},
        "stocks": categories
    }

# ═══════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(f"Global error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": str(exc)}
    )

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    log.info(f"Starting server on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
