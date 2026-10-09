"""Holes in the M1 history (> 3 h, weekends excluded) per symbol."""
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import numpy as np

UTC = timezone.utc
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
for sym in ("XAUUSDm", "EURUSDm", "USDJPYm", "BTCUSDm"):
    m1 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, now - timedelta(days=66), now)
    m15 = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M15, now - timedelta(days=66), now)
    t = m1["time"]
    gaps = []
    for a, b in zip(t[:-1], t[1:]):
        if b - a > 3 * 3600:
            da, db = datetime.fromtimestamp(int(a), UTC), datetime.fromtimestamp(int(b), UTC)
            weekend = da.weekday() == 4 and da.hour >= 20 and db.weekday() in (6, 0)
            if not weekend:
                gaps.append(f"{da:%m-%d %H:%M} -> {db:%m-%d %H:%M} ({(b - a) / 3600:.0f} h)")
    print(f"{sym}: M1 {len(m1)} bars from {datetime.fromtimestamp(int(t[0]), UTC):%m-%d}, "
          f"M15 {len(m15)} bars from {datetime.fromtimestamp(int(m15['time'][0]), UTC):%m-%d}; "
          f"non-weekend holes > 3 h: {len(gaps)}")
    for g in gaps[:8]:
        print("    " + g)
mt5.shutdown()
