"""Expectancy panel: R of limit-order trades from the bot's own journal (2026-10-12).
Exness erases SL/TP from a FILLED limit order, so the panel had no initial stop
for those trades and left them out. Fake MT5 + a temporary SQLite journal: the
real database and the broker are not touched."""
import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from core.config import runtime
import features.dashboard.service as S

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


# ── a temporary journal like the backend's (three bots) ─────────────────────
tmp = os.path.join(tempfile.mkdtemp(), "journal.db")
db = sqlite3.connect(tmp)
db.execute("CREATE TABLE trade_signals (ticket INTEGER, sl REAL)")
db.execute("CREATE TABLE vp_log (ticket INTEGER, sl REAL, status TEXT)")
db.execute("CREATE TABLE dt_log (ticket INTEGER, sl REAL, event TEXT)")
db.executemany("INSERT INTO trade_signals VALUES (?,?)", [(101, 95.0), (103, 105.0), (104, None)])
db.execute("INSERT INTO vp_log VALUES (201, 90.0, 'taken')")
db.execute("INSERT INTO vp_log VALUES (202, 91.0, 'rejected')")
db.execute("INSERT INTO dt_log VALUES (301, 80.0, 'entry')")
db.commit()
db.close()
runtime.db_path = tmp

print("1. STOPS READ FROM THE BOTS' JOURNALS")
js = S._journal_stops()
check("Scalping (trade_signals), VP 'taken' and Daily Trend 'entry' rows are read",
      js.get(101) == 95.0 and js.get(201) == 90.0 and js.get(301) == 80.0, str(js))
check("rows without a stop and VP signals that were not taken are ignored", 104 not in js and 202 not in js)
runtime.db_path = os.path.join(tempfile.mkdtemp(), "empty.db")
check("a database without these tables -> empty, no crash", S._journal_stops() == {})
runtime.db_path = tmp

print("\n2. THE PANEL WITH A FAKE ACCOUNT")
# each position: entry deal + exit deal; order sl = what the broker kept
POS = [  # ticket, order type, order sl kept by the broker, entry, exit, side
    (100, 0, 95.0, 100.0, 110.0, 0),     # market buy, stop on the order: +2R (risk 5)
    (101, 2, 0.0, 100.0, 95.0, 0),       # limit buy FILLED: broker erased the stop; journal 95 -> -1R
    (102, 2, 0.0, 100.0, 90.0, 0),       # limit buy, no journal row -> no R (as before)
    (103, 2, 0.0, 100.0, 90.0, 0),       # journal stop 105 is ABOVE a buy entry -> ignored, no R
]


def deals_get(*a, **k):
    out = []
    for t, ot, sl, e, x, side in POS:
        out.append(NS(ticket=t * 10, order=t, position_id=t, time=1_790_000_000 + t, type=side, entry=0,
                      magic=234000, symbol="BTCUSDm", volume=1.0, price=e, profit=0.0, commission=0.0, swap=0.0, fee=0.0))
        out.append(NS(ticket=t * 10 + 1, order=t + 1000, position_id=t, time=1_790_000_100 + t, type=1 - side, entry=1,
                      magic=234000, symbol="BTCUSDm", volume=1.0, price=x, profit=(x - e), commission=0.0, swap=0.0, fee=0.0))
    return out


class FakeMT5:
    DEAL_TYPE_BUY, DEAL_TYPE_SELL, DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 0, 1, 0, 1, 3
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1

    history_deals_get = staticmethod(deals_get)

    def history_orders_get(self, ticket=None):
        return [NS(ticket=t, type=ot, sl=sl) for t, ot, sl, *_ in POS if t == ticket]

    def order_calc_profit(self, t, s, vol, a, b):
        return (b - a) * vol if t == 0 else (a - b) * vol

    def symbol_info_tick(self, s):
        return NS(bid=100.0, ask=100.0)

    def account_info(self):
        return NS(balance=1000.0)


S.mt5 = FakeMT5()
S.ensure_mt5 = lambda: None
S._exp_cache.update(data=None)
smc = S.expectancy(0)["bots"]["smc"]
check(f"4 trades, 2 with an R (market +2R, limit -1R from the journal): trades_with_r={smc['trades_with_r']}",
      smc["trades"] == 4 and smc["trades_with_r"] == 2, str({k: smc.get(k) for k in ('trades', 'trades_with_r')}))
check("the limit-order trade counts as -1R: average (+2 - 1) / 2 = +0.5R", smc["expectancy_r"] == 0.5, str(smc["expectancy_r"]))
check("the panel says how many stops came from the journal", smc["r_from_journal"] == 1)
check("a journal stop on the wrong side of the entry is not used", smc["trades_with_r"] == 2)
S._exp_cache.update(data=None)
real = S._journal_stops
S._journal_stops = lambda: {}
smc0 = S.expectancy(0)["bots"]["smc"]
S._journal_stops = real
check("without the journal (old behaviour) only the market trade had an R",
      smc0["trades_with_r"] == 1 and smc0["expectancy_r"] == 2.0)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
