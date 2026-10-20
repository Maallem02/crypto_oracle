"""VP positions per symbol: settings validation, scan gate, executor gate.
Fake MT5 everywhere - nothing reaches the broker, vp_state.json is not written."""
import sys
import time
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.trading.executor as ex
import features.volume_profile.runner as R

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


R._persist = lambda: None                      # never touch the real state file
R.available_symbols = lambda: {"BTC": {"min_lot": 0.01, "lot_step": 0.01, "max_lot": 10},
                               "ETH": {"min_lot": 0.1, "lot_step": 0.1, "max_lot": 100}}
R.vp_status = lambda: {"ok": True}
R.vp_state["settings"] = {"symbols": ["BTC"], "lot_sizes": {"BTC": 0.02}, "risk_cap_pct": 15.0,
                          "daily_loss_pct": 0.0, "setups": {s: True for s in R.SETUPS},
                          "max_per_symbol": 2, "same_direction": False,
                          # this test is about positions per symbol: the other
                          # entry filters are off (they have their own tests)
                          "news_pause": False, "min_room_r": 0, "news_close_before_min": 0,
                          "first_per_edge": False, "max_spread_pct": 0}   # fake bars: other filters off

print("SETTINGS VALIDATION")
for bad, why in (({"max_per_symbol": 0}, "0"), ({"max_per_symbol": 6}, "6"),
                 ({"max_per_symbol": 2.5}, "2.5"), ({"max_per_symbol": "2"}, "text"),
                 ({"max_per_symbol": True}, "true"), ({"same_direction": "yes"}, "same_direction text")):
    try:
        R.update_settings(bad)
        check(f"refuses max/same_direction = {why}", False)
    except R.SettingsError as e:
        check(f"refuses {why}", True, str(e))
R.update_settings({"max_per_symbol": 3, "same_direction": True})
check("accepts max 3 + same direction", R.vp_state["settings"]["max_per_symbol"] == 3
      and R.vp_state["settings"]["same_direction"] is True)
R.update_settings({"max_per_symbol": 2.0, "same_direction": False})
check("accepts 2.0 as 2", R.vp_state["settings"]["max_per_symbol"] == 2)
R.update_settings({"risk_cap_pct": 20})
check("an app without the new fields leaves them unchanged",
      R.vp_state["settings"]["max_per_symbol"] == 2 and R.vp_state["settings"]["same_direction"] is False)

# â”€â”€ scan gate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\nSCAN GATE (signal: BTC buy)")
positions, placed, logged = [], [], []
R.ensure_mt5 = R._ensure_ready = R.update_outcomes = lambda: None
R.previous_session_profile = lambda s, d: ("2026-09-24", NS(poc=1, vah=2, val=0, closed_inside=True))
R._levels_logged.add("BTC|2026-09-24")
R.session_bars = lambda s, n: ([1, 2, 3], [1, 1, 1])
sig_dir = {"d": "buy"}
counter = {"t": 1_000_000}


def fake_detect(*a, **k):
    counter["t"] += 900                       # a new bar each scan -> never deduped
    return NS(setup="va_reversal", direction=sig_dir["d"], entry=100.0, sl=99.0, tp=102.0,
              trigger_time=counter["t"], reason="test", blocked=None, detail={"atr": 1.0}, risk=1.0)


R.detect = fake_detect
R._log = lambda sym, status, **k: logged.append((status, k.get("reason")))
R.live_risk_problem = lambda sig, px: None
R.mt5 = NS(positions_get=lambda symbol=None: list(positions),
           symbol_info_tick=lambda s: NS(ask=100.0, bid=100.0),
           POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1)


def fake_place(**k):
    placed.append(k)
    return {"success": True, "ticket": 1, "price": 100, "sl": 99, "tp": 102, "volume": 0.02}


R.place_trade = fake_place


def scan(direction, open_types, max_pos, same):
    positions[:] = [NS(magic=234100, type=t) for t in open_types] + [NS(magic=234000, type=0),
                                                                    NS(magic=0, type=0)]
    R.vp_state["settings"].update(max_per_symbol=max_pos, same_direction=same)
    sig_dir["d"] = direction
    placed.clear(); logged.clear()
    R.vp_scan()
    return bool(placed), (logged[-1][1] if logged else "")


cases = [
    ("max 1, nothing open            -> BUY taken", "buy", [], 1, False, True),
    ("max 1, a VP sell open          -> BUY refused", "buy", [1], 1, False, False),
    ("max 2, a VP sell open          -> BUY taken (1 buy + 1 sell)", "buy", [1], 2, False, True),
    ("max 2, a VP buy open, no stack -> BUY refused", "buy", [0], 2, False, False),
    ("max 2, a VP buy open, stacking -> BUY taken", "buy", [0], 2, True, True),
    ("max 2, buy + sell open         -> SELL refused (full)", "sell", [0, 1], 2, True, False),
    ("max 3, 2 buys open, stacking   -> BUY taken", "buy", [0, 0], 3, True, True),
    ("max 3, 3 open                  -> refused", "buy", [0, 0, 1], 3, True, False),
]
for label, d, open_types, mp, same, want in cases:
    took, reason = scan(d, open_types, mp, same)
    check(label + "   (SMC + manual BTC positions present, ignored)", took == want,
          "" if took else reason)
took, _ = scan("buy", [1], 2, False)
check("the executor receives max_per_symbol=2", placed and placed[-1].get("max_per_symbol") == 2)

# â”€â”€ executor gate (last line of defence) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
print("\nEXECUTOR GATE")
ex_positions, sent = [], []


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL = 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009
    positions_get = staticmethod(lambda symbol=None, ticket=None: list(ex_positions))
    orders_get = staticmethod(lambda symbol=None: [])
    symbol_select = staticmethod(lambda s, f: True)
    symbol_info = staticmethod(lambda s: NS(point=0.01, trade_contract_size=1.0, trade_tick_size=0.01,
                                            trade_tick_value=0.01, volume_min=0.01, volume_max=100.0,
                                            volume_step=0.01, trade_stops_level=0, filling_mode=2))
    symbol_info_tick = staticmethod(lambda s: NS(ask=100.0, bid=99.99, time=int(time.time())))
    account_info = staticmethod(lambda: NS(balance=1000.0, equity=1000.0))
    last_error = staticmethod(lambda: (0, ""))

    @staticmethod
    def order_send(req):
        sent.append(req)
        return NS(retcode=10009, order=1, comment="done")


ex.mt5 = FakeMT5()
ex._mt5_init = lambda: None
ex.can_trade = lambda n: True


def try_ex(magic, open_magics, mp, open_type=1):
    ex_positions[:] = [NS(magic=m, type=open_type, symbol="BTCUSDm", ticket=50 + k) for k, m in enumerate(open_magics)]
    sent.clear()
    r = ex.place_trade(symbol="BTC", action="buy", entry=100.0, sl=99.0, tp1=102.0,
                       confidence=0.5, fixed_lot=0.01, magic=magic, keep_sl=(magic == 234100),
                       rr=2.0, max_per_symbol=mp)
    return bool(r.get("success")), r.get("reason", "")


ok, why = try_ex(234100, [234100], 1)
check("VP, max 1, 1 VP open -> refused", not ok, why)
ok, why = try_ex(234100, [234100], 2)
check("VP, max 2, 1 VP open -> sent", ok, why)
ok, why = try_ex(234100, [234100, 234100], 2)
check("VP, max 2, 2 VP open -> refused", not ok, why)
ok, why = try_ex(234100, [234000, 0], 1, open_type=0)
check("VP ignores SMC + manual positions in its count (same direction)", ok, why)
ok, why = try_ex(234000, [234000], 5)
check("SMC stays at 1 per symbol even if max_per_symbol is passed", not ok, why)
ok, why = try_ex(234000, [234100, 234100], 1, open_type=0)
check("SMC still ignores VP positions in its count (same direction)", ok, why)
# 2026-10-13 : jamais de sens opposé ENTRE bots (test_opposite_block.py)
ok, why = try_ex(234100, [234000], 1, open_type=1)
check("VP BUY while Scalping is SHORT BTC -> refused (no opposite positions across bots)",
      not ok and "opposite position open" in why, why)
ok, why = try_ex(234000, [234100], 1, open_type=1)
check("Scalping BUY while VP is SHORT BTC -> refused", not ok and "opposite position open" in why, why)
ok, why = try_ex(234100, [0], 1, open_type=1)
check("a manual SHORT does not block VP", ok, why)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)

