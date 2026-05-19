from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from features.trading.mt5_client import connect, disconnect
from features.trading.executor import place_trade, close_all_trades, get_open_trades
from features.market.fetcher import fetch_candles
from features.smc.engine import run_smc_analysis
import MetaTrader5 as mt5

router = APIRouter(prefix="/trading", tags=["trading"])

class TradeSettings(BaseModel):
    risk_percent:       float = 1.0
    min_confidence:     float = 0.75
    max_trades:         int   = 3
    enabled_symbols:    list  = ["BTC", "XAUUSD", "GBPJPY"]
    enabled_timeframes: list  = ["15m", "1h"]

# État du bot
bot_state = {
    "running":  False,
    "settings": TradeSettings().dict(),
}

@router.get("/symbols")
def list_symbols():
    """Liste tous les symboles disponibles sur MT5"""
    symbols = mt5.symbols_get()
    if symbols is None:
        return {"symbols": []}
    crypto = [s.name for s in symbols if "BTC" in s.name or "ETH" in s.name or "XAU" in s.name]
    return {"symbols": crypto, "total": len(symbols)}
@router.get("/connect")
def mt5_connect():
    try:
        info = connect()
        return {"success": True, "account": info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/bot/start")
def start_bot(settings: TradeSettings):
    bot_state["running"]  = True
    bot_state["settings"] = settings.dict()
    return {"success": True, "message": "Bot started", "settings": settings}

@router.post("/bot/stop")
def stop_bot():
    bot_state["running"] = False
    return {"success": True, "message": "Bot stopped"}

@router.get("/bot/status")
def bot_status():
    return bot_state

@router.get("/bot/scan")
def scan_and_trade():
    """Scan tous les assets et place les trades si signal fort"""
    # Force running=True pour le scan manuel
    settings = bot_state["settings"]
    
    # Si settings vide utilise defaults
    if not settings.get("enabled_symbols"):
        settings = {
            "risk_percent":       1.0,
            "min_confidence":     0.75,
            "max_trades":         3,
            "enabled_symbols":    ["BTC", "XAUUSD"],
            "enabled_timeframes": ["15m"],
        }
    
    results = []
    
    for symbol in settings["enabled_symbols"]:
        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=200)
                analysis = run_smc_analysis(df)
                
                bias       = analysis.get("bias")
                confidence = analysis.get("confidence", 0)
                ote        = analysis.get("ote")
                
                results.append({
                    "symbol":     symbol,
                    "timeframe":  tf,
                    "bias":       bias,
                    "confidence": confidence,
                    "ote":        ote is not None,
                })
                
                # Trade si confiance élevée
                if confidence >= settings["min_confidence"] and ote and bias in ["buy", "sell"]:
                    trade = place_trade(
                        symbol=       symbol,
                        action=       bias,
                        entry=        ote["entry_price"],
                        sl=           ote["sl"],
                        tp1=          ote["tp1"],
                        confidence=   confidence,
                        risk_percent= settings["risk_percent"],
                    )
                    results[-1]["trade"] = trade
                    
            except Exception as e:
                results.append({"symbol": symbol, "timeframe": tf, "error": str(e)})
    
    return {"scanned": len(results), "results": results}

@router.get("/trades/open")
def open_trades():
    return {"trades": get_open_trades()}

@router.post("/trades/close-all")
def close_trades():
    return {"results": close_all_trades()}