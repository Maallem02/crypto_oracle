"""Why no trade? Both bots, separately. Read-only."""
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import requests

API = "http://127.0.0.1:8000/trading"
DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
UTC = timezone.utc
now_l, now_u = datetime.now(), datetime.now(UTC)
print(f"now: local {now_l:%a %d/%m %H:%M}   UTC {now_u:%H:%M}   "
      f"SMC dead zone (21-02 UTC): {now_u.hour >= 21 or now_u.hour < 2}\n")

try:
    smc = requests.get(f"{API}/scalping/status", timeout=10).json()
    vp = requests.get(f"{API}/vp/status", timeout=10).json()
except Exception as e:
    print(f"BACKEND UNREACHABLE: {e}")
    raise SystemExit

mt5.initialize(timeout=10000)
a = mt5.account_info()
print(f"account {a.login} {'DEMO' if a.trade_mode == 0 else 'REAL'}  balance ${a.balance:.2f}  "
      f"equity ${a.equity:.2f}")
since = datetime.fromisoformat(smc.get("session_start") or now_l.strftime("%Y-%m-%dT00:00:00"))
deals = mt5.history_deals_get(since - timedelta(hours=1), now_l + timedelta(hours=6)) or []
for mg, name in ((234000, "SMC"), (234100, "VP")):
    ins = [d for d in deals if d.magic == mg and d.entry == 0 and d.type in (0, 1)]
    print(f"  {name:4s} trades opened since {since:%d/%m %H:%M}: {len(ins)}")
pos = mt5.positions_get() or []
print(f"  open positions: {len(pos)}  "
      + "  ".join(f"[{'VP' if p.magic == 234100 else 'SMC' if p.magic == 234000 else 'manual'}] "
                  f"{p.symbol} {'buy' if p.type == 0 else 'sell'} {p.profit:+.2f}" for p in pos))
mt5.shutdown()

print("\n" + "=" * 90)
print("SCALPING BOT (SMC)")
print("=" * 90)
ls = smc.get("last_scan")
print(f"  running={smc.get('running')}  stopped_reason={smc.get('stopped_reason')}  "
      f"last_scan={str(ls)[:19]}  session_start={str(smc.get('session_start'))[:19]}")
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
rows = list(db.execute("SELECT timestamp, symbol, reason FROM rejection_log WHERE timestamp >= ?",
                       (since.isoformat(),)))
print(f"  {len(rows)} evaluations since the session started")
FOUND = ("htf_filter", "consensus_gate", "fast_trend", "place_trade_failed", "sl_too_wide",
         "risk_entry_reject", "pending_failed", "pending_only", "macro4h", "dir_breaker",
         "max_positions", "cooldown", "news")
for sym in sorted({r["symbol"] for r in rows}):
    rs = [r for r in rows if r["symbol"] == sym]
    c = Counter(str(r["reason"]).split(":")[0][:44] for r in rs)
    found = sum(v for k, v in c.items() if any(f in k for f in FOUND))
    top = ", ".join(f"{k} x{v}" for k, v in c.most_common(4))
    print(f"    {sym:7s} {len(rs):4d} checks, {found:3d} setups filtered  | {top}")
    last = [r for r in rs if any(f in str(r["reason"]) for f in
                                 ("place_trade_failed", "risk_entry_reject", "sl_too_wide", "pending_failed"))]
    if last:
        print(f"            last blocked setup {last[-1]['timestamp'][11:16]}: {str(last[-1]['reason'])[:100]}")

print("\n" + "=" * 90)
print("VOLUME PROFILE BOT")
print("=" * 90)
vs = vp.get("settings") or {}
print(f"  enabled={vp.get('enabled')}  last scan {vp.get('last_scan_age_min')} min ago  "
      f"paused={vp.get('paused')}  last_error={vp.get('last_error')}")
print(f"  symbols={vs.get('symbols')}  daily loss={vs.get('daily_loss_pct') or 'OFF'}")
vrows = [dict(r) for r in db.execute(
    "SELECT timestamp, symbol, setup, direction, status, reason, prev_inside, poc, vah, val "
    "FROM vp_log WHERE timestamp >= ? ORDER BY id", ((now_u - timedelta(hours=30)).isoformat(),))]
lv = [r for r in vrows if r["status"] == "levels"]
sg = [r for r in vrows if r["status"] != "levels"]
print(f"\n  levels computed (last 30 h): {len(lv)}")
for r in lv[-8:]:
    print(f"    {r['timestamp'][5:16]}  {r['symbol']:7s} POC {r['poc']:.5g}  VA {r['val']:.5g}-{r['vah']:.5g}  "
          f"yesterday closed {'INSIDE' if r['prev_inside'] else 'OUTSIDE'}")
print(f"\n  setups FOUND (last 30 h): {len(sg)}   taken: {sum(1 for r in sg if r['status']=='taken')}")
for r in sg[-12:]:
    print(f"    {r['timestamp'][5:16]}  {r['symbol']:7s} {r['setup']:12s} {r['direction']:4s} "
          f"{r['status']:8s} {str(r['reason'])[:70]}")
if not sg:
    print("    none - no candle met any of the three setups' rules")
db.close()
