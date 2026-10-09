"""User request 2026-09-27: restore the stop the user moved by mistake on the
VP ETH buy #1957479505 (2690.02 -> 2632.36 at 03:52:36). Demo account only."""
import sqlite3
import sys

import MetaTrader5 as mt5

TICKET, OLD_SL, WRONG_SL = 1957479505, 2690.02, 2632.36
DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"

db = sqlite3.connect(DB)
row = db.execute("SELECT setup, direction, entry, sl, tp FROM vp_log WHERE ticket=?", (TICKET,)).fetchone()
print("vp_log (what the bot set):", row)

mt5.initialize(timeout=10000)
a = mt5.account_info()
if a is None or a.trade_mode != 0:
    sys.exit("ABORT - not the demo account")
p = (mt5.positions_get(ticket=TICKET) or [None])[0]
if p is None:
    sys.exit("ABORT - position no longer open")
tick = mt5.symbol_info_tick(p.symbol)
print(f"before: SL {p.sl} TP {p.tp}  bid {tick.bid}")
if abs(p.sl - WRONG_SL) > 0.005:
    sys.exit(f"ABORT - SL is {p.sl}, not the {WRONG_SL} to be undone; not touching it")
if not tick.bid > OLD_SL:
    sys.exit(f"ABORT - price {tick.bid} is already at/below {OLD_SL}; restoring would be refused")

r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": TICKET, "symbol": p.symbol,
                    "sl": OLD_SL, "tp": p.tp})
print("order_send:", r.retcode if r else mt5.last_error(), r.comment if r else "")
p = mt5.positions_get(ticket=TICKET)[0]
risk = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, p.symbol, p.volume, p.price_open, p.sl)
print(f"after:  SL {p.sl} TP {p.tp}  -> loss if stopped {risk:+.2f}  (P/L now {p.profit:+.2f})")
mt5.shutdown()
