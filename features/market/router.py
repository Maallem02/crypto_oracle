from fastapi import APIRouter, HTTPException, Query
from features.market.fetcher import fetch_candles, get_current_price, get_all_prices
from core.config import settings

router = APIRouter(prefix="/market", tags=["market"])

@router.get("/prices")
def all_prices():
    """Retourne les prix de tous les assets"""
    return {"data": get_all_prices()}

@router.get("/price/{symbol}")
def single_price(symbol: str):
    """Prix + variation 24h d'un asset"""
    try:
        return get_current_price(symbol.upper())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/candles/{symbol}")
def candles(
    symbol: str,
    timeframe: str = Query(default="15m", enum=["5m", "15m", "30m", "1h", "4h"]),
    limit: int = Query(default=100, le=500),
):
    """Retourne les bougies OHLCV d'un asset"""
    try:
        df = fetch_candles(symbol.upper(), timeframe, limit)
        records = df.reset_index().tail(limit).to_dict(orient="records")
        # Convertir timestamps en string
        for r in records:
            if hasattr(r.get("timestamp"), "isoformat"):
                r["timestamp"] = r["timestamp"].isoformat()
            elif hasattr(r.get("timestamp"), "strftime"):
                r["timestamp"] = str(r["timestamp"])
        return {
            "symbol":    symbol.upper(),
            "timeframe": timeframe,
            "count":     len(records),
            "data":      records,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur fetch: {str(e)}")

@router.get("/assets")
def list_assets():
    """Liste tous les assets disponibles"""
    return {
        "crypto": list(settings.CRYPTO_ASSETS),
        "forex":  list(settings.FOREX_ASSETS),
        "timeframes": list(settings.TIMEFRAMES),
    }
