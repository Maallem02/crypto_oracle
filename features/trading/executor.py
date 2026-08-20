import time
import MetaTrader5 as mt5
from datetime import datetime, timezone
from features.trading.risk_manager import (
    calculate_lot_size, can_trade, get_daily_pnl_pct, compute_risk_based_entry, BOT_MAGIC,
)

# Symboles 24/7 — non concernés par les fenêtres rollover/ouverture forex
CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "BNB", "XRP"}

# Plafond de risque par trade, en fraction du solde. Appliqué DUR : un trade
# qui dépasse est refusé, pas averti. Voir le garde-fou dans place_trade().
#
# 5% -> 7% le 2026-08-12 (choix utilisateur, pour laisser respirer le stop).
# Effet mesuré sur l'or à 81.96$ de solde : le stop maximal passe de 4.10 à
# 5.74 points, soit 0.72xATR -> 1.00xATR, et le repli exigé pour remplir la
# limite tombe de 1.8xATR à 1.5xATR. Contrepartie : une série de 10 pertes
# coûte 52% au lieu de 40%.
MAX_RISK_PCT = 0.07

def _mt5_init():
    """
    Attache le terminal (idempotent) — délègue à mt5_client.ensure_mt5().

    L'ancienne version forçait mt5.initialize(login=...) à CHAQUE appel,
    donc à chaque place_trade(). C'était exactement le re-login que
    router._mt5_init() avait été écrit pour supprimer : le terminal
    basculait du compte réel ouvert manuellement vers le compte demo à
    chaque ordre placé.
    """
    from features.trading.mt5_client import ensure_mt5
    ensure_mt5()

# Mapping symboles → MT5
SYMBOL_MAP = {
    # Crypto
    "BTC":    "BTCUSDm",
    "ETH":    "ETHUSDm",
    "SOL":    "SOLUSDm",
    "BNB":    "BNBUSDm",
    "XRP":    "XRPUSDm",
    # Métaux
    "XAUUSD": "XAUUSDm",
    "XAGUSD": "XAGUSDm",
    # Forex
    "GBPJPY": "GBPJPYm",
    "EURUSD": "EURUSDm",
    "USDJPY": "USDJPYm",
}

def place_trade(
    symbol:       str,
    action:       str,
    entry:        float,
    sl:           float,
    tp1:          float,
    confidence:   float,
    risk_percent: float = 1.0,
    max_trades:   int   = 3,
    fixed_lot:    float = 0.0,   # 0 = auto (risk-based), >0 = lot manuel
) -> dict:

    _mt5_init()

    if not can_trade(max_trades):
        return {"success": False, "reason": "Max trades reached"}

    mt5_symbol = SYMBOL_MAP.get(symbol.upper())
    if not mt5_symbol:
        return {"success": False, "reason": f"Symbol {symbol} not supported"}

    # ── 1 position max par symbole ───────────────────────────────────────────
    # L'empilement même-sens ("averaging") a produit 4 paires perdantes
    # (ETH 07-09/07-10/07-11, USDJPY 07-13) : risque corrélé sans nouvel edge.
    _existing = mt5.positions_get(symbol=mt5_symbol)
    if _existing:
        return {
            "success": False,
            "reason": f"Position déjà ouverte sur {mt5_symbol} (max 1 par symbole)",
        }

    # ── Anti-concentration corrélée ───────────────────────────────────────────
    # Mesuré sur 6 mois de returns 1h : BTC/ETH corrélés +0.90, XAU/XAG +0.83.
    # Ouvrir BTC-buy + ETH-buy = UN pari crypto en taille double, pas 2 trades
    # (c'est ce qui a amplifié la casse du 20/07). On bloque une 2e position
    # MÊME SENS dans une paire >0.8. Les corrélations négatives (EURUSD/USDJPY
    # -0.57) restent autorisées : même sens = hedge, ça réduit le risque.
    _CORR_GROUPS = [{"BTCUSDm", "ETHUSDm"}, {"XAUUSDm", "XAGUSDm"}]
    _grp = next((g for g in _CORR_GROUPS if mt5_symbol in g), None)
    if _grp:
        for _p in (mt5.positions_get() or []):
            if _p.symbol in _grp and _p.symbol != mt5_symbol:
                _p_dir = "buy" if _p.type == 0 else "sell"
                if _p_dir == action:
                    return {
                        "success": False,
                        "reason": (
                            f"Position corrélée déjà ouverte ({_p.symbol} {_p_dir}, "
                            f"corr>0.8) — même pari, pas de doublement"
                        ),
                    }

    if not mt5.symbol_select(mt5_symbol, True):
        return {"success": False, "reason": f"Cannot select symbol {mt5_symbol}"}

    symbol_info = mt5.symbol_info(mt5_symbol)
    if symbol_info is None:
        return {"success": False, "reason": f"Symbol info not found for {mt5_symbol}"}

    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None:
        return {"success": False, "reason": f"No tick data for {mt5_symbol}"}

    # ── Fenêtres interdites forex/métaux (heure serveur) ─────────────────────
    # Lundi < 02:00 : digestion du gap week-end, spreads élargis, indicateurs
    # faussés (les 3 pertes du 07-13 : 00:05, 00:21, 01:24 serveur).
    # 23:45–00:30 quotidien : pic de spread au rollover.
    if symbol.upper() not in CRYPTO_SYMBOLS:
        _srv = datetime.fromtimestamp(tick.time, tz=timezone.utc)  # epoch MT5 = horloge serveur
        _monday_open = _srv.weekday() == 0 and _srv.hour < 2
        _rollover = (_srv.hour == 23 and _srv.minute >= 45) or (_srv.hour == 0 and _srv.minute < 30)
        if _monday_open or _rollover:
            return {
                "success": False,
                "reason": (
                    f"Fenêtre interdite ({'ouverture lundi' if _monday_open else 'rollover'}) "
                    f"— heure serveur {_srv:%a %H:%M}"
                ),
            }

    order_type = mt5.ORDER_TYPE_BUY  if action == 'buy'  else mt5.ORDER_TYPE_SELL
    price      = tick.ask            if action == 'buy'  else tick.bid

    # ── Ancrage SL/TP au prix d'exécution réel ───────────────────────────────
    # Les niveaux sl/tp ont été calculés à partir du close de la dernière bougie.
    # On conserve la même DISTANCE SL/TP, mais ancrée sur le prix live.
    original_risk   = abs(entry - sl)
    original_reward = abs(tp1 - entry)

    # Rejet si le signal est trop vieux (drift > 3× le risque)
    if original_risk > 0:
        price_drift = abs(price - entry)
        if price_drift > original_risk * 3.0:
            return {
                "success": False,
                "reason": (
                    f"Signal périmé: drift={price_drift:.5f} > 3×risk={original_risk*3:.5f} "
                    f"(analysis={entry} live={price})"
                ),
            }

    # Recalculer SL/TP depuis le prix d'exécution → trade toujours "frais"
    if original_risk > 0 and original_reward > 0:
        if action == 'buy':
            sl  = round(price - original_risk,   5)
            tp1 = round(price + original_reward, 5)
        else:
            sl  = round(price + original_risk,   5)
            tp1 = round(price - original_reward, 5)
        print(
            f"[ANCHOR] {symbol} {action} | analysis_entry={entry} live={price} "
            f"drift={abs(price-entry):.5f} | SL={sl} TP={tp1} "
            f"(risk={original_risk:.5f} reward={original_reward:.5f})"
        )

    # ── Lot size ──────────────────────────────────────────────────────────────
    _lot_is_fixed = fixed_lot and fixed_lot > 0
    if _lot_is_fixed:
        # Lot manuel : respecté tel quel — l'utilisateur assume le risque
        lot_size = fixed_lot
        print(f"[LOT] {symbol} fixed lot={lot_size}")
    else:
        # Auto : basé sur le risque (balance × risk% / sl_distance × contract_size)
        tick_size = symbol_info.trade_tick_size
        if tick_size <= 0:
            tick_size = symbol_info.point if symbol_info.point > 0 else 0.01
        sl_ticks = abs(price - sl) / tick_size if tick_size > 0 else 10
        lot_size = calculate_lot_size(mt5_symbol, risk_percent, sl_ticks)
        print(f"[LOT] {symbol} auto lot={lot_size} (risk={risk_percent}%)")

    # Clamp au min/max du broker
    lot_size = max(symbol_info.volume_min, min(symbol_info.volume_max, lot_size))
    lot_size = round(lot_size, 2)

    # ── Guard final : RR minimum 0.5 ─────────────────────────────────────────
    if action == 'buy':
        real_risk   = price - sl
        real_reward = tp1 - price
    else:
        real_risk   = sl - price
        real_reward = price - tp1

    if real_risk <= 0:
        return {"success": False, "reason": f"SL invalide après recalcul: SL={sl} price={price}"}
    if real_reward <= 0:
        return {"success": False, "reason": f"TP invalide après recalcul: TP={tp1} price={price}"}
    real_rr = real_reward / real_risk
    if real_rr < 0.95:
        return {"success": False, "reason": f"RR trop faible: {real_rr:.2f} (min 0.95)"}

    # ── Guard balance : plafond risque dollar (TOUS les lots) ────────────────
    # S'applique aussi aux lots fixes : l'UI peut pousser d'anciennes valeurs
    # (07-13 : lots 0.04/0.2 poussés par le front → stop-out du compte).
    # Risque estimé ≤ 5% du solde, sinon lot réduit ; rejet si même le lot
    # minimum risque > 10%.
    _acct = mt5.account_info()
    if _acct and _acct.balance > 0:
        point   = symbol_info.point
        _max5   = _acct.balance * MAX_RISK_PCT
        _max10  = _acct.balance * 0.10
        _cs     = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 1.0
        _ts     = symbol_info.trade_tick_size      if symbol_info.trade_tick_size  > 0 else point
        _tv     = symbol_info.trade_tick_value     if symbol_info.trade_tick_value > 0 else 1.0

        if mt5_symbol.upper().endswith(('USD', 'USDM')):
            _est_loss = lot_size               * _cs * real_risk
            _min_loss = symbol_info.volume_min * _cs * real_risk
        else:
            _ticks    = real_risk / _ts if _ts > 0 else 1
            _est_loss = lot_size               * _ticks * _tv
            _min_loss = symbol_info.volume_min * _ticks * _tv

        if _est_loss > _max5:
            # ── PLAFOND DUR À max_risk_pct — 2026-08-12 ──────────────────
            # L'ancienne règle laissait passer TOUT lot fixe jusqu'à 90% du
            # solde, en se contentant d'un avertissement. Conséquence réelle :
            # XAUUSD 0.01 lot, stop 16.12 points, -16.24$ sur un compte de
            # 81.96$ — 19.7% perdus sur un seul trade, alors que le réglage
            # affichait "5% de risque".
            #
            # L'or vaut 100$ le point à 0.01 lot : un stop structurel de 16
            # points coûte 16$ quoi qu'il arrive. Aucun réglage de lot ne
            # corrige ça (0.01 est déjà le minimum broker) — le seul choix
            # honnête est de ne pas prendre le trade.
            #
            # La voie "entrée optimale" (compute_risk_based_entry) place une
            # limite plus près du SL pour atteindre exactement le pourcentage
            # visé ; elle est tentée en amont côté router. Si on arrive ici,
            # c'est qu'elle n'a pas pu s'appliquer — on refuse.
            if _lot_is_fixed:
                return {
                    "success": False,
                    "reason": (
                        f"Risque trop élevé pour {symbol}: lot {lot_size} risque "
                        f"${_est_loss:.2f} = {100*_est_loss/_acct.balance:.0f}% du solde "
                        f"${_acct.balance:.2f} (plafond {100*MAX_RISK_PCT:.0f}%). "
                        f"Stop {real_risk:.5g} trop large pour ce solde."
                    ),
                }
            else:
                # MIXED-OPTIMAL survival cap (2026-07-23): reject any AUTO trade
                # whose MINIMUM lot still risks >15% of balance. Skips symbols too
                # big for the account (gold/silver at ~50% on a small balance) until
                # it grows — the single-trade floor June never had.
                if _min_loss > _acct.balance * 0.30:
                    return {
                        "success": False,
                        "reason": (
                            f"Symbole trop gros pour le solde: {symbol} lot min risque "
                            f"${_min_loss:.2f} > 30% du solde ${_acct.balance:.2f}"
                        ),
                    }
                if mt5_symbol.upper().endswith(('USD', 'USDM')):
                    _safe = _max5 / (_cs * real_risk) if (_cs * real_risk) > 0 else symbol_info.volume_min
                else:
                    _safe = _max5 / (_ticks * _tv) if (_ticks * _tv) > 0 else symbol_info.volume_min
                _requested = lot_size
                lot_size = round(max(symbol_info.volume_min, min(lot_size, _safe)), 2)
                print(
                    f"[BALANCE GUARD] {symbol} lot auto {_requested} → {lot_size} "
                    f"(risque max ${_max5:.2f}, balance=${_acct.balance:.2f})"
                )

    # ── Correction "Invalid stops" ────────────────────────────────────────────
    # MT5 exige une distance minimale entre le prix d'exécution et le SL/TP
    point    = symbol_info.point
    min_dist = symbol_info.trade_stops_level * point
    if min_dist == 0:
        min_dist = point * 10

    if action == 'buy':
        sl  = min(sl,  price - min_dist)
        tp1 = max(tp1, price + min_dist)
    else:
        sl  = max(sl,  price + min_dist)
        tp1 = min(tp1, price - min_dist)

    # ── Auto-detect filling mode — évite l'erreur 10030 ─────────────────────
    fm = symbol_info.filling_mode
    if fm & 2:
        type_filling = mt5.ORDER_FILLING_IOC
    elif fm & 1:
        type_filling = mt5.ORDER_FILLING_FOK
    else:
        type_filling = mt5.ORDER_FILLING_RETURN

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       mt5_symbol,
        "volume":       lot_size,
        "type":         order_type,
        "price":        price,
        "sl":           round(sl, 5),
        "tp":           round(tp1, 5),
        "deviation":    20,
        "magic":        234000,
        "comment":      f"CryptoOracle {confidence*100:.0f}%",
        "type_filling": type_filling,
    }

    result = mt5.order_send(request)

    if result is None:
        return {"success": False, "reason": f"order_send returned None: {mt5.last_error()}"}

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        _rc_msg = {
            10004: "Requote",
            10006: "Request rejected",
            10007: "Request cancelled by trader",
            10010: "Order placed (pending)",
            10013: "Invalid request",
            10014: "Invalid volume",
            10015: "Invalid price",
            10016: "Invalid stops",
            10017: "Trade disabled for this symbol",
            10018: "Market closed",
            10019: "Not enough money / margin",
            10020: "Prices changed",
            10021: "No quotes",
            10022: "Invalid order expiration",
            10023: "Order state changed",
            10024: "Too many requests",
            10025: "No changes",
            10026: "Autotrading disabled by server",
            10027: "Autotrading disabled by client",
            10028: "Request locked",
            10029: "Position already closed",
            10030: "Close order already exists",
        }.get(result.retcode, result.comment or f"code {result.retcode}")
        return {
            "success": False,
            "reason":  f"Order failed: {_rc_msg}",
            "retcode": result.retcode,
        }

    return {
        "success": True,
        "ticket":  result.order,
        "symbol":  mt5_symbol,
        "action":  action,
        "volume":  lot_size,
        "price":   price,
        "sl":      sl,
        "tp":      tp1,
    }


def place_pending_trade(
    symbol:         str,
    action:         str,
    entry_price:    float,   # computed optimal entry — closer to SL than market
    sl:             float,
    tp1:            float,
    confidence:     float,
    fixed_lot:      float,
    expire_minutes: int = 30,   # cancel automatically if not filled in time
) -> dict:
    """
    Places a LIMIT order at entry_price instead of a market order, so a
    fixed lot ends up risking a controlled % of the account instead of
    whatever the market-price SL distance happens to be. SL is passed in
    exactly as computed upstream (structurally placed) — this function
    never moves it.

    Expires automatically via MT5's own ORDER_TIME_SPECIFIED if price never
    retraces to entry_price — no separate cancellation job needed.
    """
    _mt5_init()

    if not can_trade(3):
        return {"success": False, "reason": "Max trades reached"}

    mt5_symbol = SYMBOL_MAP.get(symbol.upper())
    if not mt5_symbol:
        return {"success": False, "reason": f"Symbol {symbol} not supported"}

    _existing = mt5.positions_get(symbol=mt5_symbol)
    if _existing:
        return {
            "success": False,
            "reason": f"Position déjà ouverte sur {mt5_symbol} (max 1 par symbole)",
        }

    # Also check for an existing PENDING order on this symbol from this bot,
    # so we don't stack multiple limit orders waiting on the same setup.
    _existing_pending = mt5.orders_get(symbol=mt5_symbol)
    if _existing_pending:
        _bot_pending = [o for o in _existing_pending if o.magic == 234000]
        if _bot_pending:
            return {
                "success": False,
                "reason": f"Pending order déjà en attente sur {mt5_symbol}",
            }

    if not mt5.symbol_select(mt5_symbol, True):
        return {"success": False, "reason": f"Cannot select symbol {mt5_symbol}"}

    symbol_info = mt5.symbol_info(mt5_symbol)
    if symbol_info is None:
        return {"success": False, "reason": f"Symbol info not found for {mt5_symbol}"}

    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None:
        return {"success": False, "reason": f"No tick data for {mt5_symbol}"}

    current_price = tick.ask if action == "buy" else tick.bid

    order_type = mt5.ORDER_TYPE_BUY_LIMIT if action == "buy" else mt5.ORDER_TYPE_SELL_LIMIT

    # Sanity: a buy-limit must sit below current price, sell-limit above.
    # If it doesn't (price already moved through the level), the setup has
    # already played out — reject rather than placing an invalid order.
    if action == "buy" and entry_price >= current_price:
        return {
            "success": False,
            "reason": f"buy-limit entry {entry_price} not below market {current_price} — setup already ran",
        }
    if action == "sell" and entry_price <= current_price:
        return {
            "success": False,
            "reason": f"sell-limit entry {entry_price} not above market {current_price} — setup already ran",
        }

    lot_size = max(symbol_info.volume_min, min(symbol_info.volume_max, fixed_lot))
    lot_size = round(lot_size, 2)

    point    = symbol_info.point
    min_dist = symbol_info.trade_stops_level * point
    if min_dist == 0:
        min_dist = point * 10

    if action == "buy":
        sl_final  = min(sl, entry_price - min_dist)
        tp1_final = max(tp1, entry_price + min_dist)
    else:
        sl_final  = max(sl, entry_price + min_dist)
        tp1_final = min(tp1, entry_price - min_dist)

    request = {
        "action":       mt5.TRADE_ACTION_PENDING,
        "symbol":       mt5_symbol,
        "volume":       lot_size,
        "type":         order_type,
        "price":        round(entry_price, 5),
        "sl":           round(sl_final, 5),
        "tp":           round(tp1_final, 5),
        "deviation":    20,
        "magic":        234000,
        "comment":      f"CryptoOracle-P {confidence*100:.0f}%",
    }

    expiration = None
    if expire_minutes and expire_minutes > 0:
        from datetime import timedelta
        expiration = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
        request["type_time"]  = mt5.ORDER_TIME_SPECIFIED
        request["expiration"] = expiration
    else:
        # GTC: no expiration — order stays live until filled or manually
        # cancelled. Broker-dependent whether GTC pending orders survive
        # a platform restart; MT5 itself keeps them indefinitely.
        request["type_time"] = mt5.ORDER_TIME_GTC

    result = mt5.order_send(request)

    if result is None:
        return {"success": False, "reason": f"order_send returned None: {mt5.last_error()}"}

    if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
        return {
            "success": False,
            "reason":  f"Pending order failed: {result.comment or result.retcode}",
            "retcode": result.retcode,
        }

    return {
        "success":      True,
        "ticket":       result.order,
        "symbol":       mt5_symbol,
        "action":       action,
        "volume":       lot_size,
        "price":        entry_price,
        "sl":           sl_final,
        "tp":           tp1_final,
        "expires_at":   expiration.isoformat() if expiration else None,
        "pending":      True,
    }


def get_pending_orders() -> list:
    """Ordres en attente placés par le bot (magic=BOT_MAGIC)."""
    _mt5_init()
    orders = mt5.orders_get() or []
    out = []
    for o in orders:
        if o.magic != BOT_MAGIC:
            continue
        tick = mt5.symbol_info_tick(o.symbol)
        # time_setup et tick.time sont tous deux exprimés dans l'horloge
        # SERVEUR du broker (quirk MT5 : ce n'est pas de l'epoch UTC réel —
        # cf. le calcul de fenêtre rollover plus haut). Les comparer entre eux
        # est donc juste — MAIS SEULEMENT TANT QUE LE FLUX AVANCE.
        #
        # Marché fermé (week-end, jour férié), tick.time GÈLE sur le dernier
        # tick : l'âge cesse d'avancer et le plafond pending_max_age_minutes
        # ne voit jamais l'ordre vieillir. Mesuré le 2026-08-16 (dimanche) :
        #
        #   XAUUSDm posé vendredi 14:04, dernier tick vendredi 21:57
        #   -> âge annoncé 473.5 min alors que l'âge réel était 3279.7 min
        #
        # L'ordre repartait donc vivant à l'ouverture du lundi en portant les
        # niveaux de vendredi (limite à 4366.193, stop à 4.4 points), et la
        # purge ne le rattrapait qu'après 6 minutes de cotation.
        #
        # On prend le MAX des deux horloges : le flux reste la référence en
        # séance (immunisé au décalage de fuseau serveur/local), l'horloge
        # murale prend le relais dès que le marché ferme. Le max ne peut que
        # vieillir un ordre, jamais le rajeunir — donc jamais d'annulation
        # prématurée si les deux horloges divergent.
        tick_age = (tick.time - o.time_setup) / 60.0 if tick else None
        wall_age = (time.time() - o.time_setup) / 60.0
        age_min  = round(wall_age if tick_age is None else max(tick_age, wall_age), 1)
        out.append({
            "ticket":      o.ticket,
            "symbol":      o.symbol,
            "type":        "buy_limit" if o.type == mt5.ORDER_TYPE_BUY_LIMIT else
                           "sell_limit" if o.type == mt5.ORDER_TYPE_SELL_LIMIT else str(o.type),
            "volume":      o.volume_current,
            "price_open":  o.price_open,
            "sl":          o.sl,
            "tp":          o.tp,
            "age_minutes": age_min,
            "comment":     o.comment,
        })
    return out


def cancel_pending_order(ticket: int) -> dict:
    """Annule UN ordre en attente par son ticket."""
    _mt5_init()
    result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": int(ticket)})
    if result is None:
        return {"success": False, "ticket": ticket,
                "reason": f"order_send returned None: {mt5.last_error()}"}
    ok = result.retcode == mt5.TRADE_RETCODE_DONE
    return {"success": ok, "ticket": ticket, "retcode": result.retcode,
            "reason": None if ok else (result.comment or f"code {result.retcode}")}


def cancel_stale_pending_orders(max_age_minutes: int = 120, symbol: str = None) -> list:
    """
    Annule les ordres en attente du bot plus vieux que max_age_minutes.

    Pourquoi c'est nécessaire : avec pending_expire_minutes=0 (GTC, choix
    utilisateur) l'ordre n'expire JAMAIS côté broker. Il survit à l'arrêt du
    bot — il vit sur le serveur du broker, pas dans le process. Et comme
    place_pending_trade() refuse un 2e ordre en attente sur le même symbole,
    un ordre jamais rempli bloque ce symbole indéfiniment pour tout nouveau
    signal. Ce garde-fou est côté bot, donc GTC reste utilisable sans risque
    de blocage permanent.

    max_age_minutes <= 0 → désactivé (GTC pur, comportement d'origine).
    symbol facultatif : restreint à un symbole (utilisé par l'endpoint manuel).
    """
    force = max_age_minutes is None          # endpoint manuel : âge ignoré
    if not force and max_age_minutes <= 0:
        return []

    cancelled = []
    for o in get_pending_orders():
        if symbol and symbol.upper() not in o["symbol"].upper():
            continue
        age = o["age_minutes"]
        if not force:
            # age vient de get_pending_orders() : max(horloge flux, horloge
            # murale), donc il continue d'avancer marché fermé. Un ordre posé
            # vendredi est bien périmé lundi. (None ne devrait plus arriver —
            # garde défensive.)
            if age is None or age < max_age_minutes:
                continue
        res = cancel_pending_order(o["ticket"])
        res.update({"symbol": o["symbol"], "age_minutes": age,
                    "price_open": o["price_open"]})
        cancelled.append(res)
        if res["success"]:
            _why = ("annulation manuelle" if force else
                    f"{age:.0f}min sans exécution (max {max_age_minutes})")
            print(f"[PENDING/CANCEL] {o['symbol']} ticket={o['ticket']} "
                  f"@ {o['price_open']} - {_why} -> annule, symbole debloque")
        else:
            print(f"[PENDING/ERR] {o['symbol']} ticket={o['ticket']}: {res['reason']}")
    return cancelled


def close_all_trades():
    """Ferme tous les trades ouverts"""
    positions = mt5.positions_get()
    results   = []

    for pos in positions:
        tick  = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if pos.type == 0 else tick.ask

        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    pos.symbol,
            "volume":    pos.volume,
            "type":      mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
            "position":  pos.ticket,
            "price":     price,
            "deviation": 20,
            "magic":     234000,
            "comment":   "CryptoOracle close",
        }
        result = mt5.order_send(request)
        results.append({"ticket": pos.ticket, "success": result.retcode == mt5.TRADE_RETCODE_DONE})

    return results


def get_open_trades():
    """Retourne les trades ouverts"""
    positions = mt5.positions_get()
    if not positions:
        return []
    return [{
        "ticket":  p.ticket,
        "symbol":  p.symbol,
        "type":    "buy" if p.type == 0 else "sell",
        "volume":  p.volume,
        "price":   p.price_open,
        "sl":      p.sl,
        "tp":      p.tp,
        "profit":  p.profit,
        "comment": p.comment,
    } for p in positions]
