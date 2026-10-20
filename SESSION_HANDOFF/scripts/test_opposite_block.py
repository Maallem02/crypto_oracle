"""No opposite positions on the same coin across bots (2026-10-13).
Fake MT5 only: nothing reaches the broker; settings files and the database are
not touched."""
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.trading.executor as EX
import features.daily_trend.runner as DT

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


STATE = {"positions": [], "orders": []}


class FakeMT5:
    def positions_get(self, symbol=None):
        return [p for p in STATE["positions"] if symbol is None or p.symbol == symbol]

    def orders_get(self, symbol=None):
        return [o for o in STATE["orders"] if symbol is None or o.symbol == symbol]


def pos(magic, side, sym="ETHUSDm", ticket=1):
    return NS(magic=magic, type=0 if side == "buy" else 1, symbol=sym, ticket=ticket)


def order(magic, otype, sym="ETHUSDm", ticket=9):
    return NS(magic=magic, type=otype, symbol=sym, ticket=ticket)


EX.mt5 = FakeMT5()
SMC, VP, DTM = 234000, 234100, 234200

print("1. THE RULE")
STATE.update(positions=[pos(SMC, "sell")], orders=[])
r = EX.opposite_bot_exposure("ETHUSDm", "buy")
check("Scalping short ETH -> a BUY on ETH is refused (the 12/10 case)", r and "Scalping is short ETHUSDm" in r, r)
check("... a SELL on ETH is still allowed (same direction)", EX.opposite_bot_exposure("ETHUSDm", "sell") is None)
check("... BTC is not affected", EX.opposite_bot_exposure("BTCUSDm", "buy") is None)
STATE.update(positions=[pos(VP, "buy")])
check("VP long ETH -> a SELL is refused", "Volume Profile is long" in (EX.opposite_bot_exposure("ETHUSDm", "sell") or ""))
STATE.update(positions=[pos(DTM, "buy", "BTCUSDm")])
check("Daily Trend long BTC -> Scalping / VP cannot sell BTC",
      "Daily Trend is long BTCUSDm" in (EX.opposite_bot_exposure("BTCUSDm", "sell") or ""))
STATE.update(positions=[pos(0, "sell")])
check("a MANUAL position (magic 0) does not block the bots", EX.opposite_bot_exposure("ETHUSDm", "buy") is None)
STATE.update(positions=[pos(VP, "sell")])
check("a bot's OWN opposite position is left to its own rules (VP max per symbol / same direction)",
      EX.opposite_bot_exposure("ETHUSDm", "buy", VP) is None)
check("... but that VP short still blocks a Scalping BUY",
      "Volume Profile is short" in (EX.opposite_bot_exposure("ETHUSDm", "buy", SMC) or ""))
STATE.update(positions=[], orders=[order(SMC, 2)])
r = EX.opposite_bot_exposure("ETHUSDm", "sell")
check("a pending Scalping BUY LIMIT on ETH blocks a SELL (the limit could fill later)",
      r and "opposite order pending: Scalping has a buy order" in r, r)
check("... and does not block a BUY", EX.opposite_bot_exposure("ETHUSDm", "buy") is None)
STATE.update(orders=[order(SMC, 3)])
check("a pending SELL LIMIT blocks a BUY", "has a sell order" in (EX.opposite_bot_exposure("ETHUSDm", "buy") or ""))
STATE.update(orders=[order(0, 2)])
check("a manual pending order does not block", EX.opposite_bot_exposure("ETHUSDm", "sell") is None)

print("\n2. THE ORDER FUNCTIONS REFUSE BEFORE SENDING ANYTHING")
sent = []
EX._mt5_init = lambda: None
EX.can_trade = lambda *a, **k: True
EX.mt5.order_send = lambda req: sent.append(req)
STATE.update(positions=[pos(SMC, "sell")], orders=[])
res = EX.place_trade(symbol="ETH", action="buy", entry=2500.0, sl=2480.0, tp1=2540.0, confidence=0.5,
                     fixed_lot=0.1, magic=VP)
check("VP market BUY while Scalping is short ETH -> refused, nothing sent",
      not res["success"] and "opposite position open" in res["reason"] and not sent, res["reason"])
STATE.update(positions=[pos(VP, "sell")], orders=[])
res = EX.place_pending_trade(symbol="ETH", action="buy", entry_price=2490.0, sl=2480.0, tp1=2540.0,
                             confidence=0.5, fixed_lot=0.1)
check("Scalping BUY LIMIT while VP is short ETH -> refused, nothing sent",
      not res["success"] and "opposite position open: Volume Profile" in res["reason"] and not sent, res["reason"])
STATE.update(positions=[pos(SMC, "sell")], orders=[])
res = EX.place_pending_trade(symbol="ETH", action="buy", entry_price=2490.0, sl=2480.0, tp1=2540.0,
                             confidence=0.5, fixed_lot=0.1)
check("Scalping's OWN short -> its own 1-per-symbol rule answers (unchanged), nothing sent",
      not res["success"] and "max 1 par symbole" in res["reason"] and not sent, res["reason"])

print("\n3. DAILY TREND")
logged = []
DT._log = lambda sym, ev, **k: logged.append((sym, ev, k.get("reason")))
DT.mt5 = NS(symbol_info=lambda s: NS(), symbol_info_tick=lambda s: NS(ask=1.0), account_info=lambda: NS(balance=1000))
DT.opposite_bot_exposure = lambda *a: "opposite position open: Volume Profile is short BTCUSDm (ticket 5)"
DT._open_long("BTC", "BTCUSDm", 100.0, dict(DT.DEFAULT_SETTINGS), {"close": 1, "level": 1, "atr": 1}, 0)
check("a Daily Trend breakout while VP is short BTC -> skipped and journalled",
      logged and logged[0][1] == "skip" and "opposite position open" in logged[0][2], str(logged))
import features.daily_trend.audit as AU
check("the execution check counts it as 'skipped by design', not a problem", AU._skip_status(logged[0][2]) == "info")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
