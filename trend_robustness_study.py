"""Robustness of DAILY DONCHIAN 20/10 (trend_following_study.py). Read-only.
Checks fixed before running; the system passes only if checks 1-4 all stay
positive (avg R > 0) on BTC and ETH, Exness 2018+:
  1. entry timing: signal close (baseline) / next day's open / one full day late
  2. without 2021
  3. spread and swap x2
  4. stop 1.5 ATR and 3 ATR
Reported (no threshold): 5. long-only BTC+ETH portfolio at 1 % and 2 % risk,
6. Monte Carlo of trade order (10,000 runs), 7. account size for the smallest lots."""
import os
import sys
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import MetaTrader5 as mt5
import vp_long_history_study as L
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
COST = {"BTC": (0.000119, 0.000219), "ETH": (0.00037, 0.000219)}
MIN_LOT = {"BTC": 0.01, "ETH": 0.1}                          # Exness, contract size 1
rng = np.random.default_rng(2)


def donchian(d, cost, n_in=20, n_out=10, stop_mult=2.0, entry="close", long_only=False, cost_mult=1.0):
    sp, sw = cost[0] * cost_mult, cost[1] * cost_mult
    t = d["time"]
    o, h, l, c = (d[k].astype(float) for k in ("open", "high", "low", "close"))
    atr = atr_series(d)
    trades, pos, i = [], None, max(n_in, 20) + 1
    while i < len(c) - 1:
        if pos is None:
            hi, lo = h[i - n_in:i].max(), l[i - n_in:i].min()
            side = 1 if c[i] > hi else (-1 if c[i] < lo else 0)
            if side == -1 and long_only:
                side = 0
            if side:
                if entry == "close":
                    ie, px, first = i, c[i], i + 1
                elif entry == "next_open":
                    ie, px, first = i + 1, o[i + 1], i + 1      # its own day can already hit the stop
                else:                                           # one full day late
                    ie, px, first = i + 1, c[i + 1], i + 2
                px = px * (1 + sp) if side == 1 else px
                dist = stop_mult * atr[i]
                pos = dict(side=side, entry=px, stop=px - side * dist, r=dist, ie=ie)
                i = first
                continue
            i += 1
            continue
        s = pos["side"]
        ex = None
        if (s == 1 and l[i] <= pos["stop"]) or (s == -1 and h[i] >= pos["stop"]):
            ex = min(pos["stop"], o[i]) if s == 1 else max(pos["stop"], o[i])
            if i == pos["ie"]:
                ex = pos["stop"]                              # entered at the open, stopped the same day
        elif i > pos["ie"] and ((s == 1 and c[i] < l[i - n_out:i].min()) or (s == -1 and c[i] > h[i - n_out:i].max())):
            ex = c[i]
        if ex is not None:
            if s == -1:
                ex *= (1 + sp)
            nights = (t[i] - t[pos["ie"]]) / 86400
            pnl = s * (ex - pos["entry"]) - (sw * nights * pos["entry"] if s == 1 else 0.0)
            trades.append(dict(side=s, t0=int(t[pos["ie"]]), t1=int(t[i]), R=pnl / pos["r"],
                               stop_usd=pos["r"], price=pos["entry"]))
            pos = None
        i += 1
    return trades


def avgR(trs):
    return float(np.mean([x["R"] for x in trs])) if trs else float("nan")


def line(trs):
    if not trs:
        return "no trades"
    x = np.array([t["R"] for t in trs])
    boot = [rng.choice(x, len(x)).mean() for _ in range(2000)]
    gp, gl = x[x > 0].sum(), -x[x <= 0].sum()
    return (f"{len(x):3d} trades  avg {x.mean():+.2f}R [90% CI {np.percentile(boot, 5):+.2f}..{np.percentile(boot, 95):+.2f}]"
            f"  sum {x.sum():+6.1f}R  PF {gp / gl if gl else 9:4.2f}")


def run_equity(Rs, risk):
    eq = np.cumprod(np.r_[1.0, 1 + risk * np.asarray(Rs)])
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return eq[-1], dd


def streak(Rs):
    best = cur = 0
    for r in Rs:
        cur = cur + 1 if r <= 0 else 0
        best = max(best, cur)
    return best


mt5.initialize(timeout=10000)
EX = {s: mt5.copy_rates_from_pos(s + "USDm", mt5.TIMEFRAME_D1, 0, 5000) for s in ("BTC", "ETH")}
mt5.shutdown()
BN = {}
for s, pair in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
    d = L.resample(L.load(pair), 86400)
    BN[s] = d[d["time"] >= datetime(2023, 10, 1, tzinfo=UTC).timestamp()]
y2021 = (datetime(2021, 1, 1, tzinfo=UTC).timestamp(), datetime(2022, 1, 1, tzinfo=UTC).timestamp())

checks = {}
print("=" * 110 + "\nCHECKS 1-4 (long+short as pre-registered, and long-only), Exness daily 2018+\n" + "=" * 110)
for s in ("BTC", "ETH"):
    d, cost = EX[s], COST[s]
    print(f"\n{s}")
    for lo_only in (False, True):
        tag = "long-only " if lo_only else "long+short"
        base = donchian(d, cost, long_only=lo_only)
        print(f"  [{tag}] baseline (signal close)   {line(base)}")
        variants = [("1 next day's open", dict(entry="next_open")), ("1 one day late", dict(entry="delay1")),
                    ("3 spread+swap x2", dict(cost_mult=2.0)), ("4 stop 1.5 ATR", dict(stop_mult=1.5)),
                    ("4 stop 3 ATR", dict(stop_mult=3.0))]
        for lab, kw in variants:
            trs = donchian(d, cost, long_only=lo_only, **kw)
            print(f"  [{tag}] {lab:24s} {line(trs)}")
            if not lo_only:
                checks[(s, lab)] = avgR(trs) > 0
        no21 = [t for t in base if not (y2021[0] <= t["t1"] < y2021[1])]
        print(f"  [{tag}] 2 without 2021           {line(no21)}")
        if not lo_only:
            checks[(s, "2 without 2021")] = avgR(no21) > 0

print("\n  PRE-REGISTERED CHECKS (long+short): " + " | ".join(f"{s} {k}: {'ok' if v else 'FAIL'}" for (s, k), v in checks.items()))
print(f"  -> ROBUST: {'YES' if all(checks.values()) else 'NO'}")

print("\n" + "=" * 110 + "\n5. LONG-ONLY BTC + ETH PORTFOLIO (each trade risks a fixed % of the account)\n" + "=" * 110)
for lab, data in (("Exness 2018+", EX), ("Binance 3y", BN)):
    port = sorted(donchian(data["BTC"], COST["BTC"], long_only=True) + donchian(data["ETH"], COST["ETH"], long_only=True),
                  key=lambda x: x["t1"])
    Rs = [t["R"] for t in port]
    yrs = (port[-1]["t1"] - port[0]["t0"]) / (365 * 86400)
    print(f"\n  {lab}: {len(port)} trades over {yrs:.1f} years, worst losing streak {streak(Rs)} trades")
    for risk in (0.01, 0.02):
        final, dd = run_equity(Rs, risk)
        print(f"    {int(risk * 100)}% risk per trade: account x{final:.2f} ({100 * (final ** (1 / yrs) - 1):+.1f}%/yr), "
              f"worst drawdown {100 * dd:.1f}%")
    per = {}
    for t in port:
        per.setdefault(datetime.fromtimestamp(t["t1"], UTC).year, []).append(t["R"])
    print("    per year (R): " + "  ".join(f"{y} {sum(v):+.1f}" for y, v in sorted(per.items())))
    print(f"    losing years: {sum(1 for v in per.values() if sum(v) < 0)} of {len(per)}")
    if lab == "Exness 2018+":
        PORT = Rs
        TPY = len(port) / yrs

print("\n" + "=" * 110 + "\n6. MONTE CARLO - 10,000 random orders / resamples of the Exness 2018+ long-only trades\n" + "=" * 110)
R = np.array(PORT)
for risk in (0.01, 0.02):
    dds, finals = [], []
    for _ in range(10000):
        seq = rng.choice(R, len(R), replace=True)
        f, dd = run_equity(seq, risk)
        dds.append(dd)
        finals.append(f)
    print(f"  {int(risk * 100)}% risk: worst drawdown median {100 * np.median(dds):.1f}%, bad case (5%) "
          f"{100 * np.percentile(dds, 5):.1f}%, worst {100 * min(dds):.1f}% | final x median {np.median(finals):.2f}, "
          f"5% case x{np.percentile(finals, 5):.2f}, chance of ending below start {100 * np.mean(np.array(finals) < 1):.1f}%")
n1 = int(round(TPY))
one_year = [rng.choice(R, n1, replace=True).sum() for _ in range(10000)]
print(f"  one year (~{n1} trades): chance of a losing year {100 * np.mean(np.array(one_year) < 0):.0f}%, "
      f"median {np.median(one_year):+.1f}R, 5% case {np.percentile(one_year, 5):+.1f}R")
r_no21 = [t["R"] for t in sorted(donchian(EX["BTC"], COST["BTC"], long_only=True) + donchian(EX["ETH"], COST["ETH"], long_only=True),
                                  key=lambda x: x["t1"]) if not (y2021[0] <= t["t1"] < y2021[1])]
one_year2 = [rng.choice(r_no21, n1, replace=True).sum() for _ in range(10000)]
print(f"  same without 2021's trades: chance of a losing year {100 * np.mean(np.array(one_year2) < 0):.0f}%, "
      f"median {np.median(one_year2):+.1f}R")

print("\n" + "=" * 110 + "\n7. ACCOUNT SIZE - risk of one trade at the smallest Exness lot (2 x daily ATR stop)\n" + "=" * 110)
for s in ("BTC", "ETH"):
    trs = donchian(EX[s], COST[s], long_only=True)
    recent = [t for t in trs if t["t0"] >= datetime(2025, 10, 1, tzinfo=UTC).timestamp()] or trs[-5:]
    usd = np.array([t["stop_usd"] * MIN_LOT[s] for t in recent])
    print(f"  {s} at {MIN_LOT[s]} lot: risk per trade over the last year ${usd.min():.0f}-${usd.max():.0f} "
          f"(median ${np.median(usd):.0f}) -> account for 1% risk ${np.median(usd) / 0.01:,.0f}, "
          f"for 2% ${np.median(usd) / 0.02:,.0f}")
