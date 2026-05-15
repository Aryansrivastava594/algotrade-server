"""
╔══════════════════════════════════════════════════════════════╗
║   AlgoTrade Pro — Dhan Broker Execution Engine  v2.0        ║
║   Full order management for 40 NSE stocks                   ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import logging
from typing import Optional

log = logging.getLogger("dhan")

DHAN_CLIENT_ID    = os.getenv("DHAN_CLIENT_ID",    "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
DHAN_CONFIGURED   = bool(DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN)

# ═══════════════════════════════════════════════════════════════
#  NSE SYMBOL → DHAN SECURITY ID
#  Full list: https://api.dhan.co/v2/instruments
# ═══════════════════════════════════════════════════════════════

SYMBOL_MAP = {
    # ── Large Cap ──────────────────────────────────────────────
    "IDBI":          "1166",
    "BAJAJHFL":      "5258",
    "NHPC":          "13511",
    "IOB":           "4649",
    "SUZLON":        "3049",
    "GMRINFRA":      "3914",
    "NMDC":          "15332",
    "UCOBANK":       "4659",
    "MAHABANK":      "4718",
    "CENTRALBK":     "1180",
    # ── Infra ─────────────────────────────────────────────────
    "SJVN":          "25415",
    "NBCC":          "532955",
    "IRB":           "14977",
    "INOXWIND":      "539083",
    "RPOWER":        "532939",
    "RINFRA":        "500390",
    "SONAL":         "539082",
    "GMRAIRPORTS":   "543066",
    "PATELENG":      "531120",
    "OMINFRA":       "533138",
    # ── Finance ───────────────────────────────────────────────
    "IDFCFIRSTB":    "539437",
    "YESBANK":       "532648",
    "IFCI":          "500106",
    "MSUMI":         "543425",
    "SBFC":          "543959",
    "DOLATALGO":     "526881",
    "MASTERTRUST":   "511768",
    "UJJIVANSFB":    "542904",
    "NIVABUPA":      "543415",
    "SHRIRAMPRP":    "543258",
    # ── Industrial ────────────────────────────────────────────
    "TRIDENT":       "521064",
    "TTML":          "532371",
    "NMDCSTEEL":     "543732",
    "MOREPEN":       "500288",
    "ANDHRASUGAR":   "590062",
    "ASIANGRN":      "532888",
    "FILATEX":       "526227",
    "BALMERLAWR":    "523319",
    "SPIC":          "500405",
    "TNPETRO":       "500777",
    # ── Extra stocks ──────────────────────────────────────────
    "IRFC":          "543257",
    "RVNL":          "542649",
    "RAILVIKAS":     "542649",
    "HFCL":          "500183",
    "TATAPOWER":     "500400",
    "ADANIPOWER":    "533096",
}

# ═══════════════════════════════════════════════════════════════
#  QUANTITY MAP — based on price range
# ═══════════════════════════════════════════════════════════════

QTY_MAP = {
    # Very low price → high qty
    "YESBANK":    500,
    "RPOWER":     500,
    "UCOBANK":    400,
    "IOB":        400,
    "IFCI":       400,
    "SUZLON":     300,
    "MAHABANK":   300,
    "CENTRALBK":  300,
    "TRIDENT":    300,
    # Medium price
    "IDFCFIRSTB": 200,
    "NHPC":       200,
    "SJVN":       200,
    "UJJIVANSFB": 200,
    "TTML":       200,
    "IDBI":       200,
    "NBCC":       150,
    "IRB":        150,
    "INOXWIND":   100,
    # Default
    "DEFAULT":    100,
}

# ═══════════════════════════════════════════════════════════════
#  ORDER TYPE CONSTANTS
# ═══════════════════════════════════════════════════════════════

ORDER_TYPES = {
    "MARKET":    "MARKET",
    "LIMIT":     "LIMIT",
    "SL":        "STOP_LOSS",
    "SL_MARKET": "STOP_LOSS_MARKET",
}


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def get_dhan_client():
    if not DHAN_CONFIGURED:
        raise ValueError(
            "Dhan not configured. "
            "Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in Render environment."
        )
    from dhanhq import dhanhq
    return dhanhq(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN)


def get_security_id(stock: str) -> str:
    symbol = stock.upper().replace(".NS", "").replace("NSE:", "")
    sec_id = SYMBOL_MAP.get(symbol)
    if not sec_id:
        raise ValueError(
            f"Security ID not found for '{symbol}'. "
            f"Add it to SYMBOL_MAP in dhan_executor.py"
        )
    return sec_id


def get_quantity(stock: str, custom_qty: Optional[int] = None) -> int:
    if custom_qty and custom_qty > 0:
        return custom_qty
    symbol = stock.upper().replace(".NS", "")
    return QTY_MAP.get(symbol, QTY_MAP["DEFAULT"])


def calculate_quantity_by_risk(
    price: float,
    sl: float,
    capital: float = 100000,
    risk_pct: float = 0.02,
) -> int:
    """Calculate qty based on risk % of capital."""
    risk_amount  = capital * risk_pct
    sl_distance  = abs(price - sl)
    if sl_distance == 0:
        return QTY_MAP["DEFAULT"]
    qty = int(risk_amount / sl_distance)
    return max(qty, 1)


# ═══════════════════════════════════════════════════════════════
#  PLACE ORDER — Main Function
# ═══════════════════════════════════════════════════════════════

def place_order(
    stock:      str,
    signal:     str,
    price:      float,
    sl:         Optional[float] = None,
    tp:         Optional[float] = None,
    quantity:   Optional[int]   = None,
    order_type: str             = "MARKET",
) -> dict:
    """
    Place BUY or SELL order on Dhan.

    Returns dict with:
      status:   "success" or "failed"
      order_id: Dhan order ID
      error:    error message if failed
    """
    try:
        from dhanhq import dhanhq

        dhan      = get_dhan_client()
        sec_id    = get_security_id(stock)
        qty       = get_quantity(stock, quantity)
        direction = dhanhq.BUY if signal.upper() == "BUY" else dhanhq.SELL

        log.info(
            f"Placing {signal} | {stock} | "
            f"qty:{qty} | ₹{price} | "
            f"SL:₹{sl} | TP:₹{tp} | {order_type}"
        )

        # ── Main Order ──────────────────────────────────────────
        if order_type == "MARKET":
            response = dhan.place_order(
                security_id      = sec_id,
                exchange_segment = dhanhq.NSE,
                transaction_type = direction,
                quantity         = qty,
                order_type       = dhanhq.MARKET,
                product_type     = dhanhq.INTRA,
                price            = 0,
            )
        else:
            response = dhan.place_order(
                security_id      = sec_id,
                exchange_segment = dhanhq.NSE,
                transaction_type = direction,
                quantity         = qty,
                order_type       = dhanhq.LIMIT,
                product_type     = dhanhq.INTRA,
                price            = price,
            )

        order_id = response.get("data", {}).get("orderId", "unknown")
        log.info(f"✅ Main order placed | orderId:{order_id}")

        # ── SL Order ────────────────────────────────────────────
        sl_order_id = None
        if sl:
            try:
                sl_direction = dhanhq.SELL if signal.upper() == "BUY" else dhanhq.BUY
                sl_response  = dhan.place_order(
                    security_id      = sec_id,
                    exchange_segment = dhanhq.NSE,
                    transaction_type = sl_direction,
                    quantity         = qty,
                    order_type       = dhanhq.SL_MARKET,
                    product_type     = dhanhq.INTRA,
                    price            = 0,
                    trigger_price    = sl,
                )
                sl_order_id = sl_response.get("data", {}).get("orderId")
                log.info(f"✅ SL order placed | ₹{sl} | orderId:{sl_order_id}")
            except Exception as e:
                log.warning(f"SL order failed: {e}")

        # ── Calculate R:R ────────────────────────────────────────
        rr = 0
        if sl and tp and price:
            risk   = abs(price - sl)
            reward = abs(tp - price)
            rr     = round(reward / risk, 2) if risk > 0 else 0

        return {
            "status":       "success",
            "order_id":     order_id,
            "sl_order_id":  sl_order_id,
            "stock":        stock,
            "signal":       signal,
            "qty":          qty,
            "price":        price,
            "sl":           sl,
            "tp":           tp,
            "rr_ratio":     rr,
            "order_value":  round(qty * price, 2),
        }

    except ValueError as e:
        log.error(f"Config error: {e}")
        return {
            "status": "failed",
            "error":  str(e),
            "stock":  stock,
            "signal": signal,
        }
    except Exception as e:
        log.error(f"Order failed for {stock}: {e}")
        return {
            "status": "failed",
            "error":  str(e),
            "stock":  stock,
            "signal": signal,
        }


# ═══════════════════════════════════════════════════════════════
#  GET POSITIONS
# ═══════════════════════════════════════════════════════════════

def get_positions() -> list:
    try:
        dhan   = get_dhan_client()
        result = dhan.get_positions()
        data   = result.get("data", [])
        log.info(f"Open positions: {len(data)}")
        return data
    except Exception as e:
        log.error(f"Get positions failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
#  GET ALL ORDERS
# ═══════════════════════════════════════════════════════════════

def get_all_orders() -> list:
    try:
        dhan   = get_dhan_client()
        result = dhan.get_order_list()
        data   = result.get("data", [])
        log.info(f"Total orders today: {len(data)}")
        return data
    except Exception as e:
        log.error(f"Get orders failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
#  GET ORDER STATUS
# ═══════════════════════════════════════════════════════════════

def get_order_status(order_id: str) -> dict:
    try:
        dhan   = get_dhan_client()
        result = dhan.get_order_by_id(order_id)
        return result.get("data", {})
    except Exception as e:
        log.error(f"Order status failed: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  GET PORTFOLIO SUMMARY
# ═══════════════════════════════════════════════════════════════

def get_portfolio_summary() -> dict:
    """Get summary of all open positions with P&L."""
    try:
        positions = get_positions()
        orders    = get_all_orders()

        total_pnl    = sum(float(p.get("unrealizedProfit", 0)) for p in positions)
        buy_trades   = [o for o in orders if o.get("transactionType") == "BUY"]
        sell_trades  = [o for o in orders if o.get("transactionType") == "SELL"]

        return {
            "open_positions":  len(positions),
            "total_orders":    len(orders),
            "buy_orders":      len(buy_trades),
            "sell_orders":     len(sell_trades),
            "unrealized_pnl":  round(total_pnl, 2),
            "positions":       positions,
        }
    except Exception as e:
        log.error(f"Portfolio summary failed: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  CANCEL ALL ORDERS
# ═══════════════════════════════════════════════════════════════

def cancel_all_orders() -> bool:
    """🚨 Emergency — cancel ALL pending orders."""
    try:
        dhan      = get_dhan_client()
        orders    = dhan.get_order_list()
        cancelled = 0
        for order in orders.get("data", []):
            if order.get("orderStatus") in ["PENDING", "TRANSIT"]:
                try:
                    dhan.cancel_order(order["orderId"])
                    cancelled += 1
                    log.info(f"Cancelled: {order['orderId']}")
                except Exception as e:
                    log.warning(f"Failed to cancel {order['orderId']}: {e}")
        log.info(f"Total cancelled: {cancelled}")
        return True
    except Exception as e:
        log.error(f"Cancel all failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  SQUARE OFF ALL POSITIONS
# ═══════════════════════════════════════════════════════════════

def square_off_all() -> dict:
    """Close all open intraday positions at market price."""
    try:
        from dhanhq import dhanhq
        dhan      = get_dhan_client()
        positions = get_positions()
        closed    = []
        failed    = []

        for pos in positions:
            try:
                qty       = abs(int(pos.get("netQty", 0)))
                sec_id    = pos.get("securityId")
                direction = dhanhq.SELL if pos.get("netQty", 0) > 0 else dhanhq.BUY

                if qty == 0:
                    continue

                response = dhan.place_order(
                    security_id      = sec_id,
                    exchange_segment = dhanhq.NSE,
                    transaction_type = direction,
                    quantity         = qty,
                    order_type       = dhanhq.MARKET,
                    product_type     = dhanhq.INTRA,
                    price            = 0,
                )
                order_id = response.get("data", {}).get("orderId", "unknown")
                closed.append({
                    "stock":    pos.get("tradingSymbol"),
                    "qty":      qty,
                    "order_id": order_id,
                })
                log.info(f"Squared off {pos.get('tradingSymbol')} qty:{qty}")

            except Exception as e:
                failed.append({
                    "stock": pos.get("tradingSymbol"),
                    "error": str(e),
                })
                log.warning(f"Square off failed for {pos.get('tradingSymbol')}: {e}")

        return {
            "status":  "ok",
            "closed":  len(closed),
            "failed":  len(failed),
            "details": closed,
            "errors":  failed,
        }

    except Exception as e:
        log.error(f"Square off all failed: {e}")
        return {"status": "failed", "error": str(e)}
