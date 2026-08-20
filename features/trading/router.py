from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import Union, List
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

    # Expiry des ordres en attente — 120 -> 480 min (2026-08-08).
    # Rejeu des 37 ordres annulés de la semaine 08-03, avec la politique de
    # sortie live :
    #    120 min (avant) :  5 remplis, +5.4R
    #    480 min         : 18 remplis, +6.4R   <- retenu
    #   1440 min         : 23 remplis, +12.6R
    # Le délai de remplissage réel est court (75% en 30 min, 95% en 120 min) :
    # allonger ne sert qu'aux replis lents. 1440 mesure mieux mais immobilise
    # un symbole 24h sur un signal devenu obsolète, et ces 37 ordres viennent
    # tous d'une semaine haussière. 480 prend l'essentiel du gain sans parier
    # sur la persistance du régime. Monter à 1440 si le résultat se confirme.
    pending_max_age_minutes: int   = 480     # 2026-08-01: filet de sécurité côté bot pour
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
# XAGUSD ajouté le 2026-08-20. Le dossier le plus net du bot :
#   tout l'historique : 24 trades, 20.8% WR, -63.35$
#   semaine 08-17→20  : 0 sur 3, -11.70$, MFE moyen +0.00R
# "MFE +0.00R" = les trois trades n'ont JAMAIS coté un seul tick en vert.
# Le mécanisme est compris, pas seulement constaté : le lot minimum vaut
# 50 onces, donc 1$ de mouvement = 50$ de P&L. Le budget de 7% du solde se
# traduit par une distance entrée→SL de ~0.085, soit 0.54xATR — la largeur
# du bruit. compute_risk_based_entry ne peut pas faire mieux : le SL est
# structurel, le lot est déjà au minimum broker. L'argent se perd à
# l'entrée, aucun réglage de sortie ne rattrape ça.
# Silver déclenche aussi le plafond de risque en permanence (46% du solde
# au lot minimum) — 18 rejets executor sur la seule journée du 08-15.
BLOCKED_SYMBOLS = {"GBPJPY", "USDJPY", "XAGUSD"}

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

        # Le risque ORIGINAL, pas (entry - sl) courant : dès que le trail ou
        # le BE a déplacé le stop, cette différence devient nulle ou négative
        # et la règle en R se désarmerait toute seule au tick suivant.
        # _excursion_cache conserve le risque d'origine (renseigné juste
        # au-dessus par _track_excursion, depuis trade_signals ou le SL initial).
        _ex = _excursion_cache.get(ticket)
        _risk0 = _ex["risk"] if _ex else ((entry - sl) if is_buy else (sl - entry))
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

        # ── Consensus MTF — OBSERVATION SEULE depuis 2026-08-01 ──────────
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
                        target_risk_pct= settings.get("target_risk_pct", 5.0),
                        min_buffer_price=1.0 * _fb_atr,   # 0.3→1.0 le 2026-08-20,
                                                          # même raison qu'en LG-primary
                                                          # (stops sous 1xATR = -0.58R)
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
                        )
                        _fb_pending = True
                except Exception as _rbe_e:
                    print(f"[{tag}-RISK] {symbol}: error — {_rbe_e}")

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
                if _filter_trend != _need:
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
                if settings.get("htf_4h_filter_enabled", True):
                    if _ema_4h != "neutral" and _ema_4h != _need:
                        print(f"[MACRO-4H] {symbol} {tf} {bias} rejected — "
                              f"4H EMA trend={_ema_4h}, need {_need}")
                        _log_rejection(symbol, f"macro4h_contra_{bias}_vs_{_ema_4h}",
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
                    # Plancher de respiration 0.3xATR → 1.0xATR (2026-08-20).
                    # compute_risk_based_entry rapproche l'ENTRÉE du SL pour
                    # tenir le budget de 7%. À 0.3xATR il pouvait produire des
                    # stops de la largeur du bruit. Mesuré sur la semaine
                    # 08-17→20 :
                    #     <1xATR : 3 trades, -0.58R, 33% WR
                    #     >=1xATR: 18 trades, TOUS les cubes positifs
                    # Les trois trades sous 1xATR (0.54 / 0.63 / 0.70) valaient
                    # -6.50$. En dessous d'un ATR, le stop est atteint par la
                    # respiration normale du marché avant que le setup ait eu
                    # le temps de jouer — XAGUSD mourait en 2 minutes.
                    # Conséquence assumée : les signaux qui ne tiennent pas
                    # dans 7% avec 1xATR de marge sont refusés (mode reject)
                    # au lieu d'être pris en pari serré.
                    _atr_buf = 1.0 * analysis.get("atr", 0)
                    _rbe = compute_risk_based_entry(
                        symbol=          SYMBOL_MAP.get(symbol.upper(), symbol),
                        action=          bias,
                        sl=              entry_data["sl"],
                        market_price=    entry_data["entry"],
                        fixed_lot=       fixed_lot,
                        target_risk_pct= settings.get("target_risk_pct", 5.0),
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