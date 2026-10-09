"""Did the shifted stop cost trade #2? Compare the actual stop (84742.51, below
the swing high) with the structural stop the fix now uses (swing high + 0.1 ATR)."""
from datetime import datetime, timezone

import MetaTrader5 as mt5

UTC = timezone.utc
entry, shifted_sl, swing = 84444.66, 84742.51, 84782.13
atr = 297.97 / 1.46                       # from the log: risk 297.97 = 1.46 ATR
struct_sl = swing + 0.1 * atr
struct_tp = entry - 2 * (struct_sl - entry)
shifted_tp = 83848.59
t0 = datetime(2026, 9, 25, 1, 30, 22, tzinfo=UTC)

mt5.initialize(timeout=10000)
bars = mt5.copy_rates_range("BTCUSDm", mt5.TIMEFRAME_M1, t0, datetime.now(UTC))
mt5.shutdown()
print(f"entry {entry}   swing high {swing}   ATR(15m) ~{atr:.1f}")
print(f"  shifted stop (what was used) {shifted_sl:.2f}   target {shifted_tp:.2f}")
print(f"  structural stop (the fix)    {struct_sl:.2f}   target {struct_tp:.2f}\n")

hi = max(float(b["high"]) for b in bars)
t_hi = datetime.fromtimestamp(int(max(bars, key=lambda b: b["high"])["time"]), UTC)
lo_after = None
print(f"highest price since entry: {hi:.2f} at {t_hi:%H:%M} UTC")
print(f"  above the shifted stop?    {hi >= shifted_sl}")
print(f"  above the structural stop? {hi >= struct_sl}\n")


def walk(sl, tp):
    for b in bars:
        if b["high"] >= sl:
            return "STOP", datetime.fromtimestamp(int(b["time"]), UTC)
        if b["low"] <= tp:
            return "TARGET", datetime.fromtimestamp(int(b["time"]), UTC)
    return "still open", None


for name, sl, tp in (("shifted (actual)", shifted_sl, shifted_tp),
                     ("structural (fix)", struct_sl, struct_tp)):
    res, t = walk(sl, tp)
    print(f"  {name:18s} -> {res}{f' at {t:%H:%M} UTC' if t else ''}")
last = float(bars[-1]["close"])
print(f"\nBTC now {last:.2f}  (lowest since entry {min(float(b['low']) for b in bars):.2f})")
