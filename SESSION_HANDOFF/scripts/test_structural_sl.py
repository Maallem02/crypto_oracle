"""VP stops stay at the structure; SMC keeps its distance-anchoring unchanged.
Fake MT5 - nothing reaches the broker."""
import sys
import time
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.trading.executor as ex
import features.volume_profile.runner as R

sent = []
ASK, BID = 100.00, 99.99


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL = 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009
    positions_get = staticmethod(lambda symbol=None, ticket=None: [])
    symbol_select = staticmethod(lambda s, f: True)
    symbol_info = staticmethod(lambda s: NS(point=0.01, trade_contract_size=1.0, trade_tick_size=0.01,
                                            trade_tick_value=0.01, volume_min=0.01, volume_max=100.0,
                                            volume_step=0.01, trade_stops_level=0, filling_mode=2))
    symbol_info_tick = staticmethod(lambda s: NS(ask=ASK, bid=BID, time=int(time.time())))
    account_info = staticmethod(lambda: NS(balance=1000.0, equity=1000.0))
    last_error = staticmethod(lambda: (0, ""))

    @staticmethod
    def order_send(req):
        sent.append(req)
        return NS(retcode=10009, order=1, comment="done")


ex.mt5 = FakeMT5()
ex._mt5_init = lambda: None
ex.can_trade = lambda n: True
fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


print("=" * 84)
print("A sell signal: candle closed at 100.50, structural stop 101.50, target 98.50 (2R).")
print("By the time the order goes out, price has dropped to 99.99 (like the live BTC trade).")
print("=" * 84)

sent.clear()
r = ex.place_trade(symbol="BTC", action="sell", entry=100.50, sl=101.50, tp1=98.50,
                   confidence=0.5, fixed_lot=0.01, magic=234100, comment="VP-REV sell",
                   keep_sl=True, rr=2.0)
q = sent[-1] if sent else {}
risk = q.get("sl", 0) - BID
check("VP: stop stays AT the structure (101.50)", abs(q.get("sl", 0) - 101.50) < 1e-9,
      f"SL={q.get('sl')}")
check("VP: target = exactly 2R from the real fill (99.99 - 2 x 1.51 = 96.97)",
      abs(q.get("tp", 0) - (BID - 2 * risk)) < 1e-6, f"TP={q.get('tp')}")

sent.clear()
ex.place_trade(symbol="BTC", action="sell", entry=100.50, sl=101.50, tp1=98.50,
               confidence=0.5, fixed_lot=0.01)
q = sent[-1] if sent else {}
check("SMC unchanged: stop still shifted with price (99.99 + 1.00 = 100.99)",
      abs(q.get("sl", 0) - 100.99) < 1e-9, f"SL={q.get('sl')}")
check("SMC unchanged: target shifted too (99.99 - 2.00 = 97.99)",
      abs(q.get("tp", 0) - 97.99) < 1e-9, f"TP={q.get('tp')}")

sent.clear()
r = ex.place_trade(symbol="BTC", action="sell", entry=99.50, sl=99.80, tp1=98.90,
                   confidence=0.5, fixed_lot=0.01, magic=234100, keep_sl=True, rr=2.0)
check("VP: price already above a sell's stop -> refused, nothing sent",
      not r.get("success") and not sent, r.get("reason", "")[:70])

print("\n" + "=" * 84)
print("LIVE RISK CHECK in the runner (ATR 1.0, sell with structural stop 101.50)")
print("=" * 84)
sig = NS(direction="sell", sl=101.50, detail={"atr": 1.0})
check("normal: price 100.40 -> 1.10 ATR of risk -> OK",
      R.live_risk_problem(sig, 100.40) is None)
w = R.live_risk_problem(sig, 101.40)
check("price ran to 101.40 -> 0.10 ATR left -> refused (spread would eat it)",
      w is not None and "toward the stop" in w, w or "")
w = R.live_risk_problem(sig, 97.00)
check("price ran to 97.00 -> 4.5 ATR -> refused (entry no longer at the level)",
      w is not None and "away from the stop" in w, w or "")
w = R.live_risk_problem(sig, 101.60)
check("price above the stop -> refused", w is not None and "beyond" in w, w or "")
bsig = NS(direction="buy", sl=98.50, detail={"atr": 1.0})
check("buy mirror: price 99.60 -> 1.10 ATR -> OK", R.live_risk_problem(bsig, 99.60) is None)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
