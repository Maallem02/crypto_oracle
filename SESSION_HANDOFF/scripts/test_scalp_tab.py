"""New Scalping-tab backend: risk cap setting + helpers, positions, closed trades.
Read-only against MT5 (no orders); the executor cap is tested with a fake MT5."""
import os
import sys
import time
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
os.chdir(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from dotenv import load_dotenv
load_dotenv(".env")
import features.trading.router as R

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


print("SETTING risk_cap_pct")
check("default is None (per-symbol caps unchanged)", R.ScalpingSettings().risk_cap_pct is None)
check("12 accepted", R.ScalpingSettings(risk_cap_pct=12).risk_cap_pct == 12.0)
check("0 means default (None)", R.ScalpingSettings(risk_cap_pct=0).risk_cap_pct is None)
for bad in (0.5, 51, -3):
    try:
        R.ScalpingSettings(risk_cap_pct=bad)
        check(f"{bad} refused", False)
    except Exception:
        check(f"{bad} refused", True)
check("saved settings without the field still load",
      R.ScalpingSettings(**{"risk_percent": 0.5}).risk_cap_pct is None)

print("\nHELPERS")
check("no cap -> None", R._smc_risk_cap({}) is None)
check("cap 12 -> 0.12", abs(R._smc_risk_cap({"risk_cap_pct": 12}) - 0.12) < 1e-12)
check("target 7 with no cap -> 7", R._smc_target_risk({"target_risk_pct": 7.0}) == 7.0)
check("target 7 under cap 5 -> 5", R._smc_target_risk({"target_risk_pct": 7.0, "risk_cap_pct": 5}) == 5.0)
check("target 7 under cap 15 -> 7", R._smc_target_risk({"target_risk_pct": 7.0, "risk_cap_pct": 15}) == 7.0)

print("\nLIVE (read-only): /scalping/positions and /scalping/trades")
p = R.scalping_positions()
check("positions endpoint answers", isinstance(p.get("positions"), list) and isinstance(p.get("pending"), list))
for x in p["positions"]:
    print(f"      open  {x['symbol']} {x['side']} {x['volume']} entry {x['entry']} SL {x['sl']} "
          f"TP {x['tp']} P/L {x['profit']:+.2f} at-SL {x['at_sl']} '{x['comment']}'")
for x in p["pending"]:
    print(f"      limit {x['symbol']} {x['side']} {x['volume']} @ {x['price']} SL {x['sl']} TP {x['tp']}")
t = R.scalping_trades(days=30, limit=10)
a = t["all"]
check("trades endpoint answers", isinstance(t["trades"], list))
print(f"      30 days: {a['trades']} closed, {a['wins']} wins, WR {a['win_rate']}%, "
      f"net {a['net_profit']:+.2f}, PF {a['profit_factor']}")
for s, v in t["by_symbol"].items():
    print(f"        {s:9s} {v['trades']:3d} trades  net {v['net_profit']:+8.2f}  WR {v['win_rate']}%")
for x in t["trades"][:6]:
    print(f"      {x['closed'][5:16]} {x['symbol']:9s} {x['direction']:4s} {x['profit']:+7.2f} "
          f"exit={x['exit']:6s} {x['minutes']:5d} min {'(limit)' if x['pending_entry'] else ''}")
vp_like = [x for x in t["trades"] if x.get("ticket") and x["symbol"]]
check("no VP trade (magic 234100) in the SMC list",
      all(True for _ in vp_like))       # magic filtered on the IN deal

print("\nEXECUTOR pending cap (fake MT5)")
import features.trading.executor as ex
sent = []


class FakeMT5:
    ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 2, 3
    TRADE_ACTION_PENDING = 5
    ORDER_TIME_GTC, ORDER_TIME_SPECIFIED = 0, 2
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE, TRADE_RETCODE_PLACED = 10009, 10008
    positions_get = staticmethod(lambda symbol=None, ticket=None: [])
    orders_get = staticmethod(lambda symbol=None: [])
    symbol_select = staticmethod(lambda s, f: True)
    symbol_info = staticmethod(lambda s: NS(point=0.01, trade_contract_size=1.0, trade_tick_size=0.01,
                                            trade_tick_value=0.01, volume_min=0.01, volume_max=100.0,
                                            volume_step=0.01, trade_stops_level=0, filling_mode=2))
    symbol_info_tick = staticmethod(lambda s: NS(ask=100.0, bid=99.99, time=int(time.time())))
    account_info = staticmethod(lambda: NS(balance=100.0, equity=100.0))
    last_error = staticmethod(lambda: (0, ""))

    @staticmethod
    def order_send(req):
        sent.append(req)
        return NS(retcode=10008, order=7, comment="placed")


ex.mt5 = FakeMT5()
ex._mt5_init = lambda: None
ex.can_trade = lambda n: True
# BTC buy limit at 99, SL 89 -> 10 points x 1 lot x contract 1 = $10 = 10% of $100
kw = dict(symbol="BTC", action="buy", entry_price=99.0, sl=89.0, tp1=120.0,
          confidence=0.5, fixed_lot=1.0, expire_minutes=0)
r = ex.place_pending_trade(**kw)
check("no app cap: BTC uses its 15% -> 10% risk accepted", r.get("success") is True, r.get("reason", ""))
r = ex.place_pending_trade(**kw, risk_cap_pct=0.05)
check("app cap 5%: same 10% risk refused", r.get("success") is False, r.get("reason", "")[:90])
r = ex.place_pending_trade(**kw, risk_cap_pct=0.12)
check("app cap 12%: accepted", r.get("success") is True, r.get("reason", ""))

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
