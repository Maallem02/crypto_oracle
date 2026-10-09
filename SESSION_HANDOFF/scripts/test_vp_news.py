"""VP news pause â€” fake MT5, fake calendar; nothing reaches the broker and
vp_state.json is not written."""
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
                          "first_per_edge": False, "max_spread_pct": 0}   # fake bars: other filters off

print("SETTING")
check("default news_pause is ON", R.DEFAULT_SETTINGS["news_pause"] is True)
try:
    R.update_settings({"news_pause": "yes"})
    check("text refused", False)
except R.SettingsError as e:
    check("text refused", True, str(e))
R.update_settings({"news_pause": False})
check("can switch off", R.vp_state["settings"]["news_pause"] is False)
R.update_settings({"news_pause": True})
check("can switch back on", R.vp_state["settings"]["news_pause"] is True)
R.update_settings({"risk_cap_pct": 15})
check("an app without the field leaves it on", R.vp_state["settings"]["news_pause"] is True)

print("\nSCAN")
placed, logged, asked = [], [], []
R.ensure_mt5 = R._ensure_ready = R.update_outcomes = lambda: None
R.previous_session_profile = lambda s, d: ("2026-09-29", NS(poc=1, vah=2, val=0, closed_inside=True))
R._levels_logged.add("BTC|2026-09-29")
R.session_bars = lambda s, n: ([1, 2, 3], [1, 1, 1])
cnt = {"t": 2_000_000}


def fake_detect(*a, **k):
    cnt["t"] += 900
    return NS(setup="va_reversal", direction="sell", entry=100.0, sl=101.0, tp=98.0,
              trigger_time=cnt["t"], reason="test", blocked=None, detail={"atr": 1.0}, risk=1.0)


R.detect = fake_detect
R._log = lambda sym, status, **k: logged.append((status, k.get("reason")))
R.live_risk_problem = lambda sig, px: None
R.mt5 = NS(positions_get=lambda symbol=None: [], symbol_info_tick=lambda s: NS(ask=100.0, bid=100.0),
           POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1)
R.place_trade = lambda **k: (placed.append(k), {"success": True, "ticket": 1, "price": 100,
                                                  "sl": 101, "tp": 98, "volume": 0.02})[1]
news = {"v": {"blocked": False}}


def fake_news(sym):
    asked.append(sym)
    if isinstance(news["v"], Exception):
        raise news["v"]
    return news["v"]


R.is_news_blocked = fake_news


def scan():
    placed.clear(); logged.clear(); asked.clear()
    R.vp_scan()


news["v"] = {"blocked": True, "reason": "Core PCE Price Index m/m (USD High) in 12min"}
scan()
check("High-impact event in 12 min -> NOT taken", not placed)
check("... logged as 'news pause' with the event", logged and logged[-1][0] == "rejected"
      and "news pause: Core PCE" in logged[-1][1], str(logged[-1:]))
check("calendar asked for 'BTC' (-> USD news)", asked == ["BTC"], str(asked))

scan()
check("same moment, next minute's scan: still not taken (new bar, still blocked)", not placed)

news["v"] = {"blocked": False}
scan()
check("after the window -> taken normally", len(placed) == 1)

news["v"] = {"blocked": True, "reason": "NFP (USD High) in 5min"}
R.vp_state["settings"]["news_pause"] = False
scan()
check("pause switched OFF -> taken even during news", len(placed) == 1)
check("... and the calendar is not even asked", not asked)
R.vp_state["settings"]["news_pause"] = True

news["v"] = RuntimeError("feed down")
scan()
check("calendar broken -> no pause, trade taken (fail-open, as the scalping bot)", len(placed) == 1)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)

