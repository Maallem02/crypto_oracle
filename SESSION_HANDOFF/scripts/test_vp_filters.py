"""VP spread filter (max_spread_pct) + first signal per edge (first_per_edge).
Parts 1-3: fake MT5 / synthetic bars, nothing reaches the broker and
vp_state.json is not written. Part 4: READ-ONLY real MT5 history, checks that
the live edge count matches the replay study (vp_trend_rows.json n_edge)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.volume_profile.runner as R
from features.volume_profile.strategy import detect

UTC = timezone.utc
fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


REAL = dict(mt5=R.mt5, ensure_mt5=R.ensure_mt5, session_bars=R.session_bars,
            previous_session_profile=R.previous_session_profile, earlier_on_edge=R.earlier_on_edge)
R._persist = lambda: None
R.available_symbols = lambda: {"BTC": {"min_lot": 0.01, "lot_step": 0.01, "max_lot": 10}}
R.vp_status = lambda: {"ok": True}
BASE = {"symbols": ["BTC"], "lot_sizes": {"BTC": 0.02}, "risk_cap_pct": 15.0, "daily_loss_pct": 0.0,
        "setups": {"poc_bounce": False, "va_reversal": True, "va_breakout": False},
        "max_per_symbol": 1, "same_direction": False, "news_pause": True, "min_room_r": 0.0,
        "news_close_before_min": 0, "max_spread_pct": 10.0, "first_per_edge": True}
R.vp_state["settings"] = R._copy(BASE)

print("1. SETTINGS")
check("default max_spread_pct = 10", R.DEFAULT_SETTINGS["max_spread_pct"] == 10.0)
check("default first_per_edge = on", R.DEFAULT_SETTINGS["first_per_edge"] is True)
for bad in ("10", True, 51, -1):
    try:
        R.update_settings({"max_spread_pct": bad})
        check(f"max_spread_pct {bad!r} refused", False)
    except R.SettingsError as e:
        check(f"max_spread_pct {bad!r} refused", True, str(e))
for bad in ("yes", 1, None):
    try:
        R.update_settings({"first_per_edge": bad})
        check(f"first_per_edge {bad!r} refused", False)
    except R.SettingsError as e:
        check(f"first_per_edge {bad!r} refused", True, str(e))
R.update_settings({"max_spread_pct": 12.5, "first_per_edge": False})
check("12.5 / false accepted", R.vp_state["settings"]["max_spread_pct"] == 12.5
      and R.vp_state["settings"]["first_per_edge"] is False)
R.update_settings({"max_spread_pct": 0})
check("0 (off) accepted", R.vp_state["settings"]["max_spread_pct"] == 0)
try:
    R.update_settings({"max_spread_pct": 10, "first_per_edge": "x"})
except R.SettingsError:
    pass
check("all-or-nothing: a bad field leaves the good one unsaved", R.vp_state["settings"]["max_spread_pct"] == 0)
R.vp_state["settings"] = R._copy(BASE)
R.update_settings({"risk_cap_pct": 15})
check("an app without the new fields leaves them unchanged",
      R.vp_state["settings"]["max_spread_pct"] == 10.0 and R.vp_state["settings"]["first_per_edge"] is True)
saved = {k: v for k, v in BASE.items() if k not in ("max_spread_pct", "first_per_edge")}
tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vp_state_filters_test.json")
json.dump({"enabled": True, "settings": saved}, open(tmp, "w"))
old_file, R._STATE_FILE = R._STATE_FILE, tmp
_, loaded = R._load_state()
R._STATE_FILE = old_file
os.remove(tmp)
check("an existing vp_state.json without the fields loads them ON (10% / true)",
      loaded["max_spread_pct"] == 10.0 and loaded["first_per_edge"] is True)

print("\n2. SPREAD FILTER (stop distance at the live price)")
sig = lambda d, sl: NS(direction=d, sl=sl)
check("buy, ask 100.1 bid 100.0, SL 99.0 -> 0.1 / 1.1 = 9.1%",
      abs(R.spread_share_pct(sig("buy", 99.0), NS(ask=100.1, bid=100.0)) - 100 * 0.1 / 1.1) < 1e-9)
check("sell, ask 100.1 bid 100.0, SL 101.0 -> 0.1 / 1.0 = 10%",
      abs(R.spread_share_pct(sig("sell", 101.0), NS(ask=100.1, bid=100.0)) - 10.0) < 1e-9)
check("price already past the stop -> None",
      R.spread_share_pct(sig("buy", 100.5), NS(ask=100.1, bid=100.0)) is None)

PROF = NS(poc=100.0, vah=110.0, val=90.0, closed_inside=True)
placed, logged = [], []
tick = {"ask": 100.1, "bid": 100.0}
R.ensure_mt5 = R._ensure_ready = R.update_outcomes = lambda: None
R.previous_session_profile = lambda s, d: ("2026-10-07", PROF)
R._levels_logged.add("BTC|2026-10-07")
R.is_news_blocked = lambda s: {"blocked": False}
R._log = lambda sym, status, **k: logged.append((status, k.get("reason")))
R.mt5 = NS(positions_get=lambda symbol=None: [], symbol_info_tick=lambda s: NS(**tick),
           POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1)
R.place_trade = lambda **k: (placed.append(k), {"success": True, "ticket": 1, "price": 100,
                                                  "sl": 0, "tp": 0, "volume": 0.02})[1]
cnt = {"t": 3_000_000}
nxt = {}
real_detect = R.detect


def fake_detect(*a, **k):
    cnt["t"] += 900
    return NS(**nxt, trigger_time=cnt["t"], reason="test", blocked=None, detail={"atr": 1.0},
              risk=abs(nxt["entry"] - nxt["sl"]))


R.detect = fake_detect
R.session_bars = lambda s, n: ([1, 2, 3], [1, 1, 1])
R.earlier_on_edge = lambda b, a, p, s: 0          # part 2 tests the spread rule only


def scan(direction, sl, setup="va_reversal"):
    nxt.update(setup=setup, direction=direction, entry=100.0, sl=sl, tp=0.0)
    placed.clear(); logged.clear()
    R.vp_scan()
    return bool(placed), (logged[-1][1] if logged else "")


took, why = scan("buy", 99.0)
check("buy, spread 9.1% of the stop -> taken", took, why)
took, why = scan("buy", 99.2)
check("buy, spread 11.1% -> SKIPPED", not took)
check("... logged with the reason", "spread too large for this stop" in why and "11.1%" in why, why)
took, why = scan("sell", 101.0)
check("sell, exactly 10% -> SKIPPED (the limit itself is too large)", not took, why)
took, why = scan("sell", 101.2)
check("sell, 0.1 / 1.2 = 8.3% -> taken", took, why)
took, why = scan("buy", 99.2, setup="va_breakout")
check("applies to every setup (a breakout with 11.1% is skipped too)", not took, why)
R.vp_state["settings"]["max_spread_pct"] = 15.0
took, why = scan("buy", 99.2)
check("limit raised to 15% -> the 11.1% trade is taken", took, why)
R.vp_state["settings"]["max_spread_pct"] = 0
tick.update(ask=100.5)
took, why = scan("buy", 99.0)
check("filter OFF (0) -> taken even with spread 0.5 / 1.5 = 33%", took, why)
tick.update(ask=100.1)
R.vp_state["settings"]["max_spread_pct"] = 10.0

print("\n3. FIRST SIGNAL PER EDGE (real detector on synthetic M15 bars, VAL 90 / VAH 110)")
R.earlier_on_edge = REAL["earlier_on_edge"]
closes = [95, 89, 92, 93, 88, 91, 105, 112, 108, 87, 92]
T0 = 1_790_000_000 - 1_790_000_000 % 86400
dt = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
               ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
bars = np.zeros(len(closes), dtype=dt)
prev = 96.0
for i, c in enumerate(closes):
    bars[i] = (T0 + 900 * i, prev, max(prev, c) + 0.5, min(prev, c) - 0.5, c, 100, 1, 0)
    prev = c
atr = np.full(len(closes), 3.0)
sigs = {}
for k in range(2, len(bars) + 1):
    s = R.detect_va_reversal(bars[:k], atr[:k], PROF)
    if s is not None and not s.blocked:
        sigs[k - 1] = s
check("detector fires: buy @bar2, buy @bar5, sell @bar8, buy @bar10",
      [(i, s.direction) for i, s in sorted(sigs.items())] == [(2, "buy"), (5, "buy"), (8, "sell"), (10, "buy")],
      str([(i, s.direction) for i, s in sorted(sigs.items())]))
exp = {2: 0, 5: 1, 8: 0, 10: 2}
for i, s in sorted(sigs.items()):
    n = R.earlier_on_edge(bars[:i + 1], atr[:i + 1], PROF, s)
    check(f"bar {i:2d} {s.direction}: earlier signals on this edge = {exp[i]}", n == exp[i], f"got {n}")

R.detect = real_detect
upto = {"n": 0}
R.session_bars = lambda s, n: (bars[:upto["n"]], atr[:upto["n"]])


def scan_upto(n):
    upto["n"] = n
    placed.clear(); logged.clear()
    R.vp_scan()
    return bool(placed), (logged[-1][1] if logged else "")


tick.update(ask=92.0, bid=92.0)                       # no spread: part 3 isolates the edge rule
R._handled.clear()
took, why = scan_upto(3)
check("1st buy at VAL (bar 2) -> taken", took, why)
took, why = scan_upto(6)
check("2nd buy at VAL (bar 5) -> SKIPPED", not took)
check("... logged as '#2 buy at VAL'", "#2 buy at VAL (first-per-edge filter)" in why, why)
tick.update(ask=108.0, bid=108.0)
took, why = scan_upto(9)
check("1st sell at VAH (bar 8) -> taken (the other edge has its own count)", took, why)
R.vp_state["settings"]["first_per_edge"] = False
R._handled.clear()
tick.update(ask=91.0, bid=91.0)
took, why = scan_upto(6)
check("switch OFF -> the 2nd buy at VAL is taken", took, why)
R.vp_state["settings"]["first_per_edge"] = True

print("\n4. REAL HISTORY (read-only): live edge count == replay study n_edge")
for k, v in REAL.items():
    setattr(R, k, v)
rows = json.load(open(r"C:\Users\MSI\AppData\Local\Temp\claude\C--Users-MSI-Desktop-loumi-crypt"
                      r"\b8590a57-fc16-47b4-84f6-bc4648c42317\scratchpad\vp_trend_rows.json"))
R.ensure_mt5()
since = (datetime.now(UTC) - timedelta(days=21)).timestamp()
rows = [r for r in rows if r["t"] >= since and r["sym"] in ("BTCUSD", "ETHUSD", "XAUUSD", "USDJPY")]
agree = total = 0
bad = []
for r in rows:
    mt5sym = r["sym"] + "m"
    trig = r["t"] - 900
    end = datetime.fromtimestamp(r["t"], UTC)
    b, a = R.session_bars(mt5sym, end)                 # same call as the live scan at that moment
    _, prof = R.previous_session_profile(mt5sym, end.date())
    if b is None or prof is None or not len(b) or int(b["time"][-1]) != trig:
        continue
    s = detect(b, a, prof, enabled={"poc_bounce": False, "va_breakout": False})
    if s is None or s.direction != r["dir"]:
        continue
    total += 1
    n = R.earlier_on_edge(b, a, prof, s)
    if n == r["n_edge"] - 1:
        agree += 1
    else:
        bad.append(f"{r['sym']} {r['dir']} {end:%d/%m %H:%M} live {n + 1} study {r['n_edge']}")
check(f"{agree}/{total} signals of the last 21 days give the same rank as the study",
      total > 20 and agree == total, "; ".join(bad[:5]))

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
