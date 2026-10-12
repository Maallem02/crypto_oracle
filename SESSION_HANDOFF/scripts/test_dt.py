"""Daily Trend bot (features/daily_trend/runner.py).
Parts 1-4: fake MT5 / synthetic bars; nothing reaches the broker, the real
dt_state.json and the database are not touched.
Part 5: READ-ONLY Exness daily history: the live decision code, run day by day
with a simulated broker, must reproduce the EXACT trades of the tested study
code (trend_robustness_study.donchian, long-only, next-day-open entry)."""
import ast
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import numpy as np

BK = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
sys.path.insert(0, BK)
import features.daily_trend.runner as R

UTC = timezone.utc
fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


REAL_MT5, REAL_ENSURE = R.mt5, R.ensure_mt5
R._persist = lambda: None
R.dt_status = lambda: {"ok": True}
DT = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
               ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
DAY0 = 1_780_000_000 - 1_780_000_000 % 86400


def make_bars(closes, rng_width=1.0):
    b = np.zeros(len(closes), dtype=DT)
    prev = closes[0]
    for i, c in enumerate(closes):
        b[i] = (DAY0 + 86400 * i, prev, max(prev, c) + rng_width, min(prev, c) - rng_width, c, 100, 0, 0)
        prev = c
    return b


print("1. SETTINGS")
R.dt_state["settings"] = dict(R.DEFAULT_SETTINGS)
check("default: BTC + ETH, 2% risk, 3% shared crypto cap, bot OFF on first start",
      R.DEFAULT_SETTINGS == {"symbols": ["BTC", "ETH"], "risk_pct": 2.0, "max_crypto_risk_pct": 3.0}
      and R._load_state.__defaults__ is None)
for bad, lab in ((0.05, "0.05%"), (6, "6%"), ("2", "'2' (text)"), (True, "true")):
    try:
        R.update_settings({"risk_pct": bad})
        check(f"risk {lab} refused", False)
    except R.SettingsError as e:
        check(f"risk {lab} refused", True, str(e))
R.update_settings({"risk_pct": 1.5})
check("risk 1.5% accepted", R.dt_state["settings"]["risk_pct"] == 1.5)
R.update_settings({"risk_pct": 2.25})
check("risk 2.25% accepted (manual value)", R.dt_state["settings"]["risk_pct"] == 2.25)
try:
    R.update_settings({"symbols": ["BTC", "XAUUSD"]})
    check("an untested symbol (gold) refused", False)
except R.SettingsError as e:
    check("an untested symbol (gold) refused", True, str(e))
try:
    R.update_settings({"risk_pct": 3, "symbols": "BTC"})
except R.SettingsError:
    pass
check("all-or-nothing: bad symbols leave the risk unchanged", R.dt_state["settings"]["risk_pct"] == 2.25)
R.update_settings({"symbols": ["ETH"]})
check("symbols = ETH only accepted", R.dt_state["settings"]["symbols"] == ["ETH"])
for bad, lab in ((0.05, "0.05%"), (11, "11%"), ("3", "'3' (text)"), (True, "true")):
    try:
        R.update_settings({"max_crypto_risk_pct": bad})
        check(f"crypto cap {lab} refused", False)
    except R.SettingsError as e:
        check(f"crypto cap {lab} refused", True, str(e))
R.update_settings({"max_crypto_risk_pct": 4.5})
check("crypto cap 4.5% accepted", R.dt_state["settings"]["max_crypto_risk_pct"] == 4.5)
try:
    R.update_settings({"max_crypto_risk_pct": 2, "risk_pct": 9})
except R.SettingsError:
    pass
check("all-or-nothing: a bad risk leaves the cap unchanged", R.dt_state["settings"]["max_crypto_risk_pct"] == 4.5)
real_file, tmp_dir = R._STATE_FILE, tempfile.mkdtemp()
R._STATE_FILE = os.path.join(tmp_dir, "dt_state.json")
with open(R._STATE_FILE, "w", encoding="utf-8") as fh:
    json.dump({"enabled": True, "settings": {"symbols": ["BTC", "ETH"], "risk_pct": 5.0}}, fh)
old = R._load_state()
R._STATE_FILE = real_file
check("a state file saved before the cap existed: keeps its 5% risk, gets the 3% cap",
      old["settings"]["risk_pct"] == 5.0 and old["settings"]["max_crypto_risk_pct"] == 3.0, str(old["settings"]))
R.dt_state["settings"] = dict(R.DEFAULT_SETTINGS)

print("\n2. POSITION SIZE (risk_pct of the balance if the stop is hit)")
check("$3,000, 2%, BTC risk $5,000/lot -> 0.01 lot ($50 <= $60)",
      R.size_trade(3000, 2, 5000, 0.01, 0.01, 200) == 0.01)
check("$3,000, 2%, ETH risk $200/lot -> 0.30 lot ($60)", abs(R.size_trade(3000, 2, 200, 0.1, 0.01, 2000) - 0.30) < 1e-9)
check("$40, 2%, BTC -> refused (min lot would risk 125%)", R.size_trade(40, 2, 5000, 0.01, 0.01, 200) is None)
check("$2,000, 2%, BTC $5,000/lot -> refused ($50 > $40), never forced to the min lot",
      R.size_trade(2000, 2, 5000, 0.01, 0.01, 200) is None)
check("lot rounded DOWN to the broker step (never more risk)", R.size_trade(10000, 2, 230, 0.1, 0.01, 2000) == 0.86)

print("\n3. DECISION ON A DAILY CANDLE")
flat = [100.0] * 60                                           # >= 50 candles: the scan needs a warmed-up ATR
up = flat + [101, 102, 103, 112]                              # last close breaks the 20-day high
b = make_bars(up)
d = R.decide(b)
check("flat + close above the 20-day high -> ENTER", d["action"] == "enter", d["why"])
check("stop distance = 2 x ATR of that day", abs(d["stop_dist"] - 2 * R.atr_series(b)[-1]) < 1e-9)
check("same candle, but a position was stopped during it -> no entry", R.decide(b, closed_during_bar=True)["action"] == "none")
check("close below the 20-day high -> nothing", R.decide(make_bars(flat + [100.5]))["action"] == "none")
down = flat + [99, 98, 90]
bd = make_bars(down)
check("position from before + close below the 10-day low -> EXIT", R.decide(bd, position_open_time=int(bd["time"][-3]))["action"] == "exit")
check("position opened DURING this candle -> hold (only the stop applies on the entry day)",
      R.decide(bd, position_open_time=int(bd["time"][-1]) + 60)["action"] == "hold")
check("position + close above the 10-day low -> hold", R.decide(b, position_open_time=int(b["time"][-5]))["action"] == "hold")

print("\n4. THE SCAN WITH A FAKE BROKER")
sent, logged, closed = [], [], []
STATE = {"positions": [], "deals": [], "balance": 3000.0, "bars": b}
NOW = datetime.fromtimestamp(int(b["time"][-1]) + 86400 + 300, UTC)       # 00:05 after the last candle


class FakeMT5:
    TIMEFRAME_D1, ORDER_TYPE_BUY, TRADE_ACTION_DEAL, ORDER_TIME_GTC = 16408, 0, 1, 0
    ORDER_FILLING_IOC, ORDER_FILLING_FOK, ORDER_FILLING_RETURN, TRADE_RETCODE_DONE = 1, 0, 2, 10009

    def copy_rates_from_pos(self, sym, tf, a, n):
        return STATE.get("bars_by", {}).get(sym, STATE["bars"])

    def positions_get(self, symbol=None, ticket=None):
        return [p for p in STATE["positions"] if (symbol is None or p.symbol == symbol)
                and (ticket is None or p.ticket == ticket)]

    def history_deals_get(self, *a, **k):
        return STATE["deals"]

    def symbol_info(self, s):
        return NS(digits=2, volume_min=0.01 if s.startswith("BTC") else 0.1, volume_step=0.01, volume_max=200,
                  filling_mode=2, trade_stops_level=0, point=0.01)

    def symbol_info_tick(self, s):
        return NS(ask=112.0, bid=111.9)

    def account_info(self):
        return NS(balance=STATE["balance"], trade_mode=0)

    def order_calc_profit(self, t, s, vol, a, b_):
        return (b_ - a) * vol * (100 if s.startswith("BTC") else 1)   # BTC: $100/pt/lot, ETH: $1

    def order_send(self, req):
        sent.append(req)
        if STATE.get("fill"):                                         # part 4b: the order becomes a position
            STATE["positions"].append(NS(symbol=req["symbol"], magic=req["magic"], ticket=800 + len(sent),
                                         volume=req["volume"], time=int(NOW.timestamp()),
                                         price_open=req["price"], sl=req["sl"]))
        return NS(retcode=10009, order=777, price=req["price"], comment="")

    def last_error(self):
        return (0, "")


R.mt5 = FakeMT5()
R.ensure_mt5 = R._ensure_ready = R.update_outcomes = lambda: None
R._log = lambda sym, ev, **k: logged.append((sym, ev, k.get("reason")))
def fake_close(p, v, c):
    closed.append((p.symbol, v, c))
    STATE["positions"] = [x for x in STATE["positions"] if x is not p]
    return {"success": True, "price": 90}


R.close_partial = fake_close


def scan():
    sent.clear(); logged.clear(); closed.clear()
    R.dt_scan(NOW)


R.dt_state.update(enabled=True, last_bar={}, last_check={})
scan()
btc = [r for r in sent if r["symbol"] == "BTCUSDm"]
check("bot ON, breakout -> one BUY per symbol", len(sent) == 2 and all(r["type"] == 0 for r in sent))
check("orders carry magic 234200, no take-profit, stop = price - 2 ATR",
      all(r["magic"] == 234200 and r["tp"] == 0.0 for r in sent)
      and abs(sent[0]["sl"] - round(112.0 - d["stop_dist"], 2)) < 1e-9, str(sent[0]))
risk_btc = (112.0 - btc[0]["sl"]) * btc[0]["volume"] * 100
check("BTC lot sized to <= 2% of $3,000 ($60)", risk_btc <= 60 + 1e-6, f"lot {btc[0]['volume']} risk ${risk_btc:.2f}")
scan()
check("same candle scanned again (every 5 min) -> NO second order", len(sent) == 0)
R.dt_state.update(last_bar={})
STATE["balance"] = 40.0
scan()
check("$40 balance: BTC min lot risks 14% -> BTC refused, 'account too small' journalled",
      not any(r["symbol"] == "BTCUSDm" for r in sent)
      and any(s_ == "BTC" and ev == "skip" and "account too small" in (why or "") for s_, ev, why in logged),
      str(logged[:1]))
check("$40 balance: ETH is cheap with fake prices -> sized to the $0.80 budget (0.14 lot, risk $0.79)",
      [(r["symbol"], r["volume"]) for r in sent] == [("ETHUSDm", 0.14)], str([(r["symbol"], r["volume"]) for r in sent]))
STATE["balance"] = 3000.0
R.dt_state.update(enabled=False, last_bar={})
scan()
check("bot STOPPED + breakout -> no order, logged as not taken",
      not sent and any("stopped" in (why or "") for _, _, why in logged))
STATE["bars"] = bd
STATE["positions"] = [NS(symbol="BTCUSDm", magic=234200, ticket=5, volume=0.01, time=int(bd["time"][-5])),
                      NS(symbol="BTCUSDm", magic=234000, ticket=6, volume=0.02, time=0),
                      NS(symbol="ETHUSDm", magic=234100, ticket=7, volume=0.5, time=0)]
R.dt_state.update(enabled=False, last_bar={})
R.dt_state["settings"] = {"symbols": ["ETH"], "risk_pct": 2.0}
scan()
check("bot STOPPED + BTC removed from its symbols, but its BTC position hits the 10-day exit -> still closed",
      closed == [("BTCUSDm", 0.01, "DT exit 10d low")], str(closed))
check("the Scalping (234000) and VP (234100) positions are never touched", len(closed) == 1)
R.dt_state["settings"] = dict(R.DEFAULT_SETTINGS)
STATE["positions"] = []
STATE["bars"] = b
STATE["deals"] = [NS(magic=234200, symbol="BTCUSDm", entry=1, time=int(b["time"][-1]) + 3600, comment="[sl 104.5]")]
R.dt_state.update(enabled=True, last_bar={})
scan()
check("BTC stopped out during the breakout candle -> no BTC entry on it; ETH still enters",
      [r["symbol"] for r in sent] == ["ETHUSDm"], str([r["symbol"] for r in sent]))
STATE["deals"] = [NS(magic=234200, symbol="BTCUSDm", entry=1, time=int(b["time"][-1]) + 120, comment="DT exit 10d low")]
R.dt_state.update(last_bar={})
scan()
check("the bot's own 10-day exit filled after midnight does NOT block an entry on that candle",
      sorted(r["symbol"] for r in sent) == ["BTCUSDm", "ETHUSDm"])
STATE["deals"] = []

print("\n4b. SHARED CRYPTO RISK CAP (BTC + ETH open together = one bet)")
check("cap 3%, nothing open -> next trade gets the full 2%", R.risk_left(2, 3, 0) == 2)
check("cap 3%, 2% already open -> next trade gets 1%", abs(R.risk_left(2, 3, 2) - 1) < 1e-12)
check("cap 3%, 3% open -> nothing left", R.risk_left(2, 3, 3) == 0)
check("cap 4% (= today without a cap), 2% open -> full 2%", R.risk_left(2, 4, 2) == 2)
check("risk 5% with cap 3% -> a single trade is limited to 3%", R.risk_left(5, 3, 0) == 3)


def risk_pct_of(req):
    return 100 * (req["price"] - req["sl"]) * req["volume"] * (100 if req["symbol"].startswith("BTC") else 1) / STATE["balance"]


def pos(sym, risk_usd, sl_ok=True, bars=None):
    """an open DT position whose stop loses risk_usd (fake prices: BTC $100/pt/lot, ETH $1)"""
    vol = 0.02 if sym == "BTC" else 2.0
    mult = 100 if sym == "BTC" else 1
    return NS(symbol=sym + "USDm", magic=234200, ticket=900 + len(STATE["positions"]), volume=vol,
              time=int((bars if bars is not None else b)["time"][-5]), price_open=112.0,
              sl=(112.0 - risk_usd / (vol * mult)) if sl_ok else 0.0)


STATE.update(balance=10000.0, positions=[], deals=[], bars=b, fill=True)
R.dt_state.update(enabled=True, last_bar={})
R.dt_state["settings"] = dict(R.DEFAULT_SETTINGS)
scan()
rk = {r["symbol"][:3]: risk_pct_of(r) for r in sent}
check("both break out the same day: BTC first gets ~2%, ETH only what is left of the 3% cap (~1%)",
      [r["symbol"] for r in sent] == ["BTCUSDm", "ETHUSDm"] and 1.8 < rk["BTC"] <= 2.0 + 1e-9
      and 0.8 < rk["ETH"] <= 3.0 - rk["BTC"] + 1e-9 and sum(rk.values()) <= 3.0 + 1e-9,
      str({k: round(v, 3) for k, v in rk.items()}))
check("the ETH entry is journalled with the reason it is smaller",
      any(s_ == "ETH" and ev == "entry" and "crypto cap" in (why or "") for s_, ev, why in logged), str(logged))

STATE["positions"] = [pos("BTC", 200.0)]                     # BTC open, its stop loses $200 = 2%
R.dt_state.update(last_bar={})
scan()
check("BTC already open at 2% -> ETH breakout sized to the 1% left",
      [r["symbol"] for r in sent] == ["ETHUSDm"] and 0.8 < risk_pct_of(sent[0]) <= 1.0 + 1e-9,
      f"{[r['symbol'] for r in sent]} {risk_pct_of(sent[0]) if sent else None}")

STATE["positions"] = [pos("BTC", 200.0)]
R.update_settings({"max_crypto_risk_pct": 2.0})
R.dt_state.update(last_bar={})
scan()
check("cap 2% and BTC open at 2% -> ETH skipped, 'crypto risk cap full' journalled",
      not sent and any(s_ == "ETH" and ev == "skip" and "cap full" in (why or "") for s_, ev, why in logged), str(logged))

STATE["positions"] = [pos("BTC", 200.0, sl_ok=False)]
R.update_settings({"max_crypto_risk_pct": 3.0})
R.dt_state.update(last_bar={})
scan()
check("BTC open WITHOUT a stop (unlimited loss) -> counts as the whole cap, ETH skipped",
      not sent and any("NO stop" in (why or "") for _, _, why in logged), str(logged))

STATE["positions"] = [pos("BTC", 200.0)]
R.update_settings({"max_crypto_risk_pct": 4.0})
R.dt_state.update(last_bar={})
scan()
check("cap 4% (= no link at 2% each, as before) -> ETH gets the full 2%",
      [r["symbol"] for r in sent] == ["ETHUSDm"] and 1.8 < risk_pct_of(sent[0]) <= 2.0 + 1e-9)

STATE["bars_by"] = {"BTCUSDm": b, "ETHUSDm": bd}            # BTC breaks out, ETH closes under its 10-day low
STATE["positions"] = [pos("ETH", 200.0, bars=bd)]
R.update_settings({"max_crypto_risk_pct": 3.0})
R.dt_state.update(last_bar={})
scan()
check("same scan: ETH exits and BTC breaks out -> the exit is done FIRST, BTC gets the full 2%",
      closed and closed[0][0] == "ETHUSDm" and [r["symbol"] for r in sent] == ["BTCUSDm"]
      and 1.8 < risk_pct_of(sent[0]) <= 2.0 + 1e-9, f"{closed} {[(r['symbol'], risk_pct_of(r)) for r in sent]}")

STATE.update(positions=[], fill=False)
STATE.pop("bars_by")
R.update_settings({"risk_pct": 5.0, "max_crypto_risk_pct": 3.0})
R.dt_state.update(last_bar={})
scan()
rk = {r["symbol"][:3]: risk_pct_of(r) for r in sent}
check("risk 5% with cap 3% -> BTC limited to 3% (and ETH, sized alone here, also 3%)",
      2.7 < rk.get("BTC", 0) <= 3.0 + 1e-9 and 2.7 < rk.get("ETH", 0) <= 3.0 + 1e-9, str(rk))
R.dt_state["settings"] = dict(R.DEFAULT_SETTINGS)
STATE["balance"] = 3000.0

print("\n5. REAL HISTORY (read-only): live decisions == the tested study, trade for trade")
R.mt5, R.ensure_mt5 = REAL_MT5, REAL_ENSURE
src = open(os.path.join(BK, "trend_robustness_study.py"), encoding="utf-8").read()
fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "donchian")
ns = {"np": np, "atr_series": R.atr_series}
exec(compile(ast.Module(body=[fn], type_ignores=[]), "trend_robustness_study.donchian", "exec"), ns)
study_donchian = ns["donchian"]
REAL_MT5.initialize(timeout=10000)


def live_replay(d):
    trades, pos = [], None
    for k in range(R.N_ENTRY + 2, len(d)):
        stopped_here = False
        if pos is not None and d["low"][k] <= pos["sl"] and k >= pos["k0"]:
            trades.append((pos["t0"], int(d["time"][k])))
            pos, stopped_here = None, True
        dec = R.decide(d[:k + 1], position_open_time=pos["open_time"] if pos else None, closed_during_bar=stopped_here)
        if k + 1 >= len(d):
            break
        if dec["action"] == "enter":
            px = float(d["open"][k + 1])
            pos = dict(t0=int(d["time"][k + 1]), k0=k + 1, open_time=int(d["time"][k + 1]) + 60,
                       sl=px - dec["stop_dist"])
        elif dec["action"] == "exit":
            trades.append((pos["t0"], int(d["time"][k])))
            pos = None
    return trades


for sym in ("BTCUSDm", "ETHUSDm"):
    d = REAL_MT5.copy_rates_from_pos(sym, REAL_MT5.TIMEFRAME_D1, 0, 5000)
    study = [(t["t0"], t["t1"]) for t in study_donchian(d, (0.0, 0.0), long_only=True, entry="next_open")]
    live = live_replay(d)
    common = len(set(study) & set(live))
    check(f"{sym[:3]} {datetime.fromtimestamp(int(d['time'][0]), UTC):%Y}->{datetime.fromtimestamp(int(d['time'][-1]), UTC):%Y}: "
          f"study {len(study)} trades, live code {len(live)}, identical {common}",
          study == live, f"first difference: {next(((a, b) for a, b in zip(study, live) if a != b), None)}")
REAL_MT5.shutdown()

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
