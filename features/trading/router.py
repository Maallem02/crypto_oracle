from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import Union, List
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
from features.trading.mt5_client import connect, disconnect
from core.config import runtime

def _mt5_init():
    """
    Initialise MT5 avec credentials.

    IDEMPOTENT: si le terminal est déjà connecté au bon compte, ne fait RIEN.
    L'ancienne version forçait mt5.initialize(login=...) à CHAQUE appel
    (toutes les 30s via manage_open_positions) — chaque appel re-loggait le
    terminal sur le compte demo, écrasant toute session manuelle (compte réel)
    ouverte dans le GUI → le terminal basculait de compte toutes les
    quelques secondes.

    Le re-login forcé n'est conservé que pour les cas où il est nécessaire :
    terminal déconnecté ou connecté au MAUVAIS compte (résout aussi le
    retcode 10027 dans les threads du scheduler, car le premier appel de
    chaque thread passe encore par le login explicite si besoin).
    """
    import os
    login_id = os.getenv("MT5_LOGIN")
    password  = os.getenv("MT5_PASSWORD")
    server    = os.getenv("MT5_SERVER")

    # Déjà connecté au bon compte → no-op (pas de re-login, pas de bascule)
    try:
        acc = mt5.account_info()
        if acc and (not login_id or acc.login == int(login_id)):
            return
    except Exception:
        pass

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
from features.trading.executor import place_trade, close_all_trades, get_open_trades, SYMBOL_MAP
from features.market.fetcher import fetch_candles
from features.smc.engine import run_smc_analysis, run_scalping_analysis
from features.smc.structure import detect_market_structure
from features.trading.data_collector import save_signal, check_and_update_outcomes, get_stats, get_training_data
from features.trading.risk_manager import get_daily_pnl_pct
from features.trading.ml_model import predict_win_probability, predict_decision, maybe_retrain, load_model, train_model, get_model_info, get_lot_multiplier

router = APIRouter(prefix="/trading", tags=["trading"])

class TradeSettings(BaseModel):
    risk_percent:       float = 1.0
    min_confidence:     float = 0.75
    max_trades:         int   = 3
    enabled_symbols:    list  = ["BTC", "XAUUSD", "GBPJPY"]
    enabled_timeframes: list  = ["15m"]

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
    min_score:          float = 75.0
    max_trades:         int   = 3
    max_daily_trades:   int   = 15
    max_daily_loss_pct: float = 3.0
    enabled_symbols:    list  = ["BTC", "ETH", "XAUUSD", "EURUSD"]   # GBPJPY disabled — repeat worst performer across multiple sessions
    enabled_timeframes: list  = ["15m"]
    cooldown_minutes:   int        = 8
    htf_timeframe:      List[str]  = ["5m", "1h"]
    lot_sizes:          dict       = {"BTC": 0.02, "ETH": 0.28, "XAUUSD": 0.01, "GBPJPY": 0.05, "EURUSD": 0.03}
    ml_threshold:       float      = 0.0

    @field_validator('htf_timeframe', mode='before')
    @classmethod
    def parse_htf(cls, v):
        if isinstance(v, str):
            return [v]   # "1h" → ["1h"]
        return v         # ["5m", "1h"] → ["5m", "1h"]

import json as _json, os as _os

_SETTINGS_FILE = _os.path.join(
    _os.path.dirname(_os.path.abspath(__file__)),
    "scalp_settings_saved.json"
)
print(f"[SETTINGS] file path: {_SETTINGS_FILE}")

def _load_saved_settings() -> dict:
    try:
        with open(_SETTINGS_FILE, "r") as _f:
            return _json.load(_f)
    except Exception:
        return {}

def _persist_settings(d: dict):
    import sys as _sys
    _sys.stdout.write(f"[SETTINGS] persisting to {_SETTINGS_FILE}\n")
    _sys.stdout.flush()
    try:
        with open(_SETTINGS_FILE, "w") as _f:
            _json.dump(d, _f, indent=2)
        _sys.stdout.write(f"[SETTINGS] persisted OK\n")
        _sys.stdout.flush()
    except Exception as _pe:
        _sys.stdout.write(f"[SETTINGS] persist error: {_pe}\n")
        _sys.stdout.flush()

_boot_settings = {**ScalpingSettings().dict(), **_load_saved_settings()}

scalp_state = {
    "running":        False,
    "settings":       _boot_settings,
    "last_scan":      None,
    "trades_today":   0,
    "daily_pnl":      0.0,
    "stopped_reason": None,
}

import threading

scalp_history        = []
scalp_cooldowns      = {}   # {symbol: datetime expiry}
symbol_paused_until  = {}   # {symbol: datetime} — paused after 3 consecutive losses
direction_blocked    = {}   # {f"{symbol}_{direction}": datetime expiry} — 2h block after 2 consecutive same-direction losses
direction_breaker    = {}   # {"buy"/"sell": datetime} — GLOBAL directional circuit breaker (all symbols)
btc_lead_signal      = {}   # {"bias": "sell"/"buy", "ts": datetime} — BTC lead-lag tracker
_scan_lock           = threading.Lock()   # atomic lock — prevents overlapping scans across threads
scan_rejections      = []   # last 200 rejection/skip reasons from auto-scan


def get_extra_features(symbol: str, analysis: dict, entry_price: float) -> dict:
    """Compute volume_ratio, spread_pct, symbol_winrate, consecutive_losses for ML."""
    import sqlite3, MetaTrader5 as mt5
    result = {"volume_ratio": None, "spread_pct": None,
              "symbol_winrate": None, "consecutive_losses": 0}

    # Resolve broker symbol ("BTC" → "BTCUSDm") — symbol_info() with the
    # short name returns None, which left spread_pct NULL on every trade.
    from features.market.fetcher import MT5_SYMBOLS
    broker_sym = MT5_SYMBOLS.get(symbol, symbol)

    try:
        info = mt5.symbol_info(broker_sym)
        atr  = analysis.get("atr") or 0
        if info and atr > 0:
            spread_price = info.spread * info.point
            result["spread_pct"] = round(spread_price / atr * 100, 3)
    except Exception:
        pass

    # Volume ratio: last closed M15 candle vs 20-candle average.
    # Was declared in the schema but never computed — NULL on every trade.
    try:
        rates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M15, 1, 21)
        if rates is not None and len(rates) >= 21:
            vols = [r["tick_volume"] for r in rates]
            avg  = sum(vols[:-1]) / len(vols[:-1])
            if avg > 0:
                result["volume_ratio"] = round(vols[-1] / avg, 3)
    except Exception:
        pass
    try:
        from features.trading.data_collector import get_ml_db
        conn = sqlite3.connect(runtime.ml_db_path)
        rows = conn.execute(
            "SELECT outcome FROM trade_signals WHERE symbol=? AND outcome IS NOT NULL "
            "ORDER BY COALESCE(closed_at, timestamp) DESC LIMIT 10",
            (symbol,)
        ).fetchall()
        if rows:
            outcomes = [r[0] for r in rows]
            result["symbol_winrate"] = round(sum(outcomes) / len(outcomes), 3)
            streak = 0
            for o in outcomes:
                if o == 0:
                    streak += 1
                else:
                    break
            result["consecutive_losses"] = streak
        conn.close()
    except Exception:
        pass
    return result


def check_symbol_loss_streaks():
    """
    Pause any symbol with 2+ consecutive losses in the CURRENT session —
    counting BOTH closed losses AND currently-open positions that are
    underwater. Closed-only counting (and a 3-loss threshold) let a real
    losing streak run too long: trade N+1 often opens before trade N has
    even closed, so the closed-trade count lags reality. This is what let
    4 straight same-direction BTC losses fire — the streak-pause never
    saw 3 CLOSED losses in time to block the 4th entry.

    Reads from scalp_log, not trade_signals — trade_signals only ever
    gets a row via save_signal(), which is LG-primary only. S/R and
    M1-confirmation losses were completely invisible to this check until
    this fix, since those two strategies never write to trade_signals at
    all (same gap found earlier for strategy-stats / build_meta_dataset).

    closed_streak and open_streak were ALSO checked as two separate
    buckets, each needing 2 on its own — so 1 closed loss + 1 still-open
    underwater position (very common: trade N+1 opens a minute before
    trade N closes) was never caught, since neither bucket alone hit 2.
    mixed_streak below combines them: the most recent CLOSED result (if
    a loss) plus any currently-open underwater position for the same
    symbol now count together as a real 2-in-a-row.
    """
    session_start = scalp_state.get("session_start")
    if not session_start:
        return
    try:
        from core.database import get_db
        conn = get_db()
        c = conn.cursor()

        from features.trading.risk_manager import BOT_MAGIC
        try:
            open_positions = mt5.positions_get() or []
        except Exception:
            open_positions = []

        for symbol in scalp_state["settings"].get("enabled_symbols", []):
            c.execute(
                "SELECT outcome FROM scalp_log WHERE symbol=? AND outcome IS NOT NULL "
                "AND timestamp >= ? "
                "ORDER BY COALESCE(closed_at, timestamp) DESC LIMIT 2",
                (symbol, session_start)
            )
            recent_closed  = [r[0] for r in c.fetchall()]
            closed_streak  = len(recent_closed) >= 2 and all(o == 0 for o in recent_closed)

            # Currently open + underwater positions count too — catches a
            # fast same-direction sequence before any of them officially close.
            underwater = [
                p for p in open_positions
                if p.magic == BOT_MAGIC and symbol.upper() in p.symbol.upper() and p.profit < 0
            ]
            open_streak  = len(underwater) >= 2
            mixed_streak = (len(recent_closed) >= 1 and recent_closed[0] == 0
                             and len(underwater) >= 1)

            if closed_streak or open_streak or mixed_streak:
                now = datetime.now()
                if symbol not in symbol_paused_until or now >= symbol_paused_until[symbol]:
                    resume_at = now + timedelta(hours=1)
                    symbol_paused_until[symbol] = resume_at
                    if closed_streak:
                        reason = "2 consecutive closed losses"
                    elif open_streak:
                        reason = f"{len(underwater)} open positions underwater"
                    else:
                        reason = "1 closed loss + 1 open position underwater"
                    print(f"[STREAK] {symbol} {reason} this session → "
                          f"paused 1h until {resume_at.strftime('%H:%M')}")
        conn.close()
    except Exception as e:
        print(f"[STREAK] check error: {e}")


def _ema_macro_trend(symbol: str, tf: str) -> str:
    """EMA20-slope trend on a higher timeframe — bullish/bearish/neutral.
    Matches the definition validated in test_1h_vs_4h.py (2026-07-23):
    requiring BOTH 1h and 4h to agree with the signal = +0.12R vs +0.07R
    (4h-only), and it blocks downtrend-buys earlier because 1h flips first."""
    try:
        df = fetch_candles(symbol, tf, limit=60)
        if df is None or len(df) < 25:
            return "neutral"
        ema = df["close"].ewm(span=20).mean()
        c, e, e_prev = df["close"].iloc[-1], ema.iloc[-1], ema.iloc[-3]
        if c > e and e > e_prev:
            return "bullish"
        if c < e and e < e_prev:
            return "bearish"
        return "neutral"
    except Exception:
        return "neutral"


def _asset_class(symbol: str) -> str:
    """Group symbols so a reversal in one market doesn't freeze another
    (a bad BTC-buy shouldn't block a good gold-buy)."""
    s = (symbol or "").upper()
    if s in ("BTC", "ETH", "SOL", "BNB", "XRP"):
        return "crypto"
    if s in ("XAUUSD", "XAGUSD"):
        return "metals"
    return "forex"


def update_direction_breaker(consec: int = 2, block_hours: int = 4):
    """PER-ASSET-CLASS directional circuit breaker (the real 4H-lag fix).

    For each asset class (crypto / metals / forex) independently: if the most
    recent `consec` resolved trades IN THAT CLASS are same-direction SL losses,
    that (class, direction) is blocked for `block_hours` from the last loss.
    Per-class (not global) so a crypto reversal doesn't freeze gold/forex;
    per-class (not per-symbol) so it still catches a correlated crash within a
    class. Any win breaks the streak; breakeven (|pnl|<0.15) ignored. Stateless
    replay from scalp_log, survives restart. Original GLOBAL version validated
    on 07-20→22 (-$29.86 → +$13.74); reversal test 07-23 confirmed no profitable
    counter-trade exists during a block, so the block only sits out chop.
    Duration 6h→4h and global→per-class on 2026-07-23 (user tuning).
    """
    try:
        from core.database import get_db
        day_start = scalp_state.get("session_start") or datetime.now().strftime("%Y-%m-%dT00:00:00")
        conn = get_db()
        rows = conn.execute(
            "SELECT symbol, action, outcome, profit, COALESCE(closed_at, timestamp) ct "
            "FROM scalp_log WHERE outcome IS NOT NULL AND timestamp >= ? "
            "ORDER BY COALESCE(closed_at, timestamp) DESC LIMIT 40",
            (day_start,)
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"[DIR-BREAKER] read error: {e}")
        return

    now = datetime.now()
    direction_breaker.clear()
    for cls in ("crypto", "metals", "forex"):
        streak_dir, streak, last_loss_ct = None, 0, None
        for symbol, action, outcome, profit, ct in rows:      # most-recent first
            if _asset_class(symbol) != cls:
                continue
            is_loss = outcome == 0 and abs(profit or 0) >= 0.15
            is_win = outcome == 1 and (profit or 0) > 0.15
            if not is_loss and not is_win:
                continue                                       # breakeven → ignore
            if is_win:
                break                                          # a win ends the streak
            if streak_dir is None:
                streak_dir, streak, last_loss_ct = action, 1, ct
            elif action == streak_dir:
                streak += 1
            else:
                break                                          # opposite-dir loss ends streak
        if streak >= consec and last_loss_ct:
            try:
                until = datetime.fromisoformat(last_loss_ct) + timedelta(hours=block_hours)
            except Exception:
                until = now + timedelta(hours=block_hours)
            if until > now:
                direction_breaker[(cls, streak_dir)] = until
                rem = int((until - now).total_seconds() // 60)
                print(f"[DIR-BREAKER] {cls} {streak_dir.upper()} blocked {rem}min "
                      f"({consec}+ consecutive {cls} {streak_dir} SL losses)")


def _update_direction_locks():
    """
    Block a trade direction for 2h after 2 consecutive closed losses in that
    direction. Separate from the symbol-level pause: that blocks ALL trades
    on a symbol; this only blocks ONE direction, allowing the opposite.
    E.g. two consecutive EURUSD SELL losses → SELL blocked 2h, BUY still allowed.
    """
    # Rolling 24h window — losses from yesterday still count so the lock
    # survives midnight resets and server restarts.
    now = datetime.now()
    today_start = (now - timedelta(hours=24)).isoformat()
    try:
        from core.database import get_db
        conn = get_db()
        c = conn.cursor()
        symbols = scalp_state["settings"].get("enabled_symbols", [])
        for symbol in symbols:
            for direction in ("buy", "sell"):
                c.execute(
                    "SELECT outcome FROM scalp_log "
                    "WHERE symbol=? AND action=? AND outcome IS NOT NULL "
                    "AND timestamp >= ? "
                    "ORDER BY COALESCE(closed_at, timestamp) DESC LIMIT 2",
                    (symbol, direction, today_start)
                )
                recent = [r[0] for r in c.fetchall()]
                if len(recent) >= 2 and recent[0] == 0 and recent[1] == 0:
                    key = f"{symbol}_{direction}"
                    if key not in direction_blocked or now >= direction_blocked[key]:
                        expire = now + timedelta(hours=4)
                        direction_blocked[key] = expire
                        print(f"[DIR-LOCK] {symbol} {direction}: 2 consecutive losses "
                              f"→ direction blocked 4h until {expire.strftime('%H:%M')}")
        conn.close()
    except Exception as e:
        print(f"[DIR-LOCK] update error: {e}")


def is_trading_session(symbol: str) -> bool:
    """Vérifie si c'est une session active pour ce symbole."""
    hour_utc = datetime.now(timezone.utc).hour
    # ── Zone morte 21h→02h serveur — TOUS symboles ──────────────────────
    # Étude 2026-07-15 sur les 336 trades historiques : cette fenêtre
    # (fin NY → pré-Asie, liquidité morte) = 20.7% WR sur 58 trades,
    # -$65 — toutes les autres sessions sont à 36-44% WR. Les 4 pertes
    # de la nuit du 14→15/07 étaient toutes dedans (whipsaw de range).
    if hour_utc >= 21 or hour_utc < 2:
        return False
    if symbol in ["BTC", "ETH", "SOL", "BNB", "XRP"]:
        return True                              # Crypto → 24/7
    if symbol in ["XAUUSD", "XAGUSD"]:
        return True                              # Gold trades 24h (Asian session active 00-08 UTC)
    if symbol in ["GBPJPY", "EURUSD"]:
        return 7 <= hour_utc <= 16               # London session
    if symbol == "USDJPY":
        return (0 <= hour_utc <= 9) or (7 <= hour_utc <= 16)   # Tokyo + London
    return True


def get_obv_bias(symbol: str, tf: str = "1h", window: int = 20) -> str:
    """
    On-Balance Volume slope: rising OBV = institutional buying (bullish),
    declining OBV = institutional selling (bearish).
    Normalised by total volume so the threshold is symbol-agnostic.
    Returns 'bullish', 'bearish', or 'neutral'.
    """
    try:
        df = fetch_candles(symbol, tf, limit=window + 10)
        if len(df) < window:
            return "neutral"
        close  = df['close'].values[-window:]
        volume = df['volume'].values[-window:]
        obv = [0.0]
        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv.append(obv[-1] + volume[i])
            elif close[i] < close[i - 1]:
                obv.append(obv[-1] - volume[i])
            else:
                obv.append(obv[-1])
        q         = max(window // 4, 1)
        old_avg   = sum(obv[:q]) / q
        new_avg   = sum(obv[-q:]) / q
        total_vol = sum(volume) or 1
        norm      = (new_avg - old_avg) / total_vol
        if norm > 0.10:
            return "bullish"
        elif norm < -0.10:
            return "bearish"
        return "neutral"
    except Exception as e:
        print(f"[OBV] {symbol}: error — {e}")
        return "neutral"


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
            df_htf    = fetch_candles(symbol, tf, limit=250)
            structure = detect_market_structure(df_htf)
            trend     = structure.get("trend", "neutral")

            # Fresh CHoCH override: catches a structure break the moment
            # price closes beyond the last confirmed swing point, instead
            # of waiting for a NEW swing point to confirm (multi-hour lag).
            from features.smc.structure import detect_fresh_choch
            fresh = detect_fresh_choch(df_htf)
            if fresh["choch"] and fresh["choch"] != trend:
                print(f"[CHOCH] {symbol} {tf}: fresh {fresh['choch']} break "
                      f"@ {fresh['broke_level']:.5f} → overriding stale trend={trend}")
                trend = fresh["choch"]

            trends[tf] = trend
        except Exception:
            trends[tf] = "neutral"

    non_neutral = [t for t in trends.values() if t != "neutral"]

    if not non_neutral:
        # Structure gave no confirmed BOS on any timeframe.
        # Use 1h EMA50/200 as tiebreaker — catches slow downtrends that
        # never print a clean BOS but are clearly below their moving average.
        # 0.1% gap threshold prevents false signals right at the crossover.
        try:
            from features.smc.engine import calculate_ema
            df_1h = fetch_candles(symbol, "1h", limit=250)
            if len(df_1h) >= 200:
                ema50  = calculate_ema(df_1h, 50)
                ema200 = calculate_ema(df_1h, 200)
                if ema50 > ema200 * 1.001:
                    consensus = "bullish"
                    print(f"[HTF] {symbol}: structure neutral → EMA50>200 on 1h → bullish bias")
                elif ema200 > ema50 * 1.001:
                    consensus = "bearish"
                    print(f"[HTF] {symbol}: structure neutral → EMA200>50 on 1h → bearish bias")
                else:
                    consensus = "neutral"
            else:
                consensus = "neutral"
        except Exception:
            consensus = "neutral"
    elif all(t == "bullish" for t in non_neutral):
        consensus = "bullish"
    elif all(t == "bearish" for t in non_neutral):
        consensus = "bearish"
    else:
        consensus = "conflict"   # HTFs ne s'accordent pas → bloqué

    return {"trends": trends, "consensus": consensus}


def get_ema_confluence(symbol: str) -> dict:
    """
    50/200 EMA regime on 5m + 30m + 1h. Bonus-only signal — never blocks.
    Counter-trend scalps (e.g. a clean 5m SELL while 1H is bullish) still
    fire normally with no penalty; this only adds a bonus on the rare,
    high-conviction case where ALL THREE timeframes agree on direction.
    """
    from features.smc.engine import calculate_ema

    tfs     = ["5m", "30m", "1h"]
    regimes = {}
    for tf in tfs:
        try:
            df = fetch_candles(symbol, tf, limit=250)
            if len(df) < 200:
                regimes[tf] = "neutral"
                continue
            ema50  = calculate_ema(df, 50)
            ema200 = calculate_ema(df, 200)
            regimes[tf] = "bullish" if ema50 > ema200 else "bearish"
        except Exception:
            regimes[tf] = "neutral"

    non_neutral = [r for r in regimes.values() if r != "neutral"]
    if len(non_neutral) == len(tfs) and len(set(non_neutral)) == 1:
        confluence = non_neutral[0]
    else:
        confluence = "mixed"

    return {"regime": confluence, "tfs": regimes}


# ── MFE/MAE excursion tracking ────────────────────────────────────────────
# ticket → {"risk": original R, "mfe_r": max excursion, "mae_r": min excursion}
# Original risk is read from trade_signals on first sight because the live
# pos.sl may already be at breakeven (risk would compute as 0).
_excursion_cache: dict = {}


def _track_excursion(ticket, symbol, entry, live_sl, moved, is_buy):
    """Record how far each trade went for/against us, in R units."""
    ex = _excursion_cache.get(ticket)
    if ex is None:
        risk = 0.0
        try:
            from core.database import get_db
            conn = get_db()
            row  = conn.execute(
                "SELECT entry, sl FROM trade_signals WHERE ticket=?", (ticket,)
            ).fetchone()
            conn.close()
            if row and row[0] is not None and row[1] is not None:
                risk = abs(row[0] - row[1])
        except Exception:
            pass
        if risk <= 0:   # not in DB (S/R, M1 trades) → live SL as fallback
            risk = (entry - live_sl) if is_buy else (live_sl - entry)
        if risk <= 0:
            return      # SL already moved and no DB record — cannot compute R
        ex = _excursion_cache[ticket] = {"risk": risk, "mfe_r": 0.0, "mae_r": 0.0}

    r = moved / ex["risk"]
    changed = False
    if r > ex["mfe_r"]:
        ex["mfe_r"] = r
        changed = True
    if r < ex["mae_r"]:
        ex["mae_r"] = r
        changed = True
    if changed:
        try:
            from features.trading.data_collector import update_excursion
            update_excursion(ticket, ex["mfe_r"], ex["mae_r"])
        except Exception as _exe:
            print(f"[MFE] persist error t={ticket}: {_exe}")


def _prune_excursion_cache(open_tickets: set):
    """Drop closed tickets from the cache (their final values are already saved)."""
    for t in list(_excursion_cache):
        if t not in open_tickets:
            del _excursion_cache[t]


def manage_open_positions():
    """
    Two-step exit — runs every 30 seconds:

      1.5R profit  → breakeven (SL moves to entry, zero loss guaranteed)
      70% toward TP → dynamic trail: max(5% of TP dist, 10% of current profit)

    BE trigger is expressed in R (risk units), not % of TP distance:
    the old 30%-of-TP trigger fired at ≈0.35R with historical RR=1.18 —
    price barely moved, SL jumped to entry, normal noise retraced and
    scratched the trade (16% of ALL trades died at BE). At 1R the move
    has proven itself before we protect it.

    2026-07-23: BE trigger moved 1R → 1.5R. Exit study on ~28k with-trend
    trades (BTC/ETH/XAU/XAG, both split-halves): BE@1.0 was strangling
    pullback-then-continue winners (exit at 0 instead of +2.5R). BE@1.5R
    beat it by ~+0.05R robustly; no-ratchet was best (+0.10R) but higher
    variance — 1.5R chosen as the balanced middle. Reversible one-liner.

    Trail buffer scales with unrealized profit so big runners (800+ pts BTC)
    aren't stopped by 5-pt noise. A 800-pt BTC move gives 80-pt buffer;
    MT5's TP at 4× still fires if price runs cleanly without reversal.
    """
    _mt5_init()
    from features.trading.risk_manager import BOT_MAGIC

    positions = mt5.positions_get()
    if not positions:
        if _excursion_cache:
            _prune_excursion_cache(set())
        return

    _prune_excursion_cache({p.ticket for p in positions if p.magic == BOT_MAGIC})

    for pos in positions:
        if pos.magic != BOT_MAGIC:
            continue

        entry  = pos.price_open
        sl     = pos.sl
        tp     = pos.tp
        ticket = pos.ticket

        if tp == 0 or sl == 0:
            continue

        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            continue

        is_buy     = pos.type == 0
        price      = tick.bid if is_buy else tick.ask
        total_dist = abs(tp - entry)
        if total_dist == 0:
            continue

        moved    = (price - entry) if is_buy else (entry - price)
        progress = moved / total_dist   # 0.0 = entry, 1.0 = TP

        _track_excursion(ticket, pos.symbol, entry, sl, moved, is_buy)

        new_sl = sl
        action = None

        # ── 70% → trail tightly, let TP fire naturally ────────────────────
        if progress >= 0.70:
            # Buffer scales with current profit so big moves (800+ pts on BTC)
            # aren't stopped out by tiny 5-pt noise. 10% of unrealized profit
            # beats 5% of original TP once the trade runs far past TP.
            buffer = max(total_dist * 0.05, moved * 0.10)
            if is_buy:
                candidate = round(price - buffer, 5)
                if candidate > sl:
                    new_sl = candidate
                    action = "trail"
            else:
                candidate = round(price + buffer, 5)
                if candidate < sl:
                    new_sl = candidate
                    action = "trail"

        # ── 1.5R profit → breakeven — zero loss, full upside still open ───
        # risk > 0 also means SL is still on the loss side (original SL);
        # after the BE move risk becomes 0 and this block never re-fires.
        # Trigger 1.5R (was 1.0R) — exit study 2026-07-23: at 1.0R the BE
        # scratched pullback-continue winners; 1.5R lets them breathe first.
        else:
            risk = (entry - sl) if is_buy else (sl - entry)
            if risk > 0 and moved >= risk * 1.5:
                new_sl = entry
                action = "breakeven"

        if action is None or new_sl == sl:
            continue

        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol":   pos.symbol,
            "sl":       new_sl,
            "tp":       tp,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"[POS/{action.upper()}] {pos.symbol} ticket={ticket} "
                  f"progress={progress:.0%} SL: {sl} → {new_sl}")
        else:
            err = result.comment if result else mt5.last_error()
            print(f"[POS/ERR] {pos.symbol} ticket={ticket}: {err}")

    # Signal invalidation DISABLED — was causing whipsaws from analysis noise.
    # SMC analysis oscillates BUY/SELL within 30s at candle edges, closing correct trades.
    # Position management: breakeven at 1R, tight trail at 70% of TP distance.


def scalp_auto_scan():
    """Appelé automatiquement toutes les minutes par le scheduler"""
    acquired = _scan_lock.acquire(blocking=False)
    if not acquired:
        print("[SCAN] Previous scan still running — skipping to prevent duplicate trades")
        return
    try:
        _scalp_auto_scan_inner()
    finally:
        _scan_lock.release()


def _scalp_auto_scan_inner():
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
        scalp_state["daily_pnl"] = get_daily_pnl_pct(scalp_state.get("session_start"))
    except Exception:
        pass

    # ── Limites journalières ──────────────────────────────────────────────────
    # Daily loss limit — ACTIVE (user setting, default 15%)
    max_loss = settings.get("max_daily_loss_pct", 0.0)
    if max_loss > 0:
        daily_pnl = scalp_state.get("daily_pnl", 0.0)
        if daily_pnl <= -max_loss:
            scalp_state["running"]        = False
            scalp_state["stopped_reason"] = f"Daily loss limit hit: {daily_pnl:.1f}% (limit: -{max_loss}%)"
            print(f"[STOP] Daily loss limit hit: {daily_pnl:.1f}% <= -{max_loss}%")
            return

    # ── MIXED-OPTIMAL survival breaker (2026-07-23) — the floor June lacked.
    # DAILY loss is now controlled ENTIRELY by the app's max_daily_loss_pct
    # setting (user choice 2026-07-24: "always accept daily loss from the app").
    # The only code-enforced floor left is the DRAWDOWN halt: -35% below session
    # peak equity → full stop, no auto-reset. This is the ultimate wipe-preventer
    # (June had none → account went to $0 in July); it stays regardless of app.
    try:
        _acct = mt5.account_info()
        _eq = _acct.equity if _acct else None
    except Exception:
        _eq = None
    if _eq and _eq > 0:
        _peak = max(scalp_state.get("peak_equity") or _eq, _eq)
        scalp_state["peak_equity"] = _peak
        _dd = (_eq - _peak) / _peak * 100.0
        if _dd <= -35.0:
            scalp_state["running"]        = False
            scalp_state["stopped_reason"] = (f"SURVIVAL drawdown halt: equity ${_eq:.2f} "
                                             f"is {_dd:.0f}% below peak ${_peak:.2f} — manual review")
            print(f"[SURVIVAL] DRAWDOWN HALT {_dd:.0f}% below peak ${_peak:.2f} — full stop")
            return

    # Balance protection disabled — bot runs regardless of balance
    try:
        pass
    except Exception:
        pass

    if settings["max_daily_trades"] > 0 and scalp_state["trades_today"] >= settings["max_daily_trades"]:
        scalp_state["running"]        = False
        scalp_state["stopped_reason"] = f"Daily trade limit hit: {scalp_state['trades_today']}"
        print(f"STOP daily trade limit: {scalp_state['trades_today']}")
        return

    # Vérifier les trades fermés et mettre à jour les outcomes DB
    check_and_update_outcomes()

    # Pause symbols with 3 consecutive losses for 1h
    check_symbol_loss_streaks()

    # GLOBAL directional circuit breaker — la vraie correction du "4H lag".
    # Le trend 4H (structure BOS) retarde de plusieurs heures un retournement :
    # le bot a acheté un marché qui chutait 32 fois de suite (07-20→22), tous
    # les SELL bloqués par MTF-contra. Ce breaker : après 2 pertes SL
    # CONSÉCUTIVES dans le même sens (tous symboles confondus), bloque ce sens
    # 6h. Validé sur la période réelle : -$29.86 → +$13.74 (les 2 gros gains
    # or bloqués sont déjà déduits). Rejoue la série depuis scalp_log →
    # survit aux redémarrages.
    update_direction_breaker(consec=2, block_hours=4)

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

    def _log_rejection(sym, reason, **extra):
        entry = {"ts": now.isoformat(), "symbol": sym, "reason": reason, **extra}
        scan_rejections.append(entry)
        if len(scan_rejections) > 200:
            scan_rejections.pop(0)
        try:
            from features.trading.data_collector import save_rejection
            save_rejection(entry)
        except Exception as _rle:
            print(f"[REJECTION-LOG] persist error: {_rle}")

    for symbol in settings["enabled_symbols"]:

        # ── Filtre session : évite les marchés fermés / spreads larges ──────
        if not is_trading_session(symbol):
            continue   # silencieux — trop fréquent pour logger

        # ── Pause symbole après pertes consécutives (1h) ─────────────────
        # Silencieux — déjà annoncé une fois par check_symbol_loss_streaks()
        # quand la pause démarre. Logger ici à CHAQUE scan (toutes les
        # minutes pendant 1h) noyait le scan-log et masquait les vraies
        # rejections (HTF, P/D, counter-trend...) derrière du bruit répété.
        if symbol in symbol_paused_until and now < symbol_paused_until[symbol]:
            continue

        # ── BTC Lead-Lag scoring boost (no cooldown bypass) ─────────────
        btc_lead_age = (now - btc_lead_signal["ts"]).total_seconds() / 60 if btc_lead_signal.get("ts") else 999
        btc_lead_active = (symbol in ["ETH", "SOL"] and btc_lead_age <= 45)

        # ── Cooldown temporel après trade réussi ─────────────────────────
        if symbol in scalp_cooldowns and now < scalp_cooldowns[symbol]:
            remaining = int((scalp_cooldowns[symbol] - now).total_seconds() // 60)
            print(f"[~] {symbol} cooldown {remaining}min restant → skip")
            _log_rejection(symbol, f"cooldown {remaining}min")
            continue

        # ── News filter: block trades near high-impact events ────────────
        try:
            from features.news.calendar import is_news_blocked, get_news_momentum
            news_block = is_news_blocked(symbol)
            if news_block["blocked"]:
                print(f"[NEWS] {symbol} blocked: {news_block['reason']}")
                _log_rejection(symbol, f"news_block: {news_block['reason']}")
                continue
        except Exception as _ne:
            news_block = {"blocked": False}
            print(f"[NEWS] calendar error: {_ne}")

        # ── Max 2 positions par symbole ───────────────────────────────────────
        sym_positions = [p for p in _all_open
                         if p.magic == BOT_MAGIC and symbol.upper() in p.symbol.upper()]
        if len(sym_positions) >= 2:
            print(f"[!]  {symbol} 2/2 positions ouvertes → skip")
            _log_rejection(symbol, "max_positions 2/2")
            continue
        # 2nd trade allowed only if: same bias + better price + cooldown already respected above
        _second_trade_check = None
        if len(sym_positions) == 1:
            _existing = sym_positions[0]
            _second_trade_check = {
                "action":      "buy" if _existing.type == 0 else "sell",
                "entry_price": _existing.price_open,
            }

        # ── Filtre MTF — tendance Higher Timeframe (1H default) ─────────
        htf_tf   = settings.get("htf_timeframe", ["1h"])
        htf_data = get_htf_trend(symbol, htf_tf)
        consensus = htf_data["consensus"]

        # 50/200 EMA confluence across 5m+30m+1h — bonus signal only.
        ema_confluence = get_ema_confluence(symbol)

        # Conflit entre HTFs → 1H maître SEULEMENT si la LTF est neutre (pas de
        # structure confirmée opposée). Si la LTF a un BOS/CHoCH confirmé contre
        # 1H, c'est un vrai signal de retournement — pas un pullback — on skip.
        ltf_label = next((t for t in htf_tf if t != "1h"), htf_tf[0])
        htf_blocked_for_lg = False
        h1_master_dir      = None   # conflit confirmé mais 1H directionnel → entrées côté 1H uniquement
        if consensus == "conflict":
            h1_trend  = htf_data["trends"].get("1h", "neutral")
            ltf_trend = htf_data["trends"].get(ltf_label, "neutral")
            if ltf_trend == "neutral" and h1_trend in ("bullish", "bearish"):
                consensus = h1_trend   # LTF calme, 1H tranche → pullback entry
                print(f"[HTF] {symbol} {ltf_label} neutral → 1H={h1_trend} master (pullback entry)")
            elif h1_trend in ("bullish", "bearish"):
                # Étude contrefactuelle des 86 épisodes bloqués (11→13/07,
                # bracket ATR RR2.5) : suivre le côté 5m = 19.3% WR (-0.33R)
                # → reste bloqué ; suivre le côté 1H = 36.1% WR (+0.27R)
                # → autorisé. Le 5m qui monte contre un 1H baissier est un
                # pullback à vendre, pas un retournement à suivre. Les autres
                # gates (P/D, momentum, macro-4H, zones, ML, blended) filtrent.
                h1_master_dir = h1_trend
                print(f"[HTF] {symbol} conflict {htf_data['trends']} → 1H master: "
                      f"{'buy' if h1_trend == 'bullish' else 'sell'} entries only")
            else:
                print(f"[!] HTF conflict {symbol}: {htf_data['trends']} → LG-primary skipped "
                      f"(pas de direction 1H pour arbitrer)")
                _log_rejection(symbol, f"htf_conflict_confirmed {htf_data['trends']}")
                htf_blocked_for_lg = True

        # OBV bias — computed once per symbol, reused inside the tf loop below.
        # Rising OBV = institutional buying = bullish. Declining = bearish.
        obv_bias = get_obv_bias(symbol)
        if obv_bias != "neutral":
            print(f"[OBV] {symbol}: {obv_bias} volume bias on 1h")

        # Tendance macro 4H — calculée une fois par symbole. Passée à l'analyse
        # pour assouplir les vetos counter-trend/P/D sur les pullbacks alignés
        # 4H, et réutilisée par le gate macro-4H crypto plus bas.
        try:
            _macro4h = get_htf_trend(symbol, ["4h"]).get("consensus", "neutral")
        except Exception as _m4e:
            _macro4h = "neutral"
            print(f"[MACRO-4H] {symbol}: fetch failed ({_m4e}) — neutral")

        # EMA20-slope trends on 1h + 4h — the "require BOTH to agree" filter
        # (test_1h_vs_4h 2026-07-23: +0.12R vs +0.07R, blocks downtrend-buys
        # earlier since 1h flips before 4h). Computed once per symbol.
        _ema_1h = _ema_macro_trend(symbol, "1h")
        _ema_4h = _ema_macro_trend(symbol, "4h")

        _fallback_traded = False   # at most one fallback trade (S/R or M1) per symbol per scan

        # ── Market regime — applies to ALL strategies, not a direction call ─
        # Not a hard block: raises the score bar during choppy conditions
        # instead of blocking outright, so an exceptionally strong signal
        # can still get through. Targets the recurring pattern where
        # structure/CHoCH/EMA/momentum all agreed and were wrong together —
        # that happens specifically when ADX is low AND the symbol has been
        # whipsawing between HTF agreement and conflict recently.
        try:
            from features.regime.detector import get_market_regime
            regime_info = get_market_regime(symbol)
        except Exception as _re:
            regime_info = {"regime": "trending", "adx_1h": None, "recent_conflicts": 0}
            print(f"[REGIME] {symbol}: error — {_re}")

        is_choppy = regime_info["regime"] == "choppy"
        if is_choppy:
            print(f"[REGIME] {symbol}: CHOPPY (ADX(1H)={regime_info['adx_1h']}, "
                  f"{regime_info['recent_conflicts']} recent HTF conflicts) — "
                  f"raising LG score bar, disabling M1 fallback (S/R still active)")

        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=100)
                analysis = run_scalping_analysis(df, symbol, macro_trend=_macro4h)

                if htf_blocked_for_lg:
                    # Hard block — conflit sans direction 1H pour arbitrer.
                    analysis["scalping_score"] = 0
                    analysis["should_scalp"]   = False
                    analysis["conditions"]     = (
                        [f"HTF conflict hard block ({htf_data['trends']}) — "
                         f"no 1H direction to arbitrate"]
                    )
                    print(f"[HTF-BLOCK] {symbol} {tf}: conflict {htf_data['trends']} → LG-primary blocked")
                elif h1_master_dir and analysis.get("should_scalp"):
                    # Conflit avec 1H directionnel : seul le côté 1H peut trader.
                    # (étude : côté 5m 19.3% WR → bloqué ; côté 1H 36.1% WR → OK)
                    _need = "buy" if h1_master_dir == "bullish" else "sell"
                    _sig  = analysis.get("bias")
                    if _sig and _sig != _need:
                        analysis["scalping_score"] = 0
                        analysis["should_scalp"]   = False
                        analysis["conditions"]     = (
                            [f"HTF conflict: signal {_sig} vs 1H {h1_master_dir} "
                             f"— only {_need} allowed (1H master)"]
                        )
                        print(f"[HTF-1H] {symbol} {tf}: {_sig} blocked — 1H master allows {_need} only")

                # ── XAUUSD specific rules ─────────────────────────────────
                # Gold needs stronger trend confirmation and no approaching zones
                # (ATR noise on 15m is too large for approach entries)
                if symbol == "XAUUSD" and analysis.get("should_scalp"):
                    adx_val = analysis.get("adx", 0)
                    if adx_val < 20:
                        analysis["should_scalp"]   = False
                        analysis["scalping_score"] = 0
                        analysis["conditions"]     = [f"XAUUSD: ADX {adx_val:.1f} < 20 — need stronger trend"]
                    elif "approaching" in str(analysis.get("scalping_entry", {}).get("description", "")):
                        analysis["should_scalp"]   = False
                        analysis["scalping_score"] = 0
                        analysis["conditions"]     = ["XAUUSD: approaching zone rejected — inside zone only"]

                ta_score  = analysis.get("scalping_score", 0)
                min_score = settings.get("min_score", 75)
                if is_choppy:
                    min_score += 7    # 75 → 82: slightly tighter, still reachable for clean LG setups

                # Weekend liquidity is thin (crypto only — forex is closed):
                # Saturday WR was 31.6% over 57 trades. Demand a stronger setup.
                if now.weekday() >= 5:
                    min_score += 10

                if not analysis.get("should_scalp") or ta_score < min_score:
                    conds = " | ".join(analysis.get("conditions", []))
                    print(f"SKIP {symbol} {tf}: score={ta_score}/{min_score} [{conds}]")
                    _log_rejection(symbol, f"score={ta_score:.1f}/{min_score}", conditions=conds,
                                   price=analysis.get("current_price"), bias=analysis.get("bias"),
                                   timeframe=tf)

                    # ── Fallback signals: LG found nothing usable ────────────
                    # Two independent paths, tried in order, at most one
                    # fallback trade per symbol per scan cycle:
                    #   1. Support/Resistance — ranging markets: buy near
                    #      support, sell near resistance
                    #   2. M1 momentum — trending markets: a strong, clear
                    #      M1(1h) move worth trading directly
                    # These are opposite-regime tools on purpose: S/R wins
                    # when momentum would chase a reversal, M1 wins when
                    # there's a real trend with no LG pattern to catch it.
                    def _try_fallback(sig: dict, signal_type: str, tag: str) -> bool:
                        if not sig.get("detected"):
                            return False
                        fb_action = sig["action"]

                        # ── HTF consensus gate ────────────────────────────────────
                        # LG-primary already rejects contra-HTF trades (lines above).
                        # Fallback had NO such check — it could still sell in a
                        # bullish 1h environment via S/R or M1. Fixed here.
                        if (consensus in ("bullish", "strong_bullish") and fb_action == "sell") or \
                           (consensus in ("bearish", "strong_bearish") and fb_action == "buy"):
                            print(f"[{tag}-HTF] {symbol}: {fb_action} fallback blocked — HTF {consensus}")
                            _log_rejection(symbol, f"{signal_type}_htf_block_{consensus}")
                            return False
                        # Even in conflict, the 1h is the master trend — don't
                        # trade against it via fallback (S/R/M1 are for range, not reversal)
                        if consensus == "conflict":
                            h1_dir = htf_data["trends"].get("1h", "neutral")
                            if (h1_dir == "bullish" and fb_action == "sell") or \
                               (h1_dir == "bearish" and fb_action == "buy"):
                                print(f"[{tag}-HTF] {symbol}: {fb_action} fallback blocked — 1H={h1_dir} (conflict)")
                                _log_rejection(symbol, f"{signal_type}_1h_block_{h1_dir}")
                                return False

                        # ── OBV counter-trend block (fallback) ───────────────────
                        if obv_bias == "bearish" and fb_action == "buy":
                            print(f"[{tag}-OBV] {symbol}: buy fallback blocked — declining OBV")
                            _log_rejection(symbol, f"{signal_type}_obv_contra_buy")
                            return False
                        if obv_bias == "bullish" and fb_action == "sell":
                            print(f"[{tag}-OBV] {symbol}: sell fallback blocked — rising OBV")
                            _log_rejection(symbol, f"{signal_type}_obv_contra_sell")
                            return False

                        # ── Direction lock ────────────────────────────────────────
                        _fb_dir_key = f"{symbol}_{fb_action}"
                        if _fb_dir_key in direction_blocked and datetime.now() < direction_blocked[_fb_dir_key]:
                            _fb_rem = int((direction_blocked[_fb_dir_key] - datetime.now()).total_seconds() // 60)
                            print(f"[{tag}-DIR-LOCK] {symbol}: {fb_action} direction locked {_fb_rem}min")
                            return False

                        # SL width gate (same as LG-primary)
                        _fb_sl_w = abs(sig["entry"] - sig["sl"])
                        _fb_atr  = sig.get("atr", 0)
                        if _fb_atr > 0 and _fb_sl_w > 2.5 * _fb_atr:
                            print(f"[{tag}] {symbol}: SL {_fb_sl_w:.3f} > 2.5×ATR({_fb_atr:.3f}) — skipped")
                            _log_rejection(symbol, f"{signal_type}_sl_too_wide: {round(_fb_sl_w,3)}")
                            return False

                        if _second_trade_check:
                            _chk_action = _second_trade_check["action"]
                            _chk_price  = _second_trade_check["entry_price"]
                            if fb_action != _chk_action:
                                print(f"[{tag}] {symbol}: {fb_action} != open {_chk_action} → skip")
                                return False
                            if (fb_action == "buy"  and sig["entry"] >= _chk_price) or \
                               (fb_action == "sell" and sig["entry"] <= _chk_price):
                                print(f"[{tag}] {symbol}: {fb_action} @ {sig['entry']} "
                                      f"not better than existing {_chk_price} → skip")
                                return False

                        lot_sizes  = settings.get("lot_sizes", {})
                        fixed_lot  = float(lot_sizes.get(symbol, 0))
                        confidence = min(0.5 + abs(sig.get("momentum", 0.5)) / 10
                                          + sig.get("pd_bonus", 0.0)
                                          + sig.get("m5_bonus", 0.0), 0.9)

                        print(f"[{tag}] {symbol} {fb_action} @ {sig['entry']} → standalone entry "
                              f"({sig['description']})")

                        fb_trade = place_trade(
                            symbol=       symbol,
                            action=       fb_action,
                            entry=        sig["entry"],
                            sl=           sig["sl"],
                            tp1=          sig["tp1"],
                            confidence=   confidence,
                            risk_percent= settings["risk_percent"],
                            max_trades=   settings["max_trades"],
                            fixed_lot=    fixed_lot,
                        )

                        if not fb_trade.get("success"):
                            print(f"[{tag}] {symbol}: place_trade failed — {fb_trade.get('reason')}")
                            _log_rejection(symbol, f"{signal_type}_place_trade_failed: {fb_trade.get('reason')}")
                            return False

                        cooldown_min = settings.get("cooldown_minutes", 8)
                        scalp_cooldowns[symbol] = now + timedelta(minutes=cooldown_min)
                        scalp_state["trades_today"] += 1
                        print(f"[!] {tag}: {symbol} {fb_action} ticket={fb_trade.get('ticket')}")

                        fb_entry = {
                            "timestamp":     now.isoformat(),
                            "symbol":        symbol,
                            "timeframe":     tf,
                            "action":        fb_action,
                            "signal_type":   signal_type,
                            "score":         None,
                            "blended_score": None,
                            "entry":         sig["entry"],
                            "sl":            sig["sl"],
                            "tp1":           sig["tp1"],
                            "rr":            sig["rr_ratio"],
                            "conditions":    [sig["description"]],
                            "result":        fb_trade,
                        }
                        scalp_history.append(fb_entry)
                        try:
                            from features.trading.data_collector import save_scalp_log
                            save_scalp_log(fb_entry)
                        except Exception as _sle:
                            print(f"[SCALP-LOG] persist error: {_sle}")
                        return True

                    if is_choppy:
                        # S/R is a RANGE tool — exactly right for choppy.
                        # M1 is a trend tool — disabled here (false signals in ranging markets).
                        _sr_allowed_c = False  # S/R disabled globally: 31% WR, -$51 over 200 trades
                        if not _fallback_traded and _sr_allowed_c:
                            try:
                                from features.momentum.sr_signal import get_support_resistance_signal
                                sr_sig_c = get_support_resistance_signal(symbol)
                            except Exception as _srce:
                                sr_sig_c = {"detected": False, "reason": f"error: {_srce}"}
                                print(f"[SR-SIGNAL] {symbol}: error — {_srce}")
                            if _try_fallback(sr_sig_c, "support_resistance", "SR-SIGNAL"):
                                _fallback_traded = True
                            else:
                                _log_rejection(symbol, f"sr_no_signal: {sr_sig_c.get('reason', '?')}",
                                               timeframe=tf, signal_type="support_resistance",
                                               detail={k: v for k, v in sr_sig_c.items() if k != "detected"})
                        if not _fallback_traded:
                            _log_rejection(symbol, "regime_choppy_m1_skipped",
                                           timeframe=tf, detail=regime_info)
                    elif not _fallback_traded:
                        # S/R is a range strategy — don't use it on BTC (trending
                        # asset: 19 trades, 37% WR, -$10.50 net historically).
                        # ETH S/R works (72% WR, +$4.23) because ETH ranges more.
                        _sr_allowed = False  # S/R disabled globally: 31% WR, -$51 over 200 trades
                        if _sr_allowed:
                            try:
                                from features.momentum.sr_signal import get_support_resistance_signal
                                sr_sig = get_support_resistance_signal(symbol)
                            except Exception as _sre:
                                sr_sig = {"detected": False, "reason": f"error: {_sre}"}
                                print(f"[SR-SIGNAL] {symbol}: error — {_sre}")
                        else:
                            sr_sig = {"detected": False, "reason": "sr_disabled_trending_asset"}

                        if _try_fallback(sr_sig, "support_resistance", "SR-SIGNAL"):
                            _fallback_traded = True
                        else:
                            # Every non-detection logged too — not just fires —
                            # so we have full coverage per symbol per strategy
                            # for future training, not just the rare positives.
                            _log_rejection(symbol, f"sr_no_signal: {sr_sig.get('reason', '?')}",
                                           timeframe=tf, signal_type="support_resistance",
                                           detail={k: v for k, v in sr_sig.items() if k != "detected"})

                            # M1 disabled globally: 27% WR, -$24.07 over 74 trades —
                            # below RR2.5 breakeven (28.6%). Bypassed zone/blended/
                            # macro-4H gates and produced every trade of the
                            # 07-10→07-11 losing streak (8 straight SL hits).
                            _m1_allowed = False
                            if not _m1_allowed:
                                m1_sig = {"detected": False, "reason": "m1_disabled_for_symbol"}
                                _log_rejection(symbol, "m1_disabled_for_symbol", timeframe=tf)
                            else:
                                try:
                                    from features.momentum.m1_signal import get_m1_momentum_signal
                                    m1_sig = get_m1_momentum_signal(symbol)
                                except Exception as _m1e:
                                    m1_sig = {"detected": False, "reason": f"error: {_m1e}"}
                                    print(f"[M1-SIGNAL] {symbol}: error — {_m1e}")

                            if _try_fallback(m1_sig, "m1_confirmation", "M1-SIGNAL"):
                                _fallback_traded = True
                            else:
                                _log_rejection(symbol, f"m1_no_signal: {m1_sig.get('reason', '?')}",
                                               timeframe=tf, signal_type="m1_confirmation",
                                               detail={k: v for k, v in m1_sig.items() if k != "detected"})

                    continue

                bias = analysis["bias"]

                # ── PER-ASSET-CLASS directional circuit breaker ──────────
                # After 2 consecutive SL losses in this direction WITHIN this
                # symbol's asset class (crypto/metals/forex), that side is
                # frozen 4h for that class only — stops buying a falling market
                # while the 4H trend lags, without freezing unrelated markets.
                _brk_key = (_asset_class(symbol), bias)
                if _brk_key in direction_breaker and now < direction_breaker[_brk_key]:
                    _rem = int((direction_breaker[_brk_key] - now).total_seconds() // 60)
                    print(f"[DIR-BREAKER] {symbol} {bias} blocked {_rem}min "
                          f"({_brk_key[0]} {bias} reversal protection)")
                    _log_rejection(symbol, f"dir_breaker_{bias}_{_rem}min",
                                   score=ta_score, bias=bias)
                    continue

                # ── Record BTC lead signal for ETH/SOL lead-lag ─────────
                if symbol == "BTC":
                    btc_lead_signal["bias"] = bias
                    btc_lead_signal["ts"]   = now
                    print(f"[LEAD] BTC fired {bias} score={ta_score} → ETH/SOL prioritized for 45min")

                # Rejet si contra-tendance HTF
                if consensus == "bearish" and bias == "buy":
                    conds = " | ".join(analysis.get("conditions", []))
                    print(f"[MTF] {symbol} {tf} BUY rejected (HTF: bearish) score={ta_score} [{conds}]")
                    _log_rejection(symbol, f"htf_contra BUY vs bearish", score=ta_score)
                    continue
                if consensus == "bullish" and bias == "sell":
                    conds = " | ".join(analysis.get("conditions", []))
                    print(f"[MTF] {symbol} {tf} SELL rejected (HTF: bullish) score={ta_score} [{conds}]")
                    _log_rejection(symbol, f"htf_contra SELL vs bullish", score=ta_score)
                    continue

                # ── HTF trend filter PER TRADING TIMEFRAME (user framework 2026-07-24)
                # Trade only in the direction of the higher-TF trend, mapped to the
                # signal's timeframe (avoids buying an HTF pullback / selling an HTF
                # rally):
                #     M5  -> filter with H1
                #     M15 -> filter with H4
                #     M30 -> filter with H4
                # EMA20-slope trend. Replaces the prior "require both 1h+4h" gate
                # at user request. (Note: test_1h_vs_4h had both marginally better,
                # +0.12R vs 1h-only +0.09 / 4h-only +0.07 — but this is the
                # standard top-down MTF approach and lets more valid trades through.)
                _filter_tf    = "1h" if tf in ("5m", "1m", "3m") else "4h"
                _filter_trend = _ema_1h if _filter_tf == "1h" else _ema_4h
                _need = "bullish" if bias == "buy" else "bearish"
                if _filter_trend != _need:
                    print(f"[HTF-FILTER] {symbol} {tf} {bias} rejected — "
                          f"{_filter_tf} trend={_filter_trend}, need {_need}")
                    _log_rejection(symbol,
                                   f"htf_filter_{tf}_vs_{_filter_tf}={_filter_trend}",
                                   score=ta_score, bias=bias)
                    continue

                # Macro-4H hard block SUPPRIMÉ (gate trial 2026-07-14, 35 épisodes
                # bloqués : 42.9% WR / +0.50R — il bloquait des trades gagnants,
                # notamment les sells score 95-120 des 13-14/07). L'arbitrage de
                # direction reste assuré par MTF_contra (-0.05R, justifié) +
                # 1H-master (-0.10R, justifié) + le waiver 4H dans le scoring.
                # _macro4h reste calculé plus haut et passé à l'engine.

                # OBV gate SUPPRIMÉ (gate trial 2026-07-14, 24 épisodes bloqués :
                # 54.2% WR / +0.90R — les trades qu'il bloquait étaient les plus
                # rentables de tous les gates). L'OBV reste calculé/loggé plus haut.

                # Direction lock SUPPRIMÉ (2026-07-14, demande utilisateur) —
                # punir une direction après 2 pertes = réagir au bruit.

                entry_data = analysis.get("scalping_entry")
                if not entry_data:
                    _log_rejection(symbol, "no_entry_data", score=ta_score)
                    continue

                # ── 2nd trade: same bias + strictly better price ──────────────
                if _second_trade_check:
                    new_entry   = entry_data["entry"]
                    _chk_action = _second_trade_check["action"]
                    _chk_price  = _second_trade_check["entry_price"]
                    if bias != _chk_action:
                        print(f"[2ND] {symbol}: {bias} ≠ open {_chk_action} → skip")
                        _log_rejection(symbol, f"2nd_direction {bias}!={_chk_action}")
                        continue
                    if bias == "buy" and new_entry >= _chk_price:
                        print(f"[2ND] {symbol}: BUY {new_entry} ≥ existing {_chk_price} → not better")
                        _log_rejection(symbol, f"2nd_buy_not_cheaper")
                        continue
                    if bias == "sell" and new_entry <= _chk_price:
                        print(f"[2ND] {symbol}: SELL {new_entry} ≤ existing {_chk_price} → not better")
                        _log_rejection(symbol, f"2nd_sell_not_higher")
                        continue
                    print(f"[2ND] {symbol}: {bias} @ {new_entry} vs existing {_chk_price} → better price ✓")

                # ── Zone memory gate (before ML — saves ML call on rejection) ──
                # Gate 1: hard reject if price is at a broken or historically
                #         weak zone (bounce_rate < 30% with ≥ 3 touches).
                # Gate 2 (below, at blended_score): zone boost is real —
                #         strong zone can rescue a news-penalized setup;
                #         no zone + negative news can sink it.
                zone_boost = 0.0
                try:
                    from features.zones.scorer import get_zone_boost
                    zone_boost = get_zone_boost(symbol, entry_data["entry"], bias, analysis)
                except Exception as _ze:
                    print(f"[ZONE] scorer error: {_ze}")

                if zone_boost <= -10:
                    print(f"[ZONE-GATE] {symbol} {tf} {bias.upper()}: REJECTED — "
                          f"broken/weak historical zone (boost={zone_boost:+.0f})")
                    _log_rejection(symbol, f"zone_broken_weak boost={zone_boost:+.0f}",
                                   score=ta_score, bias=bias)
                    continue

                # ── Extra features for ML ────────────────────────────────
                extra = get_extra_features(symbol, analysis, entry_data["entry"])

                # ── ML decision (3-class: buy / sell / no_trade) ─────────
                htf_tf_str  = htf_tf[0] if isinstance(htf_tf, list) else htf_tf
                ml_decision = predict_decision(analysis, entry_data, consensus, htf_tf_str, extra, symbol=symbol)
                ml_action   = ml_decision["action"]
                ml_conf     = ml_decision["confidence"]
                ml_probs    = ml_decision["probs"]
                ml_ready    = ml_decision["ready"]

                # Use direction-matching prob for score blending (backward-compat)
                ml_prob = ml_probs.get(bias, 0.33)

                if ml_ready:
                    print(f"[ML] {symbol} {tf}: decision={ml_action} conf={ml_conf:.2f} "
                          f"| buy={ml_probs['buy']:.2f} sell={ml_probs['sell']:.2f} "
                          f"no_trade={ml_probs['no_trade']:.2f}")

                    # ML "no_trade" est désormais CONSULTATIF (gate trial 2026-07-14,
                    # 62 épisodes bloqués : 29.0% WR / +0.02R net — bloquait de bons
                    # sells (+0.20R) autant que de mauvais buys (-0.27R)). Le modèle
                    # est entraîné sur la population -EV historique ; son avis reste
                    # loggé (ml_win_prob au journal) pour ré-évaluation future.
                    if ml_action == "no_trade":
                        print(f"[ML] {symbol} {tf}: modèle défavorable (no_trade="
                              f"{ml_probs['no_trade']:.2f}) — consultatif, on continue")

                    # ML direction conflicts with SMC signal.
                    # no_trade n'est PAS un conflit de direction : il est
                    # consultatif (trial 2026-07-14) et pénalise déjà le
                    # blended score via ml_prob bas. Sans ce filtre, tous
                    # les no_trade retombaient ici et bloquaient quand même
                    # (bug post-trial : ml_direction=no_trade_vs_smc=...).
                    if ml_action in ("buy", "sell") and ml_action != bias:
                        # If MLP AND CNN both independently agree on the same direction
                        # → override SMC bias and compute a fresh ATR-based entry.
                        # Two independent models seeing the same pattern is stronger
                        # evidence than the SMC indicator alone.
                        _cnn_res = ml_decision.get("cnn") or {}
                        _cnn_agrees = (
                            _cnn_res.get("ready") and _cnn_res.get("action") == ml_action
                        )
                        if _cnn_agrees:
                            _atr   = analysis.get("atr") or 0
                            _price = entry_data["entry"]
                            if _atr <= 0:
                                # Can't compute valid SL/TP without ATR — skip
                                print(f"[ML-OVERRIDE] {symbol} {tf}: MLP+CNN agree on "
                                      f"{ml_action.upper()} but ATR=0 → cannot compute entry, skipping")
                                _log_rejection(symbol, f"ml_override_no_atr", score=ta_score, bias=bias)
                                continue
                            if ml_action == "buy":
                                _ml_sl  = round(_price - _atr * 1.5, 5)
                                _ml_tp1 = round(_price + _atr * 3.0, 5)
                            else:
                                _ml_sl  = round(_price + _atr * 1.5, 5)
                                _ml_tp1 = round(_price - _atr * 3.0, 5)
                            entry_data = {**entry_data,
                                          "sl": _ml_sl, "tp1": _ml_tp1, "rr_ratio": 2.0}
                            print(f"[ML-OVERRIDE] {symbol} {tf}: MLP+CNN both say "
                                  f"{ml_action.upper()} (SMC={bias}) → trading ML direction "
                                  f"entry={_price} sl={entry_data['sl']} tp={entry_data['tp1']}")
                            bias     = ml_action
                            ml_prob  = ml_probs.get(ml_action, 0.33)
                        else:
                            print(f"[ML] {symbol} {tf}: DIRECTION CONFLICT — ML says {ml_action}, "
                                  f"SMC says {bias} → skipping (CNN did not confirm)")
                            _log_rejection(symbol, f"ml_direction={ml_action}_vs_smc={bias}",
                                           score=ta_score, bias=bias)
                            continue

                # ── Symbol priority (from historical win rate per symbol/tf)
                try:
                    from features.trading.ta_optimizer import get_symbol_priority, get_config as _ta_cfg
                    sym_priority = get_symbol_priority(symbol, tf)
                except Exception:
                    sym_priority = 1.0

                # ── Blended score: TA + ML confidence boost + other boosts ─
                # ML confidence above 0.33 (random baseline) adds pts
                ml_boost  = round((ml_prob - 0.33) * 30, 1)
                sym_boost = round((sym_priority - 1.0) * 10, 1)

                # Lead-lag boost: ETH/SOL get +3pts when BTC just fired same direction
                lead_boost = 0.0
                if btc_lead_active and btc_lead_signal.get("bias") == bias:
                    lead_boost = 3.0
                    print(f"[LEAD] {symbol} +{lead_boost}pts (BTC lead {bias} confirmed, {btc_lead_age:.0f}min ago)")

                # XAUUSD/USD correlation boost: EURUSD direction confirms XAU signal
                correlation_boost = 0.0
                if symbol == "XAUUSD":
                    try:
                        from features.smc.engine import detect_market_structure
                        from features.market.fetcher import fetch_candles as _fc
                        eurusd_df    = _fc("EURUSD", "1h", limit=50)
                        eurusd_trend = detect_market_structure(eurusd_df).get("trend", "neutral")
                        # XAU inversely correlated with USD: EURUSD bearish = USD strong = XAU falls
                        xau_confirmed = (bias == "sell" and eurusd_trend == "bearish") or \
                                        (bias == "buy"  and eurusd_trend == "bullish")
                        if xau_confirmed:
                            correlation_boost = 5.0
                            print(f"[CORR] XAUUSD {bias} confirmed by EURUSD {eurusd_trend} → +{correlation_boost}pts")
                        else:
                            print(f"[CORR] XAUUSD {bias} diverges from EURUSD {eurusd_trend} → no boost")
                    except Exception as _ce:
                        print(f"[CORR] EURUSD check failed: {_ce}")

                # ── News momentum boost ───────────────────────────────────
                news_boost = 0.0
                try:
                    from features.news.calendar import get_news_momentum
                    nm = get_news_momentum(symbol)
                    if nm["detected"]:
                        if nm["direction"] == bias:
                            news_boost = nm["boost"]
                            print(f"[NEWS] {symbol} {bias} ✓ '{nm['title']}' "
                                  f"actual={nm['actual']} forecast={nm['forecast']} "
                                  f"({nm['minutes_since']}min ago) +{news_boost}pts")
                        else:
                            news_boost = -10.0
                            print(f"[NEWS] {symbol} {bias} ✗ against {nm['direction']} news "
                                  f"'{nm['title']}' → {news_boost}pts")
                except Exception as _nme:
                    print(f"[NEWS] momentum error: {_nme}")

                # ── Zone Memory boost (computed above at zone gate, reused here) ──
                # zone_boost already set; printed by get_zone_boost() above.

                # ── 50/200 EMA confluence bonus (5m+30m+1h all agree) ─────
                # Counter-regime scalps still fire normally with no penalty
                # (e.g. a clean 5m SELL during a 1H bullish stretch). The
                # bonus only kicks in on the rarer, higher-conviction case
                # where ALL THREE timeframes' EMA50/200 agree with the bias.
                ema_boost   = 0.0
                conf_regime = ema_confluence.get("regime", "mixed")
                if (bias == "buy"  and conf_regime == "bullish") or \
                   (bias == "sell" and conf_regime == "bearish"):
                    ema_boost = 15.0
                    print(f"[EMA-CROSS] {symbol}: 5m+30m+1h EMA confluence="
                          f"{conf_regime} confirms {bias} +{ema_boost}pts")
                else:
                    print(f"[EMA-CROSS] {symbol}: regimes={ema_confluence['tfs']} "
                          f"(no confluence with {bias}, no score impact)")

                blended_score = round(
                    ta_score + ml_boost + sym_boost + lead_boost
                    + correlation_boost + news_boost + zone_boost + ema_boost, 1
                )

                print(f"[SCORE] {symbol} {tf}: TA={ta_score} ML={ml_boost:+.1f} "
                      f"sym={sym_boost:+.1f} lead={lead_boost:+.1f} corr={correlation_boost:+.1f} "
                      f"news={news_boost:+.1f} zone={zone_boost:+.1f} ema={ema_boost:+.1f} "
                      f"=> {blended_score}")

                # Expose blended_score back into analysis for logging
                analysis["blended_score"] = blended_score

                # ── Blended score gate ─────────────────────────────────────
                # ta_score already passed min_score. But zone + news penalties
                # can sink the composite. Allow 5pts of leeway:
                #   • strong zone (+15) rescues a news-penalized trade
                #   • no zone + counter-news → composite drops → reject
                _min_blended = min_score - 5
                if blended_score < _min_blended:
                    print(f"[BLEND-GATE] {symbol} {tf}: blended={blended_score} < "
                          f"{_min_blended} (min_score {min_score} - 5) → composite too weak → skip")
                    _log_rejection(symbol,
                                   f"blended_below_threshold: {blended_score}/{_min_blended}",
                                   score=ta_score, bias=bias)
                    continue

                # SL width gate: reject if SL > 2.5× ATR.
                # LG-primary uses swing highs/lows as SL which can be far from
                # entry (e.g. entry 22pts below swing high = 3× ATR SL on XAU).
                # When SL fires those trades lose 3× a normal loss while wins
                # are cut at the ratchet — destroying average RR even at 50% win rate.
                _sl_width = abs(entry_data["entry"] - entry_data["sl"])
                _atr_for_gate = analysis.get("atr", 0)
                if _atr_for_gate > 0 and _sl_width > 2.5 * _atr_for_gate:
                    print(f"[SL-WIDE] {symbol} {tf}: SL width {_sl_width:.3f} > "
                          f"2.5×ATR({_atr_for_gate:.3f}={2.5*_atr_for_gate:.3f}) — skipped")
                    _log_rejection(symbol,
                        f"sl_too_wide: {round(_sl_width,3)} > 2.5×ATR({round(_atr_for_gate,3)})",
                        score=ta_score, bias=bias)
                    continue

                # Full app control (user choice 2026-07-24: "respect every parameter
                # from the app"). Lot per symbol comes from the app's lot_sizes;
                # auto (risk-based) only if a symbol has no lot set.
                lot_sizes  = settings.get("lot_sizes", {})
                fixed_lot  = float(lot_sizes.get(symbol, 0))

                # ML-driven position sizing: scale lot by AI confidence
                ml_threshold = settings.get("ml_threshold", 0.0)
                if ml_threshold > 0 and fixed_lot > 0:
                    lot_mult  = get_lot_multiplier(ml_prob)
                    fixed_lot = round(fixed_lot * lot_mult, 2)
                    fixed_lot = max(0.01, fixed_lot)
                    print(f"[ML-LOT] {symbol} ml_prob={ml_prob:.2f} → ×{lot_mult} → lot={fixed_lot}")

                # ── Minimum SL floor (LG primary) ─────────────────────────
                # LG SL = sweep_low * 0.9995, which gives 3-9pt on ETH —
                # too close to spread noise. Same floor as M1.
                _LG_MIN_SL = {
                    "BTC": 120.0, "ETH": 12.0,
                    "EURUSD": 0.00150, "USDJPY": 0.200,
                    "XAUUSD": 5.0, "GBPJPY": 0.250,
                }
                _lg_min = _LG_MIN_SL.get(symbol.upper(), 0)
                _lg_entry = entry_data["entry"]
                _lg_sl    = entry_data["sl"]
                _lg_risk  = abs(_lg_entry - _lg_sl)
                if _lg_min > 0 and _lg_risk < _lg_min:
                    _lg_risk = _lg_min
                    _lg_sl   = round(_lg_entry - _lg_risk, 5) if bias == "buy" else round(_lg_entry + _lg_risk, 5)
                    _lg_tp1  = round(_lg_entry + _lg_risk * 2.5, 5) if bias == "buy" else round(_lg_entry - _lg_risk * 2.5, 5)
                    print(f"[SL-FLOOR] {symbol} LG SL widened: risk {round(abs(entry_data['entry']-entry_data['sl']),3)} → {_lg_min}")
                    entry_data = {**entry_data, "sl": _lg_sl, "tp1": _lg_tp1}

                # ── Enforce minimum RR 2.5 ────────────────────────────────────
                # At 40% WR, RR=1.18 is mathematically losing (-12.8% EV/trade).
                # At 40% WR, RR=2.5 is profitable (+40% EV/trade).
                # Break-even WR at RR=2.5 is only 28.6% — well below observed WR.
                # If SMC set TP too close, push it out to maintain 2.5× risk.
                _MIN_RR = 2.5
                _rr_entry = entry_data["entry"]
                _rr_sl    = entry_data["sl"]
                _rr_tp    = entry_data.get("tp1", _rr_entry)
                _rr_risk  = abs(_rr_entry - _rr_sl)
                if _rr_risk > 0:
                    _rr_actual = abs(_rr_tp - _rr_entry) / _rr_risk
                    if _rr_actual < _MIN_RR:
                        if bias == "buy":
                            _rr_new_tp = round(_rr_entry + _rr_risk * _MIN_RR, 5)
                        else:
                            _rr_new_tp = round(_rr_entry - _rr_risk * _MIN_RR, 5)
                        print(f"[RR-FIX] {symbol} {tf}: TP extended "
                              f"{_rr_tp:.5g} → {_rr_new_tp:.5g} "
                              f"(RR {_rr_actual:.1f}x → {_MIN_RR}x)")
                        entry_data = {**entry_data, "tp1": _rr_new_tp, "rr_ratio": _MIN_RR}

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

                if trade.get("success"):
                    _log_entry = {
                        "timestamp":     now.isoformat(),
                        "symbol":        symbol,
                        "timeframe":     tf,
                        "action":        bias,
                        "signal_type":   "lg_primary",
                        "score":         analysis["scalping_score"],
                        "blended_score": blended_score,
                        "htf_trends":    htf_data["trends"],
                        "htf_consensus": consensus,
                        "atr":           analysis.get("atr"),
                        "entry":         entry_data["entry"],
                        "sl":            entry_data["sl"],
                        "tp1":           entry_data["tp1"],
                        "rr":            entry_data.get("rr_ratio"),
                        "ml_win_prob":   ml_prob,
                        "news_boost":    news_boost,
                        "zone_boost":    zone_boost,
                        "ema_boost":     ema_boost,
                        "conditions":    analysis.get("conditions"),
                        "result":        trade,
                    }
                    scalp_history.append(_log_entry)
                    try:
                        from features.trading.data_collector import save_scalp_log
                        save_scalp_log(_log_entry)
                    except Exception as _sle:
                        print(f"[SCALP-LOG] persist error: {_sle}")

                    # ── Sauvegarder le signal pour entraînement IA ───────────
                    # Must be wrapped — an exception here previously skipped the
                    # cooldown and break, causing 15m AND 5m to both trade the
                    # same symbol in the same scan cycle (double-trade bug).
                    try:
                        save_signal(analysis, entry_data, trade, symbol, tf, consensus, htf_tf_str,
                                    volume_ratio=extra.get("volume_ratio"),
                                    spread_pct=extra.get("spread_pct"),
                                    symbol_winrate=extra.get("symbol_winrate"),
                                    consecutive_losses=extra.get("consecutive_losses"))
                    except Exception as _sse:
                        print(f"[SAVE-SIGNAL] {symbol} {tf}: error — {_sse}")

                    cooldown_min = settings.get("cooldown_minutes", 8)
                    scalp_cooldowns[symbol] = now + timedelta(minutes=cooldown_min)
                    scalp_state["trades_today"] += 1
                    print(f"[!] LG-PRIMARY: {symbol} {bias} score={analysis['scalping_score']} HTF={consensus} ticket={trade.get('ticket')}")
                else:
                    fail_reason = trade.get('reason', 'unknown')
                    print(f"[NO] Scalp failed: {fail_reason}")
                    _log_rejection(symbol, f"place_trade_failed: {fail_reason}", score=ta_score, bias=bias, ml_prob=ml_prob)

                # Break : évite que 5m ET 15m tradent le même symbole dans le même scan
                break

            except Exception as e:
                err = str(e).encode('ascii', errors='replace').decode()
                print(f"Scalp error {symbol} {tf}: {err}")
                _log_rejection(symbol, f"exception: {err}")

    scalp_state["last_scan"] = now.isoformat()


@router.post("/scalping/start")
def start_scalping(settings: ScalpingSettings):
    # Use what the app sends directly — the app already loaded the saved settings
    # via /status on startup, so by the time /start is called the app state
    # already reflects the user's tuned values plus any new changes they made.
    d = settings.dict()

    _persist_settings(d)   # save merged settings to disk
    print(f"[SETTINGS] saved to disk: cooldown={d.get('cooldown_minutes')} "
          f"max_trades={d.get('max_trades')} lots={d.get('lot_sizes')}")

    scalp_state["running"]        = True
    scalp_state["settings"]       = d
    scalp_state["trades_today"]   = 0
    scalp_state["daily_pnl"]      = 0.0
    scalp_state["stopped_reason"] = None
    scalp_state["session_start"]  = datetime.now().isoformat()
    # Fresh session → clear streak pauses so previously paused symbols can trade again
    symbol_paused_until.clear()
    scan_rejections.clear()
    return {"success": True, "message": "Scalping bot started", "settings": d}

@router.post("/scalping/unpause/{symbol}")
def unpause_symbol(symbol: str):
    """Clear the streak pause for a specific symbol immediately."""
    sym = symbol.upper()
    if sym in symbol_paused_until:
        del symbol_paused_until[sym]
        return {"success": True, "message": f"{sym} unpaused"}
    return {"success": True, "message": f"{sym} was not paused"}

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

@router.patch("/scalping/settings")
def patch_scalping_settings(patch: dict):
    """Hot-update individual settings — works whether bot is running or not."""
    current = scalp_state["settings"]
    current.update(patch)
    scalp_state["settings"] = current
    _persist_settings(current)   # survive server restart + Flutter reset
    return {"success": True, "updated": patch, "settings": current}

@router.get("/scalping/scan-log")
def get_scan_log(limit: int = 50):
    """Last N scan rejections — shows exactly why each signal was blocked."""
    return {"rejections": scan_rejections[-limit:], "total": len(scan_rejections)}

@router.get("/meta-dataset/stats")
def meta_dataset_stats():
    """
    Readiness check for the future meta-model: joins scalp_log decisions
    with trade_signals outcomes on ticket. Not enough volume to train on
    yet (needs 200+ closed, logged trades) — this just tracks progress.
    """
    from features.trading.meta_dataset import get_meta_dataset_stats
    return get_meta_dataset_stats()

@router.get("/meta-dataset/export")
def meta_dataset_export():
    """Write the current meta-dataset to meta_dataset.csv and return row count."""
    from features.trading.meta_dataset import export_meta_dataset_csv
    n = export_meta_dataset_csv()
    return {"rows_written": n, "path": "meta_dataset.csv"}

@router.get("/meta-dataset/evaluate-gate")
def meta_dataset_evaluate_gate(reason_contains: str = "M1 momentum", lookback_candles: int = 8):
    """
    Checks every persisted rejection matching `reason_contains` against
    what price did afterward — answers "was this filter right to block
    these trades?" with real data instead of a guess. Needs rejection_log
    data accumulated AFTER persistence was added; won't have history before that.
    """
    from features.trading.meta_dataset import evaluate_gate_rejections
    return evaluate_gate_rejections(reason_contains, lookback_candles)

@router.get("/scalping/history")
def scalping_history_endpoint():
    return {"history": scalp_history[-100:]}

@router.get("/scalping/strategy-stats")
def scalping_strategy_stats(since: str = None):
    """
    Win rate + P&L broken down by which strategy opened each trade —
    lg_primary (LG/SMC engine), support_resistance, or m1_momentum
    (the M1 confirmation entry). Joins scalp_log with real outcomes,
    so this only counts trades that have actually closed.

    Pass since=2026-06-28T00:00:00 to only count trades opened after a
    given timestamp — e.g. right after a strategy rebuild/restart, so
    legacy results from the old logic don't dilute the new numbers.
    """
    from features.trading.meta_dataset import build_meta_dataset

    data = build_meta_dataset()
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            data = [r for r in data if r.get("timestamp") and
                    datetime.fromisoformat(r["timestamp"]) >= since_dt]
        except Exception as e:
            return {"error": f"invalid 'since' timestamp: {e}"}

    by_type: dict = {}
    for row in data:
        st = row.get("signal_type", "lg_primary")
        bucket = by_type.setdefault(st, {"trades": 0, "wins": 0, "losses": 0, "profit": 0.0})
        bucket["trades"] += 1
        bucket["profit"] += row.get("profit") or 0.0
        if row.get("outcome") == 1:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1

    for st, b in by_type.items():
        b["win_rate_pct"] = round(b["wins"] / b["trades"] * 100, 1) if b["trades"] else 0.0
        b["profit"] = round(b["profit"], 2)

    # Expectancy en R — la seule stat qui répond "les maths sont-elles
    # positives ?" (un WR de 38% à RR2.5 bat un WR de 71% à RR0.4).
    # Découpé par stratégie, symbole et cohorte de règle pour vérifier
    # chaque changement de règle sur les trades RÉELS, pas en simulation.
    from features.trading.meta_dataset import compute_expectancy_stats
    try:
        expectancy = compute_expectancy_stats(since=since)
    except Exception as _ee:
        expectancy = {"error": str(_ee)}

    return {"by_strategy": by_type, "total_closed_trades": len(data),
            "expectancy": expectancy}

@router.get("/scalping/no-signal-stats")
def scalping_no_signal_stats(signal_type: str = None, limit: int = 5000):
    """
    Breakdown of every time a strategy evaluated a symbol and did NOT
    fire — and why. These negative examples are persisted to rejection_log
    same as everything else now, so the dataset isn't just "what worked,"
    it's "what the market looked like every time nothing happened too."
    """
    from features.trading.data_collector import load_rejections
    from collections import Counter

    rows = load_rejections(limit=limit, signal_type=signal_type)
    by_strategy: dict = {}
    for r in rows:
        st = r.get("signal_type", "lg_primary")
        reasons = by_strategy.setdefault(st, Counter())
        # reason text looks like "sr_no_signal: range_too_tight" — bucket
        # by the part after the colon for a clean breakdown
        reason = r.get("reason", "?")
        key = reason.split(":", 1)[-1].strip() if ":" in reason else reason
        reasons[key] += 1

    return {
        "by_strategy": {st: dict(c.most_common()) for st, c in by_strategy.items()},
        "total_entries_scanned": len(rows),
    }

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
        try:
            _macro4h = get_htf_trend(symbol, ["4h"]).get("consensus", "neutral")
        except Exception:
            _macro4h = "neutral"

        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=100)
                analysis = run_scalping_analysis(df, symbol, macro_trend=_macro4h)
                bias     = analysis.get("bias", "neutral")

                # XAUUSD specific rules (same as auto-scan)
                if symbol == "XAUUSD" and analysis.get("should_scalp"):
                    adx_val = analysis.get("adx", 0)
                    if adx_val < 20:
                        analysis["should_scalp"]   = False
                        analysis["scalping_score"] = 0
                        analysis["conditions"]     = [f"XAUUSD: ADX {adx_val:.1f} < 20 — need stronger trend"]
                    elif "approaching" in str(analysis.get("scalping_entry", {}).get("description", "")):
                        analysis["should_scalp"]   = False
                        analysis["scalping_score"] = 0
                        analysis["conditions"]     = ["XAUUSD: approaching zone rejected — inside zone only"]

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