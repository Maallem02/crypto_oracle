"""Why no crypto trades on the real account?

Separate two very different causes:
  a) the strategy found nothing (score 0, no liquidity grab, counter-trend...)
     -> normal, same as on the demo
  b) the strategy found a trade but the $70 account can't afford it
     (risk cap / sl too wide / risk_entry_reject) -> caused by the balance

Only (b) is new with the real account.
"""
import sqlite3
from collections import Counter
from datetime import datetime

import requests

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

st = requests.get("http://127.0.0.1:8000/trading/scalping/status", timeout=10).json()
start = st.get("session_start")
now = datetime.now()
mins = (now - datetime.fromisoformat(start)).total_seconds() / 60
print(f"real-account session started {start[:19]}  ->  running {mins:.0f} min")
print(f"trades_today={st.get('trades_today')}   last_scan={str(st.get('last_scan'))[:19]}\n")

AFFORD = ("risque trop", "risk_entry_reject", "risk_reject", "sl_too_wide",
          "plafond", "place_trade_failed", "pending_failed")


def cls(reason):
    r = reason.lower()
    if any(k in r for k in AFFORD):
        return "CAN'T AFFORD (balance)"
    return "strategy: no setup"


for sym in ("BTC", "ETH", "XAGUSD"):
    rows = list(db.execute(
        "select reason from rejection_log where timestamp >= ? and upper(symbol)=?",
        (start, sym)))
    print("=" * 90)
    print(f"{sym}: {len(rows)} evaluations since the real session started")
    print("=" * 90)
    if not rows:
        print("  none\n")
        continue
    by_cls = Counter(cls(str(r["reason"])) for r in rows)
    for k, v in by_cls.most_common():
        print(f"  {k:26s} {v:5d}  ({100*v/len(rows):4.1f}%)")
    print("  top reasons:")
    c = Counter(str(r["reason"]).split(":")[0][:60] for r in rows)
    for k, v in c.most_common(7):
        print(f"    {v:5d}  {k}")
    aff = [str(r["reason"]) for r in rows if cls(str(r["reason"])) != "strategy: no setup"]
    if aff:
        print("  a balance rejection, verbatim:")
        print(f"    {aff[-1][:150]}")
    print()

print("=" * 90)
print("FOR COMPARISON - the demo on a normal day (09-22, full session)")
print("=" * 90)
for sym in ("BTC", "ETH"):
    rows = list(db.execute(
        "select reason from rejection_log where timestamp >= '2026-09-22T03:00' "
        "and timestamp < '2026-09-22T21:00' and upper(symbol)=?", (sym,)))
    if rows:
        aff = sum(1 for r in rows if cls(str(r["reason"])) != "strategy: no setup")
        print(f"  {sym}: {len(rows)} evaluations, {aff} balance rejections "
              f"({100*aff/len(rows):.1f}%)")

print("\n" + "=" * 90)
print("HOW OFTEN DOES CRYPTO NORMALLY TRADE?  (demo, since 09-15)")
print("=" * 90)
import MetaTrader5  # noqa - only to keep the same env; counts come from scalp_log
for sym in ("BTC", "ETH"):
    n = db.execute("select count(*) from scalp_log where upper(symbol)=? "
                   "and timestamp >= '2026-09-15T22:15'", (sym,)).fetchone()[0]
    print(f"  {sym}: {n} trades in ~8 days  = about {n/8:.1f} per day")
