"""
╔══════════════════════════════════════════════════════════════╗
║   AlgoTrade Pro — Dhan Broker Execution Engine  v3.0        ║
║   Compatible with dhanhq == 2.2.0                           ║
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
    # ── Extra ─────────────────────────────────────────────────
    "IRFC":          "543257",
    "RVNL":          "542649",
    "HFCL":          "500183",
    "TATAPOWER":     "500400",
    "ADANIPOWER":    "533096",
}

# ═══════════════════════════════════════════════════════════════
#  QUANTITY MAP
# ═══════════════════════════════════════════════════════════════

QTY_MAP = {
    "YESBANK":    500,
    "RPOWER":     500,
    "UCOBANK":    400,
    "IOB":        400,
    "IFCI":       400,
    "SUZLON":     300,
    "MAHABANK":   300,
    "CENTRALBK":  300,
    "TRIDENT":    300,
    "IDFCFIRSTB": 200,
    "NHPC":       200,
    "SJVN":       200,
    "UJJIVANSFB": 200,
    "TTML":       200,
    "IDBI":       200,
    "NBCC":       150,
    "IRB":        150,
    "DEFAULT":    100,
}


# ═══════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════

def get_dhan_client():
    """Get dhanhq v2.2.0 client instance."""
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


# ═══════════════════════════════════════════════════════════════
#  PLACE ORDER — v2.2.0 syntax
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
    Place BUY or SELL order on Dhan using dhanhq v2.2.0.
    """
    try:
        from dhanhq import dhanhq

        dhan      = get_dhan_client()
        sec_id    = get_security_id(stock)
        qty       = get_quantity(stock, quantity)

        # v2.2.0 constants
        direction  = dhanhq.BUY  if signal.upper() == "BUY" else dhanhq.SELL
        o_type     = dhanhq.MARKET if order_type == "MARKET" else dhanhq.LIMIT
        price_val  = 0 if order_type == "MARKET" else price

        log.info(
            f"Placing {signal} | {stock} | "
            f"qty:{qty} | ₹{price} | "
            f"SL:₹{sl} | TP:₹{tp} | {order_type}"
        )

        # ── Main Order ──────────────────────────────────────────
        response = dhan.place_order(
            security_id      = sec_id,
            exchange_segment = dhanhq.NSE,
            transaction_type = direction,
            quantity         = qty,
            order_type       = o_type,
            product_type     = dhanhq.INTRA,
            price            = price_val,
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
                    order_type       = dhanhq.SLM,
                    product_type     = dhanhq.INTRA,
                    price            = 0,
                    trigger_price    = sl,
                )
                sl_order_id = sl_response.get("data", {}).get("orderId")
                log.info(f"✅ SL order placed | ₹{sl} | orderId:{sl_order_id}")
            except Exception as e:
                log.warning(f"SL order failed: {e}")

        # ── Calculate R:R ────────────────────────────────────────
        rr = 0.0
        if sl and tp and price:
            risk   = abs(price - sl)
            reward = abs(tp - price)
            rr     = round(reward / risk, 2) if risk > 0 else 0.0

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
#  GET POSITIONS — v2.2.0
# ═══════════════════════════════════════════════════════════════

def get_positions() -> list:
    try:
        dhan   = get_dhan_client()
        result = dhan.get_positions()
        data   = result.get("data", [])
        log.info(f"Open positions: {len(data)}")
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error(f"Get positions failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
#  GET ALL ORDERS — v2.2.0
# ═══════════════════════════════════════════════════════════════

def get_all_orders() -> list:
    try:
        dhan   = get_dhan_client()
        result = dhan.get_order_list()
        data   = result.get("data", [])
        log.info(f"Total orders today: {len(data)}")
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error(f"Get orders failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
#  GET ORDER STATUS — v2.2.0
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
#  GET FUND LIMITS — v2.2.0
# ═══════════════════════════════════════════════════════════════

def get_fund_limits() -> dict:
    try:
        dhan   = get_dhan_client()
        result = dhan.get_fund_limits()
        return result.get("data", {})
    except Exception as e:
        log.error(f"Get fund limits failed: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  GET PORTFOLIO SUMMARY — v2.2.0
# ═══════════════════════════════════════════════════════════════

def get_portfolio_summary() -> dict:
    try:
        positions  = get_positions()
        orders     = get_all_orders()
        funds      = get_fund_limits()

        total_pnl  = sum(
            float(p.get("unrealizedProfit", 0))
            for p in positions
        )
        buy_orders  = [o for o in orders if o.get("transactionType") == "BUY"]
        sell_orders = [o for o in orders if o.get("transactionType") == "SELL"]

        return {
            "open_positions":  len(positions),
            "total_orders":    len(orders),
            "buy_orders":      len(buy_orders),
            "sell_orders":     len(sell_orders),
            "unrealized_pnl":  round(total_pnl, 2),
            "available_funds": funds.get("availabelBalance", 0),
            "positions":       positions,
        }
    except Exception as e:
        log.error(f"Portfolio summary failed: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  CANCEL ALL ORDERS — v2.2.0
# ═══════════════════════════════════════════════════════════════

def cancel_all_orders() -> bool:
    """🚨 Emergency — cancel ALL pending orders."""
    try:
        dhan      = get_dhan_client()
        orders    = get_all_orders()
        cancelled = 0

        for order in orders:
            status   = order.get("orderStatus", "")
            order_id = order.get("orderId")
            if status in ["PENDING", "TRANSIT", "PART_TRADED"] and order_id:
                try:
                    dhan.cancel_order(order_id)
                    cancelled += 1
                    log.info(f"Cancelled: {order_id}")
                except Exception as e:
                    log.warning(f"Failed to cancel {order_id}: {e}")

        log.info(f"Total cancelled: {cancelled}")
        return True
    except Exception as e:
        log.error(f"Cancel all failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  SQUARE OFF ALL POSITIONS — v2.2.0
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
                net_qty = int(pos.get("netQty", 0))
                qty     = abs(net_qty)
                sec_id  = str(pos.get("securityId", ""))

                if qty == 0 or not sec_id:
                    continue

                # Opposite direction to close
                direction = dhanhq.SELL if net_qty > 0 else dhanhq.BUY

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
