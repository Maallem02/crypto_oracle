"""Periodic bot health check (read-only). Prints 'STATUS: OK' or 'STATUS: ISSUES'
followed by one line per finding, then a short stats line. Remembers what it
already reported (bot_health_state.json) so log events are only shown once.

  python bot_health.py            # health check
  python bot_health.py --summary  # + today's closed trades per bot
"""
import glob
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import requests

UTC = timezone.utc
BK = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
API = "http://127.0.0.1:8000"
DEMO_LOGIN = 262890622
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_health_state.json")
BOT = {234000: "SMC", 234100: "VP", 0: "manual"}
issues, notes = [], []

try:
    st = json.load(open(STATE))
except (OSError, ValueError):
    st = {}

# ── backend + bots ───────────────────────────────────────────────────────────
smc = vp = None
try:
    smc = requests.get(f"{API}/trading/scalping/status", timeout=20).json()
    vp = requests.get(f"{API}/trading/vp/status", timeout=30).json()
except Exception as e:
    issues.append(f"backend unreachable ({type(e).__name__})")
    wd = open(os.path.join(BK, "watchdog.log"), encoding="utf-8", errors="replace").read().splitlines()[-3:]
    issues.append("watchdog last lines: " + " | ".join(wd))

if smc:
    if not smc.get("running"):
        issues.append(f"Scalping bot STOPPED: {smc.get('stopped_reason')}")
    else:
        ls = smc.get("last_scan")
        age = (datetime.now() - datetime.fromisoformat(ls)).total_seconds() / 60 if ls else 999
        if age > 6:
            issues.append(f"Scalping last scan {age:.0f} min ago (stalled?)")
if vp:
    if not vp.get("enabled"):
        issues.append("Volume Profile bot is switched OFF")
    elif (vp.get("last_scan_age_min") or 999) > 6:
        issues.append(f"VP last scan {vp.get('last_scan_age_min')} min ago (stalled?)")
    if vp.get("last_error"):
        issues.append(f"VP error: {vp['last_error']}")
    if vp.get("paused"):
        notes.append(f"VP paused: {vp['paused']}")

# ── account + positions ──────────────────────────────────────────────────────
mt5.initialize(timeout=10000)
a = mt5.account_info()
if a is None:
    issues.append("MT5 terminal not reachable")
else:
    if a.trade_mode != 0 or a.login != DEMO_LOGIN:
        issues.append(f"MT5 is on account {a.login} ({'REAL' if a.trade_mode else 'demo'}), expected demo {DEMO_LOGIN}")
    db = sqlite3.connect(os.path.join(BK, "crypto_oracle_8000.db"))
    for p in mt5.positions_get() or []:
        who = BOT.get(p.magic, p.magic)
        buy = p.type == 0
        if not p.sl:
            issues.append(f"{who} {p.symbol} {'buy' if buy else 'sell'} #{p.ticket} has NO STOP LOSS "
                          f"(P/L {p.profit:+.2f})")
            continue
        # original stop: opening order, else the bots' own records
        o = next(iter(mt5.history_orders_get(ticket=p.identifier) or []), None)
        sl0 = o.sl if o is not None and o.sl else None
        if sl0 is None:
            row = (db.execute("SELECT sl FROM vp_log WHERE ticket=?", (p.ticket,)).fetchone()
                   or db.execute("SELECT sl FROM trade_signals WHERE ticket=?", (p.ticket,)).fetchone())
            sl0 = row[0] if row and row[0] else None
        if sl0:
            tol = abs(p.price_open - sl0) * 0.02
            widened = (p.sl < sl0 - tol) if buy else (p.sl > sl0 + tol)
            if widened:
                issues.append(f"{who} {p.symbol} #{p.ticket}: stop moved FURTHER than the bot's "
                              f"({sl0:.5g} -> {p.sl:.5g}) - manual change?")
    # equity drop since the start of the local day
    day0 = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(day0.astimezone(UTC), datetime.now(UTC) + timedelta(hours=3)) or []
    today = [d for d in deals if d.entry in (1, 3)]
    pnl_today = sum(d.profit + d.commission + d.swap for d in deals if d.type in (0, 1))
    start_bal = a.balance - pnl_today - sum(d.profit for d in deals if d.type not in (0, 1))
    if start_bal > 0 and (a.equity - start_bal) / start_bal < -0.15:
        issues.append(f"equity {a.equity:.2f} is {100 * (a.equity - start_bal) / start_bal:.0f}% "
                      f"vs today's start {start_bal:.2f}")
    open_n = len(mt5.positions_get() or [])
    stats = (f"balance {a.balance:.2f} equity {a.equity:.2f} | open {open_n} | "
             f"today closed {len(today)} trades {pnl_today:+.2f}")

    if "--summary" in sys.argv:
        by = {}
        pos = {}
        for d in deals:
            if d.type in (0, 1):
                pos.setdefault(d.position_id, []).append(d)
        for pid, ds in pos.items():
            outs = [d for d in ds if d.entry in (1, 3)]
            if not outs:
                continue
            ins = mt5.history_deals_get(position=pid) or []
            first = min(ins, key=lambda d: d.time) if ins else outs[0]
            b = BOT.get(first.magic, "other")
            v = by.setdefault(b, [0, 0, 0.0])
            pl = sum(d.profit + d.commission + d.swap for d in outs)
            v[0] += 1
            v[1] += pl > 0
            v[2] += pl
        notes.append("today by bot: " + (", ".join(f"{b} {n} trades ({w} won) {p:+.2f}"
                                                    for b, (n, w, p) in by.items()) or "no closed trade"))
mt5.shutdown()

# ── new events in the backend log since the last check ───────────────────────
logs = sorted(glob.glob(os.path.join(BK, "backend_8000_*.log")), key=os.path.getmtime)
if logs:
    cur = logs[-1]
    off = st.get("log_off", 0) if st.get("log") == cur else 0
    with open(cur, encoding="utf-8", errors="replace") as fh:
        fh.seek(off)
        new = fh.read()
        st["log"], st["log_off"] = cur, fh.tell()
    errs = {}                                            # same line repeated -> shown once, with a count
    for line in new.splitlines():
        if "[POS/HALF]" in line or "[POS/HALF-BE]" in line:
            notes.append("half-at-1R: " + line.strip()[:160])
        elif any(k in line for k in ("[POS/HALF-ERR]", "[POS/HALF-BE-ERR]", "[POS/ERR]",
                                     "[VP] scan error", "Traceback", "[FATAL]")):
            k = line.strip()[:160]
            errs[k] = errs.get(k, 0) + 1
    for k, n in errs.items():
        issues.append(f"log ({n}x): {k}")
# watchdog restarts since last check
wd_path = os.path.join(BK, "watchdog.log")
wd_lines = open(wd_path, encoding="utf-8", errors="replace").read().splitlines()
seen = st.get("wd_lines", len(wd_lines))
for line in wd_lines[seen:]:
    if "NOTHING LISTENING" in line or "STALLED" in line or "RESTART FAILED" in line:
        notes.append("watchdog: " + line.strip()[:140])
st["wd_lines"] = len(wd_lines)
json.dump(st, open(STATE, "w"))

print("STATUS: " + ("ISSUES" if issues else "OK"))
for i in issues:
    print("  ! " + i)
for n in notes:
    print("  - " + n)
if a is not None:
    print("  " + stats)
