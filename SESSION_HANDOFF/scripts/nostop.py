"""The open position(s) without a stop loss: details, order history, VP log, backend log."""
import glob
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

UTC = timezone.utc
BK = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
mt5.initialize(timeout=10000)
for p in mt5.positions_get() or []:
    if p.sl:
        continue
    t0 = datetime.fromtimestamp(p.time, UTC)
    print(f"NO STOP: ticket {p.ticket} {p.symbol} {'buy' if p.type == 0 else 'sell'} {p.volume} "
          f"@ {p.price_open} SL {p.sl} TP {p.tp} now {p.price_current} P/L {p.profit:+.2f} "
          f"magic {p.magic} '{p.comment}' opened {t0:%Y-%m-%d %H:%M:%S} UTC")
    for o in mt5.history_orders_get(position=p.ticket) or []:
        print(f"   order {o.ticket} type {o.type} state {o.state} price {o.price_open} "
              f"sl {o.sl} tp {o.tp} setup {datetime.fromtimestamp(o.time_setup, UTC):%H:%M:%S} "
              f"'{o.comment}'")
    for d in mt5.history_deals_get(position=p.ticket) or []:
        print(f"   deal {d.ticket} entry {d.entry} @ {d.price} {datetime.fromtimestamp(d.time, UTC):%H:%M:%S} '{d.comment}'")
    db = sqlite3.connect(os.path.join(BK, "crypto_oracle_8000.db"))
    db.row_factory = sqlite3.Row
    for r in db.execute("SELECT * FROM vp_log WHERE ticket=?", (p.ticket,)):
        print(f"   vp_log: {dict(r)}")
    # backend log lines around the open time
    stamp = t0.astimezone().strftime("%H:%M")
    for f in sorted(glob.glob(os.path.join(BK, "backend_8000_*.log")), key=os.path.getmtime)[-4:]:
        with open(f, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        hits = [i for i, l in enumerate(lines) if str(p.ticket) in l or "STRUCT-SL] XAU" in l]
        for i in hits[:6]:
            print(f"   {os.path.basename(f)}: " + "".join(lines[max(0, i - 3):i + 3]).strip()[:900])
mt5.shutdown()
