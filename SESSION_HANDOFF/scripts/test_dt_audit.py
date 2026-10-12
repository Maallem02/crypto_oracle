"""Daily Trend execution check (features/daily_trend/audit.py).
Real Exness daily candles are read from MT5 (read-only). The ACCOUNT is fake:
deals / orders / positions built from the tested trades, so nothing reaches the
broker, and the real database is not touched (journal rows are passed in).
  1. the audit's copy of the rules == the tested study code, trade for trade
  2. perfect execution over 2024-2025 -> no warning, no problem, 0R gap
  3. one fault at a time -> each one is caught, nothing else is
  4. the expected-range check
  5. smoke run on the real account (read-only)"""
import ast
import copy
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import numpy as np

BK = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
sys.path.insert(0, BK)
import MetaTrader5 as REAL
import features.daily_trend.audit as A
import features.daily_trend.runner as R

UTC = timezone.utc
fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


def iso(ts):
    return datetime.fromtimestamp(int(ts), UTC).isoformat()


REAL.initialize(timeout=10000)
BARS, COST = {}, {}
for sym in ("BTC", "ETH"):
    d = REAL.copy_rates_from_pos(sym + "USDm", REAL.TIMEFRAME_D1, 0, 5000)
    BARS[sym] = d[d["time"] + 86400 <= datetime.now(UTC).timestamp()]
    COST[sym] = A.costs(sym + "USDm", BARS[sym])
REAL.shutdown()
R.dt_state["settings"] = dict(R.DEFAULT_SETTINGS)
R._persist = lambda: None

print("1. THE AUDIT'S COPY OF THE RULES == THE TESTED STUDY CODE")
src = open(os.path.join(BK, "trend_robustness_study.py"), encoding="utf-8").read()
fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "donchian")
ns = {"np": np, "atr_series": A.atr_series}
exec(compile(ast.Module(body=[fn], type_ignores=[]), "donchian", "exec"), ns)
for sym, d in BARS.items():
    for cost, lab in (((0.0, 0.0), "no costs"), (COST[sym], "Exness costs")):
        st = ns["donchian"](d, cost, long_only=True, entry="next_open")
        me = A.reference_trades(d, *cost)
        same = [(x["t0"], x["t1"]) for x in st] == [(x["t0"], x["t1"]) for x in me]
        dr = max((abs(a["R"] - b["R"]) for a, b in zip(st, me)), default=0.0)
        check(f"{sym} {lab}: study {len(st)} trades, audit {len(me)}, same days {same}, max R difference {dr:.1e}",
              same and dr < 1e-9)
ref = [x["R"] for s in BARS for x in A.reference_trades(BARS[s], *COST[s]) if x["t0"] >= A.REF_START]
print(f"  info: reference 2018+ BTC+ETH = {len(ref)} trades, average {np.mean(ref):+.2f}R")


# ── a fake account where the bot did exactly what the tested code says ───────
def ideal(d, sp, sw, k0):
    atr = A.atr_series(d)
    h, c = d["high"].astype(float), d["close"].astype(float)
    out, k = [], k0
    while k <= len(d) - 1:
        if c[k] > h[k - 20:k].max():
            t = A.follow(d, k, sp, sw, atr)
            if t is None:
                return None                             # signal on the very last candle: pick another end
            out.append(t)
            if t["ix"] is None:
                break
            k = t["ix"] + 1
        else:
            k += 1
    return out


START = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
btc_all = A.reference_trades(BARS["BTC"], *COST["BTC"])
long_one = next(x for x in btc_all if x["t0"] >= datetime(2025, 3, 1, tzinfo=UTC).timestamp() and x["ix"] - x["ie"] >= 10)
end_t = int(BARS["BTC"]["time"][long_one["ie"] + 4])
W = None
for shift in range(6):                                  # same end for both symbols, no signal on the last candle
    D = {s: BARS[s][BARS[s]["time"] <= end_t + shift * 86400] for s in BARS}
    IDEAL = {s: ideal(D[s], *COST[s], int(np.searchsorted(D[s]["time"], START))) for s in D}
    if all(v is not None for v in IDEAL.values()):
        break
NOW = int(D["BTC"]["time"][-1]) + 86400 + 7200          # 02:00 after the last close


def build(trades_by_sym):
    deals, orders, positions, journal, tk = [], [], [], [], 1000
    for sym, trs in trades_by_sym.items():
        sw = COST[sym][1]
        for t in trs:
            tk += 1
            t["ticket"] = tk
            t_in = t["t0"] + 120
            deals.append(NS(time=t_in, magic=234200, entry=0, type=0, position_id=tk, volume=1.0, price=t["entry"],
                            profit=0.0, commission=0.0, swap=0.0, fee=0.0, comment="DT long", reason=3, order=tk,
                            symbol=sym + "USDm"))
            orders.append(NS(ticket=tk, sl=t["stop"]))
            journal.append(dict(symbol=sym, event="entry", bar_time=iso(t["t0"] - 86400), reason="breakout",
                                ticket=tk, risk_pct=2.0, timestamp=iso(t_in)))
            if t["ix"] is None:
                positions.append(NS(ticket=tk, sl=t["stop"]))
                continue
            nights = (t["t1"] - t["t0"]) / 86400
            if t["kind"] == "channel":
                t_out, reason, com = t["t1"] + 86400 + 120, 3, "DT exit 10d low"
            else:
                t_out, reason, com = t["t1"] + 3600, 4, f"[sl {t['stop']:.2f}]"
            deals.append(NS(time=t_out, magic=234200, entry=1, type=1, position_id=tk, volume=1.0, price=t["exit"],
                            profit=t["exit"] - t["entry"], commission=0.0, swap=-sw * nights * t["entry"], fee=0.0,
                            comment=com, reason=reason, order=tk + 50000, symbol=sym + "USDm"))
    return {"deals": deals, "orders": orders, "positions": positions, "journal": journal}


class FakeMT5:
    def __init__(self):
        self.w = None

    def __getattr__(self, name):
        return getattr(REAL, name)

    def history_deals_get(self, *a, **k):
        return list(self.w["deals"])

    def history_orders_get(self, ticket=None):
        return [o for o in self.w["orders"] if o.ticket == ticket]

    def positions_get(self, ticket=None, symbol=None):
        return [p for p in self.w["positions"] if ticket is None or p.ticket == ticket]

    def order_calc_profit(self, t, s, vol, a, b):
        return (b - a) * vol


FAKE = FakeMT5()
A.mt5 = FAKE


def run(w, syms=("BTC", "ETH")):
    FAKE.w = w
    lives = A.live_trades(START, datetime.fromtimestamp(NOW, UTC))
    ev, tr = [], []
    for s in syms:
        e, t, _ = A.audit_symbol(s, D[s], *COST[s], lives, w["journal"], START, NOW)
        ev += e
        tr += t
    return ev, tr


def bad(ev, tr, level):
    return [e["text"] for e in ev if e["status"] == level] + \
           ["; ".join(x["notes"]) for x in tr if x["check"] == level]


print(f"\n2. PERFECT EXECUTION {iso(START)[:10]} -> {iso(D['BTC']['time'][-1])[:10]} (fake account = the tested trades)")
base = build(IDEAL)                                     # stamps each tested trade with its fake ticket
ev, tr = run(base)
n_ideal = sum(len(v) for v in IDEAL.values())
check(f"every tested trade found and matched ({len(tr)}/{n_ideal}), one of them still open",
      len(tr) == n_ideal and sum(1 for x in tr if x["status"] == "open") >= 1)
check("no warning, no problem", not bad(ev, tr, "warn") and not bad(ev, tr, "problem"),
      str(bad(ev, tr, "warn") + bad(ev, tr, "problem"))[:300])
gaps = [x["gap_r"] for x in tr if x["gap_r"] is not None]
check("live R == tested R on every closed trade (gap 0)", gaps and max(abs(g) for g in gaps) < 1e-6, f"{len(gaps)} closed")
check("entry slippage 0 and stop exactly 2 ATR", all(abs(x["entry_slip_r"]) < 1e-9 and abs(x["stop_dev_pct"]) < 1e-6
                                                       for x in tr))
check("timeline: one 'bought' per trade, exits reported", sum("bought" in e["text"] for e in ev) == n_ideal
      and sum(("sold" in e["text"]) or ("stopped out" in e["text"]) for e in ev) == n_ideal - sum(
          1 for x in tr if x["status"] == "open"))

print("\n3. ONE FAULT AT A TIME")
btc = [t for t in IDEAL["BTC"] if t["ix"] is not None]
nxt = {id(a): b for a, b in zip(IDEAL["BTC"], IDEAL["BTC"][1:])}
j = next(t for t in btc if t["kind"] == "channel" and nxt.get(id(t)) and nxt[id(t)]["k"] - t["ix"] >= 6)
jt = None


def variant(fn):
    w = copy.deepcopy(base)
    fn(w)
    return run(w)


def tick_of(t):
    return t["ticket"]


def drop_trade(w, with_skips):
    tk = tick_of(j)
    w["deals"] = [x for x in w["deals"] if x.position_id != tk]
    w["journal"] = [r for r in w["journal"] if r["ticket"] != tk]
    if with_skips:
        for k in range(j["k"], j["ix"] + 1):
            w["journal"].append(dict(symbol="BTC", event="skip", bar_time=iso(D["BTC"]["time"][k]), ticket=None,
                                     risk_pct=None, timestamp=iso(D["BTC"]["time"][k] + 86400 + 60),
                                     reason="account too small: the minimum lot 0.01 risks $45 = 45% of $100 (limit 2%)"))


ev, tr = variant(lambda w: drop_trade(w, True))
check("entry not taken, journal says 'account too small' -> reported as skipped by design, no problem",
      any("account too small" in e["text"] and e["status"] == "info" for e in ev) and not bad(ev, tr, "problem"),
      str(bad(ev, tr, "problem"))[:200])
ev, tr = variant(lambda w: drop_trade(w, False))
check("entry not taken and NO journal record -> problem (backend down at the close?)",
      any("no journal record" in x for x in bad(ev, tr, "problem")))


def late(w):
    next(x for x in w["deals"] if x.position_id == tick_of(j) and x.entry == 0).time += 3 * 3600


ev, tr = variant(late)
check("entry 3 h after the close -> warning on that trade only",
      bad(ev, tr, "warn") == [next(x for x in tr if x["ticket"] == tick_of(j))["notes"][0]]
      and "3.0 h after" in bad(ev, tr, "warn")[0] and not bad(ev, tr, "problem"), str(bad(ev, tr, "warn")))


def slip(w):
    d_in = next(x for x in w["deals"] if x.position_id == tick_of(j) and x.entry == 0)
    d_in.price += 0.3 * j["dist"]
    next(o for o in w["orders"] if o.ticket == tick_of(j)).sl += 0.3 * j["dist"]


ev, tr = variant(slip)
check("bought 0.3R above the tested price (stop still 2 ATR) -> 'entry price 0.30R worse'",
      any("entry price 0.30R worse" in x for x in bad(ev, tr, "warn")) and not bad(ev, tr, "problem"),
      str(bad(ev, tr, "warn")))


def wide_stop(w):
    next(o for o in w["orders"] if o.ticket == tick_of(j)).sl -= 0.1 * j["dist"]


ev, tr = variant(wide_stop)
check("stop 10% wider than 2 ATR -> 'stop distance +10.0%'", any("+10.0% from 2 ATR" in x for x in bad(ev, tr, "warn")))


def no_stop(w):
    next(o for o in w["orders"] if o.ticket == tick_of(j)).sl = 0.0


ev, tr = variant(no_stop)
check("entry order without a stop -> problem", any("no stop" in x for x in bad(ev, tr, "problem")))


def by_hand(w):
    x = next(x for x in w["deals"] if x.position_id == tick_of(j) and x.entry == 1)
    x.reason, x.comment = 0, ""


ev, tr = variant(by_hand)
check("closed by hand (instead of the bot's 10-day exit) -> warning 'closed by hand', no problem",
      any("closed by hand" in x for x in bad(ev, tr, "warn")) and not bad(ev, tr, "problem"),
      str(bad(ev, tr, "problem"))[:200])


def missed_exit(w):
    next(x for x in w["deals"] if x.position_id == tick_of(j) and x.entry == 1).time += 3 * 86400


ev, tr = variant(missed_exit)
check("10-day exit signal but sold 3 days later -> problem 'not sold that day'",
      any("not sold that day" in x for x in bad(ev, tr, "problem")))

gap_k = next(k for k in range(j["ix"] + 2, nxt[id(j)]["k"] - 2)
             if R.decide(D["BTC"][:k + 1])["action"] == "none" and R.decide(D["BTC"][:k + 2])["action"] == "none")


def extra(w):
    T = int(D["BTC"]["time"][gap_k]) + 86400
    w["deals"] += [NS(time=T + 120, magic=234200, entry=0, type=0, position_id=99, volume=1.0, price=100.0, profit=0.0,
                      commission=0.0, swap=0.0, fee=0.0, comment="DT long", reason=3, order=99, symbol="BTCUSDm"),
                   NS(time=T + 3600, magic=0, entry=1, type=1, position_id=99, volume=1.0, price=100.0, profit=0.0,
                      commission=0.0, swap=0.0, fee=0.0, comment="", reason=0, order=98, symbol="BTCUSDm")]
    w["orders"].append(NS(ticket=99, sl=90.0))


ev, tr = variant(extra)
check("a bot position opened on a day without a breakout -> problem 'without a breakout'",
      any("without a breakout" in x for x in bad(ev, tr, "problem")))

open_t = next(t for t in IDEAL["BTC"] if t["ix"] is None)


def moved(w):
    next(p for p in w["positions"] if p.ticket == tick_of(open_t)).sl += 0.5 * open_t["dist"]


ev, tr = variant(moved)
check("open position whose stop was moved by hand -> warning", any("stop moved by hand" in x for x in bad(ev, tr, "warn")))


def big_risk(w):
    next(r for r in w["journal"] if r["ticket"] == tick_of(j))["risk_pct"] = 4.0


ev, tr = variant(big_risk)
check("journal says 4% risked with a 2% setting -> warning", any("risked 4.00%" in x for x in bad(ev, tr, "warn")))

print("\n4. EXPECTED RANGE (live result vs 5,000 draws of the tested trades)")
r = A.expected_range(ref, [float(np.mean(ref))] * 12)
check("12 trades at the tested average -> inside", r["status"] == "inside", str(r))
r = A.expected_range(ref, [-1.0] * 12)
check(f"12 losses in a row (-12R) -> below the expected range (5% worst case {r['expected_p5']}R, "
      f"streak p95 {r['streak_p95']})", r["status"] == "below")
r = A.expected_range(ref, [])
check("no closed trade yet -> no_trades", r["status"] == "no_trades")
live = [x["r"] for x in sorted(run(base)[1], key=lambda x: x["exit_time"] or "~") if x["r"] is not None]
r = A.expected_range(ref, live)
print(f"  info: the 2024-2025 tested trades as 'live': {r['n']} trades, {r['sum_r']:+.1f}R, "
      f"expected 90% range {r['expected_p5']:+.1f}..{r['expected_p95']:+.1f}R -> {r['status']}")

print("\n5. REAL ACCOUNT (read-only smoke run)")
from core.config import runtime
runtime.db_path = os.path.join(BK, "crypto_oracle_8000.db")        # the backend's journal (only read here)
A.mt5 = REAL
REAL.initialize(timeout=10000)
real = A._run(None)
REAL.shutdown()
check(f"runs on the live account: verdict '{real['verdict']}', {real['checked_closes']} daily closes checked since "
      f"{(real['since'] or '')[:10]}, {real['summary'].get('live_trades')} bot trades",
      real["verdict"] in ("no_trades", "ok", "warnings", "problems", "not_started"))
for e in real["events"][:5]:
    print(f"    {e['time'][:16]} {e['symbol']} {e['status']}: {e['text']}")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
