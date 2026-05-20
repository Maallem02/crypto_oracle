import MetaTrader5 as mt5
from features.trading.risk_manager import calculate_lot_size, can_trade, get_daily_pnl_pct

# Mapping symboles → MT5
SYMBOL_MAP = {
    "BTC":    "BTCUSDm",
    "ETH":    "ETHUSDm",
    "XAUUSD": "XAUUSDm",
    "XAGUSD": "XAGUSDm",
    "GBPJPY": "GBPJPYm",
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
    
    # Active le symbole
    if not mt5.symbol_select(mt5_symbol, True):
        return {"success": False, "reason": f"Cannot select symbol {mt5_symbol}"}
    
    symbol_info = mt5.symbol_info(mt5_symbol)
    if symbol_info is None:
        return {"success": False, "reason": f"Symbol info not found for {mt5_symbol}"}
    
    tick = mt5.symbol_info_tick(mt5_symbol)
    if tick is None:
        return {"success": False, "reason": f"No tick data for {mt5_symbol}"}
    
    # Calcule sl_pips
    point    = symbol_info.point
    sl_pips  = abs(entry - sl) / point / 10 if point > 0 else 10
    lot_size = calculate_lot_size(mt5_symbol, risk_percent, sl_pips)
    
    order_type = mt5.ORDER_TYPE_BUY  if action == 'buy'  else mt5.ORDER_TYPE_SELL
    price      = tick.ask            if action == 'buy'  else tick.bid
    
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
        "type_filling": mt5.ORDER_FILLING_IOC,
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