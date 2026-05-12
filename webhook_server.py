"""
╔══════════════════════════════════════════════════════════════╗
║     AlgoTrade Webhook Server  v2.0  — FastAPI               ║
║     Receives TradingView/Activepieces webhooks              ║
║     Saves to Supabase · Sends Telegram · Backtest API      ║
╚══════════════════════════════════════════════════════════════╝

Usage:
    uvicorn webhook_server:app --host 0.0.0.0 --port 8080

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

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import requests

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHATID = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("webhook")

app = FastAPI(
    title="AlgoTrade API",
    description="Webhook receiver for NSE trading signals",
    version="2.0"
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

class BacktestRequest(BaseModel):
    """Request to run backtest on a symbol"""
    symbol: str
    category: Optional[str] = None
    days: int = 60
    strategy: Optional[str] = "all"

class SignalResponse(BaseModel):
    """Response after processing signal"""
    status: str
    signal_id: Optional[int] = None
    symbol: str
    decision: str
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
        return resp.status_code in (200, 201)
    except Exception as e:
        log.error(f"Supabase save failed: {e}")
        return False

def get_stock_universe(category: Optional[str] = None) -> List[dict]:
    """Fetch stocks from stock_universe table"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    try:
        url = f"{SUPABASE_URL}/rest/v1/stock_universe"
        if category:
            url += f"?category=eq.{category}"

        resp = requests.get(url, headers=supabase_headers(), timeout=15)
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
            json={"chat_id": TELEGRAM_CHATID, "text": message, "parse_mode": "Markdown"},
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False

def format_telegram_message(data: dict, decision: str, confidence: str) -> str:
    """Format rich Telegram message"""
    emoji = "🟢" if decision == "approved" else "🟡" if decision == "approved_caution" else "🔴"
    conf_emoji = "🔥" if confidence == "high" else "⚡" if confidence == "medium" else "❄️"

    return f"""{emoji} *AlgoTrade Signal*

📊 *Trade Setup*
*Stock:* {data['symbol']}
*Signal:* {data['signal'].upper()}
*Price:* ₹{data['price']}

✅ *Decision:* {decision.upper()}
*Confidence:* {conf_emoji} {confidence}

📝 *Analysis*
*Reason:* {data.get('reason', 'N/A')}
*AI Insight:* {data.get('insight', 'N/A')}

🎯 *Levels*
*Entry:* ₹{data['price']}
*Stop Loss:* ₹{data['sl']}
*Target:* ₹{data['tp']}

📉 *Indicators*
*ADX:* {data.get('adx', 'N/A')} | *RSI:* {data.get('rsi', 'N/A')}
*Volume:* {data.get('volume_ratio', 'N/A')}x | *EMA:* {data.get('ema_setup', 'N/A')}
*Supertrend:* {data.get('supertrend', 'N/A')} | *MACD:* {data.get('macd', 'N/A')}

⏰ *Time:* {datetime.now().strftime('%H:%M:%S')} IST
"""

# ═══════════════════════════════════════════════════════════════
#  DECISION ENGINE (Simple AI Logic)
# ═══════════════════════════════════════════════════════════════

def evaluate_signal(data: dict) -> tuple:
    """
    Evaluate signal quality
    Returns: (decision, confidence, score)
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
        return "approved", "high", score
    elif score >= 0.50:
        return "approved_caution", "medium", score
    else:
        return "rejected", "low", score

# ═══════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    """Health check"""
    return {
        "status": "AlgoTrade Server Running",
        "version": "2.0",
        "time": datetime.now().isoformat(),
        "supabase_connected": bool(SUPABASE_URL and SUPABASE_KEY),
        "telegram_connected": bool(TELEGRAM_TOKEN and TELEGRAM_CHATID)
    }

@app.get("/health")
async def health():
    """Simple health check"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@app.post("/webhook", response_model=SignalResponse)
async def receive_webhook(signal: TradingSignal, background_tasks: BackgroundTasks):
    """
    Receive trading signal from TradingView/Activepieces
    """
    log.info(f"Received signal: {signal.symbol} @ {signal.price} ({signal.signal})")

    # Evaluate signal
    data = signal.dict()
    decision, confidence, score = evaluate_signal(data)

    # Prepare database record
    db_record = {
        "symbol": signal.symbol,
        "category": data.get('category', 'Unknown'),
        "price": signal.price,
        "signal_type": signal.signal,
        "decision": decision,
        "confidence": confidence,
        "score": score,
        "entry_price": signal.price,
        "stop_loss": signal.sl,
        "target": signal.tp,
        "rr_ratio": round(abs(signal.tp - signal.price) / abs(signal.price - signal.sl), 2),
        "reason": signal.reason,
        "ai_insight": signal.insight,
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

    # Save to Supabase (async)
    supabase_ok = save_signal_to_supabase(db_record)

    # Send Telegram notification (async)
    telegram_msg = format_telegram_message(data, decision, confidence)
    telegram_ok = send_telegram(telegram_msg)

    if telegram_ok:
        db_record["telegram_sent"] = True

    log.info(f"Signal processed: {decision} (score: {score:.2f})")

    return SignalResponse(
        status="success",
        symbol=signal.symbol,
        decision=decision,
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
            return {"count": len(resp.json()), "signals": resp.json()}
        raise HTTPException(500, f"Supabase error: {resp.status_code}")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/universe")
async def get_universe(category: Optional[str] = None):
    """Get stock universe from database"""
    stocks = get_stock_universe(category)
    return {"count": len(stocks), "stocks": stocks}

@app.post("/backtest")
async def run_backtest_api(request: BacktestRequest):
    """
    Run backtest on a symbol (integrates with backtest_engine.py)
    """
    # This would call your backtest engine
    # For now, return placeholder
    return {
        "symbol": request.symbol,
        "status": "backtest_queued",
        "message": "Connect backtest_engine.py for full implementation",
        "params": request.dict()
    }

@app.get("/stats")
async def get_stats():
    """Get trading statistics"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {"error": "Supabase not configured"}

    try:
        # Get today's signals
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
        rejected = sum(1 for s in signals if s.get('decision') == 'rejected')

        return {
            "today_signals": len(signals),
            "approved": approved,
            "rejected": rejected,
            "win_rate": approved / len(signals) * 100 if signals else 0
        }
    except Exception as e:
        return {"error": str(e)}

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
