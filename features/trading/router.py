from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import Union, List
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
from features.trading.mt5_client import connect, disconnect
from core.config import runtime

def _mt5_init():
    kwargs = {"path": runtime.mt5_path} if runtime.mt5_path else {}
    mt5.initialize(**kwargs)
from features.trading.executor import place_trade, close_all_trades, get_open_trades
from features.market.fetcher import fetch_candles
from features.smc.engine import run_smc_analysis, run_scalping_analysis
from features.smc.structure import detect_market_structure
from features.trading.data_collector import save_signal, check_and_update_outcomes, get_stats, get_training_data
from features.trading.risk_manager import get_daily_pnl_pct

router = APIRouter(prefix="/trading", tags=["trading"])

class TradeSettings(BaseModel):
    risk_percent:       float = 1.0
    min_confidence:     float = 0.75
    max_trades:         int   = 3
    enabled_symbols:    list  = ["BTC", "XAUUSD", "GBPJPY"]
    enabled_timeframes: list  = ["15m", "1h"]

bot_state = {
    "running":        False,
    "settings":       TradeSettings().dict(),
    "last_scan":      None,
    "last_scan_date": None,
    "trades_today":   0,
    "total_profit":   0.0,
}

# Historique des trades placés par le bot
trade_history = []

def auto_scan():
    """Appelé automatiquement toutes les 6 minutes par le scheduler"""
    now   = datetime.now()
    today = now.date().isoformat()

    # Reset journalier — avant le check running
    if bot_state.get("last_scan_date") != today:
        bot_state["trades_today"]   = 0
        bot_state["total_profit"]   = 0.0
        bot_state["last_scan_date"] = today
        print(f"[DAILY RESET] Normal bot: {today}")

    if not bot_state["running"]:
        return

    print(f"[{now}] Auto scan started...")
    settings = bot_state["settings"]

    try:
        _mt5_init()
    except Exception:
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
                liq_grab         = analysis.get("liquidity_grab", {})
                current_price    = analysis.get("current_price", 0)
                structure        = analysis.get("structure", {})

                # Trade si : score >= 65 ET signal fort/modéré ET biais clair
                # OTE n'est plus un prérequis dur — il améliore l'entrée s'il existe
                should_trade = (
                    confluence_score >= 65 and
                    trade_signal in ["strong", "moderate"] and
                    bias in ["buy", "sell"]
                )

                if should_trade:
                    atr      = analysis.get("atr", 0) or 0
                    scalping = analysis.get("scalping_entry")

                    # ── Sélection entrée / SL / TP (priorité décroissante) ──
                    if scalping and liq_grab.get("detected"):
                        entry = scalping["entry"]
                        sl    = scalping["sl"]
                        tp1   = scalping["tp1"]
                        src   = "LG"
                    elif ote and ote.get("in_zone"):
                        entry = ote["entry_price"]
                        sl    = ote["sl"]
                        tp1   = ote["tp1"]
                        src   = "OTE-in-zone"
                    elif ote:
                        entry = current_price
                        sl    = ote["sl"]
                        tp1   = ote["tp1"]
                        src   = "OTE-market"
                    else:
                        # Fallback structurel — SL cappé à 3×ATR pour éviter
                        # des lots minuscules sur un swing trop large (ex: BTC 80k→100k)
                        sh  = structure.get("last_swing_high")
                        sl_ = structure.get("last_swing_low")
                        if not sh or not sl_:
                            print(f"SKIP {symbol} {tf}: no structure levels")
                            continue
                        entry = current_price
                        if bias == "buy":
                            raw_sl = float(sl_) * 0.999
                            sl     = round(max(raw_sl, entry - atr * 3), 5) if atr else round(raw_sl, 5)
                            tp1    = round(float(sh), 5)
                        else:
                            raw_sl = float(sh) * 1.001
                            sl     = round(min(raw_sl, entry + atr * 3), 5) if atr else round(raw_sl, 5)
                            tp1    = round(float(sl_), 5)
                        src = "structure"

                    print(f"SIGNAL {symbol} {tf}: score={confluence_score} bias={bias} src={src}")
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
                        "score":      confluence_score,
                        "entry_src":  src,
                        "result":     trade,
                    })

                    if trade.get("success"):
                        bot_state["trades_today"] += 1
                        print(f"✅ Trade opened: {symbol} {bias} ticket={trade.get('ticket')}")
                    else:
                        print(f"❌ Trade failed: {trade.get('reason')}")
                else:
                    print(f"📊 {symbol} {tf}: score={confluence_score} signal={trade_signal} bias={bias} → no trade")

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
                liq_grab         = analysis.get("liquidity_grab", {})
                current_price    = analysis.get("current_price", 0)
                structure        = analysis.get("structure", {})

                should_trade = (
                    confluence_score >= 65 and
                    trade_signal in ["strong", "moderate"] and
                    bias in ["buy", "sell"]
                )

                if should_trade:
                    atr      = analysis.get("atr", 0) or 0
                    scalping = analysis.get("scalping_entry")
                    if scalping and liq_grab.get("detected"):
                        entry = scalping["entry"]
                        sl    = scalping["sl"]
                        tp1   = scalping["tp1"]
                        src   = "LG"
                    elif ote and ote.get("in_zone"):
                        entry = ote["entry_price"]
                        sl    = ote["sl"]
                        tp1   = ote["tp1"]
                        src   = "OTE-in-zone"
                    elif ote:
                        entry = current_price
                        sl    = ote["sl"]
                        tp1   = ote["tp1"]
                        src   = "OTE-market"
                    else:
                        sh  = structure.get("last_swing_high")
                        sl_ = structure.get("last_swing_low")
                        if not sh or not sl_:
                            results[-1]["skip_reason"] = "no OTE/structure levels"
                            continue
                        entry = current_price
                        if bias == "buy":
                            raw_sl = float(sl_) * 0.999
                            sl     = round(max(raw_sl, entry - atr * 3), 5) if atr else round(raw_sl, 5)
                            tp1    = round(float(sh), 5)
                        else:
                            raw_sl = float(sh) * 1.001
                            sl     = round(min(raw_sl, entry + atr * 3), 5) if atr else round(raw_sl, 5)
                            tp1    = round(float(sl_), 5)
                        src = "structure"

                    trade = place_trade(
                        symbol=       symbol,
                        action=       bias,
                        entry=        entry,
                        sl=           sl,
                        tp1=          tp1,
                        confidence=   confluence_score / 100,
                        risk_percent= settings["risk_percent"],
                    )

                    results[-1]["trade"]      = trade
                    results[-1]["entry_src"]  = src
                    results[-1]["score"]      = confluence_score

                    trade_history.append({
                        "timestamp":  datetime.now().isoformat(),
                        "symbol":     symbol,
                        "timeframe":  tf,
                        "action":     bias,
                        "confidence": confidence,
                        "score":      confluence_score,
                        "entry_src":  src,
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
    min_score:          float = 75.0
    max_trades:         int   = 10
    max_daily_trades:   int   = 0        # 0 = illimité
    max_daily_loss_pct: float = 0.0      # 0 = désactivé
    enabled_symbols:    list  = ["BTC", "ETH", "SOL", "XAUUSD", "GBPJPY"]
    enabled_timeframes: list  = ["5m", "15m"]
    cooldown_minutes:   int        = 5
    htf_timeframe:      List[str]  = ["1h"]
    lot_sizes:          dict       = {}   # ex: {"BTC": 0.01, "ETH": 0.10} — 0 = auto

    @field_validator('htf_timeframe', mode='before')
    @classmethod
    def parse_htf(cls, v):
        if isinstance(v, str):
            return [v]   # "1h" → ["1h"]
        return v         # ["30m", "1h"] → ["30m", "1h"]

scalp_state = {
    "running":        False,
    "settings":       ScalpingSettings().dict(),
    "last_scan":      None,
    "trades_today":   0,
    "daily_pnl":      0.0,
    "stopped_reason": None,
}

scalp_history  = []
scalp_cooldowns = {}   # {symbol: datetime expiry}


def is_trading_session(symbol: str) -> bool:
    """Vérifie si c'est une session active pour ce symbole"""
    hour_utc = datetime.now(timezone.utc).hour
    if symbol in ["BTC", "ETH", "SOL", "BNB", "XRP"]:
        return True                              # Crypto → 24/7
    if symbol in ["XAUUSD", "XAGUSD"]:
        return 7 <= hour_utc <= 20               # London + NY
    if symbol in ["GBPJPY", "EURUSD"]:
        return 7 <= hour_utc <= 16               # London session
    if symbol == "USDJPY":
        return (0 <= hour_utc <= 9) or (7 <= hour_utc <= 16)   # Tokyo + London
    return True


def get_htf_trend(symbol: str, htf_tf: Union[str, list]) -> dict:
    """
    Retourne la tendance sur un ou plusieurs HTF.
    Consensus :
      - tous bullish  → bullish
      - tous bearish  → bearish
      - conflit       → conflict  (trade bloqué)
      - tous neutral  → neutral   (pas de filtre)
    """
    if isinstance(htf_tf, str):
        htf_tf = [htf_tf]

    trends = {}
    for tf in htf_tf:
        try:
            df_htf    = fetch_candles(symbol, tf, limit=50)
            structure = detect_market_structure(df_htf)
            trends[tf] = structure.get("trend", "neutral")
        except Exception:
            trends[tf] = "neutral"

    non_neutral = [t for t in trends.values() if t != "neutral"]

    if not non_neutral:
        consensus = "neutral"
    elif all(t == "bullish" for t in non_neutral):
        consensus = "bullish"
    elif all(t == "bearish" for t in non_neutral):
        consensus = "bearish"
    else:
        consensus = "conflict"   # HTFs ne s'accordent pas → bloqué

    return {"trends": trends, "consensus": consensus}

def scalp_auto_scan():
    """Appelé automatiquement toutes les 2 minutes par le scheduler"""
    now   = datetime.now()
    today = now.date().isoformat()

    # ── Reset journalier — s'exécute même si le bot est arrêté ───────────────
    # IMPORTANT : doit être AVANT le check running pour auto-relancer le bot
    # le lendemain après un arrêt par limite journalière.
    if scalp_state.get("last_scan_date") != today:
        prev = scalp_state.get("last_scan_date", "never")
        scalp_state["trades_today"]   = 0
        scalp_state["daily_pnl"]      = 0.0
        scalp_state["last_scan_date"] = today
        # Auto-relance si arrêt = limite journalière
        if "Daily" in (scalp_state.get("stopped_reason") or ""):
            scalp_state["running"]        = True
            scalp_state["stopped_reason"] = None
        print(f"[DAILY RESET] {today} (prev: {prev})")

    if not scalp_state["running"]:
        return

    settings = scalp_state["settings"]

    try:
        _mt5_init()
    except Exception:
        pass

    # ── Mise à jour du P&L journalier (réalisé + non-réalisé) ────────────────
    try:
        scalp_state["daily_pnl"] = get_daily_pnl_pct()
    except Exception:
        pass

    # ── Limites journalières ──────────────────────────────────────────────────
    # Daily loss limit — désactivé temporairement
    # if (settings["max_daily_loss_pct"] > 0
    #         and scalp_state["trades_today"] > 0
    #         and scalp_state["daily_pnl"] <= -settings["max_daily_loss_pct"]):
    #     scalp_state["running"]        = False
    #     scalp_state["stopped_reason"] = f"Daily loss limit hit: {scalp_state['daily_pnl']}%"
    #     print(f"STOP daily loss limit: {scalp_state['daily_pnl']}%")
    #     return

    if settings["max_daily_trades"] > 0 and scalp_state["trades_today"] >= settings["max_daily_trades"]:
        scalp_state["running"]        = False
        scalp_state["stopped_reason"] = f"Daily trade limit hit: {scalp_state['trades_today']}"
        print(f"STOP daily trade limit: {scalp_state['trades_today']}")
        return

    # Vérifier les trades fermés et mettre à jour les outcomes DB
    check_and_update_outcomes()

    for symbol in settings["enabled_symbols"]:

        # ── Filtre session : évite les marchés fermés / spreads larges ──────
        if not is_trading_session(symbol):
            continue   # silencieux — trop fréquent pour logger

        # ── Fix 4 : Cooldown par symbole ──────────────────────────────────
        if symbol in scalp_cooldowns and now < scalp_cooldowns[symbol]:
            remaining = int((scalp_cooldowns[symbol] - now).total_seconds() // 60)
            print(f"⏳ {symbol} cooldown {remaining}min restant → skip")
            continue

        # ── Fix 1 : Filtre MTF — tendance Higher Timeframe ────────────────
        htf_tf   = settings.get("htf_timeframe", "1h")
        htf_data = get_htf_trend(symbol, htf_tf)
        consensus = htf_data["consensus"]

        # Conflit entre HTFs → skip tout le symbole
        if consensus == "conflict":
            print(f"⚡ HTF conflict {symbol}: {htf_data['trends']} → skip")
            continue

        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=100)
                analysis = run_scalping_analysis(df)

                score     = analysis.get("scalping_score", 0)
                min_score = settings.get("min_score", 75)

                if not analysis.get("should_scalp") or score < min_score:
                    # Seulement loguer si on a un LG (score > 0) mais pas assez haut
                    if score > 0:
                        conds = " | ".join(analysis.get("conditions", []))
                        print(f"NEAR {symbol} {tf}: score={score}/{min_score} [{conds}]")
                    continue

                bias = analysis["bias"]

                # Rejet si contra-tendance HTF
                if consensus == "bearish" and bias == "buy":
                    print(f"[MTF] {symbol} {tf} BUY rejected (HTF: bearish)")
                    continue
                if consensus == "bullish" and bias == "sell":
                    print(f"[MTF] {symbol} {tf} SELL rejected (HTF: bullish)")
                    continue

                entry_data = analysis.get("scalping_entry")
                if not entry_data:
                    continue

                # Lot fixe par symbole si défini, sinon auto (risk-based)
                lot_sizes  = settings.get("lot_sizes", {})
                fixed_lot  = float(lot_sizes.get(symbol, 0))

                trade = place_trade(
                    symbol=       symbol,
                    action=       bias,
                    entry=        entry_data["entry"],
                    sl=           entry_data["sl"],
                    tp1=          entry_data["tp1"],
                    confidence=   analysis["scalping_score"] / 100,
                    risk_percent= settings["risk_percent"],
                    max_trades=   settings["max_trades"],
                    fixed_lot=    fixed_lot,
                )

                scalp_history.append({
                    "timestamp":   now.isoformat(),
                    "symbol":      symbol,
                    "timeframe":   tf,
                    "action":      bias,
                    "score":       analysis["scalping_score"],
                    "htf_trends":  htf_data["trends"],
                    "htf_consensus": consensus,
                    "atr":         analysis.get("atr"),
                    "entry":       entry_data["entry"],
                    "sl":          entry_data["sl"],
                    "tp1":         entry_data["tp1"],
                    "rr":          entry_data.get("rr_ratio"),
                    "result":      trade,
                })

                # ── Sauvegarder le signal pour entraînement IA ───────────
                save_signal(analysis, entry_data, trade, symbol, tf, consensus)

                # ── Fix 4 : Activer cooldown après chaque trade ───────────
                cooldown_min = settings.get("cooldown_minutes", 5)
                scalp_cooldowns[symbol] = now + timedelta(minutes=cooldown_min)

                if trade.get("success"):
                    scalp_state["trades_today"] += 1
                    print(f"⚡ Scalp: {symbol} {bias} score={analysis['scalping_score']} HTF={consensus} ticket={trade.get('ticket')}")
                else:
                    print(f"❌ Scalp failed: {trade.get('reason')}")

            except Exception as e:
                print(f"Scalp error {symbol} {tf}: {e}")

    scalp_state["last_scan"] = now.isoformat()


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
    settings  = scalp_state["settings"]
    htf_tf    = settings.get("htf_timeframe", "15m")
    results   = []

    for symbol in settings["enabled_symbols"]:
        htf_data  = get_htf_trend(symbol, htf_tf)
        consensus = htf_data["consensus"]
        session   = is_trading_session(symbol)
        cooldown  = symbol in scalp_cooldowns and datetime.now() < scalp_cooldowns[symbol]

        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=100)
                analysis = run_scalping_analysis(df)
                bias     = analysis.get("bias", "neutral")

                # Vérification filtres sans placer de trade
                htf_ok = (
                    consensus != "conflict" and
                    not (consensus == "bearish" and bias == "buy") and
                    not (consensus == "bullish" and bias == "sell")
                )

                results.append({
                    "symbol":       symbol,
                    "timeframe":    tf,
                    "should_scalp": analysis.get("should_scalp") and htf_ok and not cooldown,
                    "score":        analysis.get("scalping_score"),
                    "bias":         bias,
                    "htf_trends":   htf_data["trends"],
                    "htf_consensus": consensus,
                    "htf_ok":       htf_ok,
                    "session_ok":   session,
                    "cooldown":     cooldown,
                    "atr":          analysis.get("atr"),
                    "entry":        analysis.get("scalping_entry"),
                    "conditions":   analysis.get("conditions"),
                })
            except Exception as e:
                results.append({"symbol": symbol, "timeframe": tf, "error": str(e)})

    return {"scanned": len(results), "results": results}


# ─────────────────────────────────────────────────────────────────────────────
# AI DATA COLLECTION
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/ai/stats")
def ai_stats():
    """Statistiques sur les données collectées pour l'IA"""
    return get_stats()

@router.get("/ai/data")
def ai_data():
    """Retourne les données labellisées pour entraîner le modèle"""
    data = get_training_data()
    return {
        "count": len(data),
        "data":  data[-100:],  # 100 derniers
    }


@router.get("/symbols")
def list_symbols():
    _mt5_init()
    symbols = mt5.symbols_get()
    if symbols is None:
        return {"symbols": [], "error": str(mt5.last_error())}
    crypto = [s.name for s in symbols if any(x in s.name for x in ["BTC", "ETH", "XAU", "XAG", "GBP"])]
    return {"symbols": crypto, "total": len(symbols)}