"""
AlgoTrade Pro — Railway API Server
POST /run  →  runs backtest sweep, returns top results + strategy summary
"""

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os, time, logging, sys

# ── paste your full backtest.py contents here, OR import it ──────────────────
# If you keep backtest.py in the same folder, just do:
from backtest import (
    fetch_live_data,
    generate_ohlcv,
    run_sweep,
    run_sweep_all_symbols,
    strategy_summary,
    save_to_supabase,
    NSE_ASSETS,
    SCORE_THRESHOLD,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("api")

API_KEY = os.getenv("API_KEY", "")          # set in Railway env vars
app = FastAPI(title="AlgoTrade Pro API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    symbol:            Optional[str]  = None   # e.g. "RELIANCE.NS" — None = all symbols
    strategy:          Optional[str]  = None   # momentum | trend | breakout | sweep | None = all
    live_data:         bool           = False   # True = Yahoo Finance, False = synthetic
    top_n:             int            = 10
    save_supabase:     bool           = False
    # Optional signal from TradingView / Activepieces
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
    tradingview_echo:  Optional[dict]


# ── Auth helper ───────────────────────────────────────────────────────────────

def _check_auth(x_api_key: Optional[str]):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"service": "AlgoTrade Pro", "status": "running", "endpoints": ["/run", "/health", "/symbols"]}


@app.get("/health")
def health():
    return {"status": "ok", "market": "NSE", "currency": "INR"}


@app.get("/symbols")
def symbols():
    return {"symbols": list(NSE_ASSETS.keys())}


@app.post("/run", response_model=RunResponse)
def run_endpoint(
    body: RunRequest,
    x_api_key: Optional[str] = Header(default=None),
):
    _check_auth(x_api_key)
    t0 = time.time()

    log.info(f"/run  symbol={body.symbol}  strategy={body.strategy}  live={body.live_data}")

    # ── fetch data ──────────────────────────────────────────────────────────
    if body.symbol:
        sym = body.symbol
        if sym not in NSE_ASSETS and not sym.endswith(".NS"):
            sym = sym + ".NS"
        try:
            df = (fetch_live_data(symbol=sym)
                  if body.live_data
                  else generate_ohlcv(
                      start_price=NSE_ASSETS.get(sym, {}).get("price", 2855),
                      vol=NSE_ASSETS.get(sym, {}).get("vol", 0.014),
                  ))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Data fetch failed: {e}")

        results = run_sweep(df, strategy_filter=body.strategy,
                            symbol=sym, verbose=False)
        label = sym
    else:
        # All symbols
        results = run_sweep_all_symbols(live_data=body.live_data)
        label   = "ALL_NSE"

    if not results:
        raise HTTPException(status_code=422, detail="No valid backtest results — try more bars or different params")

    # ── analysis ────────────────────────────────────────────────────────────
    summary = strategy_summary(results)
    top     = [{k: v for k, v in r.items() if k != "equity_curve"}
                for r in results[:body.top_n]]
    best    = top[0]
    passed  = sum(1 for r in results if r["score"] > SCORE_THRESHOLD)

    # ── optional Supabase save ───────────────────────────────────────────────
    if body.save_supabase:
        save_to_supabase(results, summary)

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
