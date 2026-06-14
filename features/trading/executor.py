import MetaTrader5 as mt5
from features.trading.risk_manager import calculate_lot_size, can_trade, get_daily_pnl_pct
from core.config import runtime

def _mt5_init():
    """Initialize MT5 pointing to the configured terminal (multi-account support)."""
    import os
    if mt5.terminal_info() is not None:
        return
    kwargs = {"timeout": 10000}
    if runtime.mt5_path:
        kwargs["path"] = runtime.mt5_path
    login_id = os.getenv("MT5_LOGIN")
    password  = os.getenv("MT5_PASSWORD")
    server    = os.getenv("MT5_SERVER")
    if login_id and password and server:
        kwargs["login"]    = int(login_id)
        kwargs["password"] = password
        kwargs["server"]   = server
    mt5.initialize(**kwargs)

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

    # ── Trades basés uniquement sur l'analyse ────────────────────────────────
    # Pas de blocage anti-hedge : le bot peut ouvrir dans les deux sens sur un même
    # symbole. Si la 1ère position est perdante, la 2ème (sens opposé) la corrige.
    # Le cooldown (configuré dans les settings) contrôle l'espacement entre trades.

    if not mt5.symbol_select(mt5_symbol, True):
        return {"success": False, "reason": f"Cannot select symbol {mt5_symbol}"}

    symbol_info = mt5.symbol_info(mt5_symbol)
    if symbol_info is None:
        return {"success": False, "reason": f"Symbol info not found for {mt5_symbol}"}

    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None:
        return {"success": False, "reason": f"No tick data for {mt5_symbol}"}

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
    if real_rr < 0.5:
        return {"success": False, "reason": f"RR trop faible: {real_rr:.2f} (min 0.5)"}

    # ── Guard balance : plafond risque dollar (AUTO lots seulement) ───────────
    # Les lots manuels (fixed_lot > 0) sont respectés tels quels.
    # Pour les lots auto, on vérifie que le risque estimé ≤ 5% du solde.
    if not _lot_is_fixed:
        _acct = mt5.account_info()
        if _acct and _acct.balance > 0:
            point   = symbol_info.point
            _max5   = _acct.balance * 0.05
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
                if _min_loss > _max10:
                    return {
                        "success": False,
                        "reason": (
                            f"Balance ${_acct.balance:.2f} trop faible pour {symbol}: "
                            f"lot min risque ${_min_loss:.2f} > 10% (${_max10:.2f})"
                        ),
                    }
                if mt5_symbol.upper().endswith(('USD', 'USDM')):
                    _safe = _max5 / (_cs * real_risk) if (_cs * real_risk) > 0 else symbol_info.volume_min
                else:
                    _safe = _max5 / (_ticks * _tv) if (_ticks * _tv) > 0 else symbol_info.volume_min
                lot_size = round(max(symbol_info.volume_min, min(lot_size, _safe)), 2)
                print(
                    f"[BALANCE GUARD] {symbol} auto lot → {lot_size} "
                    f"(risque ${_min_loss:.2f} ≤ ${_max5:.2f}, balance=${_acct.balance:.2f})"
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
