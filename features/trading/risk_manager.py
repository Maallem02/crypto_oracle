import MetaTrader5 as mt5

def calculate_lot_size(
    symbol: str,
    risk_percent: float,
    sl_pips: float,
) -> float:
    account = mt5.account_info()
    if account is None:
        return 0.01
    balance     = account.balance
    risk_amount = balance * (risk_percent / 100)
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        return 0.01
    pip_value = symbol_info.trade_tick_value
    if pip_value == 0 or sl_pips == 0:
        return 0.01
    lot_size = risk_amount / (sl_pips * pip_value)
    lot_size = max(symbol_info.volume_min, lot_size)
    lot_size = min(symbol_info.volume_max, lot_size)
    lot_size = round(lot_size, 2)
    return lot_size

def can_trade(max_trades: int = 3) -> bool:
    positions = mt5.positions_get()
    if positions is None:
        return True
    return len(positions) < max_trades

def get_daily_pnl_pct() -> float:
    """Retourne le PnL du jour en % du balance"""
    account = mt5.account_info()
    if account is None:
        return 0.0
    positions = mt5.positions_get()
    if not positions:
        return 0.0
    unrealized = sum(p.profit for p in positions)
    return round((unrealized / account.balance) * 100, 2) if account.balance > 0 else 0.0