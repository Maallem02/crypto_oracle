import MetaTrader5 as mt5
from features.trading.risk_manager import calculate_lot_size, can_trade, get_daily_pnl_pct

# Mapping symboles → MT5
SYMBOL_MAP = {
    # Crypto
    "BTC":  "BTCUSDm",
    "ETH":  "ETHUSDm",
    "SOL":  "SOLUSDm",
    "BNB":  "BNBUSDm",
    "XRP":  "XRPUSDm",
    # Métaux
    "XAUUSD": "XAUUSDm",
    "XAGUSD": "XAGUSDm",
    # Forex
    "GBPJPY": "GBPJPYm",
    "EURUSD": "EURUSDm",
    "USDJPY": "USDJPYm",
}

def place_trade(
    symbol: str,
    action: str,
    entry:  float,
    sl:     float,
    tp1:    float,
    confidence: float,
    risk_percent: float = 1.0,
    max_trades: int = 3,
) -> dict:

    mt5.initialize()

    if not can_trade(max_trades):
        return {"success": False, "reason": "Max trades reached"}

    mt5_symbol = SYMBOL_MAP.get(symbol.upper())
    if not mt5_symbol:
        return {"success": False, "reason": f"Symbol {symbol} not supported"}

    # ── Anti-hedge : refuser si une position bot existe déjà dans le sens opposé ──
    # Ex : 5m donne BUY et 15m donne SELL sur le même symbole → on skip le second.
    existing = mt5.positions_get(symbol=mt5_symbol)
    if existing:
        from features.trading.risk_manager import BOT_MAGIC
        for pos in existing:
            if pos.magic == BOT_MAGIC:
                existing_dir = "buy" if pos.type == 0 else "sell"
                if existing_dir != action:
                    return {
                        "success": False,
                        "reason": f"Anti-hedge: already {existing_dir} on {mt5_symbol} (ticket {pos.ticket})",
                    }

    # Active le symbole
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
    # Entre l'analyse et l'exécution, le prix peut avoir bougé (surtout sur BTC/5m).
    # Solution : conserver la même DISTANCE SL/TP, mais l'ancrer sur le prix live.
    # Ainsi le trade a toujours la pleine distance SL depuis son entrée réelle.
    original_risk   = abs(entry - sl)    # distance en prix calculée à l'analyse
    original_reward = abs(tp1 - entry)   # récompense calculée à l'analyse

    # Rejet si le signal est trop vieux : le prix s'est éloigné de plus de 3× le risque.
    # (ex : tout le move a déjà eu lieu — le TP est déjà dépassé)
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

    # ── Lot size — calculé sur la distance SL réelle (après ancrage live) ──────
    # Formule :
    #   sl_ticks = distance SL en prix / taille d'un tick
    #   lot      = risque_$ / (sl_ticks × valeur_d_un_tick_par_lot)
    tick_size = symbol_info.trade_tick_size
    if tick_size <= 0:
        tick_size = symbol_info.point if symbol_info.point > 0 else 0.01
    sl_ticks = abs(price - sl) / tick_size if tick_size > 0 else 10
    lot_size = calculate_lot_size(mt5_symbol, risk_percent, sl_ticks)

    # ── Guard final : RR minimum 1.0 (sécurité) ──────────────────────────────
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
    if real_rr < 1.0:
        return {"success": False, "reason": f"RR trop faible: {real_rr:.2f} (min 1.0)"}

    # ── Correction "Invalid stops" ────────────────────────────────────────────
    # MT5 exige une distance minimale entre le prix d'exécution et le SL/TP
    point    = symbol_info.point
    min_dist = symbol_info.trade_stops_level * point
    if min_dist == 0:
        min_dist = point * 10   # fallback sécurisé

    if action == 'buy':
        sl  = min(sl,  price - min_dist)   # SL sous le prix
        tp1 = max(tp1, price + min_dist)   # TP au-dessus du prix
    else:
        sl  = max(sl,  price + min_dist)   # SL au-dessus du prix
        tp1 = min(tp1, price - min_dist)   # TP sous le prix

    # ── Auto-detect filling mode — évite l'erreur 10030 ─────────────────────
    # Certains brokers ne supportent pas IOC pour tous les symboles.
    # filling_mode est un bitmask : bit0=FOK, bit1=IOC, bit2=RETURN
    fm = symbol_info.filling_mode
    if fm & 2:                               # IOC supporté
        type_filling = mt5.ORDER_FILLING_IOC
    elif fm & 1:                             # FOK supporté
        type_filling = mt5.ORDER_FILLING_FOK
    else:                                    # fallback RETURN
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
        return {
            "success": False,
            "reason":  f"Order failed: {result.comment}",
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
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   pos.symbol,
            "volume":   pos.volume,
            "type":     mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price":    price,
            "deviation": 20,
            "magic":    234000,
            "comment":  "CryptoOracle close",
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