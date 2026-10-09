"""What the two signals missed while the backend was down would have done."""
from datetime import datetime, timezone

import MetaTrader5 as mt5

UTC = timezone.utc
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
# (label, entry time, direction, entry, stop) - from the replay of today's closed M15 bars
for label, t, d, e, sl in (("VAL re-entry BUY  (07:15 bar)", datetime(2026, 9, 25, 7, 30, tzinfo=UTC), "buy", 84028.00, 83700.9),
                           ("VAH breakout BUY (11:00 bar)", datetime(2026, 9, 25, 11, 15, tzinfo=UTC), "buy", 85214.28, 84349.0)):
    risk = e - sl
    tp = e + 2 * risk
    bars = mt5.copy_rates_range("BTCUSDm", mt5.TIMEFRAME_M1, t, now)
    res = "still open"
    for b in bars:
        if b["low"] <= sl:
            res = f"STOP at {datetime.fromtimestamp(int(b['time']), UTC):%H:%M} UTC  -1R"; break
        if b["high"] >= tp:
            res = f"TARGET at {datetime.fromtimestamp(int(b['time']), UTC):%H:%M} UTC  +2R"; break
    last = float(bars[-1]["close"])
    print(f"{label}: entry {e:.0f} stop {sl:.0f} target {tp:.0f} (risk {risk:.0f}) -> {res}"
          f"   now {last:.0f} = {(last - e) / risk:+.2f}R, ~${(last - e) * 0.02:+.2f} at 0.02 lot")
mt5.shutdown()
