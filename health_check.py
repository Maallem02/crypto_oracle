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


def _realised_balance_peak(current_balance, days=120):
    """Plus haut SOLDE réellement atteint DEPUIS LE DERNIER MOUVEMENT DE FONDS.

    account_info() ne donne que le solde courant. On rejoue les deals à
    l'envers depuis le solde actuel pour reconstruire la courbe, puis on prend
    son maximum.

    L'ancrage au dernier dépôt/retrait (type 2) est essentiel : sans lui, une
    fenêtre de 60 jours sur ce compte d'essai remontait à une période où le
    solde valait 4450$, et affichait un drawdown de 96.8% qui ne veut rien
    dire. Ce qui compte est "depuis que j'ai remis de l'argent, où j'en suis" —
    donc le pic est mesuré à partir du dernier mouvement de fonds.

    Retourne (peak, depuis) ou (None, None) si l'historique est indisponible —
    l'appelant retombe alors sur peak_equity seul plutôt que d'inventer un
    chiffre.
    """
    try:
        deals = mt5.history_deals_get(
            datetime.now() - timedelta(days=days),
            datetime.now() + timedelta(hours=6)) or []
        moves = sorted([d for d in deals if d.type in (0, 1, 2)],
                       key=lambda d: d.time)
        if not moves:
            return None, None

        # Rejouer à l'envers depuis le solde actuel pour dater chaque point.
        total = sum(d.profit + d.commission + d.swap for d in moves)
        run = current_balance - total
        curve = []
        for d in moves:
            run += d.profit + d.commission + d.swap
            curve.append((d.time, run, d.type))

        # Repartir du dernier mouvement de fonds (dépôt ou retrait).
        last_funding = max((i for i, (_, _, t) in enumerate(curve) if t == 2),
                           default=None)
        if last_funding is not None:
            era = curve[last_funding:]
        else:
            era = curve
        if not era:
            return None, None
        peak = max(v for _, v, _ in era)
        return peak, datetime.fromtimestamp(era[0][0])
    except Exception:
        return None, None


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

    # ── second bot : Volume Profile (2026-09-24) ─────────────────────────────
    # Indépendant du bot SMC : son propre marche/arrêt et son propre minuteur.
    # Le Stop de l'app ne l'arrête PAS — d'où cette section séparée.
    section("VOLUME PROFILE BOT (independent)")
    try:
        vp = requests.get(f"{API}/trading/vp/status", timeout=10).json()
        age = vp.get("last_scan_age_min")
        vs = vp.get("settings") or {}
        print(f"  enabled={vp.get('enabled')}  symbols={vp.get('symbols')}  "
              f"lots={vp.get('lot_sizes')}  risk cap {vp.get('risk_cap_pct')}%")
        off = [k for k, v in (vs.get("setups") or {}).items() if not v]
        print(f"  daily loss limit {vs.get('daily_loss_pct', 0) or 'OFF'}"
              f"{'%' if vs.get('daily_loss_pct') else ''}   "
              f"setups off: {', '.join(off) if off else 'none'}   (all set from the app)")
        if vp.get("paused"):
            warn(f"VP paused: {vp['paused']}")
        if not vp.get("enabled"):
            ok("stopped on purpose (POST /trading/vp/start to resume)")
        elif age is None:
            warn("enabled but no scan recorded yet")
        elif age > 5:
            alert(f"enabled but last scan was {age:.0f} min ago - VP job may be dead")
        else:
            ok(f"last scan {age:.1f} min ago")
        if vp.get("last_error"):
            warn(f"last VP error: {vp['last_error']}")
    except Exception as e:
        warn(f"VP status unreachable: {e}")

    # ── account ─────────────────────────────────────────────────────────────
    section("ACCOUNT")
    acc = mt5.account_info()
    peak = st.get("peak_equity") or (acc.equity if acc else 0)
    if acc:
        dd = 100 * (peak - acc.equity) / peak if peak else 0
        print(f"  balance ${acc.balance:.2f}   equity ${acc.equity:.2f}")

        # Deux drawdowns différents, et les confondre induit en erreur.
        #
        # peak_equity est un pic d'equity INTRADAY : il inclut le profit
        # flottant d'une position encore ouverte. Le 2026-09-15 il affichait
        # 159.18$ alors que le solde n'avait jamais dépassé 151.13$ — l'écart
        # venait d'un ETH en cours qui a ensuite rendu une partie. Résultat
        # affiché : -11.0% au lieu du -6.3% réellement encaissé.
        #
        # On garde peak_equity pour l'ALERTE parce que c'est exactement la
        # valeur que compare le halt SURVIVAL (router.py ~1722) : le seuil doit
        # être jugé sur la même grandeur que celle qui le déclenche. Mais on
        # affiche AUSSI le pic de solde réalisé, qui est ce que l'utilisateur
        # a vraiment gagné puis perdu.
        bal_peak, since = _realised_balance_peak(acc.balance)
        if bal_peak:
            bdd = 100 * (bal_peak - acc.balance) / bal_peak if bal_peak else 0
            print(f"  peak equity  ${peak:8.2f}  intraday, flottant inclus "
                  f"-> drawdown {dd:5.1f}%   [seuil du halt SURVIVAL]")
            print(f"  peak balance ${bal_peak:8.2f}  réalisé depuis "
                  f"{since:%m-%d %H:%M}   -> drawdown {bdd:5.1f}%   "
                  f"[réellement encaissé]")
        else:
            print(f"  peak ${peak:.2f}")

        if dd >= 35:
            alert(f"drawdown {dd:.1f}% from peak equity - at or past the -35% halt")
        elif dd >= 25:
            warn(f"drawdown {dd:.1f}% from peak equity")
        else:
            ok(f"drawdown {dd:.1f}% from peak equity")

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
    # 234000 = stratégie SMC, 234100 = stratégie Volume Profile (2026-09-24).
    # Les deux sont surveillées ici : l'alerte "pas de stop" doit couvrir les
    # deux stratégies, pas seulement celle qui existait à l'écriture du script.
    STRAT = {234000: "SMC", 234100: "VP "}
    positions = [p for p in (mt5.positions_get() or []) if p.magic in STRAT]
    if not positions:
        ok("none")
    for p in positions:
        age_h = (time.time() - p.time) / 3600.0
        if p.magic == 234100:
            try:
                sig = db.execute("SELECT sl FROM vp_log WHERE ticket=? AND status='taken'",
                                 (p.ticket,)).fetchone()
                sig = {"data": json.dumps({"sl": sig["sl"]})} if sig else None
            except sqlite3.OperationalError:
                sig = None
        else:
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
        line = (f"[{STRAT[p.magic]}] {p.symbol:9s} ticket={p.ticket} "
                f"{'buy' if p.type == 0 else 'sell':4s} "
                f"age={age_h:.1f}h  P&L ${p.profit:+.2f}{r_now}")
        if p.sl == 0:
            alert(f"{line} - NO STOP LOSS attached")
        elif age_h > 48:
            warn(f"{line} - open more than 2 days")
        else:
            ok(line)

    # ── performance ─────────────────────────────────────────────────────────
    since = args.since or (session_start[:10] if session_start else "2026-08-17")
    section(f"PERFORMANCE - SMC strategy (scalp_log) since {since}")
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

    # ── Volume Profile, tenue à part (journal vp_log, magic 234100) ─────────
    section(f"PERFORMANCE - VOLUME PROFILE strategy (vp_log) since {since}")
    try:
        vp = db.execute(
            "SELECT setup, outcome, profit FROM vp_log WHERE status='taken' AND timestamp>=?",
            (since,)).fetchall()
    except sqlite3.OperationalError:
        vp = []
    if not vp:
        print("  no Volume Profile trades in window")
    else:
        closed = [r for r in vp if r["outcome"] is not None]
        print(f"  trades={len(vp)}  closed={len(closed)}  open={len(vp) - len(closed)}")
        for setup in ("poc_bounce", "va_reversal", "va_breakout"):
            c = [r for r in closed if r["setup"] == setup]
            if c:
                w = sum(1 for r in c if r["outcome"] == 1)
                print(f"    {setup:12s} n={len(c):3d} net=${sum(r['profit'] for r in c):+8.2f} "
                      f"WR={100*w/len(c):5.1f}%")
        if closed:
            w = sum(1 for r in closed if r["outcome"] == 1)
            print(f"    {'ALL':12s} n={len(closed):3d} "
                  f"net=${sum(r['profit'] for r in closed):+8.2f} WR={100*w/len(closed):5.1f}%"
                  f"   (breakeven at 2R = 33.3%)")

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
