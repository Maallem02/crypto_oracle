from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import Union, List, Optional
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
from features.trading.mt5_client import connect, disconnect
from core.config import runtime

def _mt5_init():
    """Attache le terminal (idempotent) — voir mt5_client.ensure_mt5()."""
    from features.trading.mt5_client import ensure_mt5
    ensure_mt5()
from features.trading.executor import (
    place_trade, place_pending_trade, close_all_trades, get_open_trades, SYMBOL_MAP,
    get_pending_orders, cancel_pending_order, cancel_stale_pending_orders,
    split_half_volume, close_partial,
)
from features.market.fetcher import fetch_candles
from features.smc.engine import run_smc_analysis, run_scalping_analysis
from features.smc.structure import detect_market_structure
from features.trading.data_collector import save_signal, check_and_update_outcomes, get_stats, get_training_data
from features.trading.risk_manager import get_daily_pnl_pct, compute_risk_based_entry
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

@router.get("/account")
def mt5_account():
    """Solde / equity en LECTURE SEULE, pour le rafraîchissement auto de l'app
    (toutes les 30 s). /connect refait mt5.initialize(login=...) à chaque
    appel — sondé en boucle, il re-loggerait le terminal pendant les scans.
    Ici : ensure_mt5() idempotent (ne fait rien si déjà connecté)."""
    _mt5_init()
    a = mt5.account_info()
    if a is None:
        raise HTTPException(status_code=503, detail="MT5 account unavailable")
    return {"success": True, "account": {
        "login": a.login, "demo": a.trade_mode == 0, "server": a.server,
        "balance": a.balance, "equity": a.equity, "profit": a.profit,
        "margin_free": a.margin_free, "leverage": a.leverage, "currency": a.currency,
    }}

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
def scan_and_trade(execute: bool = False):
    """
    Scan manuel du bot "normal" (chemin legacy, non-scalping).

    DRY-RUN PAR DÉFAUT depuis 2026-08-01. Cet endpoint plaçait des ordres
    réels sur un simple GET : n'importe quel prefetch de navigateur, sonde
    de monitoring ou retry du tunnel cloudflared déclenchait des trades.
    Passer ?execute=true pour réellement exécuter (comportement d'avant).
    """
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

                    if not execute:
                        results[-1]["would_trade"] = {
                            "action": bias, "entry": entry, "sl": sl, "tp1": tp1,
                        }
                        results[-1]["entry_src"] = src
                        results[-1]["score"]     = confluence_score
                        results[-1]["dry_run"]   = True
                        continue

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
    return {"scanned": len(results), "dry_run": not execute, "results": results}

@router.get("/bot/history")
def get_history():
    return {"history": trade_history[-50:]}

@router.get("/trades/open")
def open_trades():
    return {"trades": get_open_trades()}

@router.post("/trades/close-all")
def close_trades():
    return {"results": close_all_trades()}

@router.get("/pending")
def list_pending():
    """Ordres en attente du bot, avec leur âge en minutes (horloge serveur)."""
    orders = get_pending_orders()
    return {"count": len(orders), "orders": orders,
            "max_age_minutes": scalp_state["settings"].get("pending_max_age_minutes", 120)}

@router.post("/pending/cancel/{symbol}")
def cancel_pending_for_symbol(symbol: str):
    """
    Annule MAINTENANT les ordres en attente du bot sur ce symbole, quel que
    soit leur âge — débloque un symbole coincé derrière un GTC non exécuté
    sans attendre pending_max_age_minutes.
    """
    res = cancel_stale_pending_orders(max_age_minutes=None, symbol=symbol)
    return {"success": all(r["success"] for r in res) if res else True,
            "cancelled": res, "count": len(res)}

@router.post("/pending/cancel-ticket/{ticket}")
def cancel_pending_by_ticket(ticket: int):
    """Annule un ordre en attente précis."""
    return cancel_pending_order(ticket)

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
    pending_entry_enabled: bool    = True    # 2026-07-30: risk-controlled limit-order entries (default ON per user — no UI toggle yet)
    target_risk_pct:       float   = 7.0     # % of balance visé via le prix d'entrée (pas le lot).
                                             # Aligné sur executor.MAX_RISK_PCT (7%) le 2026-08-12 :
                                             # viser 5% sous un plafond de 7% laissait la limite
                                             # inutilement proche du SL.
    # Risque MAX par trade, réglable dans l'onglet Scalping de l'app (demande
    # du 2026-09-26, "comme dans Vol. Profile"). None = les plafonds par
    # symbole de executor.SYMBOL_RISK_PCT (15 % crypto et métaux, 7 % forex),
    # comportement inchangé. Un nombre (1 à 50) les remplace pour TOUS les
    # symboles du bot SMC : un trade dont le stop coûterait plus est refusé, au
    # marché comme en limite, et une entrée limite ne vise jamais plus que ce
    # plafond (voir _smc_target_risk). Le bot Volume Profile a son propre réglage.
    risk_cap_pct:          Optional[float] = None
    pending_expire_minutes: int    = 0       # 0 = GTC, no expiration (per user 2026-07-30)
    # ── Filtres qualité d'entrée (mesurés sur 36 643 signaux, 2026-08-01) ──
    # Mesuré EN PLUS du filtre de tendance par timeframe déjà en place, et NET
    # d'un coût réaliste de spread+slippage de 0.04R (le dataset offline label-
    # lise sur prix bruts, sans frais — l'ignorer surestime tous les filtres) :
    #
    #   config                     n      avg R    R total    R net de frais
    #   Gate B seul (avant)     23 645   +0.0770    +1821         +875
    #   + 4H                    21 703   +0.1087    +2360        +1491   <-- retenu
    #   + 4H + ADX>=20          13 180   +0.1185    +1562        +1035
    #   + 4H + P/D hors équil.  12 108   +0.1172    +1420         +935
    #   + les trois              7 608   +0.1552    +1180         +876
    #
    # Seul le filtre 4H mérite d'être actif : il retire des trades à -0.277R de
    # moyenne (vraie perte) tout en gardant 92% du volume. Les deux autres
    # retirent des trades PROFITABLES (+0.098R et +0.053R) : ils remontent la
    # moyenne par trade mais jettent du profit — empilés, ils ramènent au même
    # résultat net qu'avant. Laissés disponibles mais désactivés.
    # Gestion de sortie — rejeu barre par barre de 23 645 signaux (2026-08-01).
    # La politique live (BE 1.5R + trail) bat largement le bracket pur
    # (+0.1696 vs +0.0770 R/trade) : le BE n'est PAS le problème, retirer
    # l'étape BE ne change rien (-0.0027R). Le vrai levier est le déclenchement
    # du trail : 70% -> 40% vaut +0.041R par trade, confirmé hors échantillon.
    # Déclenchement du trail en R (2026-08-10). Remplace trail_start_pct, qui
    # exprimait le seuil en % de la distance au TP : équivalent à RR 2.5 (0.40
    # x 2.5 = 1.0R) mais inopérant dès que les entrées limites poussent le RR
    # à 6-25, où 40% du TP vaut 2.5R à 10R. Mesuré : +0.47R/trade sur les
    # entrées limites, et STRICTEMENT identique (100% des trades) sur les
    # entrées au marché. trail_start_pct est conservé comme repli pour les
    # settings déjà enregistrés.
    trail_start_r:         float   = 1.0     # R de profit avant d'activer le trail
    # Moitié fermée à +partial_close_r R, stop du reste à l'entrée, objectif
    # gardé, plus de trail sur le reste (2026-09-28, voir manage_open_positions).
    # 0 = désactivé (trail seul, comportement d'avant).
    partial_close_r:       float   = 1.0
    trail_start_pct:       float   = 0.40    # repli historique (= 1.0R à RR 2.5)
    be_trigger_r:          float   = 1.5     # R de profit avant passage à breakeven

    # Meta-model gate — DÉSARMÉ le 2026-08-14, retour en observation.
    #
    # Armé le 08-13 sur 6 trades (Spearman +0.886, p permutation 0.051). Avec
    # 352 signaux notés sur 27 heures, le classement s'INVERSE.
    #
    # Test apparié à l'intérieur de chaque heure — la bonne façon de comparer,
    # car 352 signaux sur 27h ne sont pas 352 observations indépendantes mais
    # les mêmes conditions de marché répétées sur 5 symboles :
    #   15 heures contiennent à la fois un signal accepté et un refusé
    #   accepté  -0.292R      refusé  +0.202R
    #   écart apparié -0.494R   IC 95% [-0.839, -0.149]  -> exclut zéro
    #
    # Les signaux que le gate LAISSE PASSER font moins bien que ceux qu'il
    # BLOQUE, à heure et instrument identiques. Et aucun seuil ne bat
    # l'absence de seuil : tout garder = -0.037R, seuil 0.5155 = -0.284R.
    #
    # Déciles de score (du plus bas au plus haut) : +0.511, +0.444, +0.515,
    # +0.133, -0.058, -0.270, -0.382, -0.390, -0.090, -0.343. Monotone à
    # l'envers.
    #
    # Contre-indice honnête : les 10 vrais trades notés donnent Spearman
    # +0.576 (p=0.082), soit l'inverse — mais l'échantillon est minuscule et
    # concentré sur les scores hauts (6 sur 10 au-dessus de 0.5), donc il ne
    # couvre pas la zone où l'inversion se produit. Le test apparié l'emporte.
    #
    # Le modèle continue de noter et de journaliser. À réévaluer sur 30+ trades
    # réels avec score avant tout nouvel armement.
    #
    # Sécurité : predict_r() renvoie None si le modèle manque, si l'historique
    # est trop court ou si l'analyse est incomplète — dans ce cas le trade
    # PASSE. Un échec de notation ne doit jamais bloquer silencieusement.
    meta_gate_enabled:     bool          = False
    meta_gate_threshold:   float | None  = None

    htf_4h_filter_enabled: bool    = True    # ne jamais trader contre la pente EMA20 4H
    skip_equilibrium_pd:   bool    = False   # refuse les entrées entre 35% et 65% du range
    min_adx:               float   = 0.0     # 0 = désactivé (l'engine bloque quand même <10)

    # Expiry des ordres en attente — 480 -> 60 min (2026-09-15).
    #
    # HISTORIQUE. Le 2026-08-08 ce plafond est passé de 120 à 480 sur un rejeu
    # des 37 ordres ANNULÉS de la semaine 08-03 (120 min : 5 remplis +5.4R ;
    # 480 min : 18 remplis +6.4R). Ce rejeu portait sur des ordres jamais
    # exécutés, tous issus d'UNE semaine haussière — le commentaire d'origine
    # le signalait lui-même et concluait "à confirmer". Ce n'est pas confirmé.
    #
    # MESURE 2026-09-15, sur les 107 ordres en attente RÉELLEMENT remplis
    # depuis le 07-27 (population différente : des fills, pas des annulations),
    # classés par temps d'attente avant exécution :
    #
    #    rempli <2 min    n= 9   -23.93$   WR 22.2%   PF 0.29
    #    rempli 2-10 min  n=11   -13.27$   WR 36.4%   PF 0.64
    #    rempli 10-30 min n=24   +37.45$   WR 45.8%   PF 1.56   <- positif
    #    rempli 30-60 min n=19    +8.53$   WR 47.4%   PF 1.14   <- positif
    #    rempli 1-3 h     n=29   -64.55$   WR 20.7%   PF 0.49   <- negatif
    #    rempli 3-8 h     n=14   -61.47$   WR 35.7%   PF 0.29   <- negatif
    #
    # Deux buckets positifs adjacents, deux négatifs adjacents, n~43 de chaque
    # côté : un plateau, pas un pic isolé (cf. l'optimum XAG à +0.5R sur n=29
    # qui était du bruit et n'a délibérément PAS été retenu).
    #
    # Mécanisme : une limite qui dort des heures finit exécutée par le mouvement
    # qui la traverse, longtemps après l'expiration du contexte SMC qui la
    # justifiait. Les perdants morts en <5 min ont une MFE médiane de 0.00R —
    # 86% n'ont jamais pris 0.2R — donc ils sont remplis et partent droit
    # contre. C'est de la sélection adverse sur ordre périmé, pas une erreur
    # de direction.
    #
    # NON RETENU : la compression de l'entrée vers le SL (pour tenir sous le
    # plafond 7%) était le suspect n°1 — écarté, 96 des fills en attente sont
    # placés normalement (ratio 0.8-1.05) et perdent quand même 120$.
    #
    # RÉSERVE : raccourcir l'expiry libère le symbole plus tôt, donc un nouveau
    # signal peut prendre la place qu'occupait l'ordre annulé. Le gain réel
    # sera donc inférieur aux 132$ que pèsent les fills au-delà de 60 min.
    pending_max_age_minutes: int   = 60      # 2026-08-01: filet de sécurité côté bot pour
                                             # le GTC ci-dessus. Le broker n'expirera jamais
                                             # l'ordre ; manage_open_positions l'annule après
                                             # ce délai pour ne pas bloquer le symbole
                                             # indéfiniment. 0 = désactivé (GTC pur).

    @field_validator('htf_timeframe', mode='before')
    @classmethod
    def parse_htf(cls, v):
        if isinstance(v, str):
            return [v]   # "1h" → ["1h"]
        return v         # ["5m", "1h"] → ["5m", "1h"]

    @field_validator('risk_cap_pct')
    @classmethod
    def check_risk_cap(cls, v):
        # 0 ou vide = plafonds par symbole ; sinon 1 à 50 % comme dans l'app
        if v is None or v == 0:
            return None
        if not 1 <= v <= 50:
            raise ValueError("risk_cap_pct must be between 1 and 50 (or empty for the per-symbol defaults)")
        return round(float(v), 1)

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


# ── État de risque PERSISTANT — 2026-09-23 (choix utilisateur) ──────────────
#
# PROBLÈME. Les deux garde-fous de compte vivaient uniquement en mémoire :
#   - peak_equity (halt SURVIVAL à -35%) : remis à l'equity courante à chaque
#     redémarrage du process ;
#   - la perte "journalière" : mesurée depuis session_start, qui est remis à
#     maintenant à CHAQUE /scalping/start — y compris celui du watchdog après
#     un crash.
# Huit crashs en quatre semaines : ni l'un ni l'autre n'a jamais pu se
# déclencher. Le 09-19 par exemple, le pic réel de 173.07$ a été oublié au
# redémarrage et remplacé par 139.15$.
#
# CE QUI EST SAUVÉ (risk_state.json, à côté des réglages) :
#   login        compte MT5 auquel ces valeurs appartiennent
#   peak_equity  plus haute equity vue sur ce compte
#   day_anchor   début de la fenêtre de perte journalière
#
# LIÉ AU COMPTE — essentiel pour le passage démo -> réel. Sans ce lien, le pic
# démo (~228$) appliqué à un compte réel de 70$ donnerait -69% et
# déclencherait le halt dès le premier scan. Si le login change, tout repart
# de zéro sur l'equity du nouveau compte.
#
# QUI REMET À ZÉRO QUOI :
#   redémarrage du process / crash   -> rien, tout est relu depuis le disque
#   /scalping/start?source=watchdog  -> rien (reprise après crash)
#   /scalping/start depuis l'app     -> day_anchor = maintenant (même
#       sémantique qu'avant : relancer depuis l'app repart d'un compteur
#       journalier vierge) ; peak_equity remis SEULEMENT si le compte est déjà
#       au-delà du seuil -35% (sinon le bot repartirait en halt immédiat et ne
#       pourrait jamais être relancé : relancer depuis l'app = revue faite,
#       nouvelle base acceptée). En dessous du seuil, le pic est CONSERVÉ —
#       changer un réglage et relancer ne doit pas effacer la protection.
#   minuit                           -> la fenêtre journalière repart d'elle-même
_RISK_STATE_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                 "risk_state.json")
SURVIVAL_HALT_PCT = 35.0


def _load_risk_state() -> dict:
    try:
        with open(_RISK_STATE_FILE, "r", encoding="utf-8") as fh:
            d = _json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_risk_state(d: dict) -> None:
    try:
        tmp = _RISK_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(d, fh, indent=2)
        _os.replace(tmp, _RISK_STATE_FILE)      # écriture atomique
    except OSError as _rse:
        print(f"[RISK-STATE] save error: {_rse}")


risk_state = _load_risk_state()
if risk_state.get("peak_equity"):
    scalp_state["peak_equity"] = float(risk_state["peak_equity"])


def _bind_risk_state_to_account(login, equity) -> None:
    """Remet l'état à zéro si le compte MT5 a changé (ex. démo -> réel)."""
    if login is None or risk_state.get("login") == login:
        return
    old = risk_state.get("login")
    risk_state.clear()
    risk_state.update({"login": login,
                       "peak_equity": equity,
                       "day_anchor": datetime.now().isoformat()})
    _reset_flow_marker()
    scalp_state["peak_equity"] = equity
    _save_risk_state(risk_state)
    print(f"[RISK-STATE] compte {old} -> {login} : pic et compteur journalier "
          f"repartent de ${equity:.2f}")


def _daily_window_start() -> str:
    """Début de la fenêtre de perte journalière : le plus tardif entre minuit
    aujourd'hui et le dernier démarrage depuis l'app."""
    midnight = datetime.combine(datetime.now().date(), datetime.min.time())
    try:
        anchor = datetime.fromisoformat(risk_state.get("day_anchor") or "")
    except ValueError:
        anchor = midnight
    return max(anchor, midnight).isoformat()


# ── Dépôts / retraits neutres pour le halt SURVIVAL — 2026-10-05 (choix utilisateur)
#
# PROBLÈME. peak_equity est un pic d'EQUITY : un retrait la fait baisser
# exactement comme une perte. Le 04/10, un retrait de 31.05$ (131.05 -> 100)
# a compté pour -23% sous le pic de 134.71$ ; trois pertes SMC ont ensuite
# suffi pour déclencher le halt -35%.
#
# RÈGLE. Chaque mouvement de solde (dépôt, retrait, crédit, bonus) rééchelonne
# le pic du même facteur que le solde : pic *= solde_après / solde_avant. Le
# drawdown en % est identique juste avant et juste après le mouvement : un
# retrait ne rapproche pas du halt, et un dépôt ne cache pas une perte en
# cours. Même logique "time-weighted" que le max DD du dashboard.
#
# risk_state["flow_since"] (epoch) : seuls les mouvements postérieurs à la
# pose du pic actuel le concernent ; risk_state["flow_seen"] : tickets déjà
# appliqués (un mouvement n'est jamais compté deux fois, même après un crash).
_CASH_DEAL_TYPES = tuple(getattr(mt5, n, v) for n, v in (("DEAL_TYPE_BALANCE", 2),
                                                         ("DEAL_TYPE_CREDIT", 3),
                                                         ("DEAL_TYPE_BONUS", 6)))


def _reset_flow_marker() -> None:
    """Le pic vient d'être (re)posé : seuls les mouvements à venir le touchent."""
    risk_state["flow_since"] = int(datetime.now(timezone.utc).timestamp())
    risk_state["flow_seen"] = []


def _deal_amount(d) -> float:
    return d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)


def _apply_cash_flows(acct) -> None:
    """Rééchelonne peak_equity pour chaque dépôt/retrait pas encore appliqué."""
    pk = scalp_state.get("peak_equity")
    if acct is None or not pk:
        return
    since = risk_state.get("flow_since")
    if since is None:
        # état d'avant ce correctif : le pic actuel est la base, les
        # mouvements passés y sont déjà
        _reset_flow_marker()
        _save_risk_state(risk_state)
        return
    try:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        deals = list(mt5.history_deals_get(int(since) - 3600, now_ts + 2 * 86400) or [])
    except Exception as _fe:
        print(f"[RISK-STATE] lecture des mouvements impossible: {_fe}")
        return
    seen = set(risk_state.get("flow_seen") or [])
    deals.sort(key=lambda d: (d.time_msc, d.ticket))
    flows = [d for d in deals if d.type in _CASH_DEAL_TYPES and d.time >= since
             and d.ticket not in seen and _deal_amount(d)]
    if not flows:
        return
    for f in flows:
        key = (f.time_msc, f.ticket)
        # solde juste après ce mouvement = solde actuel - tout ce qui a suivi
        after = acct.balance - sum(_deal_amount(d) for d in deals if (d.time_msc, d.ticket) > key)
        before = after - _deal_amount(f)
        old = pk
        if before > 0 and after > 0:
            pk = pk * after / before
        else:                       # dépôt sur un compte vide (ou compte vidé)
            pk = max(after, 0.0)
        seen.add(f.ticket)
        print(f"[RISK-STATE] {'dépôt' if _deal_amount(f) > 0 else 'retrait'} "
              f"{_deal_amount(f):+.2f}$ (solde {before:.2f} -> {after:.2f}) : "
              f"pic ${old:.2f} -> ${pk:.2f}")
    pk = round(pk, 2)
    scalp_state["peak_equity"] = pk
    risk_state["peak_equity"] = pk
    risk_state["flow_seen"] = sorted(seen)[-50:]
    _save_risk_state(risk_state)

import threading

# ── Stratégies de repli (fallback) — désactivées globalement ────────────────
# Étaient pilotées par des variables locales `_sr_allowed = False` /
# `_m1_allowed = False` codées en dur au milieu de la boucle de scan.
# Remontées ici pour que réactiver soit un changement d'une ligne, et pour
# que la boucle puisse sauter le bloc entier (et sa journalisation) quand
# une stratégie est éteinte.
# ── Symboles bloqués au niveau CODE (2026-08-08) ────────────────────────────
# Volontairement ici et pas dans les settings : l'app renvoie sa propre liste
# de symboles à chaque /scalping/start et écrase le fichier de settings — ces
# deux paires y étaient déjà revenues une fois. Ce garde-fou survit à ça.
#
# Mesuré sur tout l'historique (525 trades clôturés) :
#   GBPJPY  n=39  23.1% WR  -67.90$  moyenne -1.74$ +-0.98  (IC exclut zéro)
#   USDJPY  n=22  18.2% WR  -20.20$  moyenne -0.92$ +-1.79  (non concluant seul)
#   tous les autres symboles : 33.8% WR
#   cumulé JPY : 61 trades, 21.3% WR, -88.10$
# USDJPY seul n'est pas statistiquement concluant, mais même famille, même
# sens, et 0 gain sur 5 la semaine du 08-03. Retirer une paire de la liste
# ci-dessous suffit à la réactiver.
# ── EURUSD ajouté le 2026-08-27 ─────────────────────────────────────────────
#   tout l'historique : 29 trades, 34.5% WR, -32.34$, PF 0.61
#   semaine du 08-24  : 0 sur 6, -27.21$
#   depuis la refonte du 08-03 (hors 1re semaine cassée) : -31$
# 2 semaines positives sur 5. Les pertes ne viennent pas d'une direction :
# achats ET ventes perdent (2 achats, 2 ventes cette semaine, MFE jamais
# au-dessus de +0.40R — les trades ne vont nulle part).
#
# ── XAGUSD RETIRÉ de la blocklist le 2026-08-27 (choix utilisateur) ─────────
# Il y avait été mis le 08-20 sur : 24 trades, 20.8% WR, -63.35$, et 0 sur 3
# la semaine du 08-17 avec un MFE moyen de +0.00R (jamais un tick en vert).
# L'utilisateur veut lui redonner sa chance — décision assumée.
#
# CE QUI N'A PAS CHANGÉ, et qu'il faut surveiller : le lot minimum d'argent
# vaut 50 onces, donc 1$ de mouvement = 50$ de P&L. Le budget de 7% du solde
# se traduit en une distance entrée→SL minuscule, et le solde est AUJOURD'HUI
# PLUS PETIT qu'à l'époque du blocage (65$ contre 58-92$) :
#     7% de 65$ = 4.57$  ->  0.091 de mouvement  ->  ~0.4-0.6xATR de stop
# C'est la largeur du bruit. C'est exactement ce qui tuait ces trades en
# 2 minutes. Silver déclenchait aussi le plafond de risque en permanence
# (46% du solde au lot minimum, 18 rejets executor le 08-15).
# À réévaluer sur ~15-20 trades : si le profil "0 tick en vert" se répète,
# le remettre dans le set.
#
# ── XAUUSD ajouté le 2026-09-19 (choix utilisateur) ─────────────────────────
# QUATRE semaines négatives d'affilée, 24 trades, PF jamais au-dessus de 0.49 :
#     W34  n= 4   -10.73$  WR 25.0%  PF 0.38
#     W35  n= 3   -26.84$  WR 66.7%  PF 0.23
#     W36  n= 7   -23.28$  WR 28.6%  PF 0.33
#     W37  n=10   -27.84$  WR 30.0%  PF 0.49
#
# À COMPARER à son historique complet : 141 trades, -18.74$, PF 0.97 — l'or
# n'a jamais été un mauvais symbole, il s'est cassé fin août. C'est donc un
# blocage sur CHANGEMENT DE RÉGIME, pas sur un symbole intrinsèquement nul,
# et c'est pour ça qu'il est révisable (contrairement aux paires JPY).
#
# CE QUI ÉCARTE L'HYPOTHÈSE "STOPS TROP SERRÉS" : le plafond de risque est
# passé à 15% le 09-15, les stops or sont montés de 1.01x à 1.83xATR, et le
# taux de réussite n'a pas bougé (30%). L'or ne perd pas parce qu'il se fait
# sortir par le bruit — il perd parce qu'il se trompe de direction. Pour
# mémoire la semaine W37 : 8 ventes = -8.02$ (quasi nul), 2 achats = -19.82$,
# dans un or qui baisse depuis 4619 (08-21) vers 4285. n=2 sur les achats,
# trop peu pour en faire une règle directionnelle — d'où le blocage complet.
#
# À RÉÉVALUER : rejouer l'or sur la semaine W38+ (le scan continue de le
# rejeter silencieusement, mais rejection_log garde la trace des signaux). S'il
# repasse positif sur 15-20 signaux simulés, le retirer de ce set.
#
# NOTE : ce blocage n'affecte QUE la génération de signaux. Les positions or
# déjà ouvertes restent gérées normalement (manage_open_positions ne consulte
# pas cette liste) — vérifié le 2026-09-19 avec une vente XAUUSDm ouverte.
BLOCKED_SYMBOLS = {"GBPJPY", "USDJPY", "EURUSD", "XAUUSD"}


# ── Blocage du consensus MTF "conflict" (5m vs 1h) — RÉTABLI le 2026-10-06 (choix utilisateur)
#
# Retiré le 2026-08-01 ("observation seule, à surveiller"). Bilan de la
# surveillance, 320 trades SMC du 31/07 au 06/10 (scratchpad
# smc_entry_study.py / smc_entry_tests.py), critère "+1R avant -1R" :
#
#                      n    +1R d'abord    résultat
#   aligné            166      54%         +19.8R
#   conflict          105      40%         -29.6R   -208.71$
#     dont achats      45      24%                  -151.38$
#
# Les trades en conflit ont perdu PLUS que tout le bot (-201.81$) : le reste
# était à l'équilibre. L'arbitrage "1H master" ne les sauvait pas (achats
# perdants quel que soit le côté suivi, ventes côté 1h 26%). On bloque donc
# tout le conflit. Coût : ~1/3 des trades en moins.
#
# Constante CODE et pas réglage : l'app réécrit les settings à chaque Start.
HTF_CONFLICT_BLOCK = True


# ── Symboles en ENTRÉE LIMITE UNIQUEMENT — XAGUSD le 2026-09-23 (choix utilisateur)
#
# L'argent n'est pas un mauvais symbole : ce sont ses ordres AU MARCHÉ qui
# perdent. Sur toute sa vie :
#
#     en attente (limite)  n=31  +14.85$  WR 35.5%  PF 1.12
#     au marché            n=21  -67.15$  WR 19.0%  PF 0.61
#
# La coupure tient à chaque période mesurée. Depuis le plafond 15% (09-15) :
# en attente +15.40$ sur 5, au marché -15.60$ sur 4 — le "point mort" de
# l'argent (-0.20$ sur 9) n'est que ces deux moitiés qui s'annulent. Les deux
# pertes du 09-23 étaient au marché et n'ont jamais été en vert (MFE 0.04R et
# 0.00R).
#
# Mécanisme : l'argent a la pire excursion adverse immédiate de tous les
# symboles tradés (médiane -0.70R dans les 15 min, contre -0.24R pour ETH).
# Un ordre au marché entre là où se trouve le prix, au milieu de ce bruit ; une
# limite attend le repli et gagne de la marge avant le stop.
#
# CONSÉQUENCE — l'argent devient ACHETEUR UNIQUEMENT. Les deux voies de
# placement n'utilisent l'ordre limite QUE pour les achats (les ventes en
# attente ont fait 0 gain sur 9 en live, d'où ce choix plus ancien). Bloquer le
# marché bloque donc toutes les ventes argent. Ce qu'on retire :
#     ventes argent, vie entière : n=5, 1 gain, -12.00$, PF 0.60
# BTC et ETH continuent de trader dans les deux sens.
#
# PENDING_ONLY_ALLOW_MARKET_SELLS = True rend les ventes argent au marché
# (le symbole redevient bidirectionnel, seuls les ACHATS au marché restent
# bloqués — soit 16 des 21 trades au marché, -55.15$).
#
# Couvre les deux voies qui plaçaient de l'argent au marché dans la boucle
# scalping : lg_primary (repli sur place_trade quand aucune limite n'est posée)
# et _try_fallback (price_action). L'ancienne boucle /bot (auto_scan) est
# arrêtée — vérifié le 09-23, bot_state.running = False.
#
# Retirer "XAGUSD" de ce set = retour au comportement d'avant.
PENDING_ONLY_SYMBOLS = {"XAGUSD"}
PENDING_ONLY_ALLOW_MARKET_SELLS = False


def _blocks_market_entry(symbol: str, action: str) -> bool:
    """True si ce symbole n'a pas le droit d'entrer au marché pour cette action."""
    s = (symbol or "").upper()
    if not any(k in s for k in PENDING_ONLY_SYMBOLS):
        return False
    if PENDING_ONLY_ALLOW_MARKET_SELLS and (action or "").lower() == "sell":
        return False
    return True


def _smc_risk_cap(settings: dict) -> Optional[float]:
    """Plafond de risque par trade réglé dans l'app (fraction, 0.15 = 15 %),
    ou None pour garder les plafonds par symbole de l'executor."""
    try:
        v = settings.get("risk_cap_pct")
        v = float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return None
    return v / 100.0 if v > 0 else None


def _smc_target_risk(settings: dict) -> float:
    """% de risque visé par les entrées limites (compute_risk_based_entry) :
    target_risk_pct, mais jamais au-dessus du plafond réglé — sinon chaque
    limite visant 7 % sous un plafond de 5 % serait refusée par l'executor."""
    t = float(settings.get("target_risk_pct", 5.0))
    cap = _smc_risk_cap(settings)
    return min(t, cap * 100.0) if cap else t

# ── Exemption filtre HTF sur les VENTES — 2026-08-26, TEST FORWARD ──────────
# Pour les symboles listés ici, une VENTE n'est plus refusée parce que la
# tendance HTF est haussière. Les ACHATS et tous les autres gates (score,
# contre-tendance/CHoCH, P/D, risque, corrélation) restent inchangés.
#
# Constat qui a motivé le test (depuis le 08-17) :
#     ETH  achats n=30  +30.99$  60% WR   |  ETH ventes n=6  +17.62$  83% WR
#     BTC  achats n=13  -15.51$  38% WR   |  BTC ventes n=0  (toutes bloquées)
# BTC produit PLUS de signaux vendeurs qu'ETH (1880 vs 1077) mais son 4H reste
# haussier (+1.03% sur 48h) : les ventes sont donc gatées et seuls les achats
# passent — or ce sont les achats qui perdent.
#
# Contrefactuel sur les 38 épisodes vendeurs BTC bloqués : 39.5% WR, +14.97R,
# +0.394R/épisode (breakeven 28.6%). Sur ETH le même test donne -0.514R, donc
# le blocage y est justifié — d'où une exemption PAR SYMBOLE, pas globale.
#
# !! AVERTISSEMENT — cette méthode a déjà produit un faux positif ici !!
# Le waiver contre-tendance de 2026-07-30 reposait sur le même type d'étude
# (232 épisodes bloqués, projection 49% WR / +0.71R). En réel : 21.4% WR sur
# 28 trades, annulé. Voir engine.py ~452. Les chiffres ci-dessus sont des
# fills SIMULÉS (bracket 2.5xATR / 2.5R), sans spread ni slippage, sur une
# seule fenêtre de 10 jours et un seul régime.
#
# ═══ RÉSULTAT : ANNULÉ LE MÊME JOUR (2026-08-26). NE PAS RÉACTIVER. ═══════
# Le contrefactuel "+0.394R" était FAUX. Erreur de comptage identique à celle
# de 2026-07-30, retrouvée en la refaisant avec les contraintes réelles :
#
#   NAIVE (chaque épisode = un trade)      : 55 trades, 36.4% WR, +15.65R
#   RÉEL  (1 position BTC à la fois, pas
#          de week-end, pas de zone morte) : 17 trades, 17.6% WR,  -6.82R
#
# Même données, même période (07-27 -> 08-26). La seule différence est la
# limite d'UNE position BTC ouverte à la fois — et elle inverse le signe.
#
# Pourquoi : quand BTC BAISSE, les signaux vendeurs arrivent en RAFALE (8 en
# quelques heures, tous gagnants). Le comptage naïf les compte 8 fois ; le bot
# n'en prend qu'UN. Quand BTC MONTE, les signaux perdants arrivent espacés —
# et là le bot les prend TOUS. Les gains sont donc sur-comptés et les pertes
# comptées juste. Tout contrefactuel "épisodes bloqués" a ce biais.
#
# Semaine par semaine en réel : 1 seule semaine positive sur 5 (-42.02$ au
# total). Semaine du 08-17 : 6 trades, 6 pertes.
#
# Conclusion plus large : BTC perd DANS LES DEUX SENS.
#     achats réels        -94.66$ (52 trades)
#     ventes simulées     -42.02$ (17 trades)
# Le problème de BTC n'est pas la direction autorisée, c'est BTC.
#
# Set vide = comportement normal : une vente reste autorisée dès que la
# tendance HTF est baissière (les ventes ETH du 08-25/26 sont passées avec ce
# filtre actif). Le filtre bloque aussi les ACHATS en tendance baissière —
# ce n'est pas un filtre anti-vente, c'est un filtre pro-tendance.
HTF_SELL_EXEMPT_SYMBOLS: set[str] = set()

# Gate consensus 5m/1h — voir le bloc [CONSENSUS] dans la boucle de scan.
# Bloque : conflit+ACHAT, et tout consensus bearish (achats comme ventes).
# Garde  : bullish, conflit+VENTE (meilleure cohorte vendeuse), non journalisé.
# False = comportement d'avant le 2026-09-06 (consensus purement observationnel).
CONSENSUS_GATE_ENABLED = True

PA_FALLBACK_ENABLED = True    # Price action : +0.147R hors échantillon (2026-08-01)
                              # Tourne en PASSE INDÉPENDANTE après la boucle
                              # timeframe, pas seulement quand LG rate son score.

# ── S/R et M1 : DÉBRANCHÉS, pas seulement désactivés ────────────────────────
# Leur code d'appel a été retiré lors de la restructuration price-action du
# 2026-08-01. Remettre ces drapeaux à True ne fera RIEN — il faudrait recâbler
# les appels. Conservés comme trace de la décision, pas comme interrupteurs.
#   S/R : 31% WR, -$51 sur 200 trades
#   M1  : 27% WR, -$24.07 sur 74 trades, sous le seuil RR2.5 (28.6%) ; il
#         contournait les gates zone/blended/macro-4H et a produit toute la
#         série perdante du 07-10→07-11 (8 SL d'affilée).
SR_FALLBACK_ENABLED = False   # débranché — voir ci-dessus
M1_FALLBACK_ENABLED = False   # débranché — voir ci-dessus

scalp_history        = []
scalp_cooldowns      = {}   # {symbol: datetime expiry}
symbol_paused_until  = {}   # {symbol: datetime} — paused after 3 consecutive losses
symbol_streak_anchor = {}   # {symbol: str} — horodatage de la dernière clôture
                            # ayant DÉJÀ déclenché une pause. Empêche le
                            # ré-armement en boucle (cf. check_symbol_loss_streaks).
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
                "SELECT outcome, COALESCE(closed_at, timestamp) AS t FROM scalp_log "
                "WHERE symbol=? AND outcome IS NOT NULL "
                "AND timestamp >= ? "
                "ORDER BY COALESCE(closed_at, timestamp) DESC LIMIT 2",
                (symbol, session_start)
            )
            _recent        = c.fetchall()
            recent_closed  = [r[0] for r in _recent]
            newest_close   = _recent[0][1] if _recent else None
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

                # ── Anti-deadlock — 2026-08-20 ──────────────────────────────
                # La pause "1h" se ré-armait INDÉFINIMENT. À l'expiration, ce
                # check relisait LES MÊMES deux pertes : un symbole en pause ne
                # peut produire aucun résultat neuf, donc la condition restait
                # vraie et la pause repartait pour 1h. En boucle.
                #
                # Constaté en live le 2026-08-19/20 : BTC, XAUUSD et XAGUSD
                # bloqués ~2 jours d'affilée sur une règle censée durer 1h,
                # et totalement invisibles — le skip du scan (plus bas) est
                # silencieux, donc zéro ligne dans rejection_log. 3 des 5
                # symboles avaient disparu sans que rien ne le signale.
                #
                # /scalping/unpause/{symbol} n'y pouvait rien non plus :
                # supprimer la clé rendait `symbol not in symbol_paused_until`
                # vrai, donc re-pause au scan suivant. Seul un redémarrage de
                # session débloquait (il déplace session_start, ce qui sort les
                # vieilles pertes de la fenêtre).
                #
                # Règle : on n'arme une NOUVELLE pause que si un NOUVEAU trade
                # clôturé est apparu depuis celle d'avant. Exception si une
                # position est encore ouverte et perdante — là, la condition
                # est vivante et non un écho du passé, on garde l'ancien
                # comportement.
                if (not underwater and newest_close is not None
                        and symbol_streak_anchor.get(symbol) == newest_close):
                    continue

                if symbol not in symbol_paused_until or now >= symbol_paused_until[symbol]:
                    resume_at = now + timedelta(hours=1)
                    symbol_paused_until[symbol] = resume_at
                    symbol_streak_anchor[symbol] = newest_close
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


# ── Filtre de tendance RAPIDE — 2026-08-27 ─────────────────────────────────
# Le gate HTF existant est une pente EMA20 sur H1 = 20 heures de recul, alors
# que les trades durent 12 à 42 minutes. Décalage d'un facteur 30 à 60.
#
# Cas déclencheur (observé par l'utilisateur, 08-27) : 4 achats ETH pris à
# l'intérieur d'une baisse de 2% étalée sur 4 heures. Les 4 ont touché le SL
# (MFE +0.00 / +0.07 / +0.12 / +0.60R). Le filtre H1 lisait "bullish" parce
# que sur 20 heures la tendance montait toujours.
#
# Mesure sur les 216 trades réels de la période (classification de trades
# RÉELLEMENT pris, pas un contrefactuel — donc pas le biais de comptage qui a
# invalidé l'étude des ventes BTC) :
#
#   tendance 4h D'ACCORD avec le trade  n=127  -80.68$  WR 40.9%  avgR +0.03
#   tendance 4h EN DÉSACCORD            n= 89 -135.09$  WR 30.3%  avgR -0.42
#
#   EMA20 M15 d'accord                  n=119  -83.20$  WR 41.2%  avgR -0.01
#   EMA20 M15 en désaccord              n= 77 -157.60$  WR 27.3%  avgR -0.49
#
# Deux mesures indépendantes, même verdict, écart ~0.45R par trade. La fenêtre
# 6h ne sépare RIEN : l'horizon utile est ~4-5h, pas plus.
#
# ATTENTION MÉTHODE : la première version de cette étude était FAUSSE.
# copy_rates_range renvoie la bougie qui a OUVERT avant t0 — elle se clôture
# APRÈS l'entrée (entrée 11:07 -> bougie H1 11:00 clôturée à 12:00 = 53 min de
# futur). L'effet a survécu à la correction, mais on ne lit ici que des
# bougies TERMINÉES, d'où le .iloc[:-1] : jamais la bougie en cours.
def _fast_trend_4h(symbol: str) -> str:
    """Direction sur les 4 dernières bougies H1 TERMINÉES."""
    try:
        df = fetch_candles(symbol, "1h", limit=12)
        if df is None or len(df) < 7:
            return "neutral"
        c = df["close"].iloc[:-1]          # jamais la bougie en cours
        if len(c) < 5:
            return "neutral"
        return "bullish" if c.iloc[-1] > c.iloc[-5] else "bearish"
    except Exception:
        return "neutral"


def _fast_trend_m15(symbol: str) -> str:
    """Prix vs EMA20 sur M15 (~5h), bougies TERMINÉES uniquement."""
    try:
        df = fetch_candles(symbol, "15m", limit=60)
        if df is None or len(df) < 30:
            return "neutral"
        c = df["close"].iloc[:-1]
        if len(c) < 25:
            return "neutral"
        ema = c.ewm(span=20, adjust=False).mean()
        px, e, e_prev = c.iloc[-1], ema.iloc[-1], ema.iloc[-4]
        if px > e and e > e_prev:
            return "bullish"
        if px < e and e < e_prev:
            return "bearish"
        return "neutral"
    except Exception:
        return "neutral"


# L'OR est EXCLU : l'effet y est INVERSÉ, et nettement.
#     XAU  d'accord n=20  -80.13$   en désaccord n=16  +31.02$
# Les autres vont tous dans le sens attendu :
#     ETH  +32.14$ / -19.56$   XAG  +28.70$ / -48.45$
#     BTC  -38.59$ / -52.36$   EUR  -13.41$ / -18.93$
# Même schéma que pour TRAIL_ATR_FLOOR : les métaux se comportent autrement.
# Retirer XAUUSD de ce set le remettrait sous le filtre — ne pas le faire sans
# une nouvelle mesure.
FAST_TREND_GATE_EXEMPT = {"XAUUSD"}


def _asset_class(symbol: str) -> str:
    """Group symbols so a reversal in one market doesn't freeze another
    (a bad BTC-buy shouldn't block a good gold-buy)."""
    s = (symbol or "").upper()
    if s in ("BTC", "ETH", "SOL", "BNB", "XRP"):
        return "crypto"
    if s in ("XAUUSD", "XAGUSD"):
        return "metals"
    return "forex"


def update_direction_breaker(consec: int = 2, block_hours: int = 4,
                             global_consec: int = 2, global_block_hours: int = 3):
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

    # ── Breaker GLOBAL, toutes classes confondues (2026-08-03) ──────────────
    # Le breaker par classe ne voit pas un retournement de marché ENTIER. Le
    # 08-03 : 4 pertes SELL consécutives réparties métaux(1) / crypto(2) /
    # forex(1). Chaque classe restait à son seuil ou en dessous, donc rien n'a
    # bloqué, et la journée a rendu 31.66$ d'un pic à +35.41$.
    #
    # Rejoué en file d'événements (ouvertures et clôtures dans le vrai ordre
    # chronologique, pour qu'un trade bloqué ne nourrisse pas la série
    # suivante) sur 131 trades depuis le 07-20 :
    #   consec=2 block=3h : -281.32$ -> -200.86$  (+80.46$)
    #   consec=3 block=3h : -281.32$ -> -230.60$  (+50.72$)
    #   consec=4 block=3h : -281.32$ -> -272.60$   (+8.72$)
    # Jour par jour à consec=2/3h : 8 jours meilleurs, 1 pire (-2.17$), 5
    # inchangés — c'est la régularité, pas l'ampleur, qui valide le réglage.
    #
    # La version GLOBALE existait à l'origine (validée 07-20→22 : -29.86$ ->
    # +13.74$) avant d'être restreinte par classe le 07-23. On garde les deux :
    # par classe pour un mouvement corrélé dans un marché, global pour un
    # retournement de régime qui traverse tous les marchés.
    g_dir, g_streak, g_ct = None, 0, None
    for symbol, action, outcome, profit, ct in rows:          # most-recent first
        is_loss = outcome == 0 and abs(profit or 0) >= 0.15
        is_win  = outcome == 1 and (profit or 0) > 0.15
        if not is_loss and not is_win:
            continue
        if is_win:
            break
        if g_dir is None:
            g_dir, g_streak, g_ct = action, 1, ct
        elif action == g_dir:
            g_streak += 1
        else:
            break
    if g_streak >= global_consec and g_ct:
        try:
            until = datetime.fromisoformat(g_ct) + timedelta(hours=global_block_hours)
        except Exception:
            until = now + timedelta(hours=global_block_hours)
        if until > now:
            direction_breaker[("global", g_dir)] = until
            rem = int((until - now).total_seconds() // 60)
            print(f"[DIR-BREAKER] GLOBAL {g_dir.upper()} blocked {rem}min "
                  f"({global_consec}+ consecutive {g_dir} SL losses across ALL markets)")


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
    _now_utc = datetime.now(timezone.utc)
    hour_utc = _now_utc.hour

    # ── Week-end : AUCUN trade — 2026-08-22 ─────────────────────────────
    # Seul le crypto cote samedi/dimanche, et il y perd systématiquement.
    # Mesuré sur les 4 semaines de test (179 trades) :
    #
    #     semaine  n=161  -112.03$  WR 39.1%  PF 0.80
    #     week-end n= 18   -43.62$  WR 11.1%  PF 0.09
    #
    # 2 gagnants sur 18. Et ce n'est pas un mauvais week-end isolé, les
    # QUATRE sont négatifs : -7.53$ / -2.06$ / -4.23$ / -27.80$. Le dernier
    # (22/08) a suffi à faire basculer la semaine de +56.50$ à +28.70$.
    #
    # Cause probable : liquidité morte, aucun flux institutionnel, range
    # haché — exactement le raisonnement qui a fait ajouter la zone morte
    # 21h-02h ci-dessous, qui elle a bien tenu.
    #
    # n=18 reste sous la barre des 30 observations, mais le signe est
    # constant sur 4 week-ends indépendants et l'écart est énorme
    # (11% vs 39% de réussite). Le coût d'avoir tort est de rater quelques
    # trades crypto ; le coût de ne rien faire est démontré à -43.62$.
    #
    # Note : le vendredi soir et le dimanche soir (ouverture forex) sont
    # déjà couverts par la zone morte 21h-02h, donc bloquer samedi+dimanche
    # en entier ne rogne pas sur l'ouverture du lundi.
    if _now_utc.weekday() >= 5:          # 5 = samedi, 6 = dimanche
        return False
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

# ── Plancher ATR sur le buffer du trail, PAR SYMBOLE — 2026-08-27 ───────────
# Le buffer du trail vaut max(5% de la distance TP, 10% du profit courant).
# Pour un stop SERRÉ cela donne une distance absolue minuscule : sur l'or,
# TP à 2.5R avec un stop de ~0.7-1.2xATR -> buffer ~0.15-0.40xATR. L'or
# parcourt ça en UNE bougie, donc le trail est décroché par le bruit normal
# avant que le mouvement n'aille au bout (capture 67%, sorties en 46 min
# alors que le MFE moyen des gagnants est +2.65R).
#
# Rejeu barre par barre des 23 trades or (bracket réel, règle live répliquée) :
#     live (aucun plancher)  avgR +0.245   WR 47.8%   gain moyen +1.60R
#     plancher 0.50xATR      avgR +0.275   WR 47.8%
#     plancher 0.75xATR      avgR +0.369   WR 47.8%   gain moyen +1.86R  <-
#     plancher 1.00xATR      avgR +0.272   WR 39.1%
#     plancher 1.50xATR      avgR +0.554   WR 34.8%
# Jusqu'à 0.75 le WR est INCHANGÉ : on ne troque pas des gagnants contre de
# la taille, on garde simplement davantage des mêmes gagnants. Au-delà, des
# gagnants basculent en perdants (c'est le risque de série que l'étude de
# sortie de juillet avait relevé).
#
# PAR SYMBOLE et pas global — les autres vont dans l'autre sens :
#     BTC  -0.050 -> -0.136 quand le plancher s'élargit (PIRE)
#     EUR  -0.110 -> -0.180 (PIRE)
#     ETH  plat
# C'est ce qui explique le résultat nul de l'étude poolée de juillet : les
# symboles se compensent.
#
# Propriété de sécurité : le trail ne s'arme qu'au-dessus de +1R, donc un
# trade qui n'y arrive jamais n'est pas touché. Les 9 perdants de l'échantillon
# sont IDENTIQUES sous les deux règles. Ce réglage ne peut pas aggraver une
# perte, seulement modifier un gagnant.
#
# FRAGILITÉ ASSUMÉE : n=23, et 6 trades s'améliorent contre 5 qui empirent.
# Le gain net (+2.86R) vient à +3.10R de DEUX trades (08-17 et 08-18). Sans
# eux les 21 autres font -0.24R. Le rejeu est aussi ~+0.068R optimiste
# (ni spread ni slippage).
# CRITÈRE DE SORTIE FIXÉ D'AVANCE : si les 15 prochains trades or ne montrent
# pas un gain moyen > 1.60R, retirer cette entrée du dict.
# ── Réglage PAR SYMBOLE, jugé sur le R TOTAL et pas sur le taux de réussite ──
# 2026-09-09. Un trail plus large produit bien des gagnants plus gros — le
# gain moyen passe de +1.22R à +3.26R quand on l'élargit — mais le taux de
# réussite s'effondre plus vite (41% -> 15%). En POOLÉ c'est donc perdant, ce
# qui a masqué le fait que les symboles vont dans des sens OPPOSÉS :
#
#   plancher :   0.0    0.75    1.0    1.5    2.0    3.0     (R total, 244 trades)
#   XAU        +5.0    +7.1   +4.4  +10.4   +5.2   +5.0
#   ETH        -4.1    -4.1   -3.7   -3.5   -0.9   +1.5
#   XAG        -1.5    -1.0   -2.8   -3.1   -4.1   -8.7
#   BTC        -7.2    -9.5  -10.2  -12.4  -13.0  -21.4
#
# ETH = 3.0 : la seule courbe MONOTONE de l'échantillon (elle s'améliore à
#   chaque palier, sur n=86) et elle passe positive au bout. C'est le signal
#   le plus fiable des quatre. Mécaniquement le buffer devient plus large que
#   le stop d'origine : le trail ne resserre donc plus qu'après un vrai
#   parcours, il laisse courir. Le risque reste borné — le trail ne peut
#   jamais déplacer le stop DANS le mauvais sens, d'où avgLoss stable à
#   -1.00R sur toutes les variantes.
#
# XAU = 0.75 : conservé. Le pic à 1.5 (+10.4R) est tentant mais la courbe est
#   EN DENTS DE SCIE (0.75 -> +7.1, 1.0 -> +4.4, 1.5 -> +10.4, 2.0 -> +5.2) sur
#   n=29 seulement : signature de bruit, pas d'effet stable. 0.75 capte déjà
#   +7.1 des +10.4 sans coûter un point de taux de réussite (48.3% inchangé).
#   À revoir quand l'or aura 40-50 trades — il est de toute façon presque
#   entièrement bloqué par le plafond de risque au solde actuel.
#
# XAG et BTC = AUCUN plancher : dégradation MONOTONE chez les deux. Leurs
#   gagnants ne courent pas, donc élargir ne fait que rendre plus de profit.
#   BTC est le plus net : -7.2R -> -21.4R entre 0 et 3.0.
#
# Note de méthode : le rejeu est ~0.06R optimiste (pas de spread/slippage),
# biais constant sur toutes les variantes — les comparaisons relatives
# tiennent, les valeurs absolues sont flatteuses.
TRAIL_ATR_FLOOR = {
    "XAUUSD": 1.5,     # 0.75 -> 1.5 le 2026-09-11, voir TRAIL_START_R
    "ETHUSD": 3.0,
}

# ── Point d'ARMEMENT du trail, PAR SYMBOLE — 2026-09-11 ─────────────────────
# Constat de départ : l'histogramme des gagnants montre 49 des 108 dans la
# SEULE tranche 0.8-1.0R, et 67% entre 0.8R et 1.3R. Ce pic tombe exactement
# sur trail_start_r = 1.0. Le trail récoltait donc les gagnants à l'instant
# précis où ils atteignaient 1R — c'est ce qui fabriquait le profil ~1:1
# (gain moyen +1.26R contre perte moyenne -1.16R), structurellement perdant
# sous 50% de réussite.
#
# Armer plus tard fait bien ce qu'on attend — sur les 253 trades rejoués :
#     armement   totalR    WR      gain moyen   RR
#     1.0R       -13.2   39.5%       +1.34     1.39
#     1.5R       -19.9   32.4%       +1.82     1.84
#     3.0R       -29.9   18.2%       +3.02     3.70
# Le RR monte de 1.39 à 3.70. MAIS en poolé le taux de réussite tombe juste
# ce qu'il faut pour annuler le gain : à CHAQUE palier il finit 2 à 3 points
# SOUS le seuil de rentabilité. L'échange RR/réussite est donc à peu près au
# prix du marché — aucun repas gratuit globalement.
#
# Par symbole en revanche :
#     XAU  arm 1.0 / floor 0.75 (avant) -> +6.1R
#          arm 1.0 / floor 1.50 (RETENU) -> +9.4R   WR 33.3%  RR 3.06
#     ETH  arm 1.0 / floor 3.0 (avant)  -> +0.8R
#          arm 1.5 / floor 3.0 (RETENU) -> +6.0R   WR 37.5%  RR 1.85
#
# Choix fondé sur PLATEAU, pas sur pic — 24 combinaisons testées par symbole,
# prendre le maximum brut fabriquerait un faux positif :
#   XAU floor 1.5 : TOUTE la ligne fait +6.8 à +9.4R quel que soit l'armement
#                   -> plateau stable, et ça reproduit l'étude du 09-09.
#   ETH arm 1.25-1.5 : +5.7R et +6.0R côte à côte, chute des deux côtés,
#                   sur n=88 -> plateau.
#   XAG arm 2.0 : +0.5R entouré de -0.9R et -1.0R sur n=29 -> PIC = bruit,
#                   volontairement NON retenu.
#   BTC : sa config actuelle est la meilleure des 24 testées (3e confirmation)
#         -> aucune entrée ici ni dans TRAIL_ATR_FLOOR.
#
# Set vide pour un symbole = on garde trail_start_r global des settings (1.0).
TRAIL_START_R = {
    "ETHUSD": 1.5,
}

_signal_atr_cache: dict = {}   # ticket -> ATR du signal d'origine (0.0 = inconnu)
_partial_done: set = set()     # tickets dont la moitié à +1R a été fermée
_orig_volume_cache: dict = {}  # ticket -> volume à l'ouverture


def _half_taken(ticket: int, volume: float) -> bool:
    """La moitié à +1R a-t-elle déjà été prise sur ce ticket ? Mémoire du
    process, et après un redémarrage : volume actuel < volume d'ouverture
    (deal d'entrée MT5) — sinon le reste serait recoupé en deux."""
    if ticket in _partial_done:
        return True
    ov = _orig_volume_cache.get(ticket)
    if ov is None:
        ins = [d for d in (mt5.history_deals_get(position=ticket) or [])
               if d.entry == mt5.DEAL_ENTRY_IN]
        ov = ins[0].volume if ins else volume
        _orig_volume_cache[ticket] = ov
    if volume < ov - 1e-9:
        _partial_done.add(ticket)
        return True
    return False


def _signal_atr(ticket: int) -> float:
    """
    ATR journalisé avec le signal qui a ouvert ce ticket.

    On relit l'ATR du SIGNAL plutôt que d'en recalculer un ici : c'est
    exactement la valeur sur laquelle l'étude a été faite (ATR de la timeframe
    du trade), et manage_open_positions ne connaît pas cette timeframe.
    Mis en cache — la valeur ne change pas pendant la vie de la position.
    """
    if ticket in _signal_atr_cache:
        return _signal_atr_cache[ticket]
    val = 0.0
    try:
        import json as _json
        from core.database import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT data FROM scalp_log WHERE ticket=? ORDER BY id DESC LIMIT 1",
            (ticket,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            val = float(_json.loads(row[0]).get("atr") or 0.0)
    except Exception:
        val = 0.0
    _signal_atr_cache[ticket] = val
    return val


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

    # ── Purge des ordres en attente périmés ───────────────────────────────
    # DOIT rester AVANT le early-return "pas de position" : un ordre en
    # attente n'est PAS une position, donc sans cela le nettoyage ne
    # tournerait jamais dans le cas le plus fréquent (rien d'ouvert, un
    # limit qui traîne). C'est précisément la situation où un GTC oublié
    # bloque un symbole pour rien.
    try:
        _max_age = scalp_state["settings"].get("pending_max_age_minutes", 120)
        if _max_age and _max_age > 0:
            cancel_stale_pending_orders(_max_age)
    except Exception as _pce:
        print(f"[PENDING/CANCEL] error: {_pce}")

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

        # ── Trail — déclenchement à 40% du chemin vers le TP (2026-08-01) ──
        # Était 70%. Balayé sur 23 645 signaux rejoués barre par barre, avec
        # séparation train (< 2026-05-01) / test (>= 2026-05-01) :
        #
        #   départ trail   avg R (tout)   train      test
        #        30%         +0.2046     +0.1937   +0.2201
        #        40%         +0.2075     +0.1925   +0.2289   <-- retenu
        #        50%         +0.2024     +0.1882   +0.2227
        #        70%         +0.1669     +0.1599   +0.1768   (ancien réglage)
        #
        # 40% est optimal sur l'ensemble ET sur le test hors échantillon, et le
        # plateau 30-50% est plat — signe d'un effet réel, pas d'un surajustement.
        # Gain +0.041R par trade (+24% d'espérance).
        #
        # Effet de bord : avec TP à 2.5R, 40% du chemin = 1.0R. Le trail
        # s'active donc AVANT le breakeven à 1.5R et le rend inopérant — ce
        # n'est pas une perte, c'est mieux : à 1.0R le trail verrouille déjà
        # ~+0.875R au lieu de 0. Le bloc BE reste en place pour les cas où le
        # trail est désactivé ou le TP configuré différemment.
        # ── Déclenchement exprimé en R, pas en % du TP (2026-08-10) ────────
        # CORRECTION d'une erreur d'implémentation. L'étude de sortie qui a
        # produit le "40%" tournait sur un bracket FIXE à 2.5R : à RR 2.5,
        # 40% du TP EST 1.0R. Les deux formulations sont numériquement
        # identiques dans ce jeu de données, il ne pouvait pas les
        # départager — et j'ai retenu la mauvaise.
        #
        # Le système d'entrées limites produit maintenant des RR de 6 à 25.
        # À RR 10, "40% du TP" = 4.1R : le trail ne s'arme quasiment jamais,
        # et le BE à 1.5R non plus. Le 08-10 deux trades en attente ont
        # atteint +0.72R et +1.01R sans AUCUNE protection active, puis se
        # sont retournés jusqu'au stop plein.
        #
        # Mesuré (15 515 entrées limites rejouées, RR ~9.5) :
        #   40% du TP : +0.2427R / trade,  15.2% de gagnants
        #   1.0R      : +0.7150R / trade,  64.1% de gagnants   -> +0.47R
        #   gain par symbole : BTC +0.479  ETH +0.449  XAG +0.486  XAU +0.496
        #
        # Contrôle sur les entrées AU MARCHÉ (21 703 trades, RR 2.5) :
        #   40% du TP : +0.2182R      1.0R : +0.2182R
        #   100.0% de résultats IDENTIQUES — à RR 2.5 c'est la même règle.
        # Le changement est donc sans effet sur l'existant et ne gagne que
        # là où le RR est élevé.
        #
        # trail_start_pct reste lu pour compatibilité : s'il est encore
        # présent dans des settings sauvegardés, il est converti en R
        # (0.40 -> 1.0R au RR 2.5 de référence) plutôt qu'ignoré.
        _cfg = scalp_state["settings"]
        _trail_r = _cfg.get("trail_start_r")
        if _trail_r is None:
            _trail_r = _cfg.get("trail_start_pct", 0.40) * 2.5

        # Override PAR SYMBOLE du point d'armement — voir TRAIL_START_R.
        for _sk, _sv in TRAIL_START_R.items():
            if _sk in pos.symbol.upper():
                _trail_r = _sv
                break

        # Le risque ORIGINAL, pas (entry - sl) courant : dès que le trail ou
        # le BE a déplacé le stop, cette différence devient nulle ou négative
        # et la règle en R se désarmerait toute seule au tick suivant.
        # _excursion_cache conserve le risque d'origine (renseigné juste
        # au-dessus par _track_excursion, depuis trade_signals ou le SL initial).
        _ex = _excursion_cache.get(ticket)
        _risk0 = _ex["risk"] if _ex else ((entry - sl) if is_buy else (sl - entry))

        # ── Moitié à +1R, le reste au point d'entrée — 2026-09-28 ────────────
        # Demande utilisateur après l'étude des 87 trades RÉELS du bot (30 j,
        # BTC/ETH/XAG), chaque règle rejouée minute par minute sur le même trade :
        #     trail actuel                    +51 $   DD 108 $   moitiés -79 / +130
        #     moitié à +1R, reste à l'entrée  +85 $   DD  81 $   moitiés -47 / +132
        # Seule règle meilleure que le trail dans les DEUX moitiés du mois. 43 %
        # des pertes étaient passées par +0.5R ; les gains rendaient 0.45R.
        # Règle testée, appliquée telle quelle : à +1R du risque d'ORIGINE, on
        # ferme la moitié au marché ; le stop du reste passe à l'entrée + 0.05R
        # (couvre le spread), l'objectif d'origine est gardé, et le reste n'est
        # PLUS suivi par le trail. Lot impossible à couper (0.01 = minimum
        # broker, ex. XAG) : le trail d'avant s'applique. Test mesuré : à revoir
        # après ~30 trades. partial_close_r = 0 -> désactivé.
        _pc_r = float(_cfg.get("partial_close_r", 1.0) or 0)
        if _pc_r > 0 and _risk0 > 0:
            _done = _half_taken(ticket, pos.volume)
            if not _done and moved >= _risk0 * _pc_r:
                _half = split_half_volume(pos.symbol, pos.volume)
                if _half:
                    _res = close_partial(pos, _half, f"CryptoOracle half {_pc_r:g}R")
                    if not _res["success"]:
                        print(f"[POS/HALF-ERR] {pos.symbol} ticket={ticket}: {_res['reason']}")
                        continue                         # nouvel essai dans 30 s
                    _partial_done.add(ticket)
                    _done = True
                    print(f"[POS/HALF] {pos.symbol} ticket={ticket} +{moved / _risk0:.2f}R: "
                          f"{_half} lot fermé(s) @ {_res['price']} "
                          f"(reste {round(pos.volume - _half, 8)})")
            if _done:
                # Arrondi aux décimales du BROKER avant de comparer : sinon (BTC,
                # 2 décimales) 83419.2567 devient 83419.26 chez le broker, une
                # VENTE voit son stop "pas encore posé" et renvoie la même
                # demande toutes les 30 s ("No changes") — vu le 2026-09-30.
                _si = mt5.symbol_info(pos.symbol)
                _pt = (_si.point if _si else 0.0) or 1e-5
                _be = round(entry + 0.05 * _risk0 if is_buy else entry - 0.05 * _risk0,
                            _si.digits if _si else 5)
                if (sl >= _be - _pt / 2) if is_buy else (0 < sl <= _be + _pt / 2):
                    continue                             # stop déjà à l'entrée ou mieux
                if (price > _be) if is_buy else (price < _be):
                    result = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": ticket,
                                             "symbol": pos.symbol, "sl": _be, "tp": tp})
                    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                        print(f"[POS/HALF-BE] {pos.symbol} ticket={ticket} SL: {sl} → {_be}")
                    else:
                        print(f"[POS/HALF-BE-ERR] {pos.symbol} ticket={ticket}: "
                              f"{result.comment if result else mt5.last_error()}")
                else:
                    # le prix est revenu sous l'entrée avant que le stop soit
                    # posé : c'est là que le stop du reste l'aurait sorti
                    _cur = mt5.positions_get(ticket=ticket)
                    if _cur:
                        _res = close_partial(_cur[0], _cur[0].volume, "CryptoOracle rest at BE")
                        print(f"[POS/HALF-BE-CLOSE] {pos.symbol} ticket={ticket}: reste fermé "
                              f"au marché ({'ok' if _res['success'] else _res['reason']})")
                continue                                 # pas de trail sur le reste

        if _risk0 > 0:
            _armed = moved >= _risk0 * _trail_r
        else:
            # risque d'origine inconnu (pas de ligne DB et stop déjà bougé) :
            # on retombe sur l'ancienne référence en % du TP.
            _armed = progress >= _cfg.get("trail_start_pct", 0.40)
        if _armed:
            # Buffer scales with current profit so big moves (800+ pts on BTC)
            # aren't stopped out by tiny 5-pt noise. 10% of unrealized profit
            # beats 5% of original TP once the trade runs far past TP.
            buffer = max(total_dist * 0.05, moved * 0.10)

            # Plancher ATR par symbole — voir TRAIL_ATR_FLOOR.
            # N'intervient que si le buffer calculé est plus SERRÉ que le
            # plancher : ne resserre jamais le trail, ne fait que l'élargir.
            _floor_mult = 0.0
            for _fk, _fv in TRAIL_ATR_FLOOR.items():
                if _fk in pos.symbol.upper():
                    _floor_mult = _fv
                    break
            if _floor_mult:
                _sig_atr = _signal_atr(ticket)
                if _sig_atr > 0 and _floor_mult * _sig_atr > buffer:
                    print(f"[TRAIL-FLOOR] {pos.symbol} ticket={ticket} buffer "
                          f"{buffer:.5g} -> {_floor_mult * _sig_atr:.5g} "
                          f"({_floor_mult}xATR {_sig_atr:.5g})")
                    buffer = _floor_mult * _sig_atr

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

        # ── Breakeven — filet avant que le trail ne s'active ───────────────
        # risk > 0 signifie que le SL est encore du côté perte (SL d'origine) ;
        # après le passage à BE, risk devient 0 et ce bloc ne re-déclenche plus.
        #
        # Le rejeu barre par barre (2026-08-01, 23 645 signaux) a INFIRMÉ le
        # soupçon que le BE coûtait de l'argent : retirer l'étape BE ne vaut
        # que -0.0027R par trade, du bruit. Il ne scratche que 6% des trades et
        # cette protection compense. On le garde. Avec trail_start_pct=0.40 il
        # ne se déclenche de toute façon presque jamais (le trail arrive avant).
        else:
            risk = (entry - sl) if is_buy else (sl - entry)
            _be_r = scalp_state["settings"].get("be_trigger_r", 1.5)
            if risk > 0 and _be_r > 0 and moved >= risk * _be_r:
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

    # ── Compte courant : lie l'état de risque persistant à ce login ──────────
    # (fait AVANT les deux garde-fous, pour qu'un changement de compte remette
    # le pic et la fenêtre journalière à zéro avant qu'ils ne soient évalués)
    try:
        _acct = mt5.account_info()
    except Exception:
        _acct = None
    if _acct is not None:
        _bind_risk_state_to_account(_acct.login, _acct.equity)
        _apply_cash_flows(_acct)          # dépôts/retraits : pas des pertes

    # ── Mise à jour du P&L journalier (réalisé uniquement) ───────────────────
    # Mesuré depuis _daily_window_start() (minuit, ou dernier démarrage depuis
    # l'app) et non plus depuis session_start : un redémarrage par le watchdog
    # après un crash ne remet plus le compteur à zéro.
    try:
        scalp_state["daily_pnl"] = get_daily_pnl_pct(_daily_window_start())
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
    # The only code-enforced floor left is the DRAWDOWN halt: -35% below the
    # PERSISTED peak equity (risk_state, survives crashes and restarts since
    # 2026-09-23) → full stop, no auto-reset. This is the ultimate wipe-preventer
    # (June had none → account went to $0 in July); it stays regardless of app.
    _eq = _acct.equity if _acct is not None else None
    if _eq and _eq > 0:
        _prev_peak = scalp_state.get("peak_equity")
        _peak = max(_prev_peak or _eq, _eq)
        scalp_state["peak_equity"] = _peak
        if _prev_peak is None or _peak > _prev_peak:
            # nouveau plus haut -> persister, pour qu'un crash ne l'efface pas
            risk_state["peak_equity"] = _peak
            if _acct is not None:
                risk_state["login"] = _acct.login
            _save_risk_state(risk_state)
        _dd = (_eq - _peak) / _peak * 100.0
        if _dd <= -SURVIVAL_HALT_PCT:
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

        # ── Blocklist code (voir BLOCKED_SYMBOLS) ────────────────────────
        # Placé en tête de boucle : aucun fetch, aucune analyse, aucun ordre
        # pour ces symboles même si l'app les renvoie dans enabled_symbols.
        if symbol.upper() in BLOCKED_SYMBOLS:
            continue   # silencieux — sinon une ligne par symbole par scan

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

                # ── Purge des ordres EN ATTENTE avant l'annonce — 2026-09-06 ──
                # Le filtre news bloquait les NOUVEAUX signaux mais laissait
                # vivre les limites déjà posées chez le broker. Elles se font
                # alors remplir PAR le choc lui-même.
                #
                # Cas réel, NFP du vendredi 2026-09-04 13:30 UTC :
                #   13:29  bougie M1 amplitude   6.15
                #   13:30  bougie M1 amplitude  80.81   <- l'or perd 80 points
                #   13:30:01  la limite XAUUSD est remplie à 4454.86
                #   13:30:09  stop à 4447.34, REMPLI à 4420.095 (27 pts plus bas)
                #   -> perte 34.76$ pour un risque prévu de 7.52$ (4.6x)
                # Même minute : ETH -7.33$, BTC -7.12$. Total -49.21$ en 35 s.
                # Le filtre news AVAIT bien bloqué les nouveaux signaux dès 13:15.
                # Le stop n'était pas faux : il n'y avait plus de prix en face.
                #
                # Sur 259 stop-outs, 22% glissent, en moyenne 0.71$ — trivial.
                # Mais ce seul trade pèse 27.24$ des 41.43$ de glissement total
                # de la période (66%). C'est un risque de QUEUE, pas un biais
                # moyen : on supprime la queue, pas une espérance.
                #
                # Même famille de trou que le blocage week-end qui laissait une
                # limite du vendredi se remplir le dimanche.
                try:
                    _purged = cancel_stale_pending_orders(max_age_minutes=None,
                                                          symbol=symbol)
                    if _purged:
                        print(f"[NEWS/PURGE] {symbol}: {len(_purged)} ordre(s) "
                              f"en attente annule(s) avant l'annonce")
                        _log_rejection(symbol,
                                       f"news_pending_purge: {len(_purged)} cancelled")
                except Exception as _npe:
                    print(f"[NEWS/PURGE] {symbol}: erreur {_npe}")
                continue
        except Exception as _ne:
            news_block = {"blocked": False}
            print(f"[NEWS] calendar error: {_ne}")

        # ── Max 1 position par symbole ────────────────────────────────────────
        # Le plafond était fixé à 2 ici, avec une logique "2e trade autorisé si
        # même sens + meilleur prix" plus bas. Cette logique était MORTE :
        # place_trade() et place_pending_trade() rejettent inconditionnellement
        # toute position déjà ouverte sur le symbole ("max 1 par symbole",
        # décision post-mortem sur 4 paires d'averaging perdantes). Le router
        # analysait donc entièrement un symbole déjà en position pour finir sur
        # un place_trade_failed. On skippe maintenant tout de suite : même
        # comportement, une passe d'analyse complète économisée par symbole.
        sym_positions = [p for p in _all_open
                         if p.magic == BOT_MAGIC and symbol.upper() in p.symbol.upper()]
        if sym_positions:
            print(f"[!]  {symbol} position déjà ouverte → skip")
            _log_rejection(symbol, "max_positions 1/1")
            continue

        # ── Consensus MTF — BLOQUANT à nouveau depuis 2026-10-06 ─────────
        # (voir HTF_CONFLICT_BLOCK : la période "observation seule" ci-dessous
        # a coûté plus que la perte totale du bot). Historique :
        #
        # Ce gate ("Gate A") calculait un consensus 5m+1h AU NIVEAU DU SYMBOLE
        # puis l'appliquait à TOUS les timeframes de trading. Conséquence : un
        # trade 15m ou 30m pouvait être bloqué par le graphique 5 MINUTES,
        # c'est-à-dire par une unité de temps INFÉRIEURE à celle de l'entrée —
        # l'inverse d'un filtre top-down.
        #
        # Le filtre de tendance officiel est désormais le seul à décider (voir
        # "HTF trend filter PER TRADING TIMEFRAME" plus bas) :
        #     M5  -> H1     M15 -> H4     M30 -> H4
        #
        # consensus reste CALCULÉ mais ne bloque plus rien : c'est une FEATURE
        # du modèle ML (predict_decision) et une colonne de trade_signals
        # (htf_consensus). Cesser de la calculer changerait le vecteur d'entrée
        # du modèle et rendrait ses prédictions incohérentes avec son
        # entraînement.
        #
        # Supprimé avec ce gate : l'arbitrage "1H master" (côté 5m 19.3% WR vs
        # côté 1H 36.1% WR sur les conflits). Choix utilisateur assumé
        # 2026-08-01 — à surveiller sur les prochains trades en conflit.
        htf_tf   = settings.get("htf_timeframe", ["1h"])
        htf_data = get_htf_trend(symbol, htf_tf)
        consensus = htf_data["consensus"]
        if consensus == "conflict":
            if HTF_CONFLICT_BLOCK:
                print(f"[HTF] {symbol} consensus conflict {htf_data['trends']} -> skip")
                _log_rejection(symbol, f"htf_conflict: {htf_data['trends']}")
                continue
            print(f"[HTF] {symbol} consensus conflict {htf_data['trends']} "
                  f"— observation seule, le filtre par timeframe décide")

        # 50/200 EMA confluence across 5m+30m+1h — bonus signal only.
        ema_confluence = get_ema_confluence(symbol)

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

        # Tendance RAPIDE (~4-5h) — voir _fast_trend_4h / _fast_trend_m15.
        # Calculée une fois par symbole, comme les EMA ci-dessus.
        _fast_4h  = _fast_trend_4h(symbol)
        _fast_m15 = _fast_trend_m15(symbol)

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

        # ── Helper commun aux stratégies de repli ──────────────────────
        # Défini au niveau SYMBOLE (et non plus dans la branche 'score
        # insuffisant') pour que la passe price-action qui suit la boucle
        # timeframe puisse l'utiliser. `tf` est devenu un paramètre : il
        # était capturé depuis la variable de boucle, ce qui aurait donné
        # la dernière valeur une fois appelé hors de la boucle.
        def _try_fallback(sig: dict, signal_type: str, tag: str, tf: str) -> bool:
            if not sig.get("detected"):
                return False
            fb_action = sig["action"]

            # ── Filtre de tendance par timeframe (même règle que LG) ──
            # Remplace l'ancien gate consensus 5m+1h (Gate A), retiré
            # le 2026-08-01. Les fallbacks utilisent désormais la même
            # table que la voie principale : M5->H1, M15/M30->H4.
            #
            # EXEMPTION price_action (2026-08-03) : la stratégie qualifie
            # DÉJÀ sa direction sur H4, via htf_trend() qui lit la STRUCTURE
            # (HH/HL vs LH/LL). Ce filtre-ci lit la pente de l'EMA20. Les
            # deux définitions divergeaient sur 4 symboles sur 6 en live :
            # PA validait un setup sur sa lecture H4, ce gate le tuait sur
            # l'autre. 41 setups perdus en 11h de cette seule contradiction.
            # Le backtest (+0.147R hors échantillon) mesurait PA avec son
            # propre filtre et AUCUN de ces deux gates — les ajouter fait
            # tourner autre chose que ce qui a été mesuré.
            if signal_type != "price_action":
                _fb_filter_tf    = "1h" if tf in ("5m", "1m", "3m") else "4h"
                _fb_filter_trend = _ema_1h if _fb_filter_tf == "1h" else _ema_4h
                _fb_need = "bullish" if fb_action == "buy" else "bearish"
                if _fb_filter_trend != _fb_need:
                    print(f"[{tag}-HTF] {symbol}: {fb_action} bloqué — "
                          f"{_fb_filter_tf} trend={_fb_filter_trend}, besoin {_fb_need}")
                    _log_rejection(symbol,
                                   f"{signal_type}_htf_filter_{tf}_vs_{_fb_filter_tf}={_fb_filter_trend}")
                    return False

            # ── OBV counter-trend block — RETIRÉ le 2026-08-03 ────────
            # Ce gate avait déjà été supprimé de la voie LG-primary le
            # 2026-07-14 : sur 24 épisodes bloqués il affichait 54.2% WR /
            # +0.90R, autrement dit il bloquait les trades LES PLUS
            # rentables de tous les gates testés. Il était pourtant resté
            # actif ici, où il a tué 27 setups price-action en 11h — une
            # incohérence, pas une décision. Même raisonnement que
            # ci-dessus : il ne faisait pas partie du backtest PA.
            # (obv_bias reste calculé et loggé au niveau symbole.)

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

            # (le contrôle "2e trade même sens / meilleur prix" a été
            # retiré ici : un symbole déjà en position est skippé
            # plus haut, donc ce cas ne peut plus se présenter)

            lot_sizes  = settings.get("lot_sizes", {})
            fixed_lot  = float(lot_sizes.get(symbol, 0))
            confidence = min(0.5 + abs(sig.get("momentum", 0.5)) / 10
                              + sig.get("pd_bonus", 0.0)
                              + sig.get("m5_bonus", 0.0), 0.9)

            print(f"[{tag}] {symbol} {fb_action} @ {sig['entry']} → standalone entry "
                  f"({sig['description']})")

            # ── Entrée optimale / plafond de risque (2026-08-12) ──────────
            # Cette voie appelait place_trade() directement, sans jamais
            # passer par compute_risk_based_entry() — réservé jusqu'ici à
            # LG-primary. C'est par là qu'est passé le XAUUSD price_action
            # du 08-12 : 0.01 lot, stop 16.12 points, -16.24$ soit 19.7%
            # du solde, alors que le réglage annonçait 5%.
            #
            # Même traitement que LG-primary :
            #   ACHAT  -> on cherche une entrée limite plus proche du SL qui
            #             ramène le risque au pourcentage visé
            #   VENTE  -> pas de limite (les ventes en attente sont 0 gain
            #             sur 9 en live) ; si le risque dépasse le plafond,
            #             place_trade() refusera de lui-même
            _fb_pending = False
            if fb_action == "buy" and fixed_lot > 0 and settings.get("pending_entry_enabled"):
                try:
                    _fb_atr = sig.get("atr") or 0
                    _fb_rbe = compute_risk_based_entry(
                        symbol=          SYMBOL_MAP.get(symbol.upper(), symbol),
                        action=          fb_action,
                        sl=              sig["sl"],
                        market_price=    sig["entry"],
                        fixed_lot=       fixed_lot,
                        target_risk_pct= _smc_target_risk(settings),
                        min_buffer_price=0.3 * _fb_atr,   # 1.0 -> 0.3 le 2026-08-26 :
                                                          # annulation, même raison qu'en
                                                          # LG-primary (les stops serrés
                                                          # sont les plus rentables)
                    )
                    print(f"[{tag}-RISK] {symbol}: {_fb_rbe['mode']} — {_fb_rbe['reason']}")
                    if _fb_rbe["mode"] == "reject":
                        _log_rejection(symbol, f"{signal_type}_risk_reject: {_fb_rbe['reason']}",
                                       timeframe=tf, signal_type=signal_type)
                        return False
                    if _fb_rbe["mode"] == "pending":
                        fb_trade = place_pending_trade(
                            symbol=         symbol,
                            action=         fb_action,
                            entry_price=    _fb_rbe["entry_price"],
                            sl=             sig["sl"],
                            tp1=            sig["tp1"],
                            confidence=     confidence,
                            fixed_lot=      fixed_lot,
                            expire_minutes= settings.get("pending_expire_minutes", 0),
                            risk_cap_pct=   _smc_risk_cap(settings),
                        )
                        _fb_pending = True
                except Exception as _rbe_e:
                    print(f"[{tag}-RISK] {symbol}: error — {_rbe_e}")

            # Symbole en limite uniquement (voir PENDING_ONLY_SYMBOLS) : aucune
            # limite n'a été posée, donc on n'entre PAS au marché à la place.
            if not _fb_pending and _blocks_market_entry(symbol, fb_action):
                print(f"[{tag}] {symbol} {fb_action}: limite uniquement pour ce "
                      f"symbole — entrée au marché refusée")
                _log_rejection(symbol, f"{signal_type}_pending_only_no_market: {fb_action}",
                               timeframe=tf, signal_type=signal_type)
                return False

            if not _fb_pending:
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
                    risk_cap_pct= _smc_risk_cap(settings),
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

        # ── Fallbacks S/R + M1 — voir SR_FALLBACK_ENABLED / M1_FALLBACK_ENABLED
        # Quand une stratégie est désactivée on ne journalise PLUS
        # sa non-détection : c'était 765 719 lignes sur 1 320 692
        # (58% de la base) dont le contenu se résumait à « S/R n'a
        # pas tiré parce que S/R est désactivé ». Zéro valeur pour
        # l'entraînement (la raison est une constante, pas un état
        # de marché) et ~40 000 écritures fsync par jour.

        for tf in settings["enabled_timeframes"]:
            try:
                df       = fetch_candles(symbol, tf, limit=100)
                analysis = run_scalping_analysis(df, symbol, macro_trend=_macro4h)

                # (blocs "HTF conflict hard block" et "1H master" retirés —
                #  Gate A ne bloque plus, cf. le commentaire au niveau symbole)

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
                    continue

                bias = analysis["bias"]

                # ── PER-ASSET-CLASS directional circuit breaker ──────────
                # After 2 consecutive SL losses in this direction WITHIN this
                # symbol's asset class (crypto/metals/forex), that side is
                # frozen 4h for that class only — stops buying a falling market
                # while the 4H trend lags, without freezing unrelated markets.
                # Deux verrous : celui de la classe d'actif ET le verrou GLOBAL
                # (voir update_direction_breaker). Le premier attrape un
                # mouvement corrélé dans un marché, le second un retournement
                # de régime qui traverse tous les marchés — c'est ce dernier
                # qui manquait le 08-03.
                _brk_hit = next(
                    (k for k in ((_asset_class(symbol), bias), ("global", bias))
                     if k in direction_breaker and now < direction_breaker[k]), None)
                if _brk_hit:
                    _rem = int((direction_breaker[_brk_hit] - now).total_seconds() // 60)
                    print(f"[DIR-BREAKER] {symbol} {bias} blocked {_rem}min "
                          f"({_brk_hit[0]} {bias} reversal protection)")
                    _log_rejection(symbol, f"dir_breaker_{_brk_hit[0]}_{bias}_{_rem}min",
                                   score=ta_score, bias=bias)
                    continue

                # ── Record BTC lead signal for ETH/SOL lead-lag ─────────
                if symbol == "BTC":
                    btc_lead_signal["bias"] = bias
                    btc_lead_signal["ts"]   = now
                    print(f"[LEAD] BTC fired {bias} score={ta_score} → ETH/SOL prioritized for 45min")

                # (rejets "htf_contra" retirés — c'était Gate A appliquant un
                #  consensus 5m+1h calculé au niveau symbole à TOUS les
                #  timeframes. Le filtre par timeframe ci-dessous le remplace.)

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

                # Exemption ventes par symbole — voir HTF_SELL_EXEMPT_SYMBOLS.
                # Ne touche QUE les ventes : un achat reste filtré normalement.
                _sell_exempt_symbol = (bias == "sell"
                                       and symbol.upper() in HTF_SELL_EXEMPT_SYMBOLS)
                _htf_exempt = _sell_exempt_symbol and _filter_trend != _need

                if _htf_exempt:
                    # Ligne informative, PAS un rejet : le signal continue.
                    # Même usage que meta_shadow — on journalise pour pouvoir
                    # mesurer le test forward symbole par symbole.
                    print(f"[HTF-EXEMPT] {symbol} {tf} sell autorisee malgre "
                          f"{_filter_tf} trend={_filter_trend} - test forward")
                    _log_rejection(symbol,
                                   f"htf_sell_exempt_{tf}_vs_{_filter_tf}={_filter_trend}",
                                   score=ta_score, bias=bias, timeframe=tf,
                                   signal_type="htf_sell_exempt")
                elif _filter_trend != _need:
                    print(f"[HTF-FILTER] {symbol} {tf} {bias} rejected — "
                          f"{_filter_tf} trend={_filter_trend}, need {_need}")
                    _log_rejection(symbol,
                                   f"htf_filter_{tf}_vs_{_filter_tf}={_filter_trend}",
                                   score=ta_score, bias=bias)
                    continue

                # ── Macro-4H alignment gate — RÉTABLI le 2026-08-01 ───────────
                # Il avait été supprimé le 2026-07-14 sur un essai de 35 épisodes
                # (42.9% WR / +0.50R). Ré-évalué sur 36 643 signaux du dataset
                # offline : les trades PRIS À CONTRE-SENS de la tendance 4H font
                # -0.267R de moyenne et représentent ~24% de la population brute.
                # 35 épisodes ne pouvaient pas trancher ça — l'échantillon était
                # trop petit d'un facteur 1000.
                #
                # Mesuré EN PLUS du filtre par timeframe ci-dessus (donc le gain
                # réel, pas le gain brut) : +0.0770R -> +0.1087R par trade, en ne
                # retirant que 8.2% des signaux, et le R total MONTE
                # (+1821 -> +2360). C'est le seul filtre testé qui améliore à la
                # fois la qualité par trade ET le total.
                #
                # IMPORTANT : on utilise _ema_4h (pente EMA20 sur 4H), PAS
                # _macro4h (qui est un consensus de STRUCTURE via get_htf_trend).
                # C'est la définition EMA qui a été mesurée ; les deux ne sont pas
                # interchangeables. Pour un trade 15m/30m ce gate fait doublon
                # avec le filtre par timeframe (déjà 4H) — sans effet, inoffensif.
                # Le vrai apport est sur le 5m, filtré par H1 uniquement.
                # _sell_exempt_symbol : ce gate doit sauter lui aussi, sinon il
                # re-bloque la vente que le filtre par TF vient d'autoriser
                # (macro4h_contra était 122 des 428 blocages de ventes BTC).
                if settings.get("htf_4h_filter_enabled", True) and not _sell_exempt_symbol:
                    if _ema_4h != "neutral" and _ema_4h != _need:
                        print(f"[MACRO-4H] {symbol} {tf} {bias} rejected — "
                              f"4H EMA trend={_ema_4h}, need {_need}")
                        _log_rejection(symbol, f"macro4h_contra_{bias}_vs_{_ema_4h}",
                                       score=ta_score, bias=bias, timeframe=tf)
                        continue
                elif _sell_exempt_symbol and _ema_4h != "neutral" and _ema_4h != _need:
                    print(f"[HTF-EXEMPT] {symbol} {tf} sell passe aussi le macro-4H "
                          f"(4H={_ema_4h}) - test forward")

                # ── Gate CONSENSUS 5m/1h — 2026-09-06 ────────────────────────
                # Le consensus 5m+1h était calculé mais IGNORÉ depuis le retrait
                # de Gate A (2026-08-01). Le commentaire de l'époque demandait
                # explicitement de le réexaminer sur les trades en conflit. Voilà.
                #
                # Mesure sur 261 trades réels (classification d'un champ déjà
                # enregistré à l'entrée — pas de simulation, pas de contrefactuel,
                # donc pas le biais de comptage qui a invalidé l'étude BTC) :
                #
                #   bullish   n=101   -5.48$   WR 44.6%   PF 0.98   avgR +0.12
                #   conflict  n= 71 -114.44$   WR 29.6%   PF 0.52   avgR -0.35
                #   bearish   n= 34 -132.36$   WR 23.5%   PF 0.26   avgR -0.50
                #
                # Répliqué sur des trades majoritairement NEUFS la semaine du
                # 08-31 : conflict n=17 -87.56$ PF 0.15, bullish n=11 +27.96$
                # PF 3.21. C'est la seule cohorte au-dessus de PF 1.0 du projet.
                #
                # POURQUOI seulement les ACHATS en conflit, et pas les ventes :
                #   conflit + achat   n=39  -89.78$   <- le vrai dégât
                #   conflit + vente   n=32  -24.66$   WR 37.5%  <- la MEILLEURE
                #                                       cohorte vendeuse
                # Bloquer tous les conflits couperait 87% des ventes et rendrait
                # le bot quasi mono-directionnel — inacceptable, le marché monte
                # ET descend (objection utilisateur, vérifiée sur données).
                # Cette version garde 51 ventes sur 80 (64%).
                #
                # Simulation des 261 trades : -306.21$ -> -24.60$, PF 0.66 -> 0.95,
                # 68% du volume conservé, MEILLEUR sur les 6 semaines sans exception.
                #
                # HONNÊTETÉ : ça ne rend pas le bot rentable, ça le ramène près de
                # zéro. Gains ~1R et pertes ~1R à 40% de réussite restent négatifs.
                # REVERT = passer CONSENSUS_GATE_ENABLED à False.
                if CONSENSUS_GATE_ENABLED and consensus in ("conflict", "bearish"):
                    _blocked = (consensus == "bearish") or (bias == "buy")
                    if _blocked:
                        print(f"[CONSENSUS] {symbol} {tf} {bias} rejected — "
                              f"consensus={consensus} {htf_data.get('trends')}")
                        _log_rejection(symbol,
                                       f"consensus_gate_{consensus}_{bias}",
                                       score=ta_score, bias=bias, timeframe=tf)
                        continue

                # ── Gate tendance RAPIDE (~4-5h) — voir _fast_trend_4h ────────
                # Bloque seulement quand les DEUX mesures rapides s'opposent au
                # trade : cohorte n=66, avgR -0.47, WR 28.8%, PF 0.44. Les
                # garder coûtait -131.38$ sur la période.
                # Une seule des deux en désaccord ne suffit PAS à bloquer —
                # exiger les deux réduit le volume coupé de 89 à 66 tout en
                # visant les pires (c'est la version "COMBINED" de l'étude).
                if symbol.upper() not in FAST_TREND_GATE_EXEMPT:
                    if (_fast_4h != _need and _fast_m15 != _need
                            and _fast_m15 != "neutral"):
                        print(f"[FAST-TREND] {symbol} {tf} {bias} rejected - "
                              f"4h={_fast_4h} m15={_fast_m15}, need {_need}")
                        _log_rejection(
                            symbol,
                            f"fast_trend_contra_{bias}_4h={_fast_4h}_m15={_fast_m15}",
                            score=ta_score, bias=bias, timeframe=tf)
                        continue

                # ── Zone d'équilibre P/D — rejet optionnel ────────────────────
                # Mesuré : les entrées entre 35% et 65% du range (ni discount ni
                # premium franc) font +0.0998R contre +0.1172R hors zone. Couper
                # cette zone monte la qualité par trade mais coupe ~49% du volume
                # (R total +2360 -> +1420). Activé par défaut tant que le compte
                # est petit : la survie dépend de la qualité, pas du volume.
                # À désactiver après recapitalisation pour récupérer le volume.
                if settings.get("skip_equilibrium_pd", True):
                    _pdp = (analysis.get("premium_discount") or {}).get("position_pct")
                    if _pdp is not None and 35 <= _pdp <= 65:
                        print(f"[PD-EQUIL] {symbol} {tf} {bias} rejected — "
                              f"P/D {_pdp:.0f}% is equilibrium (need <35% or >65%)")
                        _log_rejection(symbol, f"pd_equilibrium_{_pdp:.0f}pct",
                                       score=ta_score, bias=bias, timeframe=tf)
                        continue

                # ── Plancher ADX ──────────────────────────────────────────────
                # L'engine ne bloque qu'en dessous de 10 ("marché mort"). Mesuré :
                # ADX < 20 = -0.0202 à -0.0442R, ADX >= 20 = positif, ADX >= 40 =
                # +0.0869R. Ce plancher généralise la règle qui n'existait que
                # pour XAUUSD. Coûteux en volume (-40%) — comme ci-dessus, à
                # relâcher une fois le compte reconstitué.
                _min_adx = settings.get("min_adx", 20.0)
                _adx_val = analysis.get("adx") or 0
                if _min_adx > 0 and _adx_val < _min_adx:
                    print(f"[ADX-FLOOR] {symbol} {tf} {bias} rejected — "
                          f"ADX {_adx_val:.1f} < {_min_adx}")
                    _log_rejection(symbol, f"adx_below_floor_{_adx_val:.0f}",
                                   score=ta_score, bias=bias, timeframe=tf)
                    continue

                # OBV gate SUPPRIMÉ (gate trial 2026-07-14, 24 épisodes bloqués :
                # 54.2% WR / +0.90R — les trades qu'il bloquait étaient les plus
                # rentables de tous les gates). L'OBV reste calculé/loggé plus haut.

                # Direction lock SUPPRIMÉ (2026-07-14, demande utilisateur) —
                # punir une direction après 2 pertes = réagir au bruit.

                entry_data = analysis.get("scalping_entry")
                if not entry_data:
                    _log_rejection(symbol, "no_entry_data", score=ta_score)
                    continue

                # (bloc "2nd trade: same bias + strictly better price" retiré —
                #  code mort, voir le skip max-1-position plus haut)

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

                # ── 50/200 EMA confluence bonus: DISABLED as of 2026-07-30 ──
                # Originally: +15pts when 5m+30m+1h EMA50/200 all agree with
                # bias, framed as a rare high-conviction confirmation.
                # Real data (466 closed trades, scalp_log mining): this
                # condition fired in 23.5% of LOSSES vs only 11.9% of WINS —
                # roughly double the rate in losers. Likely explanation: full
                # 3-timeframe EMA agreement tends to mean the move is already
                # mature/extended across every timeframe, which correlates
                # with late entries rather than early ones (consistent with
                # "deep premium" entries also underperforming in the same
                # analysis). Zeroing the bonus rather than flipping it to a
                # penalty — data supports "this doesn't help", not yet strong
                # enough to say "this should actively subtract points".
                ema_boost   = 0.0
                conf_regime = ema_confluence.get("regime", "mixed")
                # if (bias == "buy"  and conf_regime == "bullish") or \
                #    (bias == "sell" and conf_regime == "bearish"):
                #     ema_boost = 15.0
                #     print(f"[EMA-CROSS] {symbol}: 5m+30m+1h EMA confluence="
                #           f"{conf_regime} confirms {bias} +{ema_boost}pts")
                # else:
                #     print(f"[EMA-CROSS] {symbol}: regimes={ema_confluence['tfs']} "
                #           f"(no confluence with {bias}, no score impact)")

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
                    print(f"[SL-WIDE] {symbol} {tf}: SL width {_sl_width:.5g} > "
                          f"2.5xATR({_atr_for_gate:.5g}={2.5*_atr_for_gate:.5g}) - skipped")
                    # %.5g, pas round(x,3) : sur EURUSD un ATR de 0.00044
                    # s'affichait "ATR(0.0)", ce qui donnait l'impression d'un
                    # ATR nul et d'un gate cassé. Le gate est correct (il exige
                    # _atr_for_gate > 0 juste au-dessus) — c'était l'arrondi à
                    # 3 décimales qui écrasait un instrument coté à 5.
                    _log_rejection(symbol,
                        f"sl_too_wide: {_sl_width:.5g} > 2.5xATR({_atr_for_gate:.5g})",
                        score=ta_score, bias=bias)
                    continue

                # ── Meta-model gate — DERNIER filtre, en OBSERVATION ──────────
                # Ne prédit pas le marché (build_lstm.py a déjà échoué sur cette
                # question : AUC 0.494-0.534, un tirage à pile ou face). Il note
                # un signal que le moteur a DÉJÀ décidé de prendre.
                #
                # Validation en walk-forward (4 plis glissants, avril->juillet) :
                #   prendre tout      : +0.178 à +0.265 R/trade selon le pli
                #   top 10% du modèle : +0.190 à +0.484 R/trade
                #   gain moyen +0.1247R, positif sur 4 plis / 4
                # (holdout 25% à l'entraînement : +0.2484 -> +0.4071, +0.1587R)
                #
                # Pourquoi ce n'est pas le piège des filtres précédents (P/D, ADX,
                # qui remontaient la moyenne en jetant du profit) : le bot ignore
                # DÉJÀ ~94% des signaux. Le dataset en produit ~108/jour, le bot
                # en prend ~6 — le premier qui trouve un slot libre. Passer au
                # top 10% ne retire pas des trades qu'on aurait pris, ça remplace
                # une sélection ARBITRAIRE par une sélection classée, à volume égal.
                #
                # meta_gate_enabled=False : on note et on journalise, on ne bloque
                # RIEN. À laisser en observation le temps de vérifier en live que
                # le score se comporte comme à l'entraînement.
                _meta_r = None
                try:
                    from features.trading import meta_gate
                    _meta_r = meta_gate.predict_r(analysis, symbol, bias, _ema_4h)
                except Exception as _mge:
                    print(f"[META] {symbol}: {_mge}")
                if _meta_r is not None:
                    _meta_thr = settings.get("meta_gate_threshold")
                    if _meta_thr is None:
                        _meta_thr = meta_gate.get_threshold()
                    _pass = _meta_thr is None or _meta_r >= _meta_thr
                    _mode = "GATE" if settings.get("meta_gate_enabled") else "shadow"
                    print(f"[META/{_mode}] {symbol} {tf} {bias}: predicted "
                          f"{_meta_r:+.3f}R vs threshold {_meta_thr:.3f} → "
                          f"{'pass' if _pass else 'below'}")
                    analysis["meta_r"] = _meta_r
                    # Persist EVERY score, traded or not. In shadow mode the
                    # whole point is to collect data, and a print() to stdout
                    # is not data — the first shadow run logged 13 trades and
                    # zero scores because nothing wrote them down.
                    # This row is informational, not a rejection: the signal
                    # continues unless the gate is armed AND the score is low.
                    _log_rejection(symbol,
                                   f"meta_shadow_{_meta_r:+.4f}_thr{_meta_thr:.4f}_"
                                   f"{'pass' if _pass else 'below'}",
                                   score=ta_score, bias=bias, timeframe=tf,
                                   signal_type="meta_shadow",
                                   detail={"meta_r": _meta_r, "threshold": _meta_thr,
                                           "pass": _pass, "blended": blended_score})
                    if settings.get("meta_gate_enabled") and not _pass:
                        _log_rejection(symbol, f"meta_gate_low_{_meta_r:+.3f}",
                                       score=ta_score, bias=bias, timeframe=tf)
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

                # ── Entrée limite : ACHATS UNIQUEMENT (2026-08-08) ────────────
                # Mesuré sur 21 703 signaux (entrée marché vs limite à 1xATR,
                # politique de sortie live) :
                #
                #            avg R/trade            R TOTAL
                #            marché  limite      marché  limite
                #   ACHAT    +0.189  +0.342      +2495   +3275   -> limite gagne
                #   VENTE    +0.289  +0.335      +2455   +1989   -> MARCHÉ gagne
                #
                # L'ordre limite obtient un meilleur prix dans les DEUX sens,
                # mais il ne se remplit qu'à ~71%. Sur les ventes, les setups
                # manqués coûtent plus que le meilleur prix ne rapporte : -466R
                # au total. Sur les achats il rapporte +780R.
                #
                # Le live dit la même chose en plus net (semaine du 08-03) :
                # achats en attente +70.62$, ventes en attente -45.73$ avec
                # 0 gain sur 9 — aucune vente en attente n'a JAMAIS gagné.
                #
                # Explication mécanique : un buy-limit est sous le marché, il
                # se remplit sur un repli puis le mouvement reprend. Un
                # sell-limit est au-dessus, il se remplit sur un rebond — et
                # dans un marché qui monte le rebond continue simplement.
                #
                # RÉSERVE : la semaine testée était haussière sur tous les
                # marchés (XAG +9.6%, XAU +7.0%). Le backtest janvier-juillet
                # confirme le sens, mais l'ampleur est flattée par le régime.
                # À réexaminer sur une vraie phase baissière.
                _use_pending = (settings.get("pending_entry_enabled")
                                and fixed_lot > 0
                                and bias == "buy")
                if _use_pending:
                    # Plancher de respiration : 0.3xATR.
                    #
                    # Passé à 1.0xATR le 2026-08-20 sur n=3 (!), ANNULÉ le
                    # 2026-08-26 après mesure sur les 203 trades de la période.
                    # Le classement par largeur de stop est MONOTONE et va dans
                    # l'autre sens : plus le stop est serré, mieux ça marche.
                    #
                    #     <0.6xATR    n=  5  +14.89$  PF 1.88  avgR +0.46
                    #     0.6-1.0xATR n= 10   +6.84$  PF 1.21  avgR +0.07
                    #     1.0-1.5xATR n= 25   -7.11$  PF 0.92
                    #     1.5-2.5xATR n= 53  -60.03$  PF 0.72
                    #     >2.5xATR    n= 85 -133.22$  PF 0.53  avgR -0.23
                    #
                    # Le plancher à 1.0 bloquait donc EXACTEMENT la seule
                    # tranche rentable (n=15, +21.73$, PF 1.44) et gardait le
                    # reste (n=163, -200.36$, PF 0.66).
                    #
                    # Effet de bord constaté en live : l'or a quasiment cessé
                    # de trader (10 trades/semaine -> 1). ATR or ~10.67 alors
                    # que le budget 7% n'autorise que ~6.11 d'écart entrée/SL
                    # -> risk_entry_reject à chaque signal (102 rejets le
                    # 08-24, 41 le 08-25). Or l'or sous 1xATR est rentable
                    # (n=8, +6.88$, PF 1.26) et le meilleur trade or de tout
                    # l'historique (+24.98$, +4.38R le 08-07) avait un stop de
                    # 0.99xATR — juste sous le plancher.
                    #
                    # NE PAS remonter ce chiffre sans une mesure sur >=30
                    # trades PAR tranche. La version 1.0 reposait sur 3.
                    _atr_buf = 0.3 * analysis.get("atr", 0)
                    _rbe = compute_risk_based_entry(
                        symbol=          SYMBOL_MAP.get(symbol.upper(), symbol),
                        action=          bias,
                        sl=              entry_data["sl"],
                        market_price=    entry_data["entry"],
                        fixed_lot=       fixed_lot,
                        target_risk_pct= _smc_target_risk(settings),
                        min_buffer_price=_atr_buf,
                    )
                    print(f"[RISK-ENTRY] {symbol} {tf}: {_rbe['mode']} — {_rbe['reason']}")

                    if _rbe["mode"] == "reject":
                        _log_rejection(symbol, f"risk_entry_reject: {_rbe['reason']}",
                                        score=ta_score, bias=bias)
                        continue

                    if _rbe["mode"] == "pending":
                        trade = place_pending_trade(
                            symbol=         symbol,
                            action=         bias,
                            entry_price=    _rbe["entry_price"],
                            sl=             entry_data["sl"],
                            tp1=            entry_data["tp1"],
                            confidence=     analysis["scalping_score"] / 100,
                            fixed_lot=      fixed_lot,
                            expire_minutes= settings.get("pending_expire_minutes", 0),
                            risk_cap_pct=   _smc_risk_cap(settings),
                        )
                        if not trade.get("success"):
                            print(f"[LG-PRIMARY] {symbol}: "
                                  f"place_pending_trade failed — {trade.get('reason')}")
                            _log_rejection(symbol, f"lg_primary_pending_failed: {trade.get('reason')}",
                                            score=ta_score, bias=bias)
                            continue

                        # ── Log the price the ORDER was actually placed at ────
                        # The pending entry sits closer to SL than the market
                        # price entry_data["entry"] was computed from. Logging
                        # the market price meant trade_signals recorded an
                        # entry the trade never had — and _track_excursion
                        # reads exactly that row to derive risk = |entry - sl|,
                        # so every MFE/MAE for a pending trade was scaled by
                        # the wrong R. TP is unchanged by the pending move, so
                        # the true RR is higher than the pre-move rr_ratio;
                        # recompute it here rather than logging the stale one.
                        # sl/tp come back from the executor already clamped to
                        # the broker's trade_stops_level — log what's on the
                        # order, not what we asked for.
                        _pe    = trade.get("price", _rbe["entry_price"])
                        _psl   = trade.get("sl", entry_data["sl"])
                        _ptp   = trade.get("tp", entry_data["tp1"])
                        _prisk = abs(_pe - _psl)
                        entry_data = {
                            **entry_data,
                            "entry":    _pe,
                            "sl":       _psl,
                            "tp1":      _ptp,
                            "rr_ratio": round(abs(_ptp - _pe) / _prisk, 2)
                                        if _prisk > 0 else entry_data.get("rr_ratio"),
                        }
                        print(f"[RISK-ENTRY] {symbol} {tf}: pending logged at "
                              f"entry={_pe} sl={_psl} tp={_ptp} "
                              f"RR={entry_data['rr_ratio']} (was market {_rbe.get('entry_price')})")
                        # Skip the normal market place_trade call below —
                        # already placed as a pending order.
                        _pending_already_placed = True
                    else:
                        _pending_already_placed = False
                else:
                    _pending_already_placed = False

                # Symbole en limite uniquement (voir PENDING_ONLY_SYMBOLS) : si
                # aucune limite n'a été posée — vente, ou achat dont le risque
                # tient déjà au prix du marché — on n'entre pas au marché.
                if not _pending_already_placed and _blocks_market_entry(symbol, bias):
                    print(f"[LG-PRIMARY] {symbol} {tf} {bias}: limite uniquement "
                          f"pour ce symbole — entrée au marché refusée")
                    _log_rejection(symbol, f"lg_primary_pending_only_no_market: {bias}",
                                   score=ta_score, bias=bias)
                    continue

                if not _pending_already_placed:
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
                        risk_cap_pct= _smc_risk_cap(settings),
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
                        "meta_r":        analysis.get("meta_r"),   # shadow score, for
                                                                   # predicted-vs-realised
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
                    # marque le symbole comme tradé pour que la passe
                    # price-action après la boucle le saute (place_trade la
                    # refuserait de toute façon : 1 position par symbole)
                    _fallback_traded = True
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

        # ── PASSE PRICE-ACTION — indépendante de LG (2026-08-01) ──────────
        # Elle tournait auparavant DANS la branche "score LG insuffisant",
        # donc uniquement quand LG échouait son contrôle de score. Or le
        # chemin LG contient 13 autres `continue` (filtre 4H, zone, ML,
        # largeur de SL, blended...) : un signal LG bien noté mais rejeté
        # plus loin faisait passer le symbole sans que price-action ait
        # jamais son mot à dire.
        #
        # Ici la passe s'exécute APRÈS la boucle timeframe, quelle que soit
        # la raison pour laquelle LG n'a pas tradé — c'est ce qui la rend
        # réellement additive et augmente le nombre de trades.
        #
        # Une seule position par symbole reste la règle : place_trade()
        # refuse toute 2e position (décision post-mortem sur 4 paires
        # d'averaging perdantes). PA ne double donc jamais un trade LG,
        # il occupe les symboles que LG a laissés passer.
        if PA_FALLBACK_ENABLED and not _fallback_traded:
            _pa_tf = "15m" if "15m" in settings["enabled_timeframes"] \
                     else settings["enabled_timeframes"][0]
            try:
                from features.price_action.strategy import get_price_action_signal
                pa_sig = get_price_action_signal(symbol, tf=_pa_tf, htf="4h")
            except Exception as _pae:
                pa_sig = {"detected": False, "reason": f"error: {_pae}"}
                print(f"[PA-SIGNAL] {symbol}: error — {_pae}")

            if pa_sig.get("detected"):
                print(f"[PA-SIGNAL] {symbol} {_pa_tf}: {pa_sig['description']}")
            if _try_fallback(pa_sig, "price_action", "PA-SIGNAL", _pa_tf):
                _fallback_traded = True
            else:
                _log_rejection(symbol, f"pa_no_signal: {pa_sig.get('reason', '?')}",
                               timeframe=_pa_tf, signal_type="price_action",
                               detail={k: v for k, v in pa_sig.items() if k != "detected"})

    # Le bot Volume Profile ne passe PAS par ici : c'est un second bot, avec
    # son propre minuteur (job 'vp_scan', main.py). Voir features/volume_profile.

    scalp_state["last_scan"] = now.isoformat()


@router.post("/scalping/start")
def start_scalping(settings: ScalpingSettings, source: str = "app"):
    # Use what the app sends directly — the app already loaded the saved settings
    # via /status on startup, so by the time /start is called the app state
    # already reflects the user's tuned values plus any new changes they made.
    #
    # source="watchdog" (?source=watchdog) : reprise après crash — l'état de
    # risque persistant est conservé tel quel. Tout autre appel est traité
    # comme un démarrage volontaire depuis l'app (voir risk_state plus haut).
    d = settings.dict()

    if source != "watchdog":
        risk_state["day_anchor"] = datetime.now().isoformat()
        try:
            _a = mt5.account_info()
        except Exception:
            _a = None
        if _a is not None and risk_state.get("login") == _a.login:
            _apply_cash_flows(_a)         # un retrait pendant l'arrêt n'est pas une perte
        _pk = scalp_state.get("peak_equity")
        if _a is not None and _pk and _a.equity <= _pk * (1 - SURVIVAL_HALT_PCT / 100):
            print(f"[RISK-STATE] relance depuis l'app au-delà du seuil "
                  f"(-{SURVIVAL_HALT_PCT:.0f}%) : nouvelle base ${_a.equity:.2f} "
                  f"(ancien pic ${_pk:.2f})")
            scalp_state["peak_equity"] = _a.equity
            risk_state["peak_equity"] = _a.equity
            risk_state["login"] = _a.login
            _reset_flow_marker()
        _save_risk_state(risk_state)

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
    symbol_streak_anchor.clear()
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

@router.get("/scalping/positions")
def scalping_positions():
    """Positions ouvertes et ordres limite du bot SMC (magic 234000), pour
    l'onglet Scalping de l'app — l'équivalent de "Open VP positions".
    at_sl = résultat en $ si le stop ACTUEL est touché : négatif = risque
    restant, positif = gain déjà verrouillé par le trail / breakeven."""
    _mt5_init()
    from features.trading.risk_manager import BOT_MAGIC

    def _iso(ts):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()   # heure serveur Exness = UTC

    positions = []
    for p in mt5.positions_get() or []:
        if p.magic != BOT_MAGIC:
            continue
        buy = p.type == mt5.POSITION_TYPE_BUY
        at_sl = None
        if p.sl:
            v = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                                      p.symbol, p.volume, p.price_open, p.sl)
            at_sl = round(v, 2) if v is not None else None
        positions.append({
            "ticket": p.ticket, "symbol": p.symbol, "side": "buy" if buy else "sell",
            "volume": p.volume, "entry": p.price_open, "sl": p.sl, "tp": p.tp,
            "price": p.price_current, "profit": round(p.profit + p.swap, 2),
            "at_sl": at_sl, "comment": p.comment, "opened": _iso(p.time),
        })
    pending = []
    for o in mt5.orders_get() or []:
        if o.magic != BOT_MAGIC:
            continue
        buy = o.type in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP)
        pending.append({
            "ticket": o.ticket, "symbol": o.symbol, "side": "buy" if buy else "sell",
            "volume": o.volume_current, "price": o.price_open, "sl": o.sl, "tp": o.tp,
            "placed": _iso(o.time_setup),
        })
    return {"positions": positions, "pending": pending}


@router.get("/scalping/trades")
def scalping_trades(days: int = 30, limit: int = 50):
    """Trades CLÔTURÉS du bot SMC lus dans l'historique des deals MT5 — la
    vérité du broker (prix, commissions, swaps), pas le journal des signaux.
    exit : tp | sl | trail (stop touché en gain : trail ou breakeven) | manual."""
    _mt5_init()
    from features.trading.risk_manager import BOT_MAGIC
    days = max(1, min(int(days), 365))
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=days), now + timedelta(hours=3)) or []
    by_pos: dict = {}
    for d in deals:
        if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
            by_pos.setdefault(d.position_id, []).append(d)

    trades = []
    for pid, ds in by_pos.items():
        ins = [d for d in ds if d.entry == mt5.DEAL_ENTRY_IN]
        outs = [d for d in ds if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not ins or not outs or ins[0].magic != BOT_MAGIC:
            continue
        if sum(d.volume for d in outs) + 1e-9 < sum(d.volume for d in ins):
            continue                                    # partiellement clôturé : encore ouvert
        i, last = ins[0], max(outs, key=lambda d: d.time)
        profit = round(sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
                           for d in ds), 2)
        c = (last.comment or "").lower()
        kind = ("tp" if "[tp" in c else
                ("trail" if profit > 0 else "sl") if "[sl" in c else "manual")
        if len(outs) > 1 and any("half" in (d.comment or "").lower() for d in outs):
            kind = "half+" + kind                       # moitié à +1R puis le reste

        trades.append({
            "ticket": pid, "symbol": i.symbol,
            "direction": "buy" if i.type == mt5.DEAL_TYPE_BUY else "sell",
            "volume": i.volume, "entry": i.price, "exit_price": last.price,
            "opened": datetime.fromtimestamp(i.time, timezone.utc).isoformat(),
            "closed": datetime.fromtimestamp(last.time, timezone.utc).isoformat(),
            "minutes": round((last.time - i.time) / 60),
            "profit": profit, "exit": kind,
            "state": "win" if profit > 0 else "loss",
            "pending_entry": (i.comment or "").startswith("CryptoOracle-P"),
        })
    trades.sort(key=lambda t: t["closed"], reverse=True)

    def summary(rows):
        wins = [r for r in rows if r["profit"] > 0]
        gp = sum(r["profit"] for r in wins)
        gl = -sum(r["profit"] for r in rows if r["profit"] <= 0)
        return {"trades": len(rows), "wins": len(wins),
                "win_rate": round(100 * len(wins) / len(rows), 1) if rows else None,
                "net_profit": round(sum(r["profit"] for r in rows), 2),
                "profit_factor": round(gp / gl, 2) if gl else None}

    by_symbol = {s: summary([t for t in trades if t["symbol"] == s])
                 for s in sorted({t["symbol"] for t in trades})}
    return {"bot": "smc", "magic": BOT_MAGIC, "days": days,
            "all": summary(trades), "by_symbol": by_symbol,
            "trades": trades[:max(1, int(limit))]}


@router.patch("/scalping/settings")
def patch_scalping_settings(patch: dict):
    """Hot-update individual settings — works whether bot is running or not."""
    current = scalp_state["settings"]
    current.update(patch)
    scalp_state["settings"] = current
    _persist_settings(current)   # survive server restart + Flutter reset
    return {"success": True, "updated": patch, "settings": current}

# ── Bot Volume Profile — indépendant du bot SMC (2026-09-24) ─────────────────
# Le Start/Stop de l'app ne pilote QUE le bot SMC (/scalping/*). Le bot Volume
# Profile a son propre marche/arrêt, persisté (survit aux redémarrages).
@router.get("/vp/status")
def get_vp_status():
    from features.volume_profile.runner import vp_status
    return vp_status()


@router.post("/vp/start")
def start_vp():
    from features.volume_profile.runner import set_enabled
    return set_enabled(True)


@router.post("/vp/stop")
def stop_vp():
    """Arrête le bot VP (plus de nouveaux trades). Les positions VP déjà
    ouvertes restent protégées par leur SL / TP côté broker."""
    from features.volume_profile.runner import set_enabled
    return set_enabled(False)


@router.get("/vp/stats")
def get_vp_stats(days: int = 30):
    """Résultats du bot Volume Profile SEUL (vp_log, magic 234100), par setup."""
    from features.volume_profile.runner import vp_stats
    return vp_stats(days)


@router.get("/vp/history")
def get_vp_history(limit: int = 50):
    """Derniers trades du bot Volume Profile (onglet dédié de l'app)."""
    from features.volume_profile.runner import vp_history
    return vp_history(limit)


@router.post("/vp/settings")
def post_vp_settings(patch: dict):
    """Réglages du bot Volume Profile depuis l'app : symbols, lot_sizes,
    risk_cap_pct, daily_loss_pct, setups. Validés côté serveur (lot minimum et
    pas du broker, bornes), tout ou rien, appliqués au scan suivant sans
    redémarrage. N'affecte en rien le bot Scalping."""
    from features.volume_profile.runner import SettingsError, update_settings
    try:
        return update_settings(patch)
    except SettingsError as e:
        raise HTTPException(status_code=400, detail=str(e))


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

                # Vérification filtres sans placer de trade — doit refléter le
                # filtre RÉEL du scan (par timeframe), pas l'ancien consensus.
                _prev_filter_tf = "1h" if tf in ("5m", "1m", "3m") else "4h"
                _prev_trend     = _ema_macro_trend(symbol, _prev_filter_tf)
                htf_ok = _prev_trend == ("bullish" if bias == "buy" else "bearish")

                results.append({
                    "symbol":       symbol,
                    "timeframe":    tf,
                    "should_scalp": analysis.get("should_scalp") and htf_ok and not cooldown,
                    "score":        analysis.get("scalping_score"),
                    "bias":         bias,
                    "htf_trends":   htf_data["trends"],
                    "htf_consensus": consensus,
                    "htf_filter_tf":    _prev_filter_tf,
                    "htf_filter_trend": _prev_trend,
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