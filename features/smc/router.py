from fastapi import APIRouter, HTTPException, Query
from features.market.fetcher import fetch_candles
from features.smc.engine import run_smc_analysis

router = APIRouter(prefix="/smc", tags=["smc"])

@router.get("/analysis/{symbol}")
def smc_analysis(
    symbol: str,
    timeframe: str = Query(default="15m", enum=["5m", "15m", "30m", "1h", "4h"]),
):
    try:
        df     = fetch_candles(symbol.upper(), timeframe, limit=200)
        result = run_smc_analysis(df)
        return {"symbol": symbol.upper(), "timeframe": timeframe, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur analyse: {str(e)}")

@router.get("/multi/{symbol}")
def smc_multi_timeframe(symbol: str):
    timeframes = ["5m", "15m", "30m", "1h", "4h"]
    results    = {}
    for tf in timeframes:
        try:
            df = fetch_candles(symbol.upper(), tf, limit=200)
            results[tf] = run_smc_analysis(df)
        except Exception as e:
            results[tf] = {"error": str(e)}

    biases     = [results[tf].get("bias") for tf in timeframes if "bias" in results.get(tf, {})]
    buy_count  = biases.count("buy")
    sell_count = biases.count("sell")

    if buy_count >= 3:    confluence = "strong_buy"
    elif sell_count >= 3: confluence = "strong_sell"
    elif buy_count > sell_count: confluence = "buy"
    elif sell_count > buy_count: confluence = "sell"
    else: confluence = "neutral"

    return {
        "symbol":     symbol.upper(),
        "confluence": confluence,
        "buy_tfs":    buy_count,
        "sell_tfs":   sell_count,
        "timeframes": results,
    }
