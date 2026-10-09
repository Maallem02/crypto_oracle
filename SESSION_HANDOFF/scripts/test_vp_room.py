"""VP value-area-width filter (min_room_r) — fake MT5 / fake signals; nothing
reaches the broker and vp_state.json is not written."""
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.volume_profile.runner as R

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


R._persist = lambda: None
R.available_symbols = lambda: {"BTC": {"min_lot": 0.01, "lot_step": 0.01, "max_lot": 10}}
R.vp_status = lambda: {"ok": True}
R.vp_state["settings"] = {"symbols": ["BTC"], "lot_sizes": {"BTC": 0.02}, "risk_cap_pct": 15.0,
                          "daily_loss_pct": 0.0, "setups": {s: True for s in R.SETUPS},
                          "max_per_symbol": 1, "same_direction": False, "news_pause": True,
                          "min_room_r": 1.0,
                          "first_per_edge": False, "max_spread_pct": 0}   # fake bars: other filters off

print("SETTING")
check("default min_room_r = 1.0", R.DEFAULT_SETTINGS["min_room_r"] == 1.0)
for bad in ("1", True, 6, -1):
    try:
        R.update_settings({"min_room_r": bad})
        check(f"{bad!r} refused", False)
    except R.SettingsError as e:
        check(f"{bad!r} refused", True, str(e))
R.update_settings({"min_room_r": 1.5})
check("1.5 accepted", R.vp_state["settings"]["min_room_r"] == 1.5)
R.update_settings({"min_room_r": 0})
check("0 (off) accepted", R.vp_state["settings"]["min_room_r"] == 0)
R.update_settings({"min_room_r": 1.0})
R.update_settings({"risk_cap_pct": 15})
check("an app without the field leaves it at 1.0", R.vp_state["settings"]["min_room_r"] == 1.0)

print("\nSCAN — value area VAL 90 / POC 100 / VAH 110")
PROF = NS(poc=100.0, vah=110.0, val=90.0, closed_inside=True)
placed, logged = [], []
R.ensure_mt5 = R._ensure_ready = R.update_outcomes = lambda: None
R.previous_session_profile = lambda s, d: ("2026-09-30", PROF)
R._levels_logged.add("BTC|2026-09-30")
R.session_bars = lambda s, n: ([1, 2, 3], [1, 1, 1])
R.is_news_blocked = lambda s: {"blocked": False}
R._log = lambda sym, status, **k: logged.append((status, k.get("reason")))
R.live_risk_problem = lambda sig, px: None
R.mt5 = NS(positions_get=lambda symbol=None: [], symbol_info_tick=lambda s: NS(ask=100.0, bid=100.0),
           POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1)
R.place_trade = lambda **k: (placed.append(k), {"success": True, "ticket": 1, "price": 100,
                                                  "sl": 0, "tp": 0, "volume": 0.02})[1]
cnt = {"t": 3_000_000}
nxt = {}


def fake_detect(*a, **k):
    cnt["t"] += 900
    return NS(**nxt, trigger_time=cnt["t"], reason="test", blocked=None, detail={"atr": 1.0},
              risk=abs(nxt["entry"] - nxt["sl"]))


R.detect = fake_detect


def scan(setup, direction, entry, sl):
    nxt.update(setup=setup, direction=direction, entry=entry, sl=sl, tp=0.0)
    placed.clear(); logged.clear()
    R.vp_scan()
    return bool(placed), (logged[-1][1] if logged else "")


took, why = scan("va_reversal", "sell", 108.0, 110.5)
check("sell 108, stop 110.5 -> far edge (VAL 90) 7.2R away -> taken", took)
took, why = scan("va_reversal", "sell", 108.0, 120.0)
check("sell 108, stop 120 -> 1.5R -> taken", took)
took, why = scan("va_reversal", "sell", 108.0, 130.0)
check("sell 108, stop 130 -> 0.82R -> SKIPPED", not took)
check("... logged with the reason", "value area too narrow: far edge 0.82R" in why, why)
took, why = scan("va_reversal", "buy", 92.0, 80.0)
check("buy 92, stop 80 -> far edge (VAH 110) 1.5R -> taken", took)
took, why = scan("va_reversal", "buy", 92.0, 70.0)
check("buy 92, stop 70 -> 0.82R -> SKIPPED", not took, why)
took, why = scan("va_reversal", "buy", 98.0, 90.0)
check("exactly 1.5R... and exactly at the limit 1.0R (buy 100, stop 90) -> taken", took)
took, why = scan("va_reversal", "buy", 100.0, 90.0)
check("buy 100, stop 90 -> 1.0R = limit -> taken (not below it)", took)
took, why = scan("va_breakout", "buy", 112.0, 90.0)
check("breakout signal is NOT filtered (no opposite edge for it)", took)
R.vp_state["settings"]["min_room_r"] = 0
took, why = scan("va_reversal", "sell", 108.0, 130.0)
check("filter OFF (0) -> the narrow 0.82R trade is taken", took)
R.vp_state["settings"]["min_room_r"] = 1.0

print("\nsame formula as the replay study")
s = NS(direction="sell", entry=108.0, risk=22.0)
check("room_to_far_edge(sell 108, risk 22) = (108-90)/22 = 0.818",
      abs(R.room_to_far_edge(s, PROF) - 18 / 22) < 1e-9)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
