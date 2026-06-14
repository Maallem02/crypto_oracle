from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import Union, List
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
from features.trading.mt5_client import connect, disconnect
from core.config import runtime

def _mt5_init():
    """Initialise MT5 avec credentials — force re-login pour résoudre 10027 en thread."""
    import os
    login_id = os.getenv("MT5_LOGIN")
    password  = os.getenv("MT5_PASSWORD")
    server    = os.getenv("MT5_SERVER")

    # Si credentials disponibles, toujours forcer le login explicite
    # Résout le retcode 10027 (AutoTrading disabled) dans les threads du scheduler
    if login_id and password and server:
        mt5.initialize(
            login    = int(login_id),
            password = password,
            server   = server,
            timeout  = 10000,
        )
        return

    # Fallback: connexion sans credentials (si déjà connecté)
    if mt5.terminal_info() is None:
        kwargs = {"timeout": 10000}
        if runtime.mt5_path:
            kwargs["path"] = runtime.mt5_path
        mt5.initialize(**kwargs)
from features.trading.executor import place_trade, close_all_trades, get_open_trades
from features.market.fetcher import fetch_candles
from features.smc.engine import run_smc_analysis, run_scalping_analysis
from features.smc.structure import detect_market_structure
from features.trading.data_collector import save_signal, check_and_update_outcomes, get_stats, get_training_data
from features.trading.risk_manager import get_daily_pnl_pct
from features.trading.ml_model import predict_win_probability, maybe_retrain, load_model, train_model, get_model_info

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
                        print(f"[OK] Trade opened: {symbol} {bias} ticket={trade.get('ticket')}")
                    else:
                        print(f"[NO] Trade failed: {trade.get('reason')}")
                else:
                    print(f" {symbol} {tf}: score={confluence_score} signal={trade_signal} bias={bias} → no trade")

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
    min_score:          float = 85.0
    max_trades:         int   = 10
    max_daily_trades:   int   = 0        # 0 = illimité
    max_daily_loss_pct: float = 0.0      # 0 = désactivé
    enabled_symbols:    list  = ["BTC", "ETH", "SOL", "XAUUSD", "GBPJPY"]
    enabled_timeframes: list  = ["5m", "15m"]
    cooldown_minutes:   int        = 5
    htf_timeframe:      List[str]  = ["30m"]
    lot_sizes:          dict       = {}   # ex: {"BTC": 0.01, "ETH": 0.10} — 0 = auto
    ml_threshold:       float      = 0.0  # 0 = ML filter OFF, 0.6 = trade only if win_prob >= 60%

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
    """Vérifie si c'est une session active pour ce symbole."""
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
    # Daily loss limit — ACTIVE
    max_loss = settings.get("max_daily_loss_pct", 0.0)
    if max_loss > 0:
        daily_pnl = scalp_state.get("daily_pnl", 0.0)
        if daily_pnl <= -max_loss:
            scalp_state["running"]        = False
            scalp_state["stopped_reason"] = f"Daily loss limit hit: {daily_pnl:.1f}% (limit: -{max_loss}%)"
            print(f"[STOP] Daily loss limit hit: {daily_pnl:.1f}% <= -{max_loss}%")
            return

    # Minimum balance protection — stop if balance < $2 (avoid margin calls)
    # Use $2 not $5 to avoid false triggers from MT5 reconnection returning $0 briefly
    try:
        import MetaTrader5 as mt5
        _acct = mt5.account_info()
        if _acct and 0 < _acct.balance < 2.0:
            scalp_state["running"]        = False
            scalp_state["stopped_reason"] = f"Balance too low: ${_acct.balance:.2f}"
            print(f"[STOP] Balance too low: ${_acct.balance:.2f} < $2 minimum")
            return
    except Exception:
        pass

    if settings["max_daily_trades"] > 0 and scalp_state["trades_today"] >= settings["max_daily_trades"]:
        scalp_state["running"]        = False
        scalp_state["stopped_reason"] = f"Daily trade limit hit: {scalp_state['trades_today']}"
        print(f"STOP daily trade limit: {scalp_state['trades_today']}")
        return

    # Vérifier les trades fermés et mettre à jour les outcomes DB
    check_and_update_outcomes()

    # Auto-retrain ML si assez de nouveaux samples
    try:
        maybe_retrain()
    except Exception as e:
        print(f"[ML] maybe_retrain error: {e}")

    # ── Fetch positions UNE SEULE FOIS pour tout le scan ─────────────────────
    # Ne pas appeler mt5.positions_get() dans chaque itération — trop lent.
    from features.trading.risk_manager import BOT_MAGIC
    try:
        _all_open = mt5.positions_get() or []
    except Exception:
        _all_open = []

    for symbol in settings["enabled_symbols"]:

        # ── Filtre session : évite les marchés fermés / spreads larges ──────
        if not is_trading_session(symbol):
            continue   # silencieux — trop fréquent pour logger

        # ── Cooldown temporel après trade réussi ─────────────────────────
        # Permet le pyramiding : plusieurs trades sur même symbole espacés dans le temps
        # Ex: cooldown=3min → BTC#1 à 14:00, BTC#2 possible à 14:03 si signal valide
        if symbol in scalp_cooldowns and now < scalp_cooldowns[symbol]:
            remaining = int((scalp_cooldowns[symbol] - now).total_seconds() // 60)
            print(f"[~] {symbol} cooldown {remaining}min restant → skip")
            continue

        # ── Max positions simultanées par symbole (anti-pyramiding abusif) ──
        # Utilise la liste pré-chargée en dehors de la boucle (pas de re-fetch MT5)
        sym_positions = [p for p in _all_open
                         if p.magic == BOT_MAGIC and symbol.upper() in p.symbol.upper()]
        if len(sym_positions) >= 2:
            print(f"[!]  {symbol} {len(sym_positions)}/2 positions ouvertes → skip")
            continue

        # ── Fix 1 : Filtre MTF — tendance Higher Timeframe ────────────────
        htf_tf   = settings.get("htf_timeframe", "1h")
        htf_data = get_htf_trend(symbol, htf_tf)
        consensus = htf_data["consensus"]

        # Conflit entre HTFs → skip tout le symbole
        if consensus == "conflict":
            print(f"[!] HTF conflict {symbol}: {htf_data['trends']} → skip")
            continue

        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=100)
                analysis = run_scalping_analysis(df)

                ta_score  = analysis.get("scalping_score", 0)
                min_score = settings.get("min_score", 75)

                if not analysis.get("should_scalp") or ta_score < min_score:
                    if ta_score > 0:
                        conds = " | ".join(analysis.get("conditions", []))
                        print(f"NEAR {symbol} {tf}: score={ta_score}/{min_score} [{conds}]")
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

                # ── ML probability ───────────────────────────────────────
                ml_prob = predict_win_probability(analysis, entry_data, consensus)

                # ── Symbol priority (from historical win rate per symbol/tf)
                try:
                    from features.trading.ta_optimizer import get_symbol_priority, get_config as _ta_cfg
                    sym_priority = get_symbol_priority(symbol, tf)
                    blend_factor = _ta_cfg().get("blend_factor", 20)
                except Exception:
                    sym_priority = 1.0
                    blend_factor = 20

                # ── Blended score: TA + ML contribution ─────────────────
                # ML prob 0.7 → +4 pts, prob 0.5 → 0 pts, prob 0.3 → -4 pts
                ml_boost     = round((ml_prob - 0.5) * blend_factor, 1)
                sym_boost    = round((sym_priority - 1.0) * 10, 1)
                blended_score = round(ta_score + ml_boost + sym_boost, 1)

                print(f"[SCORE] {symbol} {tf}: TA={ta_score} ML_boost={ml_boost:+.1f} "
                      f"sym={sym_boost:+.1f} => blended={blended_score}")

                # ── Final gate: ML probability threshold ─────────────────
                ml_threshold = settings.get("ml_threshold", 0.0)
                if ml_threshold > 0 and ml_prob < ml_threshold:
                    print(f"[ML] {symbol} {tf} rejected: prob={ml_prob:.2f} < {ml_threshold:.2f}")
                    continue
                if ml_threshold > 0:
                    print(f"[ML] {symbol} {tf} passed: prob={ml_prob:.2f} >= {ml_threshold:.2f}")

                # Expose blended_score back into analysis for logging
                analysis["blended_score"] = blended_score

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
                    "timestamp":     now.isoformat(),
                    "symbol":        symbol,
                    "timeframe":     tf,
                    "action":        bias,
                    "score":         analysis["scalping_score"],
                    "htf_trends":    htf_data["trends"],
                    "htf_consensus": consensus,
                    "atr":           analysis.get("atr"),
                    "entry":         entry_data["entry"],
                    "sl":            entry_data["sl"],
                    "tp1":           entry_data["tp1"],
                    "rr":            entry_data.get("rr_ratio"),
                    "ml_win_prob":   ml_prob,
                    "result":        trade,
                })

                # ── Sauvegarder le signal pour entraînement IA ───────────
                save_signal(analysis, entry_data, trade, symbol, tf, consensus)

                if trade.get("success"):
                    # Cooldown uniquement après trade réussi
                    cooldown_min = settings.get("cooldown_minutes", 5)
                    scalp_cooldowns[symbol] = now + timedelta(minutes=cooldown_min)
                    scalp_state["trades_today"] += 1
                    print(f"[!] Scalp: {symbol} {bias} score={analysis['scalping_score']} HTF={consensus} ticket={trade.get('ticket')}")
                else:
                    print(f"[NO] Scalp failed: {trade.get('reason')}")

                # Break : évite que 5m ET 15m tradent le même symbole dans le même scan
                break

            except Exception as e:
                print(f"Scalp error {symbol} {tf}: {e}".encode('ascii', errors='replace').decode())

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


@router.get("/ai/ta-config")
def ai_ta_config():
    """Current TA scoring weights and symbol priorities set by ML feedback."""
    from features.trading.ta_optimizer import get_config
    return get_config()


@router.get("/ai/model")
def ai_model_info():
    """État du modèle ML : accuracy, samples, feature importance"""
    return get_model_info()

@router.post("/ai/train")
def ai_train_now():
    """Force un retrain immédiat du modèle ML"""
    meta = train_model()
    if meta is None:
        from features.trading.data_collector import get_stats
        stats = get_stats()
        return {
            "success": False,
            "reason":  f"Pas assez de données: {stats['labeled']}/50 trades labelisés",
            "stats":   stats,
        }
    return {"success": True, "model": meta}


@router.get("/symbols")
def list_symbols():
    _mt5_init()
    symbols = mt5.symbols_get()
    if symbols is None:
        return {"symbols": [], "error": str(mt5.last_error())}
    crypto = [s.name for s in symbols if any(x in s.name for x in ["BTC", "ETH", "XAU", "XAG", "GBP"])]
    return {"symbols": crypto, "total": len(symbols)}