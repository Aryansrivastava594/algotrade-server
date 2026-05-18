"""
╔══════════════════════════════════════════════════════════════╗
║   AlgoTrade Pro — Dhan Token Auto-Refresh                   ║
║   Runs daily at 8:00 AM IST                                 ║
║   Auto generates new Access Token                           ║
║   Auto updates Render environment variable                  ║
╚══════════════════════════════════════════════════════════════╝

How it works:
1. Logs into Dhan API using your credentials
2. Generates new Access Token
3. Updates Render environment variable automatically
4. Sends Telegram confirmation

Run manually:
    python token_refresh.py

Run as scheduled job (add to main.py scheduler):
    schedule.every().day.at("08:00").do(refresh_dhan_token)

Deploy on Render as separate cron job:
    Start Command: python token_refresh.py --daemon
"""

import os
import sys
import time
import logging
import schedule
import argparse
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt = "%H:%M:%S",
    handlers= [logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("token_refresh")

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

DHAN_CLIENT_ID      = os.getenv("DHAN_CLIENT_ID",      "1111481425")
DHAN_API_KEY        = os.getenv("DHAN_API_KEY",        "")   # from API Key section
DHAN_PARTNER_ID     = os.getenv("DHAN_PARTNER_ID",     "")   # optional
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN",  "")
TELEGRAM_CHATID     = os.getenv("TELEGRAM_CHAT_ID",    "")
RENDER_API_KEY      = os.getenv("RENDER_API_KEY",      "")   # from Render dashboard
RENDER_SERVICE_ID   = os.getenv("RENDER_SERVICE_ID",   "")   # your service ID
APP_NAME            = os.getenv("DHAN_APP_NAME",        "AlgoTrade Pro")


# ═══════════════════════════════════════════════════════════════
#  IST TIME
# ═══════════════════════════════════════════════════════════════

def now_ist() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM NOTIFICATION
# ═══════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHATID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r   = requests.post(url, json={
            "chat_id":    TELEGRAM_CHATID,
            "text":       message,
            "parse_mode": "HTML"
        }, timeout=10)
        return r.ok
    except Exception as e:
        log.warning(f"Telegram failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  METHOD 1 — Dhan Token API (Direct)
# ═══════════════════════════════════════════════════════════════

def generate_token_via_api() -> str:
    """
    Generate new Access Token using Dhan Token API.
    Uses your API Key to get a fresh Access Token.
    """
    if not DHAN_API_KEY or not DHAN_CLIENT_ID:
        log.error("DHAN_API_KEY or DHAN_CLIENT_ID not set")
        return None

    try:
        url = "https://api.dhan.co/v2/token"
        headers = {
            "Content-Type": "application/json",
            "access-token": DHAN_API_KEY,
        }
        payload = {
            "clientId": DHAN_CLIENT_ID,
        }

        log.info("Requesting new Dhan Access Token...")
        r = requests.post(url, json=payload, headers=headers, timeout=15)

        if r.ok:
            data  = r.json()
            token = data.get("accessToken") or data.get("access_token")
            if token:
                log.info(f"✅ New token generated (length: {len(token)})")
                return token
            else:
                log.error(f"Token not in response: {data}")
                return None
        else:
            log.error(f"Token API failed: {r.status_code} — {r.text[:200]}")
            return None

    except Exception as e:
        log.error(f"Token generation failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  METHOD 2 — Update Render Environment Variable
# ═══════════════════════════════════════════════════════════════

def update_render_env(new_token: str) -> bool:
    """
    Update DHAN_ACCESS_TOKEN in Render environment variables.
    Requires RENDER_API_KEY and RENDER_SERVICE_ID.

    Get RENDER_API_KEY from:
    Render Dashboard → Account Settings → API Keys → Create API Key

    Get RENDER_SERVICE_ID from:
    Render Dashboard → Your Service → Settings → Service ID
    """
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        log.warning("RENDER_API_KEY or RENDER_SERVICE_ID not set — skipping Render update")
        return False

    try:
        url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars"
        headers = {
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Content-Type":  "application/json",
        }

        # Get current env vars
        r = requests.get(url, headers=headers, timeout=15)
        if not r.ok:
            log.error(f"Failed to get Render env vars: {r.status_code}")
            return False

        env_vars = r.json()

        # Find and update DHAN_ACCESS_TOKEN
        updated = False
        for var in env_vars:
            if var.get("envVar", {}).get("key") == "DHAN_ACCESS_TOKEN":
                var_id = var.get("envVar", {}).get("id")

                patch_url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars/{var_id}"
                patch_r   = requests.put(patch_url, headers=headers, json={
                    "value": new_token
                }, timeout=15)

                if patch_r.ok:
                    log.info("✅ Render DHAN_ACCESS_TOKEN updated")
                    updated = True
                else:
                    log.error(f"Render update failed: {patch_r.status_code}")
                break

        if not updated:
            # Create new env var if not exists
            create_r = requests.post(url, headers=headers, json={
                "key":   "DHAN_ACCESS_TOKEN",
                "value": new_token,
            }, timeout=15)
            if create_r.ok:
                log.info("✅ Render DHAN_ACCESS_TOKEN created")
                updated = True

        return updated

    except Exception as e:
        log.error(f"Render update failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  METHOD 3 — Update Local .env File
# ═══════════════════════════════════════════════════════════════

def update_local_env(new_token: str) -> bool:
    """Update DHAN_ACCESS_TOKEN in local .env file."""
    try:
        env_path = ".env"
        lines    = []
        updated  = False

        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                lines = f.readlines()

        new_lines = []
        for line in lines:
            if line.startswith("DHAN_ACCESS_TOKEN="):
                new_lines.append(f"DHAN_ACCESS_TOKEN={new_token}\n")
                updated = True
            else:
                new_lines.append(line)

        if not updated:
            new_lines.append(f"DHAN_ACCESS_TOKEN={new_token}\n")

        with open(env_path, "w") as f:
            f.writelines(new_lines)

        log.info("✅ Local .env updated")
        return True

    except Exception as e:
        log.error(f"Local .env update failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  VERIFY TOKEN WORKS
# ═══════════════════════════════════════════════════════════════

def verify_token(token: str) -> bool:
    """Test if new token works by calling Dhan profile API."""
    try:
        url = "https://api.dhan.co/v2/fundlimit"
        headers = {
            "access-token": token,
            "client-id":    DHAN_CLIENT_ID,
            "Content-Type": "application/json",
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.ok:
            log.info("✅ Token verified — Dhan API responding")
            return True
        else:
            log.warning(f"Token verify failed: {r.status_code} — {r.text[:100]}")
            return False
    except Exception as e:
        log.warning(f"Token verify error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
#  MAIN REFRESH FUNCTION
# ═══════════════════════════════════════════════════════════════

def refresh_dhan_token():
    """Main function — generates and updates Dhan Access Token."""
    ts = now_ist().strftime("%d %b %Y  %H:%M IST")
    log.info("═" * 50)
    log.info(f"  Dhan Token Refresh  |  {ts}")
    log.info("═" * 50)

    # Step 1 — Generate new token
    new_token = generate_token_via_api()

    if not new_token:
        msg = (
            f"❌ <b>Dhan Token Refresh FAILED</b>\n"
            f"<i>{ts}</i>\n\n"
            f"Could not generate new Access Token.\n"
            f"Please refresh manually at:\n"
            f"https://dhanhq.co → API → Generate Access Token"
        )
        send_telegram(msg)
        log.error("Token refresh failed!")
        return False

    # Step 2 — Verify token works
    is_valid = verify_token(new_token)

    # Step 3 — Update Render environment
    render_updated = update_render_env(new_token)

    # Step 4 — Update local .env
    update_local_env(new_token)

    # Step 5 — Update in-memory environment
    os.environ["DHAN_ACCESS_TOKEN"] = new_token

    # Step 6 — Send Telegram notification
    status_msg = "✅ Token Valid" if is_valid else "⚠️ Token generated but verify failed"
    render_msg = "✅ Render Updated" if render_updated else "⚠️ Update Render manually"

    msg = (
        f"🔄 <b>Dhan Token Refreshed</b>\n"
        f"<i>{ts}</i>\n\n"
        f"Token Status : {status_msg}\n"
        f"Render       : {render_msg}\n"
        f"Token Length : {len(new_token)} chars\n"
        f"Next Refresh : Tomorrow 8:00 AM IST\n\n"
        f"<i>AlgoTrade Pro is ready to trade!</i>"
    )
    send_telegram(msg)

    log.info("Token refresh complete!")
    return True


# ═══════════════════════════════════════════════════════════════
#  MANUAL TOKEN UPDATE (if API method fails)
# ═══════════════════════════════════════════════════════════════

def manual_token_update(token: str):
    """
    Call this if auto-generation fails.
    Manually paste token from Dhan website.

    Usage:
        from token_refresh import manual_token_update
        manual_token_update("eyJhbGciOiJIUzUxMiJ9...")
    """
    if not token or len(token) < 50:
        log.error("Invalid token provided")
        return False

    log.info("Manual token update...")
    render_ok = update_render_env(token)
    local_ok  = update_local_env(token)
    os.environ["DHAN_ACCESS_TOKEN"] = token
    valid     = verify_token(token)

    ts  = now_ist().strftime("%d %b %Y  %H:%M IST")
    msg = (
        f"🔑 <b>Dhan Token Manually Updated</b>\n"
        f"<i>{ts}</i>\n\n"
        f"Valid  : {'✅' if valid else '❌'}\n"
        f"Render : {'✅' if render_ok else '⚠️ Update manually'}\n"
        f"Local  : {'✅' if local_ok else '❌'}"
    )
    send_telegram(msg)
    log.info(f"Manual update done | valid:{valid} render:{render_ok}")
    return valid


# ═══════════════════════════════════════════════════════════════
#  SCHEDULER
# ═══════════════════════════════════════════════════════════════

def start_scheduler():
    """Run token refresh daily at 8:00 AM IST."""
    log.info("Token refresh scheduler started")
    log.info("Runs every day at 08:00 AM IST")

    # Run once immediately on start
    log.info("Running initial token refresh...")
    refresh_dhan_token()

    # Schedule daily refresh
    schedule.every().day.at("02:30").do(refresh_dhan_token)
    # 02:30 UTC = 08:00 IST

    while True:
        schedule.run_pending()
        time.sleep(60)


# ═══════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Dhan Access Token Auto-Refresh"
    )
    parser.add_argument("--daemon",  action="store_true",
                        help="Run as daemon with daily scheduler")
    parser.add_argument("--once",    action="store_true",
                        help="Refresh token once and exit")
    parser.add_argument("--manual",  type=str, default=None,
                        help="Manually set token: --manual eyJhbGc...")
    parser.add_argument("--verify",  type=str, default=None,
                        help="Verify a token: --verify eyJhbGc...")
    args = parser.parse_args()

    if args.verify:
        ok = verify_token(args.verify)
        print(f"Token valid: {ok}")

    elif args.manual:
        manual_token_update(args.manual)

    elif args.daemon:
        start_scheduler()

    else:
        # Default — refresh once
        refresh_dhan_token()
