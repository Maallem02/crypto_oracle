"""Inspect the live VP trades: identification, stop/target geometry, outcome."""
import json
import sqlite3
from datetime import datetime, timezone

import MetaTrader5 as mt5

UTC = timezone.utc
DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
rows = [dict(r) for r in db.execute(
    "SELECT * FROM vp_log WHERE status='taken' ORDER BY id")]
mt5.initialize(timeout=10000)

for r in rows:
    d = json.loads(r["data"] or "{}")
    print("=" * 92)
    print(f"{r['timestamp'][:19]}  {r['symbol']}  {r['setup']}  {r['direction'].upper()}  ticket {r['ticket']}")
    print("=" * 92)
    print(f"  signal     : {r['reason']}")
    print(f"  levels     : VAL {r['val']:.2f}  POC {r['poc']:.2f}  VAH {r['vah']:.2f}  "
          f"(yesterday {'INSIDE' if r['prev_inside'] else 'OUTSIDE'})")
    risk = abs(r["entry"] - r["sl"])
    reward = abs(r["tp"] - r["entry"])
    print(f"  entry {r['entry']:.2f}   stop {r['sl']:.2f}   target {r['tp']:.2f}")
    print(f"  risk {risk:.2f}  reward {reward:.2f}  ->  R:R = 1:{reward / risk:.2f}   "
          f"stop = {d.get('risk_atr', 0):.2f} x ATR(15m)   swing extreme {d.get('swing')}")
    print(f"  volume {d.get('volume')}  ->  $ risk ~ {risk * (d.get('volume') or 0):.2f}")

    p = mt5.positions_get(ticket=r["ticket"])
    if p:
        p = p[0]
        print(f"  MT5 OPEN   magic={p.magic}  comment={p.comment!r}  SL={p.sl}  TP={p.tp}  "
              f"now {p.price_current}  P/L ${p.profit:+.2f}")
        print(f"  stop/target on the broker match the log: "
              f"{abs(p.sl - r['sl']) < 1 and abs(p.tp - r['tp']) < 1}")
    else:
        deals = mt5.history_deals_get(position=r["ticket"]) or []
        for x in sorted(deals, key=lambda z: z.time):
            print(f"  MT5 deal   {datetime.fromtimestamp(x.time, UTC):%H:%M:%S} UTC  "
                  f"{'IN ' if x.entry == 0 else 'OUT'}  @ {x.price:.2f}  magic={x.magic}  "
                  f"comment={x.comment!r}  P/L {x.profit + x.commission + x.swap:+.2f}")
        print(f"  log: outcome={r['outcome']}  profit={r['profit']}  exit={d.get('exit')}  "
              f"closed_at={r['closed_at']}")
    print()

a = mt5.account_info()
print(f"account balance ${a.balance:.2f}  equity ${a.equity:.2f}")
mt5.shutdown()
