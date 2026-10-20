"""Contrôle d'exécution du bot Daily Trend (2026-10-11).

Demande utilisateur après la vidéo SMB « AI trade autopsy » : la version « bot »
de l'autopsie. Un bot suit toujours ses règles ; ce qui peut dériver, c'est
l'EXÉCUTION : backend arrêté à la clôture, entrée en retard, glissement de prix,
stop mal posé, taille trop grosse, sortie manquée, fermeture à la main. Ce module
compare ce que les règles TESTÉES disaient de faire et ce qui s'est réellement
passé sur le compte, puis place le résultat réel dans la fourchette attendue.

  1. DÉCISIONS — pour chaque clôture journalière depuis le premier scan du bot :
     decide() (prouvé identique au code de l'étude, test_dt.py partie 5) avec
     l'état RÉEL de la position -> entrée / sortie attendue ? faite ? à l'heure ?
     Une entrée non faite est expliquée par le journal dt_log (compte trop petit,
     plafond crypto plein, bot arrêté) ou signalée « aucune trace » (backend
     arrêté ?).
  2. TRADES — chaque trade réel est comparé à son « jumeau » : le trade que le
     code de l'étude fait sur le même signal et les mêmes bougies (follow) :
     retard, glissement d'entrée et de sortie en R, distance du stop / 2 ATR,
     risque %, jour et type de sortie, résultat R réel / testé.
  3. FOURCHETTE — somme des R réels, pire baisse (en R) et plus longue série de
     pertes, comparées à 5 000 tirages de n trades parmi ceux du test 2018+
     (coûts Exness du moment).
Lecture seule : aucun ordre, aucun réglage modifié."""
import threading
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import numpy as np

from core.database import get_db
from features.daily_trend import runner
from features.trading.executor import SYMBOL_MAP
from features.trading.mt5_client import ensure_mt5
from features.trading.risk_manager import DT_MAGIC
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
LATE_MIN = 30          # une entrée / sortie plus de 30 min après la clôture = en retard (scan toutes les 5 min)
SLIP_WARN_R = 0.10     # glissement au-delà du spread du test, en R
STOP_TOL = 0.02        # distance du stop à +-2 % de 2 ATR
RISK_TOL = 0.05        # points de % au-dessus du risque permis
REF_START = datetime(2018, 3, 1, tzinfo=UTC).timestamp()   # comme les études portefeuille
FULL_BARS = 5000
N_SIM = 5000
CACHE_S = 600
_cache = {"t": 0.0, "key": None, "data": None}
_lock = threading.Lock()


# ── la règle testée, trade par trade (code des études) ───────────────────────
def follow(d, k, sp, sw, atr, last=None):
    """Le trade que le code testé fait sur le signal de la bougie k : achat à
    l'ouverture de k+1 (+ spread), stop 2 ATR(k), sortie au stop (au pire à
    l'ouverture si gap) ou à la clôture sous le plus bas 10 jours. Copie de la
    boucle de trend_robustness_study.donchian (long-only, next_open). None si
    l'entrée n'a pas encore eu lieu ; ix None = toujours ouvert à la bougie last."""
    o, l, c = (d[x].astype(float) for x in ("open", "low", "close"))
    last = len(c) - 1 if last is None else last
    ie = k + 1
    if ie > last:
        return None
    px = o[ie] * (1 + sp)
    dist = runner.STOP_ATR * float(atr[k])
    t = {"k": k, "ie": ie, "entry": px, "stop": px - dist, "dist": dist,
         "ix": None, "exit": None, "kind": None, "R": None, "t0": int(d["time"][ie]), "t1": None}
    for i in range(ie, last + 1):
        if l[i] <= t["stop"]:
            t.update(ix=i, exit=t["stop"] if i == ie else min(t["stop"], o[i]), kind="stop")
            break
        if i > ie and c[i] < l[i - runner.N_EXIT:i].min():
            t.update(ix=i, exit=c[i], kind="channel")
            break
    if t["ix"] is not None:
        nights = (int(d["time"][t["ix"]]) - t["t0"]) / 86400
        t["R"] = (t["exit"] - px - sw * nights * px) / dist
        t["t1"] = int(d["time"][t["ix"]])
    return t


def reference_trades(d, sp, sw, atr=None):
    """Tous les trades du code testé sur l'historique d (mêmes bornes que l'étude :
    un trade encore ouvert à la fin n'est pas compté)."""
    atr = atr_series(d) if atr is None else atr
    h, c = d["high"].astype(float), d["close"].astype(float)
    out, i, last = [], runner.N_ENTRY + 1, len(c) - 2
    while i <= last:
        if c[i] > h[i - runner.N_ENTRY:i].max():
            t = follow(d, i, sp, sw, atr, last=last)
            if t is None or t["ix"] is None:
                break
            out.append(t)
            i = t["ix"] + 1
        else:
            i += 1
    return out


def costs(mt5sym, d):
    """Spread (part du prix) et swap acheteur par nuit, comme les études."""
    si = mt5.symbol_info(mt5sym)
    last = d[-365:]
    sp = float(np.median(last["spread"]) * si.point / np.median(last["close"]))
    px = float(d["close"][-1])
    sw = -min(0.0, si.swap_long * si.point / px) if si.swap_mode == 1 else 0.0
    return sp, sw


# ── ce qui s'est réellement passé ────────────────────────────────────────────
def _exit_kind(ds):
    if any(x.reason == mt5.DEAL_REASON_SL or (x.comment or "").startswith("[sl") for x in ds):
        return "stop"
    if any((x.comment or "").startswith("DT exit") for x in ds):
        return "channel"
    if any(x.reason == mt5.DEAL_REASON_SO for x in ds):
        return "stop-out"
    return "manual"


def live_trades(since_ts, now):
    """Positions du bot (magic DT_MAGIC à l'entrée) ouvertes depuis since_ts - 3 j.
    Les deals de sortie sont pris par position, quel que soit leur magic (une
    fermeture à la main n'a pas le magic du bot)."""
    deals = mt5.history_deals_get(datetime.fromtimestamp(since_ts - 3 * 86400, UTC), now + timedelta(hours=3)) or []
    mine = {x.position_id for x in deals if x.magic == DT_MAGIC and x.entry == mt5.DEAL_ENTRY_IN}
    by = {}
    for x in deals:
        if x.position_id in mine and x.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
            by.setdefault(x.position_id, []).append(x)
    out = []
    for pid, ds in by.items():
        ins = [x for x in ds if x.entry == mt5.DEAL_ENTRY_IN]
        outs = [x for x in ds if x.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        i = min(ins, key=lambda x: x.time)
        vol = sum(x.volume for x in ins)
        price = sum(x.price * x.volume for x in ins) / vol
        order = next(iter(mt5.history_orders_get(ticket=i.order) or []), None)
        sl0 = float(order.sl) if order is not None and order.sl else 0.0
        loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, i.symbol, vol, price, sl0) if sl0 else None
        risk_usd = -loss if loss is not None and loss < 0 else None
        out_vol = sum(x.volume for x in outs)
        closed = out_vol + 1e-9 >= vol
        pnl = sum(x.profit + x.commission + x.swap + getattr(x, "fee", 0.0) for x in ds)
        cur = next(iter(mt5.positions_get(ticket=pid) or []), None)
        out.append({
            "ticket": pid, "symbol": i.symbol[:3], "mt5sym": i.symbol, "t_in": int(i.time), "price": price,
            "volume": vol, "sl0": sl0, "risk_usd": risk_usd,
            "t_out": int(max(x.time for x in outs)) if closed else None,
            "exit": (sum(x.price * x.volume for x in outs) / out_vol) if closed else None,
            "kind": _exit_kind(outs) if closed else None,
            "partial": bool(outs) and not closed,
            "pnl": pnl if closed else None,
            "R": (pnl / risk_usd) if closed and risk_usd else None,
            "sl_now": float(cur.sl) if cur is not None else None,
        })
    return sorted(out, key=lambda x: x["t_in"])


def _journal_rows(since_ts):
    """Lignes dt_log utiles (les tests remplacent cette fonction)."""
    runner._ensure_ready()
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT timestamp, symbol, event, bar_time, reason, ticket, risk_pct FROM dt_log "
        "WHERE timestamp >= ? ORDER BY id", (datetime.fromtimestamp(since_ts - 86400, UTC).isoformat(),))]
    conn.close()
    return rows


def first_scan_bar():
    """Bougie jugée par le tout premier scan du bot (début du contrôle), ou None."""
    runner._ensure_ready()
    conn = get_db()
    r = conn.execute("SELECT MIN(bar_time) AS b FROM dt_log").fetchone()
    conn.close()
    return int(datetime.fromisoformat(r["b"]).timestamp()) if r and r["b"] else None


# ── comparaison ──────────────────────────────────────────────────────────────
def _day(ts):
    return datetime.fromtimestamp(ts, UTC).strftime("%d/%m/%Y")


def _skip_status(reason):
    """Entrée non prise : voulu (choix ou garde-fou du bot) ou anomalie ?"""
    r = (reason or "").lower()
    if ("account too small" in r or "cap full" in r or "stopped" in r or " is off" in r
            or "opposite position" in r or "opposite order" in r):
        return "info"
    return "problem"


def _compare(lt, sh, T, journal_entry):
    """Un trade réel face à son jumeau testé."""
    notes, status = [], "ok"

    def flag(level, text):
        nonlocal status
        notes.append(text)
        if level == "problem" or (level == "warn" and status == "ok"):
            status = level

    dist = sh["dist"]
    late = (lt["t_in"] - T) / 60
    slip_in = (sh["entry"] - lt["price"]) / dist                     # + = acheté moins cher que le test
    stop_dev = ((lt["price"] - lt["sl0"]) / dist - 1) if lt["sl0"] else None
    if late > LATE_MIN:
        flag("warn", f"entered {late / 60:.1f} h after the daily close (the test buys at the open)")
    if slip_in < -SLIP_WARN_R:
        flag("warn", f"entry price {(-slip_in):.2f}R worse than the test")
    if stop_dev is None:
        flag("problem", "no stop on the entry order")
    elif abs(stop_dev) > STOP_TOL:
        flag("warn", f"stop distance {100 * stop_dev:+.1f}% from 2 ATR")
    if lt["sl_now"] is not None and lt["sl0"] and abs(lt["sl_now"] - lt["sl0"]) > 1e-9 * max(1.0, lt["sl0"]):
        flag("warn", f"stop moved by hand: {lt['sl0']:.2f} -> {lt['sl_now']:.2f}")
    risk_pct = journal_entry.get("risk_pct") if journal_entry else None
    s = runner.dt_state["settings"]
    allowed = min(float(s.get("risk_pct", 2.0)), float(s.get("max_crypto_risk_pct", 99.0)))
    if risk_pct is not None and risk_pct > allowed + RISK_TOL:
        flag("warn", f"risked {risk_pct:.2f}% - above today's limit of {allowed:g}% (was the setting higher then?)")
    if lt["partial"]:
        flag("warn", "partly closed by hand")
    slip_out = gap = None
    exp_exit = None
    if sh["ix"] is not None:
        exp_exit = sh["t1"] + (86400 if sh["kind"] == "channel" else 0)
    if lt["t_out"] is not None:                         # fermeture à la main / stop-out : signalées dans la chronologie
        if sh["ix"] is None:
            if lt["kind"] in ("stop", "channel"):
                flag("warn", f"closed ({lt['kind']}) on {_day(lt['t_out'])} while the test would still hold it")
        else:
            same_day = (lt["kind"] == sh["kind"] and exp_exit <= lt["t_out"] < exp_exit + 86400)
            if not same_day and lt["kind"] in ("stop", "channel"):
                flag("warn", f"exit {lt['kind']} on {_day(lt['t_out'])}; the test exits ({sh['kind']}) "
                             f"on {_day(exp_exit)}")
            elif lt["kind"] == "channel" and (lt["t_out"] - exp_exit) / 60 > LATE_MIN:
                flag("warn", f"exit {(lt['t_out'] - exp_exit) / 3600:.1f} h after the daily close")
            if lt["kind"] == sh["kind"]:
                slip_out = (lt["exit"] - sh["exit"]) / dist             # + = vendu plus cher que le test
                if slip_out < -SLIP_WARN_R:
                    flag("warn", f"exit price {(-slip_out):.2f}R worse than the test")
            if lt["R"] is not None and sh["R"] is not None:
                gap = lt["R"] - sh["R"]
    elif sh["ix"] is not None and sh["kind"] == "stop":
        flag("warn", f"still open, but the test's stop was hit on {_day(sh['t1'])}")
    return {
        "symbol": lt["symbol"], "ticket": lt["ticket"], "signal_day": _day(T - 86400),
        "entry_time": datetime.fromtimestamp(lt["t_in"], UTC).isoformat(),
        "late_min": round(late, 1), "price": lt["price"], "test_price": round(sh["entry"], 2),
        "entry_slip_r": round(slip_in, 3), "stop_dev_pct": None if stop_dev is None else round(100 * stop_dev, 2),
        "risk_pct": risk_pct, "status": "closed" if lt["t_out"] else "open",
        "exit_kind": lt["kind"], "test_exit_kind": sh["kind"],
        "exit_time": datetime.fromtimestamp(lt["t_out"], UTC).isoformat() if lt["t_out"] else None,
        "test_exit_time": datetime.fromtimestamp(exp_exit, UTC).isoformat() if exp_exit else None,
        "exit_slip_r": None if slip_out is None else round(slip_out, 3),
        "r": None if lt["R"] is None else round(lt["R"], 3),
        "test_r": None if sh["R"] is None else round(sh["R"], 3),
        "gap_r": None if gap is None else round(gap, 3),
        "check": status, "notes": notes,
    }


def audit_symbol(symbol, d, sp, sw, lives, journal, start_bar, now_ts):
    """Bougie par bougie depuis start_bar : décisions attendues vs réalité."""
    atr = atr_series(d)
    t = d["time"].astype(np.int64)
    events, trades, used = [], [], set()
    by_bar = {}
    for r in journal:
        if r["symbol"] == symbol and r["bar_time"]:
            by_bar.setdefault(int(datetime.fromisoformat(r["bar_time"]).timestamp()), []).append(r)
    by_ticket = {r["ticket"]: r for r in journal if r["event"] == "entry" and r["ticket"]}
    mine = [x for x in lives if x["symbol"] == symbol]
    k0 = int(np.searchsorted(t, start_bar))
    checked = 0

    def ev(ts, status, text):
        events.append({"time": datetime.fromtimestamp(ts, UTC).isoformat(), "symbol": symbol,
                       "status": status, "text": text})

    for k in range(max(k0, runner.N_ENTRY + 1), len(d)):
        T = int(t[k]) + 86400
        if T > now_ts:
            break
        checked += 1
        pos = next((x for x in mine if x["t_in"] < T and (x["t_out"] is None or x["t_out"] >= T)), None)
        closed_during = pos is None and any(
            x["t_out"] is not None and t[k] <= x["t_out"] < T and x["kind"] != "channel" for x in mine)
        dec = runner.decide(d[:k + 1], position_open_time=pos["t_in"] if pos else None,
                            closed_during_bar=closed_during)
        pending = now_ts - T < LATE_MIN * 60
        if pos is None:
            entered = next((x for x in mine if T <= x["t_in"] < T + 86400 and x["ticket"] not in used), None)
            if dec["action"] == "enter":
                if entered:                     # la qualité de l'exécution est jugée dans « trades »
                    used.add(entered["ticket"])
                    trades.append(_compare(entered, follow(d, k, sp, sw, atr), T, by_ticket.get(entered["ticket"])))
                    late = (entered["t_in"] - T) / 60
                    ev(entered["t_in"], "ok", f"bought on the {_day(t[k])} breakout"
                                              + (f" ({late / 60:.1f} h after the close)" if late > LATE_MIN else ""))
                elif not pending:
                    rows = by_bar.get(int(t[k]), [])
                    why = next((r["reason"] for r in rows if r["event"] in ("skip", "error", "check")), None)
                    if why:
                        ev(T, _skip_status(why), f"{_day(t[k])} breakout not bought: {why}")
                    else:
                        ev(T, "problem", f"{_day(t[k])} breakout not bought and no journal record: "
                                         f"the backend was probably not running at the daily close")
            elif entered:
                used.add(entered["ticket"])
                ev(entered["t_in"], "problem", f"bought on {_day(entered['t_in'])} without a breakout on "
                                               f"{_day(t[k])} ({dec['why']})")
        else:
            out_now = pos["t_out"] is not None and T <= pos["t_out"] < T + 86400
            if dec["action"] == "exit":
                if out_now and pos["kind"] == "channel":
                    late = (pos["t_out"] - T) / 60
                    ev(pos["t_out"], "ok", f"sold on the {_day(t[k])} close under the 10-day low"
                                           + (f" ({late / 60:.1f} h after the close)" if late > LATE_MIN else ""))
                elif out_now:
                    pass                        # fermée autrement le même jour : stop / main, voir plus bas
                elif not pending:
                    ev(T, "problem", f"exit signal on the {_day(t[k])} close (under the 10-day low) "
                                     f"but the position was not sold that day")
            elif out_now and pos["kind"] == "channel":
                ev(pos["t_out"], "problem", f"sold on {_day(pos['t_out'])} without an exit signal ({dec['why']})")
    for x in mine:                                      # trades rattachés à aucune bougie contrôlée
        if x["ticket"] not in used and x["t_in"] >= start_bar:
            ev(x["t_in"], "problem", f"position {x['ticket']} opened outside the daily-close rules")
    for x in mine:                                      # sorties stop / main hors de la boucle des signaux
        if x["t_out"] is not None and x["kind"] in ("stop", "manual", "stop-out") and x["t_in"] >= start_bar:
            st = "ok" if x["kind"] == "stop" else ("warn" if x["kind"] == "manual" else "problem")
            res = f" ({x['R']:+.2f}R)" if x["R"] is not None else ""
            ev(x["t_out"], st, {"stop": "stopped out at the broker stop", "manual": "closed by hand",
                                "stop-out": "closed by a margin stop-out"}[x["kind"]] + res)
    return events, trades, checked


def expected_range(ref_r, live_r, seed=7):
    """Résultat réel (somme R, pire baisse R, série de pertes) face à N_SIM tirages
    de autant de trades parmi ceux du test."""
    n = len(live_r)
    if n == 0 or len(ref_r) == 0:
        return {"n": n, "status": "no_trades"}
    rng = np.random.default_rng(seed)
    sims = rng.choice(np.asarray(ref_r, float), size=(N_SIM, n), replace=True)
    cum = np.cumsum(sims, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((N_SIM, 1)), cum], axis=1), axis=1)[:, 1:]
    dd = (cum - peak).min(axis=1)

    def streak(row):
        best = cur = 0
        for x in row:
            cur = cur + 1 if x < 0 else 0
            best = max(best, cur)
        return best

    lr = np.asarray(live_r, float)
    lc = np.cumsum(lr)
    live_dd = float((lc - np.maximum.accumulate(np.concatenate([[0.0], lc]))[1:]).min())
    live_streak = streak(lr)
    sim_streak = np.array([streak(r) for r in sims[:2000]])
    p5, p50, p95 = (float(np.percentile(cum[:, -1], q)) for q in (5, 50, 95))
    dd5 = float(np.percentile(dd, 5))
    st95 = int(np.percentile(sim_streak, 95))
    inside = lc[-1] >= p5 and live_dd >= dd5
    return {"n": n, "sum_r": round(float(lc[-1]), 2), "expected_p5": round(p5, 2), "expected_p50": round(p50, 2),
            "expected_p95": round(p95, 2), "max_dd_r": round(live_dd, 2), "dd_p5": round(dd5, 2),
            "losing_streak": live_streak, "streak_p95": st95,
            "pct_below": round(float(100 * (cum[:, -1] <= lc[-1]).mean()), 1),
            "status": "inside" if inside else "below"}


def dt_audit(since: str | None = None) -> dict:
    """/trading/dt/audit — contrôle complet (gardé 10 min en cache)."""
    key = since or ""
    with _lock:
        if _cache["data"] is not None and _cache["key"] == key and time.time() - _cache["t"] < CACHE_S:
            return _cache["data"]
        data = _run(since)
        _cache.update(t=time.time(), key=key, data=data)
        return data


def _run(since):
    ensure_mt5()
    now = datetime.now(UTC)
    now_ts = int(now.timestamp())
    start_bar = (int(datetime.fromisoformat(since).replace(tzinfo=UTC).timestamp()) if since
                 else first_scan_bar())
    base = {"bot": "daily_trend", "generated": now.isoformat(),
            "rules": "tested code: trend_robustness_study.donchian (long-only, next-day open), "
                     "decisions: runner.decide (identical, test_dt.py part 5)"}
    if start_bar is None:
        return base | {"verdict": "not_started", "since": None, "checked_closes": 0, "events": [], "trades": [],
                       "summary": {}, "range": {"n": 0, "status": "no_trades"}}
    journal = _journal_rows(start_bar)
    lives = live_trades(start_bar, now)
    events, trades, ref_r, checked = [], [], [], 0
    for symbol in runner.ALLOWED:
        mt5sym = SYMBOL_MAP.get(symbol)
        d = mt5.copy_rates_from_pos(mt5sym, mt5.TIMEFRAME_D1, 0, FULL_BARS)
        if d is None or len(d) < runner.N_ENTRY + 60:
            events.append({"time": now.isoformat(), "symbol": symbol, "status": "problem",
                           "text": "no daily history from MT5"})
            continue
        d = d[d["time"] + 86400 <= now_ts]
        sp, sw = costs(mt5sym, d)
        atr = atr_series(d)
        ref_r += [x["R"] for x in reference_trades(d, sp, sw, atr) if x["t0"] >= REF_START]
        ev, tr, n = audit_symbol(symbol, d, sp, sw, lives, journal, start_bar, now_ts)
        events += ev
        trades += tr
        checked = max(checked, n)
    events.sort(key=lambda e: e["time"], reverse=True)
    trades.sort(key=lambda x: x["entry_time"], reverse=True)
    count = {s: sum(1 for e in events if e["status"] == s) + sum(1 for x in trades if x["check"] == s)
             for s in ("info", "warn", "problem")}
    closed = [x for x in trades if x["r"] is not None]
    gaps = [x["gap_r"] for x in closed if x["gap_r"] is not None]
    slips = [x["entry_slip_r"] for x in trades]
    verdict = ("problems" if count["problem"] else "warnings" if count["warn"]
               else "ok" if trades else "no_trades")
    live_r = [x["r"] for x in sorted(closed, key=lambda x: x["exit_time"])]
    return base | {
        "verdict": verdict, "since": datetime.fromtimestamp(start_bar, UTC).isoformat(), "checked_closes": checked,
        "summary": {"live_trades": len(trades), "open": sum(1 for x in trades if x["status"] == "open"),
                    "closed": len(closed), "skipped_by_design": count["info"], "warnings": count["warn"],
                    "problems": count["problem"],
                    "avg_entry_slip_r": round(float(np.mean(slips)), 3) if slips else None,
                    "avg_gap_r": round(float(np.mean(gaps)), 3) if gaps else None,
                    "risk_pct_setting": runner.dt_state["settings"].get("risk_pct"),
                    "reference_trades": len(ref_r),
                    "reference_avg_r": round(float(np.mean(ref_r)), 3) if ref_r else None},
        "range": expected_range(ref_r, live_r),
        "events": events[:60], "trades": trades[:60],
    }
