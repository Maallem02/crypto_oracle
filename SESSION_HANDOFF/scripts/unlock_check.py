"""Does $150 actually unlock trades that $88.21 was rejecting?

Two angles:
 1. How many logged rejections were risk-cap / stop-width driven?
 2. For every REAL signal with a logged SL, how wide was the stop in dollars at
    the configured lot, and would the 7% cap have passed it at $88 vs $150?
"""
import json, sqlite3
from collections import Counter, defaultdict
from datetime import datetime

DB = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\crypto_oracle_8000.db"
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
tables = [r[0] for r in db.execute(
    "select name from sqlite_master where type='table'")]
print("tables:", tables, "\n")

# ── 1. rejection reasons ────────────────────────────────────────────────────
rej_tbl = next((t for t in tables if "reject" in t.lower()), None)
if rej_tbl:
    cols = [r[1] for r in db.execute(f"pragma table_info({rej_tbl})")]
    print(f"=== {rej_tbl}  cols={cols} ===")
    rows = list(db.execute(
        f"select * from {rej_tbl} where timestamp >= '2026-08-25'"
        if "timestamp" in cols else f"select * from {rej_tbl}"))
    print(f"  rows since 08-25: {len(rows)}")
    c = Counter()
    for r in rows:
        raw = r["reason"] if "reason" in cols else str(dict(r))
        c[str(raw).split(":")[0][:46]] += 1
    for k, v in c.most_common(18):
        flag = "   <- RISK/SIZE" if any(
            w in k.lower() for w in ("risk", "lot", "cap", "sl_too", "margin")) else ""
        print(f"    {v:5d}  {k}{flag}")
else:
    print("no rejection table found")

# ── 2. stop width in dollars per signal ─────────────────────────────────────
LOTS = {"BTC": 0.01, "ETH": 0.45, "XAU": 0.01, "XAG": 0.01}
CONTRACT = {"BTC": 1.0, "ETH": 1.0, "XAU": 100.0, "XAG": 5000.0}

sigs = list(db.execute(
    "select * from scalp_log where timestamp >= '2026-08-25'"))
print(f"\n=== scalp_log signals since 08-25: {len(sigs)} ===")

per = defaultdict(list)
for r in sigs:
    try:
        d = json.loads(r["data"]) if r["data"] else {}
    except Exception:
        continue
    sl, entry = d.get("sl"), d.get("entry") or d.get("price")
    if not sl or not entry:
        continue
    sym = (r["symbol"] or "").upper()
    key = next((k for k in LOTS if k in sym), None)
    if not key:
        continue
    dist = abs(entry - sl)
    usd = dist * LOTS[key] * CONTRACT[key]
    per[key].append(usd)

print(f"\n  {'sym':5s} {'n':>4s} {'median $risk':>13s} {'p75':>8s} {'p90':>8s} "
      f"{'max':>8s} | {'pass@$88':>9s} {'pass@$150':>10s}")
for k, v in sorted(per.items()):
    v.sort()
    if not v:
        continue
    def q(p):
        return v[min(len(v) - 1, int(len(v) * p))]
    cap88, cap150 = 88.21 * 0.07, 150.0 * 0.07
    p88 = 100 * sum(1 for x in v if x <= cap88) / len(v)
    p150 = 100 * sum(1 for x in v if x <= cap150) / len(v)
    print(f"  {k:5s} {len(v):4d} {q(0.5):13.2f} {q(0.75):8.2f} {q(0.90):8.2f} "
          f"{v[-1]:8.2f} | {p88:8.0f}% {p150:9.0f}%")

print(f"\n  cap at $88.21 = ${88.21*0.07:.2f}   cap at $150 = ${150*0.07:.2f}")
print("  'pass' = signals whose full-lot risk fits under the 7% cap without")
print("  the bot having to compress the entry toward the stop.")
