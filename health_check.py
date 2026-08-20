"""
Health check for the scalping bot — STRICTLY READ-ONLY.

Exists because of the 2026-08-19/20 incident: the symbol streak-pause
deadlocked and silently removed BTC, XAUUSD and XAGUSD from the scan for two
full days. Nothing surfaced it — the skip is silent by design, so those
symbols simply stopped appearing in rejection_log and no status field, log
line or alert changed. The same week, a pending order's age froze over the
weekend and the 480-minute purge never saw it.

Both bugs were invisible from the outside and both were found only by
manually digging through the database. This script turns that dig into one
command.

It NEVER places, cancels, modifies or closes anything. It only reads:
MT5 account/positions/orders, the sqlite log tables, and GET /status.

Usage:
    venv\\Scripts\\python.exe health_check.py
    venv\\Scripts\\python.exe health_check.py --hours 4 --since 2026-08-17

Exit code is 1 if any [ALERT] fired, so it can be wired to a scheduler later.
"""
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import requests
import MetaTrader5 as mt5

DB = "crypto_oracle_8000.db"
API = "http://127.0.0.1:8000"

alerts, warns = [], []


def alert(msg):
    alerts.append(msg)
    print(f"  [ALERT] {msg}")


def warn(msg):
    warns.append(msg)
    print(f"  [WARN ] {msg}")


def ok(msg):
    print(f"  [ ok  ] {msg}")


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0,
                    help="silence threshold per symbol, in hours (default 3)")
    ap.add_argument("--since", default=None,
                    help="performance window start, YYYY-MM-DD (default: session start)")
    args = ap.parse_args()

    # Reuse the REAL gating logic so this check can never drift from the bot.
    from features.trading.router import BLOCKED_SYMBOLS, is_trading_session

    if not mt5.initialize(timeout=10000):
        print(f"[FATAL] MT5 attach failed: {mt5.last_error()}")
        return 1

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    now = datetime.now()

    # ── bot state ───────────────────────────────────────────────────────────
    section("BOT")
    try:
        st = requests.get(f"{API}/trading/scalping/status", timeout=15).json()
    except Exception as e:
        print(f"  [FATAL] status endpoint unreachable: {e}")
        mt5.shutdown()
        return 1

    settings = st.get("settings", {})
    session_start = st.get("session_start")
    print(f"  running={st.get('running')}  session_start={session_start}")
    print(f"  trades_today={st.get('trades_today')}  daily_pnl={st.get('daily_pnl')}")

    if not st.get("running"):
        alert(f"bot is NOT running (stopped_reason={st.get('stopped_reason')})")
    if st.get("last_scan"):
        scan_age = (now - datetime.fromisoformat(st["last_scan"])).total_seconds() / 60
        if scan_age > 5:
            alert(f"last scan was {scan_age:.0f} min ago - scan loop may be dead")
        else:
            ok(f"last scan {scan_age:.1f} min ago")
    else:
        warn("no scan recorded yet this session")

    # ── account ─────────────────────────────────────────────────────────────
    section("ACCOUNT")
    acc = mt5.account_info()
    peak = st.get("peak_equity") or (acc.equity if acc else 0)
    if acc:
        dd = 100 * (peak - acc.equity) / peak if peak else 0
        print(f"  balance ${acc.balance:.2f}   equity ${acc.equity:.2f}   peak ${peak:.2f}")
        if dd >= 35:
            alert(f"drawdown {dd:.1f}% from peak - at or past the -35% halt")
        elif dd >= 25:
            warn(f"drawdown {dd:.1f}% from peak")
        else:
            ok(f"drawdown {dd:.1f}% from peak")

    # ── symbol coverage: THE lockout detector ───────────────────────────────
    section(f"SYMBOL COVERAGE (silence threshold {args.hours}h)")
    enabled = settings.get("enabled_symbols", [])
    cut = (now - timedelta(hours=args.hours)).strftime("%Y-%m-%dT%H:%M:%S")
    for sym in enabled:
        row = db.execute(
            "SELECT COUNT(*) n, MAX(timestamp) last FROM rejection_log "
            "WHERE symbol=? AND timestamp>=?", (sym, cut)).fetchone()
        last_any = db.execute(
            "SELECT MAX(timestamp) last FROM rejection_log WHERE symbol=?",
            (sym,)).fetchone()["last"]

        if sym.upper() in BLOCKED_SYMBOLS:
            ok(f"{sym:8s} blocked at code level (BLOCKED_SYMBOLS) - silence expected")
            continue
        if not is_trading_session(sym):
            ok(f"{sym:8s} outside its trading session - silence expected")
            continue

        if row["n"] == 0:
            silent_for = "never"
            if last_any:
                silent_for = f"{(now - datetime.fromisoformat(last_any)).total_seconds()/3600:.1f}h"
            alert(f"{sym:8s} in session but ZERO evaluations in {args.hours}h "
                  f"(last seen {silent_for} ago) - likely streak-paused or locked out")
        else:
            ok(f"{sym:8s} {row['n']:5d} evaluations, last {row['last'][11:19]}")

    # ── streak-pause state (reconstructed from the DB, same query as the bot)
    section("STREAK PAUSE STATE")
    if session_start:
        for sym in enabled:
            if sym.upper() in BLOCKED_SYMBOLS:
                continue
            rows = db.execute(
                "SELECT outcome, COALESCE(closed_at, timestamp) t FROM scalp_log "
                "WHERE symbol=? AND outcome IS NOT NULL AND timestamp>=? "
                "ORDER BY COALESCE(closed_at, timestamp) DESC LIMIT 2",
                (sym, session_start)).fetchall()
            o = [r["outcome"] for r in rows]
            if len(o) >= 2 and all(x == 0 for x in o):
                warn(f"{sym:8s} last 2 closed are both losses {o} "
                     f"(last {rows[0]['t'][:16]}) - pause condition is TRUE; "
                     f"with the 08-20 fix it should pause once, not forever")
            else:
                ok(f"{sym:8s} last2={o if o else 'no closes this session'}")

    # ── pending orders: real calendar age, not tick age ──────────────────────
    section("PENDING ORDERS")
    cap = settings.get("pending_max_age_minutes", 480)
    orders = [o for o in (mt5.orders_get() or []) if o.magic == 234000]
    if not orders:
        ok("none")
    for o in orders:
        wall = (time.time() - o.time_setup) / 60.0
        tick = mt5.symbol_info_tick(o.symbol)
        tick_age = (tick.time - o.time_setup) / 60.0 if tick else None
        line = (f"{o.symbol:9s} ticket={o.ticket} @ {o.price_open} "
                f"wall_age={wall:.0f}min tick_age="
                f"{'n/a' if tick_age is None else f'{tick_age:.0f}min'} cap={cap}")
        if wall > cap * 1.25:
            alert(f"{line} - well past cap, purge is not firing")
        elif wall > cap:
            warn(f"{line} - past cap, should be purged on the next scan")
        else:
            ok(line)

    # ── open positions ──────────────────────────────────────────────────────
    section("OPEN POSITIONS")
    positions = [p for p in (mt5.positions_get() or []) if p.magic == 234000]
    if not positions:
        ok("none")
    for p in positions:
        age_h = (time.time() - p.time) / 3600.0
        sig = db.execute("SELECT data FROM scalp_log WHERE ticket=?", (p.ticket,)).fetchone()
        r_now = ""
        if sig and sig["data"]:
            try:
                sl0 = json.loads(sig["data"]).get("sl")
                if sl0:
                    risk = abs(p.price_open - sl0)
                    sign = 1 if p.type == 0 else -1
                    if risk:
                        r_now = f"  now {sign * (p.price_current - p.price_open) / risk:+.2f}R"
            except Exception:
                pass
        line = (f"{p.symbol:9s} ticket={p.ticket} {'buy' if p.type == 0 else 'sell':4s} "
                f"age={age_h:.1f}h  P&L ${p.profit:+.2f}{r_now}")
        if p.sl == 0:
            alert(f"{line} - NO STOP LOSS attached")
        elif age_h > 48:
            warn(f"{line} - open more than 2 days")
        else:
            ok(line)

    # ── performance ─────────────────────────────────────────────────────────
    since = args.since or (session_start[:10] if session_start else "2026-08-17")
    section(f"PERFORMANCE since {since}")
    rows = db.execute(
        "SELECT symbol, profit FROM scalp_log WHERE profit IS NOT NULL AND timestamp>=?",
        (since,)).fetchall()
    if not rows:
        print("  no closed trades in window")
    else:
        n = len(rows)
        net = sum(r["profit"] for r in rows)
        wins = [r for r in rows if r["profit"] > 0]
        losses = [r for r in rows if r["profit"] <= 0]
        print(f"  n={n}  net=${net:+.2f}  WR={100*len(wins)/n:.1f}%")
        if wins:
            print(f"  avg win  ${sum(r['profit'] for r in wins)/len(wins):+.2f}")
        if losses:
            print(f"  avg loss ${sum(r['profit'] for r in losses)/len(losses):+.2f}")
        per = {}
        for r in rows:
            a = per.setdefault(r["symbol"], [0, 0.0, 0])
            a[0] += 1
            a[1] += r["profit"]
            a[2] += 1 if r["profit"] > 0 else 0
        print("  per symbol:")
        for s, (c, p, w) in sorted(per.items(), key=lambda kv: kv[1][1]):
            print(f"    {s:9s} n={c:3d} net=${p:+8.2f} WR={100*w/c:5.1f}%")
        if n < 30:
            print(f"\n  NOTE: n={n} is below the ~30 this system needs to be "
                  f"informative.\n  Three times a smaller sample pointed the wrong way "
                  f"(metals, pendings, meta gate).")

    # ── verdict ─────────────────────────────────────────────────────────────
    section("VERDICT")
    if alerts:
        print(f"  {len(alerts)} ALERT(S):")
        for a in alerts:
            print(f"    - {a}")
    if warns:
        print(f"  {len(warns)} warning(s):")
        for w in warns:
            print(f"    - {w}")
    if not alerts and not warns:
        print("  all clear")

    mt5.shutdown()
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
