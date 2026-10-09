"""If gold had been blocked all week, what would the week have been?

The naive answer is: week -14.81 minus gold -27.84 = +13.03. That subtraction
is only valid if removing gold changes NOTHING about the other symbols.

It does not, necessarily. update_direction_breaker() is PER ASSET CLASS:
after 2 consecutive same-direction SL losses within a class, that
(class, direction) is blocked for 4 hours. XAU and XAG are BOTH "metals".
So gold losses could have blocked SILVER trades - and removing gold would
UNBLOCK them, adding silver trades that never happened.

Crypto (BTC/ETH) is a different class, so it is genuinely unaffected.

Measure:
  1. Is XAU/XAG really the same class?
  2. How often did the metals breaker fire this week, and was gold the cause?
  3. How many silver signals were blocked by a gold-caused breaker?
"""
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from features.trading.router import _asset_class

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
WEEK = "2026-09-14"

print("=" * 92)
print("1. ASSET CLASSES")
print("=" * 92)
for s in ("XAUUSD", "XAGUSD", "BTC", "ETH", "EURUSD"):
    print(f"  {s:8s} -> {_asset_class(s)}")
print("\n  => gold and silver share a breaker. Crypto does not.")

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

print("\n" + "=" * 92)
print("2. DIRECTION-BREAKER BLOCKS LOGGED THIS WEEK")
print("=" * 92)
rows = list(db.execute(
    "select timestamp, symbol, reason from rejection_log "
    "where timestamp >= ? and (reason like '%breaker%' or reason like '%dir_%' "
    "or reason like '%direction%') order by timestamp", (WEEK,)))
print(f"  {len(rows)} breaker rejections logged")
c = Counter(f"{(r['symbol'] or '?'):8s} {str(r['reason'])[:56]}" for r in rows)
for k, v in c.most_common(15):
    print(f"    {v:5d}  {k}")
if not rows:
    print("    (none - the breaker never blocked anything this week)")

print("\n" + "=" * 92)
print("3. THE WEEK'S RESOLVED TRADES IN ORDER, WITH ASSET CLASS")
print("=" * 92)
sl = list(db.execute(
    "select symbol, action, outcome, profit, coalesce(closed_at, timestamp) ct "
    "from scalp_log where outcome is not null and timestamp >= ? "
    "order by coalesce(closed_at, timestamp)", (WEEK,)))
print(f"  {'closed':16s} {'sym':8s} {'class':7s} {'dir':5s} {'out':4s} {'pnl':>8s}")
for r in sl:
    print(f"  {str(r['ct'])[:16]:16s} {str(r['symbol']):8s} "
          f"{_asset_class(r['symbol'] or ''):7s} {str(r['action']):5s} "
          f"{r['outcome']:4d} {r['profit'] or 0:+8.2f}")

print("\n" + "=" * 92)
print("4. WOULD REMOVING GOLD HAVE UNBLOCKED SILVER?")
print("=" * 92)
print("  Replay the metals breaker over the week's metal trades, with and")
print("  without gold, and see which (direction, window) blocks disappear.\n")


def metal_blocks(trades, consec=2, block_hours=4):
    """Windows during which (metals, direction) was blocked."""
    blocks = []
    streak_dir, streak, last_ct = None, 0, None
    for sym, action, outcome, profit, ct in trades:
        is_loss = outcome == 0 and abs(profit or 0) >= 0.15
        is_win = outcome == 1 and (profit or 0) > 0.15
        if not is_loss and not is_win:
            continue
        if is_win:
            streak_dir, streak = None, 0
            continue
        if action == streak_dir:
            streak += 1
        else:
            streak_dir, streak = action, 1
        last_ct = ct
        if streak >= consec:
            t = datetime.fromisoformat(str(last_ct)[:19])
            blocks.append((streak_dir, t, t + timedelta(hours=block_hours)))
    return blocks


metals = [(r["symbol"], r["action"], r["outcome"], r["profit"], r["ct"])
          for r in sl if _asset_class(r["symbol"] or "") == "metals"]
silver_only = [t for t in metals if "XAG" in (t[0] or "").upper()]

with_gold = metal_blocks(metals)
without_gold = metal_blocks(silver_only)
print(f"  metal trades this week: {len(metals)}  (of which silver: {len(silver_only)})")
print(f"\n  breaker windows WITH gold    : {len(with_gold)}")
for d, a, b in with_gold:
    print(f"    block {d:5s} {a:%a %m-%d %H:%M} -> {b:%H:%M}")
print(f"\n  breaker windows WITHOUT gold : {len(without_gold)}")
for d, a, b in without_gold:
    print(f"    block {d:5s} {a:%a %m-%d %H:%M} -> {b:%H:%M}")

print("\n" + "=" * 92)
print("5. THE ARITHMETIC, WITH ITS CAVEAT STATED")
print("=" * 92)
import MetaTrader5 as mt5
mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(datetime(2026, 9, 14),
                              datetime.now() + timedelta(hours=6)) or []
mt5.shutdown()
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)
byk = defaultdict(float)
n = defaultdict(int)
for pid, ds in pos.items():
    ds.sort(key=lambda z: z.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs:
        continue
    s = ins[0].symbol.upper()
    k = ("XAU" if "XAU" in s else "XAG" if "XAG" in s else
         "BTC" if "BTC" in s else "ETH" if "ETH" in s else "?")
    byk[k] += sum(x.profit + x.commission + x.swap for x in ds)
    n[k] += 1
tot = sum(byk.values())
print(f"  actual week: ${tot:+.2f} over {sum(n.values())} trades")
for k in sorted(byk, key=lambda z: -byk[z]):
    print(f"    {k:5s} n={n[k]:3d}  ${byk[k]:+8.2f}")
print(f"\n  week without gold's P/L: ${tot - byk['XAU']:+.2f}  "
      f"({sum(n.values())-n['XAU']} trades)")
print(f"  balance would be:        ${150.00 + (tot - byk['XAU']):.2f}  "
      f"(vs ${150.00 + tot:.2f} actual)")
