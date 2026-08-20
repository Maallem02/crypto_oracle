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

def compute_risk_based_entry(
    symbol:        str,
    action:        str,     # 'buy' or 'sell'
    sl:            float,   # structurally-placed SL — NEVER moved by this function
    market_price:  float,   # current price, for comparison only
    fixed_lot:     float,
    target_risk_pct: float = 5.0,
    min_buffer_price: float = 0.0,  # e.g. 0.3 * ATR — minimum breathing room from SL
) -> dict:
    """
    Solves for the ENTRY PRICE (not the lot, not the SL) that makes a fixed
    lot risk exactly target_risk_pct of account balance, given the SL is
    already correctly placed behind real structure.

    Why entry and not lot or SL: the user wants risk controlled by waiting
    for a better (closer to SL) entry price instead of shrinking the
    position or moving the stop somewhere structurally invalid.

    Returns:
      {
        "mode": "market" | "pending" | "reject",
        "entry_price": float or None,
        "reason": str,
      }
    "market"  -> current market price already risks <= target, take it now,
                 no need to wait for a better price.
    "pending" -> place a limit order at entry_price (closer to SL than the
                 current market price) to hit the target risk exactly.
    "reject"  -> even the tightest safe entry (respecting min_buffer_price)
                 can't hit the target risk with this fixed lot — the SL is
                 too far away for this lot size on this account, no matter
                 where entry is placed. Reduce lot or skip the symbol
                 instead of forcing an unsafe SL-entry gap.
    """
    account = mt5.account_info()
    if account is None or account.balance <= 0:
        return {"mode": "reject", "entry_price": None, "reason": "no account info"}

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return {"mode": "reject", "entry_price": None, "reason": f"no symbol info for {symbol}"}

    tick_size = symbol_info.trade_tick_size if symbol_info.trade_tick_size > 0 else symbol_info.point
    if tick_size <= 0:
        tick_size = 0.01

    # $ risk per unit of price movement, for this fixed lot — same two
    # conventions used elsewhere in the codebase (USD-quoted vs cross-pairs).
    if symbol.upper().endswith(("USD", "USDM")):
        cs = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 1.0
        value_per_price_unit = fixed_lot * cs
    else:
        tick_value = symbol_info.trade_tick_value if symbol_info.trade_tick_value > 0 else 1.0
        value_per_price_unit = fixed_lot * (tick_value / tick_size)

    if value_per_price_unit <= 0:
        return {"mode": "reject", "entry_price": None, "reason": "could not compute $ per price unit"}

    target_risk_dollars = account.balance * (target_risk_pct / 100.0)
    implied_distance     = target_risk_dollars / value_per_price_unit

    # Current market-price risk, for comparison
    market_distance = abs(market_price - sl)
    market_risk_dollars = market_distance * value_per_price_unit

    if market_risk_dollars <= target_risk_dollars:
        return {
            "mode": "market",
            "entry_price": round(market_price, 5),
            "reason": (
                f"market price already risks ${market_risk_dollars:.2f} "
                f"<= target ${target_risk_dollars:.2f}, no need to wait"
            ),
        }

    if implied_distance < min_buffer_price:
        return {
            "mode": "reject",
            "entry_price": None,
            "reason": (
                f"target {target_risk_pct}% needs entry only {implied_distance:.5f} "
                f"from SL, below min breathing room {min_buffer_price:.5f} — "
                f"fixed lot {fixed_lot} is too big for this SL distance on this "
                f"balance no matter where entry is placed"
            ),
        }

    if action == "buy":
        entry_price = round(sl + implied_distance, 5)
        # Sanity: pending buy-limit must sit BELOW current market price
        if entry_price >= market_price:
            entry_price = round(market_price - tick_size, 5)
    else:
        entry_price = round(sl - implied_distance, 5)
        if entry_price <= market_price:
            entry_price = round(market_price + tick_size, 5)

    return {
        "mode": "pending",
        "entry_price": entry_price,
        "reason": (
            f"waiting for entry at {entry_price} (vs market {market_price}) to hit "
            f"target ${target_risk_dollars:.2f} ({target_risk_pct}%) with fixed lot {fixed_lot}"
        ),
    }


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

def get_daily_pnl_pct(session_start: str = None) -> float:
    """
    Retourne le PnL depuis le dernier démarrage du bot (magic=234000).
    session_start: ISO datetime string — only count deals after this time.
    """
    from datetime import date, datetime as dt
    account = mt5.account_info()
    if account is None or account.balance == 0:
        return 0.0

    # Count deals from session start (when bot was last started), not midnight
    # This prevents old losses from blocking a fresh bot session
    if session_start:
        try:
            from_dt = dt.fromisoformat(session_start)
        except Exception:
            from_dt = dt.combine(date.today(), dt.min.time())
    else:
        from_dt = dt.combine(date.today(), dt.min.time())

    deals = mt5.history_deals_get(from_dt, dt.now())
    realized = 0.0
    if deals:
        realized = sum(
            d.profit for d in deals
            if d.entry == 1 and d.magic == BOT_MAGIC
        )

    # Divide by opening balance of this session to avoid amplification
    opening_balance = account.balance - realized
    denominator = opening_balance if opening_balance > 1 else account.balance
    total_pct = round(realized / denominator * 100, 2)
    return total_pct