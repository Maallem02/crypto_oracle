from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
import MetaTrader5 as mt5
from features.trading.mt5_client import connect, disconnect
from features.trading.executor import place_trade, close_all_trades, get_open_trades
from features.market.fetcher import fetch_candles
from features.smc.engine import run_smc_analysis, run_scalping_analysis

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


                confluence_score = analysis.get("confluence_score", 0)
                trade_signal     = analysis.get("trade_signal", "weak")
                pd_zone          = analysis.get("premium_discount", {})
                liq_grab         = analysis.get("liquidity_grab", {})

                # Trade seulement si :
                # 1. Confluence score >= 65
                # 2. Prix dans la bonne zone P/D
                # 3. Signal fort ou modéré
                should_trade = (
                    confluence_score >= 65 and
                    trade_signal in ["strong", "moderate"] and
                    bias in ["buy", "sell"] and
                    ote is not None
                )

                if should_trade:
                    # Priorité au scalping entry si liquidity grab détecté
                    scalping = analysis.get("scalping_entry")
                    if scalping and liq_grab.get("detected"):
                        entry = scalping["entry"]
                        sl    = scalping["sl"]
                        tp1   = scalping["tp1"]
                    else:
                        entry = ote["entry_price"]
                        sl    = ote["sl"]
                        tp1   = ote["tp1"]
                    
                    trade = place_trade(
                        symbol=       symbol,
                        action=       bias,
                        entry=        entry,
                        sl=           sl,
                        tp1=          tp1,
                        confidence=   confluence_score / 100,
                        risk_percent= settings["risk_percent"],
                    )

                    trade_history.append({
                        "timestamp":  datetime.now().isoformat(),
                        "symbol":     symbol,
                        "timeframe":  tf,
                        "action":     bias,
                        "confidence": confidence,
                        "result":     trade,
                    })

                    if trade.get("success"):
                        bot_state["trades_today"] += 1
                        print(f"✅ Trade opened: {symbol} {bias} ticket={trade.get('ticket')}")
                    else:
                        print(f"❌ Trade failed: {trade.get('reason')}")
                        
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

                confluence_score = analysis.get("confluence_score", 0)
                trade_signal     = analysis.get("trade_signal", "weak")
                pd_zone          = analysis.get("premium_discount", {})
                liq_grab         = analysis.get("liquidity_grab", {})

                # Trade seulement si :
                # 1. Confluence score >= 65
                # 2. Prix dans la bonne zone P/D
                # 3. Signal fort ou modéré
                should_trade = (
                    confluence_score >= 65 and
                    trade_signal in ["strong", "moderate"] and
                    bias in ["buy", "sell"] and
                    ote is not None
                )

                if should_trade:
                    # Priorité au scalping entry si liquidity grab détecté
                    scalping = analysis.get("scalping_entry")
                    if scalping and liq_grab.get("detected"):
                        entry = scalping["entry"]
                        sl    = scalping["sl"]
                        tp1   = scalping["tp1"]
                    else:
                        entry = ote["entry_price"]
                        sl    = ote["sl"]
                        tp1   = ote["tp1"]
                    
                    trade = place_trade(
                        symbol=       symbol,
                        action=       bias,
                        entry=        entry,
                        sl=           sl,
                        tp1=          tp1,
                        confidence=   confluence_score / 100,
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

# ─────────────────────────────────────────────────────────────────────────────
# SCALPING MODE
# ─────────────────────────────────────────────────────────────────────────────

class ScalpingSettings(BaseModel):
    risk_percent:       float = 0.5
    min_score:          float = 60.0
    max_trades:         int   = 10
    max_daily_trades:   int   = 30
    max_daily_loss_pct: float = 3.0
    enabled_symbols:    list  = ["BTC", "XAUUSD", "GBPJPY"]
    enabled_timeframes: list  = ["1m", "5m"]

scalp_state = {
    "running":        False,
    "settings":       ScalpingSettings().dict(),
    "last_scan":      None,
    "trades_today":   0,
    "daily_pnl":      0.0,
    "stopped_reason": None,
}

scalp_history = []

def scalp_auto_scan():
    """Appelé automatiquement toutes les 2 minutes par le scheduler"""
    if not scalp_state["running"]:
        return

    settings = scalp_state["settings"]

    # Stop si limite perte journalière atteinte
    if scalp_state["daily_pnl"] <= -settings["max_daily_loss_pct"]:
        scalp_state["running"]        = False
        scalp_state["stopped_reason"] = f"Daily loss limit: {scalp_state['daily_pnl']}%"
        print(f"🛑 Scalping stopped: daily loss limit reached")
        return

    # Stop si limite trades journaliers atteinte
    if scalp_state["trades_today"] >= settings["max_daily_trades"]:
        scalp_state["running"]        = False
        scalp_state["stopped_reason"] = f"Daily trade limit: {scalp_state['trades_today']}"
        print(f"🛑 Scalping stopped: daily trade limit reached")
        return

    try:
        mt5.initialize()
    except Exception:
        pass

    for symbol in settings["enabled_symbols"]:
        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=100)
                analysis = run_scalping_analysis(df)

                if not analysis.get("should_scalp"):
                    continue

                entry_data = analysis.get("scalping_entry")
                if not entry_data:
                    continue

                trade = place_trade(
                    symbol=       symbol,
                    action=       analysis["bias"],
                    entry=        entry_data["entry"],
                    sl=           entry_data["sl"],
                    tp1=          entry_data["tp1"],
                    confidence=   analysis["scalping_score"] / 100,
                    risk_percent= settings["risk_percent"],
                    max_trades=   settings["max_trades"],
                )

                scalp_history.append({
                    "timestamp": datetime.now().isoformat(),
                    "symbol":    symbol,
                    "timeframe": tf,
                    "action":    analysis["bias"],
                    "score":     analysis["scalping_score"],
                    "entry":     entry_data["entry"],
                    "sl":        entry_data["sl"],
                    "tp1":       entry_data["tp1"],
                    "result":    trade,
                })

                if trade.get("success"):
                    scalp_state["trades_today"] += 1
                    print(f"⚡ Scalp: {symbol} {analysis['bias']} score={analysis['scalping_score']} ticket={trade.get('ticket')}")
                else:
                    print(f"❌ Scalp failed: {trade.get('reason')}")

            except Exception as e:
                print(f"Scalp error {symbol} {tf}: {e}")

    scalp_state["last_scan"] = datetime.now().isoformat()


@router.post("/scalping/start")
def start_scalping(settings: ScalpingSettings):
    scalp_state["running"]        = True
    scalp_state["settings"]       = settings.dict()
    scalp_state["trades_today"]   = 0
    scalp_state["daily_pnl"]      = 0.0
    scalp_state["stopped_reason"] = None
    return {"success": True, "message": "Scalping bot started", "settings": settings}

@router.post("/scalping/stop")
def stop_scalping():
    scalp_state["running"]        = False
    scalp_state["stopped_reason"] = "Manual stop"
    return {"success": True, "message": "Scalping bot stopped"}

@router.get("/scalping/status")
def scalping_status():
    return {
        **scalp_state,
        "open_trades": len(get_open_trades()),
        "history_count": len(scalp_history),
    }

@router.get("/scalping/history")
def scalping_history_endpoint():
    return {"history": scalp_history[-100:]}

@router.get("/scalping/scan")
def scalping_scan_now():
    """Scan manuel scalping — ne place pas de trades, analyse seulement"""
    settings = scalp_state["settings"]
    results  = []

    for symbol in settings["enabled_symbols"]:
        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=100)
                analysis = run_scalping_analysis(df)
                results.append({
                    "symbol":      symbol,
                    "timeframe":   tf,
                    "should_scalp": analysis.get("should_scalp"),
                    "score":       analysis.get("scalping_score"),
                    "bias":        analysis.get("bias"),
                    "entry":       analysis.get("scalping_entry"),
                    "conditions":  analysis.get("conditions"),
                })
            except Exception as e:
                results.append({"symbol": symbol, "timeframe": tf, "error": str(e)})

    return {"scanned": len(results), "results": results}


@router.get("/symbols")
def list_symbols():
    mt5.initialize()
    symbols = mt5.symbols_get()
    if symbols is None:
        return {"symbols": [], "error": str(mt5.last_error())}
    crypto = [s.name for s in symbols if any(x in s.name for x in ["BTC", "ETH", "XAU", "XAG", "GBP"])]
    return {"symbols": crypto, "total": len(symbols)}