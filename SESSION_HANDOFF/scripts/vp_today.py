"""Everything the VP bot did today + the BTC M15 path vs the levels."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
UTC = timezone.utc
DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
now = datetime.now(UTC)
print(f"now {now:%Y-%m-%d %H:%M} UTC")
day0 = datetime(now.year, now.month, now.day, tzinfo=UTC)

print("\n--- vp_log since 24h ---")
for r in db.execute("SELECT * FROM vp_log WHERE timestamp >= ? ORDER BY id",
                    ((now - timedelta(hours=26)).isoformat(),)):
    r = dict(r)
    print(f"{r['timestamp'][:19]} {r['symbol']:6s} {r['status']:8s} {r['setup'] or '':11s} "
          f"{r['direction'] or '':4s} trig={(r['trigger_bar'] or '')[:16]} "
          f"e={r['entry']} sl={r['sl']} tp={r['tp']} tk={r['ticket']} out={r['outcome']} "
          f"pnl={r['profit']} | {(r['reason'] or '')[:90]}")

mt5.initialize(timeout=10000)
a = mt5.account_info()
print(f"\naccount {a.login} trade_mode={a.trade_mode} bal {a.balance:.2f} eq {a.equity:.2f}")
print("\n--- open positions ---")
for p in mt5.positions_get() or []:
    print(f"  {p.symbol} {'buy' if p.type == 0 else 'sell'} {p.volume} magic={p.magic} "
          f"open {p.price_open} sl {p.sl} tp {p.tp} now {p.price_current} pnl {p.profit:+.2f} "
          f"'{p.comment}' t={datetime.fromtimestamp(p.time, UTC):%H:%M}")
print("\n--- deals today ---")
for d in sorted(mt5.history_deals_get(day0 - timedelta(hours=2), now + timedelta(hours=1)) or [],
                key=lambda x: x.time):
    if d.type > 1:
        continue
    print(f"  {datetime.fromtimestamp(d.time, UTC):%m-%d %H:%M:%S} {d.symbol} "
          f"{'IN ' if d.entry == 0 else 'OUT'} {'buy' if d.type == 0 else 'sell'} {d.volume} "
          f"@ {d.price} magic={d.magic} pos={d.position_id} pnl {d.profit:+.2f} '{d.comment}'")

sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDm"
from features.volume_profile.runner import previous_session_profile, session_bars
from features.volume_profile.strategy import detect
prev, prof = previous_session_profile(sym, now.date())
print(f"\n{sym} profile {prev}: VAL {prof.val:.2f} POC {prof.poc:.2f} VAH {prof.vah:.2f} "
      f"inside={prof.closed_inside}")
bars, atr = session_bars(sym, now)
print(f"{'time':5s} {'open':>9s} {'high':>9s} {'low':>9s} {'close':>9s}  atr   zone   detect(all setups)")
for i in range(len(bars)):
    b = bars[i]
    c = float(b['close'])
    z = "ABOVE" if c > prof.vah else "below" if c < prof.val else "in"
    s = detect(bars[:i + 1], atr[:i + 1], prof)
    ss = f"{s.setup} {s.direction} sl={s.sl:.0f} {'BLOCKED ' + s.blocked if s.blocked else ''}" if s else ""
    print(f"{datetime.fromtimestamp(int(b['time']), UTC):%H:%M} {b['open']:9.2f} {b['high']:9.2f} "
          f"{b['low']:9.2f} {c:9.2f} {atr[i]:5.0f} {z:5s}  {ss}")
mt5.shutdown()
