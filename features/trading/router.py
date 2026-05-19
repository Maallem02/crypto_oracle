from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
import MetaTrader5 as mt5
from features.trading.mt5_client import connect, disconnect
from features.trading.executor import place_trade, close_all_trades, get_open_trades
from features.market.fetcher import fetch_candles
from features.smc.engine import run_smc_analysis

router = APIRouter(prefix="/trading", tags=["trading"])

class TradeSettings(BaseModel):
    risk_percent:       float = 1.0
    min_confidence:     float = 0.75
    max_trades:         int   = 3
    enabled_symbols:    list  = ["BTC", "XAUUSD", "GBPJPY"]
    enabled_timeframes: list  = ["15m", "1h"]

bot_state = {
    "running":       False,
    "settings":      TradeSettings().dict(),
    "last_scan":     None,
    "trades_today":  0,
    "total_profit":  0.0,
}

# Historique des trades placés par le bot
trade_history = []

def auto_scan():
    """Appelé automatiquement toutes les 15 minutes par le scheduler"""
    if not bot_state["running"]:
        return
    
    print(f"[{datetime.now()}] Auto scan started...")
    settings = bot_state["settings"]
    
    try:
        mt5.initialize()
    except:
        pass

    for symbol in settings["enabled_symbols"]:
        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=200)
                analysis = run_smc_analysis(df)
                bias       = analysis.get("bias")
                confidence = analysis.get("confidence", 0)
                ote        = analysis.get("ote")

                if confidence >= settings["min_confidence"] and ote and bias in ["buy", "sell"]:
                    result = place_trade(
                        symbol=       symbol,
                        action=       bias,
                        entry=        ote["entry_price"],
                        sl=           ote["sl"],
                        tp1=          ote["tp1"],
                        confidence=   confidence,
                        risk_percent= settings["risk_percent"],
                    )
                    
                    trade_history.append({
                        "timestamp":  datetime.now().isoformat(),
                        "symbol":     symbol,
                        "timeframe":  tf,
                        "action":     bias,
                        "confidence": confidence,
                        "result":     result,
                    })
                    
                    if result.get("success"):
                        bot_state["trades_today"] += 1
                        print(f"✅ Trade opened: {symbol} {bias} ticket={result.get('ticket')}")
                    else:
                        print(f"❌ Trade failed: {result.get('reason')}")
                        
            except Exception as e:
                print(f"Error scanning {symbol} {tf}: {e}")
    
    bot_state["last_scan"] = datetime.now().isoformat()
    print(f"[{datetime.now()}] Auto scan finished.")

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
    return {
        **bot_state,
        "open_trades": len(get_open_trades()),
    }

@router.get("/bot/scan")
def scan_and_trade():
    settings = bot_state["settings"]
    if not settings.get("enabled_symbols"):
        settings = TradeSettings().dict()
    
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
                    
                    trade_history.append({
                        "timestamp":  datetime.now().isoformat(),
                        "symbol":     symbol,
                        "timeframe":  tf,
                        "action":     bias,
                        "confidence": confidence,
                        "result":     trade,
                    })
                    
            except Exception as e:
                results.append({"symbol": symbol, "timeframe": tf, "error": str(e)})

    bot_state["last_scan"] = datetime.now().isoformat()
    return {"scanned": len(results), "results": results}

@router.get("/bot/history")
def get_history():
    return {"history": trade_history[-50:]}

@router.get("/trades/open")
def open_trades():
    return {"trades": get_open_trades()}

@router.post("/trades/close-all")
def close_trades():
    return {"results": close_all_trades()}

@router.get("/symbols")
def list_symbols():
    mt5.initialize()
    symbols = mt5.symbols_get()
    if symbols is None:
        return {"symbols": [], "error": str(mt5.last_error())}
    crypto = [s.name for s in symbols if any(x in s.name for x in ["BTC", "ETH", "XAU", "XAG", "GBP"])]
    return {"symbols": crypto, "total": len(symbols)}