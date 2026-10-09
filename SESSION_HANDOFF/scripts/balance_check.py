"""Balance went 88.21 -> 150. What does that change?

Sizing is balance-derived: MAX_RISK_PCT (0.07) caps the dollar loss, so the
binding constraint is how WIDE a stop the bot can afford at minimum lot.
Recompute the max affordable stop distance per symbol, old balance vs new.
"""
import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
a = mt5.account_info()
print(f"account   login {a.login}  server {a.server}")
print(f"  balance ${a.balance:.2f}  equity ${a.equity:.2f}  "
      f"free margin ${a.margin_free:.2f}  leverage 1:{a.leverage}")
print(f"  currency {a.currency}   trade_allowed={a.trade_allowed}")

LOTS = {"BTCUSDm": 0.01, "ETHUSDm": 0.45, "XAUUSDm": 0.01, "XAGUSDm": 0.01}
CAP = 0.07

print(f"\n{'symbol':9s} {'lot':>5s} {'minlot':>7s} {'point':>9s} "
      f"{'$/point@lot':>12s} {'max stop $88':>13s} {'max stop $150':>14s} {'margin':>8s}")
for sym, lot in LOTS.items():
    si = mt5.symbol_info(sym)
    if si is None:
        print(f"  {sym}: not available")
        continue
    cs = si.trade_contract_size or 1.0
    ts = si.trade_tick_size or si.point
    tv = si.trade_tick_value or 1.0
    # executor.py: USD-quoted symbols use lot * contract_size * distance
    if sym.upper().endswith(("USD", "USDM")):
        per_unit = lot * cs                      # $ per 1.0 of price distance
    else:
        per_unit = lot * (1.0 / ts) * tv
    per_point = per_unit * si.point
    old = (88.21 * CAP) / per_unit if per_unit else 0
    new = (150.00 * CAP) / per_unit if per_unit else 0
    tick = mt5.symbol_info_tick(sym)
    px = tick.ask if tick else 0
    marg = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, sym, lot, px) if px else 0
    print(f"  {sym:9s} {lot:5.2f} {si.volume_min:7.2f} {si.point:9.5f} "
          f"{per_point:12.4f} {old:13.2f} {new:14.2f} {marg or 0:8.2f}")

print("\n  'max stop' = widest stop distance (in price) the 7% cap allows at that lot.")
print("  If the signal's stop is wider than this, the trade is REJECTED by the cap.")
mt5.shutdown()
