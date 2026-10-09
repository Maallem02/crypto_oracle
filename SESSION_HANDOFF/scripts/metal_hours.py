"""Metals bleed in one time window. Pin it down in UTC and per symbol.

is_trading_session() decides in UTC (datetime.now(timezone.utc).hour), while
MT5 deal timestamps come back in LOCAL time (UTC+1 here). Any recommendation
must be expressed in UTC or it will be applied an hour off.

Check the effect holds for XAU and XAG SEPARATELY - a pattern that only exists
in one of them is not a "metals" rule.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
START = datetime(2026, 7, 27)
deals = mt5.history_deals_get(START, datetime.now() + timedelta(hours=6)) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)

T = []
for pid, ds in pos.items():
    ds.sort(key=lambda d: d.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    sym = ins[0].symbol.upper()
    key = "XAU" if "XAU" in sym else "XAG" if "XAG" in sym else \
          "BTC" if "BTC" in sym else "ETH" if "ETH" in sym else None
    if not key:
        continue
    T.append(dict(
        key=key,
        utc_hour=datetime.fromtimestamp(ins[0].time, tz=timezone.utc).hour,
        t0=datetime.fromtimestamp(ins[0].time),
        net=sum(x.profit + x.commission + x.swap for x in ds)))
mt5.shutdown()


def rep(v, label):
    if not v:
        print(f"  {label:20s} n=  0")
        return
    w = [x for x in v if x["net"] > 0]
    l = [x for x in v if x["net"] <= 0]
    gp, gl = sum(x["net"] for x in w), abs(sum(x["net"] for x in l))
    print(f"  {label:20s} n={len(v):3d}  net=${sum(x['net'] for x in v):+8.2f}  "
          f"WR={100*len(w)/len(v):5.1f}%  PF={(gp/gl if gl else 9.99):5.2f}")


print("=" * 88)
print("METALS BY UTC HOUR-OF-ENTRY")
print("=" * 88)
met = [x for x in T if x["key"] in ("XAU", "XAG")]
for h in range(24):
    v = [x for x in met if x["utc_hour"] == h]
    if not v:
        continue
    net = sum(x["net"] for x in v)
    bar = ("+" if net > 0 else "-") * min(30, int(abs(net) / 3) + 1)
    print(f"  {h:02d}:00 UTC  n={len(v):3d}  ${net:+8.2f}  {bar}")

print("\n" + "=" * 88)
print("GROUPED - and does it hold for BOTH metals?")
print("=" * 88)
WINDOWS = [(0, 6, "00-06 UTC Asia"), (6, 9, "06-09 UTC pre-London"),
           (9, 13, "09-13 UTC London"), (13, 17, "13-17 UTC London/NY"),
           (17, 21, "17-21 UTC NY")]
for lo, hi, lbl in WINDOWS:
    rep([x for x in met if lo <= x["utc_hour"] < hi], lbl)

for k in ("XAU", "XAG"):
    print(f"\n  {k} alone:")
    for lo, hi, lbl in WINDOWS:
        rep([x for x in T if x["key"] == k and lo <= x["utc_hour"] < hi],
            "  " + lbl)

print("\n  CRYPTO in the same windows (is 00-06 bad for everything?):")
for lo, hi, lbl in WINDOWS:
    rep([x for x in T if x["key"] in ("BTC", "ETH") and lo <= x["utc_hour"] < hi],
        "  " + lbl)

print("\n" + "=" * 88)
print("WHAT IF METALS SKIPPED 00-06 UTC?")
print("=" * 88)
cut = [x for x in met if 0 <= x["utc_hour"] < 6]
keep = [x for x in met if not (0 <= x["utc_hour"] < 6)]
rep(met, "metals as-is")
rep(keep, "metals skipping 00-06")
print(f"\n  removed {len(cut)} trades worth ${sum(x['net'] for x in cut):+.2f}")
print(f"  that is {100*len(cut)/len(met):.0f}% of metal trades and "
      f"{100*sum(x['net'] for x in cut)/sum(x['net'] for x in met):.0f}% of the metal loss")

print("\n  month by month, metals 00-06 UTC (is it a persistent pattern?):")
bym = defaultdict(list)
for x in cut:
    bym[x["t0"].strftime("%Y-%m")].append(x)
for k in sorted(bym):
    rep(bym[k], "  " + k)
