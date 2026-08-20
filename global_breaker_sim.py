"""
Global directional circuit-breaker simulation (2026-08-03)
==========================================================
The existing breaker is PER ASSET CLASS. On 08-03 four consecutive SELL
stop-outs spread across metals(1) / crypto(2) / forex(1) — every class stayed
at or under its own threshold, so nothing fired, and the day gave back $31.66
of a $35.41 peak.

This replays real trades as an EVENT QUEUE (open and close interleaved in true
chronological order) so that a trade blocked by the breaker correctly does NOT
contribute to the streak that follows. A naive "filter the list" pass would
overstate the benefit, because most blocked trades are losers whose removal
would have changed everything after them.

Run:  python global_breaker_sim.py
"""
import sqlite3
from datetime import datetime, timedelta

DB    = "crypto_oracle_8000.db"
SINCE = "2026-07-20"
BE_EPS = 0.15          # |profit| below this = breakeven, ignored (same as live)


def load():
    c = sqlite3.connect(DB)
    rows = c.execute(
        "SELECT timestamp, COALESCE(closed_at,timestamp), symbol, action, outcome, profit "
        "FROM scalp_log WHERE timestamp>=? AND outcome IS NOT NULL AND ticket IS NOT NULL "
        "ORDER BY timestamp", (SINCE,)).fetchall()
    c.close()
    out = []
    for o, cl, sym, act, outc, prof in rows:
        try:
            od, cd = datetime.fromisoformat(o), datetime.fromisoformat(cl)
        except Exception:
            continue
        if cd < od:
            cd = od
        out.append({"open": od, "close": cd, "symbol": sym, "action": act,
                    "outcome": outc, "profit": prof or 0.0})
    return out


def simulate(trades, consec, block_hours):
    """Event-queue replay. Returns (net, n_taken, n_blocked, blocked_pnl)."""
    events = []
    for i, t in enumerate(trades):
        events.append((t["open"], 0, i))     # 0 = open, sorts before close at same ts
        events.append((t["close"], 1, i))
    events.sort(key=lambda e: (e[0], e[1]))

    blocked_until = {}      # direction -> datetime
    streak_dir, streak = None, 0
    taken, net, blocked, blocked_pnl = set(), 0.0, 0, 0.0

    for ts, kind, i in events:
        t = trades[i]
        if kind == 0:                                    # a trade wants to open
            if consec and blocked_until.get(t["action"], datetime.min) > ts:
                blocked += 1
                blocked_pnl += t["profit"]
                continue
            taken.add(i)
        else:                                            # a taken trade resolves
            if i not in taken:
                continue
            net += t["profit"]
            if abs(t["profit"]) < BE_EPS:                # breakeven -> ignore
                continue
            if t["outcome"] == 1:                        # a win breaks the streak
                if t["action"] == streak_dir:
                    streak_dir, streak = None, 0
                continue
            if t["action"] == streak_dir:
                streak += 1
            else:
                streak_dir, streak = t["action"], 1
            if consec and streak >= consec:
                blocked_until[streak_dir] = ts + timedelta(hours=block_hours)
    return net, len(taken), blocked, blocked_pnl


def main():
    tr = load()
    print(f"resolved trades since {SINCE}: {len(tr)}")
    base, n, _, _ = simulate(tr, consec=0, block_hours=0)
    print(f"baseline (no global breaker): {n} trades, net ${base:+.2f}\n")

    print(f"  {'consec':>7} {'block_h':>8} {'taken':>6} {'blocked':>8} "
          f"{'net':>9} {'vs base':>9} {'blocked P&L':>12}")
    print("  " + "-" * 64)
    best = None
    for consec in (2, 3, 4):
        for bh in (2, 3, 4, 6):
            net, n2, bl, bpnl = simulate(tr, consec, bh)
            d = net - base
            if best is None or d > best[0]:
                best = (d, consec, bh, net, n2, bl)
            print(f"  {consec:>7} {bh:>8} {n2:>6} {bl:>8} {net:>+9.2f} {d:>+9.2f} {bpnl:>+12.2f}")
    print("  " + "-" * 64)
    d, consec, bh, net, n2, bl = best
    print(f"  BEST: consec={consec} block={bh}h -> ${net:+.2f} ({d:+.2f} vs base), "
          f"{n2} taken / {bl} blocked")

    # per-day, to check it is not one lucky day
    print(f"\n=== day-by-day with consec={consec}, block={bh}h ===")
    days = sorted({t["open"].date() for t in tr})
    tot_b = tot_a = 0.0
    for day in days:
        sub = [t for t in tr if t["open"].date() == day]
        b, _, _, _ = simulate(sub, 0, 0)
        a, _, nb, _ = simulate(sub, consec, bh)
        tot_b += b; tot_a += a
        flag = "" if abs(a - b) < 0.005 else ("  BETTER" if a > b else "  worse")
        print(f"  {day}  base ${b:>+8.2f} -> ${a:>+8.2f}  (blocked {nb}){flag}")
    print(f"  {'TOTAL':10s} base ${tot_b:>+8.2f} -> ${tot_a:>+8.2f}")


if __name__ == "__main__":
    main()
