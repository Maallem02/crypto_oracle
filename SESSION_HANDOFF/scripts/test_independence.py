"""The two bots must be independent in BOTH directions, while every rule stays
exactly as it was for the SMC bot. Fake MT5 - nothing reaches the broker."""
import sys
import time
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.trading.executor as ex
import features.trading.risk_manager as rm

SMC, VP, MANUAL = 234000, 234100, 0
positions, sent = [], []


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 0, 1, 2, 3
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING = 1, 5
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC, ORDER_TIME_SPECIFIED = 0, 2
    TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED = 10009, 10008

    def positions_get(self, symbol=None, ticket=None):
        return [p for p in positions if symbol is None or p.symbol == symbol]

    def orders_get(self, symbol=None):
        return []

    def symbol_select(self, s, flag):
        return True

    def symbol_info(self, s):
        return NS(point=0.01, trade_contract_size=1.0, trade_tick_size=0.01,
                  trade_tick_value=0.01, volume_min=0.01, volume_max=100.0,
                  volume_step=0.01, trade_stops_level=0, filling_mode=2, digits=2)

    def symbol_info_tick(self, s):
        return NS(ask=100.0, bid=99.99, time=int(time.time()))

    def account_info(self):
        return NS(balance=1000.0, equity=1000.0, login=1)

    def order_send(self, req):
        sent.append(req)
        return NS(retcode=10009, order=555, comment="done")

    def last_error(self):
        return (0, "")


ex.mt5 = FakeMT5()
ex._mt5_init = lambda: None
ex.can_trade = lambda n: True
fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


def pos(sym, magic, side=0):
    return NS(symbol=sym, magic=magic, type=side)


def trade(sym="BTC", magic=SMC, action="buy", **kw):
    sent.clear()
    return ex.place_trade(symbol=sym, action=action, entry=100.0,
                          sl=99.0 if action == "buy" else 101.0,
                          tp1=102.0 if action == "buy" else 98.0,
                          confidence=0.5, fixed_lot=0.01, magic=magic,
                          comment=("VP-BRK buy" if magic == VP else None), **kw)


def ok_or_reason(r):
    return "placed" if r.get("success") else r.get("reason", "")[:70]


print("=" * 84)
print("ONE POSITION PER SYMBOL - each bot counts only its own")
print("=" * 84)
for label, existing, magic, want in [
    ("SMC order, a VP position on BTC        -> SMC NOT blocked", VP, SMC, True),
    ("SMC order, an SMC position on BTC      -> blocked (unchanged)", SMC, SMC, False),
    ("SMC order, a MANUAL position on BTC    -> blocked (unchanged)", MANUAL, SMC, False),
    ("VP order,  an SMC position on BTC      -> VP NOT blocked", SMC, VP, True),
    ("VP order,  a MANUAL position on BTC    -> VP NOT blocked", MANUAL, VP, True),
    ("VP order,  a VP position on BTC        -> blocked (VP's own slot)", VP, VP, False),
]:
    positions[:] = [pos("BTCUSDm", existing)]
    r = trade(magic=magic)
    check(label, bool(r.get("success")) == want, ok_or_reason(r))

print("\n" + "=" * 84)
print("BTC+ETH SAME DIRECTION GUARD - SMC keeps it, ignoring VP; VP is free of it")
print("=" * 84)
for label, existing, magic, want in [
    ("SMC BTC buy, VP ETH buy open           -> SMC NOT blocked", VP, SMC, True),
    ("SMC BTC buy, SMC ETH buy open          -> blocked (unchanged)", SMC, SMC, False),
    ("SMC BTC buy, MANUAL ETH buy open       -> blocked (unchanged)", MANUAL, SMC, False),
    ("VP BTC buy,  SMC ETH buy open          -> VP NOT blocked", SMC, VP, True),
    ("VP BTC buy,  VP ETH buy open           -> VP NOT blocked (no guard)", VP, VP, True),
]:
    positions[:] = [pos("ETHUSDm", existing, 0)]
    r = trade(magic=magic)
    check(label, bool(r.get("success")) == want, ok_or_reason(r))

print("\n" + "=" * 84)
print("MAX OPEN TRADES - SMC only")
print("=" * 84)
positions[:] = []
ex.can_trade = lambda n: False                   # SMC is at its max_trades
r = trade(magic=SMC)
check("SMC at max_trades                      -> SMC blocked (unchanged)",
      not r.get("success"), ok_or_reason(r))
r = trade(magic=VP)
check("SMC at max_trades                      -> VP NOT blocked", bool(r.get("success")),
      ok_or_reason(r))
ex.can_trade = lambda n: True

print("\n" + "=" * 84)
print("SMC LIMIT ORDERS")
print("=" * 84)
for label, existing, want in [
    ("SMC limit order, a VP position on BTC  -> NOT blocked", VP, True),
    ("SMC limit order, an SMC position       -> blocked (unchanged)", SMC, False),
]:
    positions[:] = [pos("BTCUSDm", existing)]
    sent.clear()
    r = ex.place_pending_trade(symbol="BTC", action="buy", entry_price=99.5, sl=99.0,
                               tp1=101.0, confidence=0.5, fixed_lot=0.01)
    check(label, bool(r.get("success")) == want, ok_or_reason(r))

print("\n" + "=" * 84)
print("IDENTIFICATION AND RISK CAP")
print("=" * 84)
positions[:] = []
trade(magic=VP)
check("VP order carries magic 234100 and a VP- comment",
      sent and sent[-1]["magic"] == VP and sent[-1]["comment"].startswith("VP-"),
      f"magic={sent[-1]['magic']} comment={sent[-1]['comment']!r}" if sent else "nothing sent")
trade(magic=SMC)
check("SMC order carries magic 234000 and 'CryptoOracle'",
      sent and sent[-1]["magic"] == SMC and sent[-1]["comment"].startswith("CryptoOracle"))
r = trade(magic=VP, risk_cap_pct=0.000001)
check("VP's own risk cap is applied            -> rejected when tiny",
      not r.get("success") and "Risque" in r.get("reason", ""), ok_or_reason(r))

print("\n" + "=" * 84)
print("DAILY LOSS LIMIT - SMC counts only SMC trades")
print("=" * 84)
rm.mt5 = NS(
    account_info=lambda: NS(balance=100.0),
    history_deals_get=lambda a, b: [NS(entry=1, magic=SMC, profit=-5.0),
                                    NS(entry=1, magic=VP, profit=-40.0)])
pct = rm.get_daily_pnl_pct("2026-09-24T00:00:00")
check("a -$40 VP loss does not count toward SMC's daily loss",
      abs(pct - (-5 / 105 * 100)) < 0.01, f"SMC daily P/L = {pct:.2f}%")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
