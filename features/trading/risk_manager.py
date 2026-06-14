import MetaTrader5 as mt5

def calculate_lot_size(
    symbol: str,
    risk_percent: float,
    sl_ticks: float,          # nombre de ticks entre entry et SL
) -> float:
    """
    Calcule le lot size en fonction du risque.

    Pour paires USD (BTC/USD, ETH/USD, XAU/USD …) :
        risque_$ = lot × contract_size × (sl_ticks × tick_size)
        → lot = risk_$ / (contract_size × sl_distance)
        Formule directe, indépendante de trade_tick_value qui est souvent
        mal renseigné sur les comptes micro de certains brokers.

    Pour cross-pairs (GBP/JPY …) :
        lot = risk_$ / (sl_ticks × tick_value)   ← formule classique
    """
    account = mt5.account_info()
    if account is None:
        return 0.01
    balance     = account.balance
    risk_amount = balance * (risk_percent / 100)
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info or sl_ticks == 0:
        return 0.01

    tick_size = symbol_info.trade_tick_size if symbol_info.trade_tick_size > 0 else 0.01
    sl_distance = sl_ticks * tick_size          # distance en unités de prix

    # Paires cotées en USD : risque direct en $ = lot × contract_size × sl_distance
    if symbol.upper().endswith(('USD', 'USDM')):
        cs = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 1.0
        denominator = cs * sl_distance
    else:
        # Cross-pairs (GBPJPY…) : utiliser trade_tick_value
        tick_value = symbol_info.trade_tick_value
        if tick_value == 0:
            return 0.01
        denominator = sl_ticks * tick_value

    if denominator == 0:
        return 0.01

    lot_size = risk_amount / denominator
    lot_size = max(symbol_info.volume_min, lot_size)
    lot_size = min(symbol_info.volume_max, lot_size)
    lot_size = round(lot_size, 2)
    return lot_size

BOT_MAGIC = 234000

def can_trade(max_trades: int = 3) -> bool:
    """
    Vérifie si le bot peut ouvrir un trade supplémentaire.
    Compare uniquement les positions ouvertes par ce bot (magic=234000),
    pas les trades manuels de l'utilisateur.
    """
    positions = mt5.positions_get()
    if positions is None:
        return True
    bot_positions = [p for p in positions if p.magic == BOT_MAGIC]
    return len(bot_positions) < max_trades

def get_daily_pnl_pct() -> float:
    """
    Retourne le PnL du jour du BOT uniquement (magic=234000).

    IMPORTANT : ne compte PAS les positions manuelles de l'utilisateur —
    seuls les trades ouverts/fermés par ce bot sont pris en compte.

    - Réalisé   : deals fermés aujourd'hui (entry=DEAL_ENTRY_OUT) avec magic 234000
    - Non-réalisé : positions actuellement ouvertes avec magic 234000
    """
    from datetime import date, datetime as dt
    account = mt5.account_info()
    if account is None or account.balance == 0:
        return 0.0

    # ── Réalisé : deals de fermeture aujourd'hui par le bot ─────────────────
    start_of_day = dt.combine(date.today(), dt.min.time())
    deals = mt5.history_deals_get(start_of_day, dt.now())
    realized = 0.0
    if deals:
        realized = sum(
            d.profit for d in deals
            if d.entry == 1 and d.magic == BOT_MAGIC   # DEAL_ENTRY_OUT = 1
        )

    # ── Non-réalisé : uniquement les positions du bot ────────────────────────
    positions  = mt5.positions_get()
    unrealized = 0.0
    if positions:
        unrealized = sum(p.profit for p in positions if p.magic == BOT_MAGIC)

    # Use realized PnL only — unrealized fluctuates and can prematurely
    # stop the bot while a trade is in normal drawdown before recovering.
    total_pct = round(realized / account.balance * 100, 2)
    return total_pct