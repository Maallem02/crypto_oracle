"""News windows (18 min after HIGH) + VP close-before-news. Fake calendar,
fake MT5 — nothing reaches the broker, vp_state.json is not written."""
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.news.calendar as C
import features.volume_profile.runner as R

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


def ev(minutes_from_now, impact="High", country="USD", title="NFP"):
    t = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    return {"title": title, "country": country, "impact": impact, "date": t.isoformat()}


def with_events(*evs):
    C._fetch = lambda: list(evs)


print("PAUSE WINDOWS (both bots)")
check("HIGH_AFTER_MIN is 18", C.HIGH_AFTER_MIN == 18)
with_events(ev(-15)); check("High 15 min ago -> still paused (18 min after)", C.is_news_blocked("BTC")["blocked"])
with_events(ev(-17)); check("High 17 min ago -> still paused", C.is_news_blocked("BTC")["blocked"])
with_events(ev(-19)); check("High 19 min ago -> free again", not C.is_news_blocked("BTC")["blocked"])
with_events(ev(+14)); check("High in 14 min -> paused (15 min before)", C.is_news_blocked("BTC")["blocked"])
with_events(ev(+16)); check("High in 16 min -> not yet", not C.is_news_blocked("BTC")["blocked"])
with_events(ev(-4, "Medium")); check("Medium 4 min ago -> paused (5 after)", C.is_news_blocked("BTC")["blocked"])
with_events(ev(-6, "Medium")); check("Medium 6 min ago -> free (medium unchanged)", not C.is_news_blocked("BTC")["blocked"])
with_events(ev(-5, country="EUR")); check("EUR High -> BTC not paused (USD only)", not C.is_news_blocked("BTC")["blocked"])
with_events(ev(-5, country="JPY")); check("JPY High -> USDJPY paused", C.is_news_blocked("USDJPY")["blocked"])

print("\nupcoming_high_event")
with_events(ev(+10), ev(+40))
e = C.upcoming_high_event("BTC", 15)
check("High in 10 min found within 15", e is not None and 9 <= e["minutes"] <= 10.5, str(e))
with_events(ev(+20)); check("High in 20 min -> not within 15", C.upcoming_high_event("BTC", 15) is None)
with_events(ev(-1)); check("already released -> None (closing after the spike is pointless)",
                           C.upcoming_high_event("BTC", 15) is None)
with_events(ev(+5, "Medium")); check("Medium event -> None (High only)", C.upcoming_high_event("BTC", 15) is None)
with_events(ev(+5, country="JPY"))
check("JPY High -> USDJPY yes, BTC no", C.upcoming_high_event("USDJPY", 15) is not None
      and C.upcoming_high_event("BTC", 15) is None)
check("within 0 -> None", C.upcoming_high_event("BTC", 0) is None)

print("\nSETTING")
R._persist = lambda: None
R.available_symbols = lambda: {"BTC": {"min_lot": 0.01, "lot_step": 0.01, "max_lot": 10}}
R.vp_status = lambda: {"ok": True}
R.vp_state["settings"] = {"symbols": ["BTC"], "lot_sizes": {"BTC": 0.02}, "risk_cap_pct": 15.0,
                          "daily_loss_pct": 0.0, "setups": {s: True for s in R.SETUPS},
                          "max_per_symbol": 1, "same_direction": False, "news_pause": True,
                          "min_room_r": 1.0, "news_close_before_min": 15}
check("default 15 min", R.DEFAULT_SETTINGS["news_close_before_min"] == 15)
for bad in ("15", True, 61, -1, 7.5):
    try:
        R.update_settings({"news_close_before_min": bad})
        check(f"{bad!r} refused", False)
    except R.SettingsError as e2:
        check(f"{bad!r} refused", True, str(e2))
R.update_settings({"news_close_before_min": 0}); check("0 (off) accepted", R.vp_state["settings"]["news_close_before_min"] == 0)
R.update_settings({"news_close_before_min": 15})
R.update_settings({"risk_cap_pct": 15})
check("an app without the field leaves it at 15", R.vp_state["settings"]["news_close_before_min"] == 15)

print("\nCLOSE BEFORE NEWS")
positions = [NS(ticket=1, symbol="BTCUSDm", magic=234100, volume=0.02, profit=3.2, type=0),
             NS(ticket=2, symbol="XAUUSDm", magic=234100, volume=0.01, profit=-1.0, type=1),
             NS(ticket=3, symbol="BTCUSDm", magic=234000, volume=0.02, profit=1.0, type=0),
             NS(ticket=4, symbol="ETHUSDm", magic=0, volume=0.5, profit=0.0, type=0)]
R.mt5 = NS(positions_get=lambda symbol=None: list(positions))
closes = []
R.close_partial = lambda p, v, c: (closes.append((p.ticket, v, c)), {"success": True})[1]
R.upcoming_high_event = lambda sym, m: {"title": "NFP", "currency": "USD", "minutes": 12.0}
got = R.close_before_news(15)
check("both VP trades closed, full volume", sorted(got) == [1, 2] and (1, 0.02, "VP news close") in closes,
      str(closes))
check("scalping (234000) and manual trades NOT touched", all(c[0] in (1, 2) for c in closes))
closes.clear()
R.upcoming_high_event = lambda sym, m: None
check("no event coming -> nothing closed", R.close_before_news(15) == [] and not closes)


def boom(sym, m):
    raise RuntimeError("feed down")


R.upcoming_high_event = boom
check("calendar broken -> nothing closed (no blind closing)", R.close_before_news(15) == [] and not closes)

print("\nVP SCAN calls it only when switched on")
called = []
R.close_before_news = lambda m: called.append(m) or []
R.ensure_mt5 = R._ensure_ready = R.update_outcomes = lambda: None
R.vp_state["settings"]["symbols"] = []                 # no signal work, only the protection
R.vp_scan()
check("setting 15 -> called with 15", called == [15], str(called))
called.clear()
R.vp_state["settings"]["news_close_before_min"] = 0
R.vp_scan()
check("setting 0 -> not called", called == [])

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
