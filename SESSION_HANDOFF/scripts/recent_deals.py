"""Open positions + deals of the last 8 hours (both bots)."""
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

UTC = timezone.utc
BOT = {234000: "SMC", 234100: "VP", 0: "manual"}
mt5.initialize(timeout=10000)
now = datetime.now(UTC)
for p in mt5.positions_get() or []:
    print(f"OPEN  [{BOT.get(p.magic, p.magic)}] {p.symbol} {'buy' if p.type == 0 else 'sell'} {p.volume} "
          f"@ {p.price_open} SL {p.sl} TP {p.tp} now {p.price_current} {p.profit:+.2f} '{p.comment}' "
          f"since {datetime.fromtimestamp(p.time, UTC):%H:%M} UTC")
for d in sorted(mt5.history_deals_get(now - timedelta(hours=8), now + timedelta(hours=2)) or [],
                key=lambda x: x.time):
    if d.type in (0, 1):
        print(f"deal  {datetime.fromtimestamp(d.time, UTC):%H:%M} UTC [{BOT.get(d.magic, d.magic)}] {d.symbol} "
              f"{'IN ' if d.entry == 0 else 'OUT'} {'buy' if d.type == 0 else 'sell'} {d.volume} @ {d.price} "
              f"{d.profit + d.commission + d.swap:+.2f} '{d.comment}'")
mt5.shutdown()
