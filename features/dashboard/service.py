"""Tableau de bord de l'app (demande du 2026-09-26) : performance par période,
par bot et par symbole, risque en cours, courbe du solde, actualités.

Tout vient de l'historique des deals MT5 du compte connecté — la vérité du
broker (commissions, swaps, dépôts compris) — et non des journaux des bots.
Lecture seule : rien ici ne passe d'ordre ni ne modifie un bot.

Périodes en heure LOCALE du PC (celle de l'utilisateur) : "aujourd'hui" part
de minuit local, la semaine du lundi, le mois du 1er. Les deals MT5 sont
horodatés à l'heure serveur Exness, qui est l'UTC (vérifié le 2026-09-24).
"""
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import MetaTrader5 as mt5
import requests

from features.trading.mt5_client import ensure_mt5
from features.trading.risk_manager import BOT_MAGIC, DT_MAGIC, VP_MAGIC

BOTS = {BOT_MAGIC: "smc", VP_MAGIC: "vp", DT_MAGIC: "dt", 0: "manual"}
UTC = timezone.utc


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC).astimezone()


def _sym(s: str) -> str:
    return s[:-1] if s.endswith("m") else s          # BTCUSDm -> BTCUSD (suffixe Exness)


# ── historique ───────────────────────────────────────────────────────────────
def _history():
    """-> (trades clôturés, mouvements de solde) sur toute la vie du compte."""
    deals = mt5.history_deals_get(datetime(2015, 1, 1, tzinfo=UTC),
                                  datetime.now(UTC) + timedelta(hours=3)) or []
    by_pos, cash = {}, []
    for d in deals:
        if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
            by_pos.setdefault(d.position_id, []).append(d)
        else:                                          # dépôt, retrait, crédit, bonus...
            amt = d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
            if amt:
                cash.append((d.time, amt))
    trades = []
    for pid, ds in by_pos.items():
        ins = [d for d in ds if d.entry == mt5.DEAL_ENTRY_IN]
        outs = [d for d in ds if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not ins or not outs:
            continue
        if sum(d.volume for d in outs) + 1e-9 < sum(d.volume for d in ins):
            continue                                   # encore partiellement ouvert
        i, last = ins[0], max(outs, key=lambda d: d.time)
        trades.append({
            "symbol": _sym(i.symbol),
            "bot": BOTS.get(i.magic, "other"),
            "direction": "buy" if i.type == mt5.DEAL_TYPE_BUY else "sell",
            "profit": sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in ds),
            "opened_ts": i.time, "closed_ts": last.time,
        })
    trades.sort(key=lambda t: t["closed_ts"])
    return trades, cash


def _summary(rows: list) -> dict:
    wins = [r["profit"] for r in rows if r["profit"] > 0]
    losses = [r["profit"] for r in rows if r["profit"] <= 0]
    gp, gl = sum(wins), -sum(losses)
    n = len(rows)
    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(100 * len(wins) / n, 1) if n else None,
        "net": round(gp - gl, 2),
        "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
        "profit_factor": round(gp / gl, 2) if gl else None,
        "avg_win": round(gp / len(wins), 2) if wins else None,
        "avg_loss": round(-gl / len(losses), 2) if losses else None,
        "best": round(max(r["profit"] for r in rows), 2) if rows else None,
        "worst": round(min(r["profit"] for r in rows), 2) if rows else None,
        "expectancy": round((gp - gl) / n, 2) if n else None,
    }


def _period_block(rows, start_ts, balance_now, events, cash) -> dict:
    """Résumé d'une période + rendement en % du solde au début de la période.
    events = tout ce qui a fait bouger le solde (trades + dépôts/retraits)."""
    sel = [r for r in rows if r["closed_ts"] >= start_ts]
    s = _summary(sel)
    bal_start = balance_now - sum(x for t, x in events if t >= start_ts)
    deposits = sum(x for t, x in cash if t >= start_ts)
    # rendement sur le capital engagé : solde de départ + dépôts de la période
    # (sinon un dépôt en cours de mois donne des -147 % absurdes)
    capital = bal_start + max(0.0, deposits)
    s["balance_start"] = round(bal_start, 2)
    s["deposits"] = round(deposits, 2)
    s["return_pct"] = round(100 * s["net"] / capital, 1) if capital > 0 else None
    s["by_bot"] = {b: _summary([r for r in sel if r["bot"] == b])
                   for b in ("smc", "vp", "dt", "manual") if any(r["bot"] == b for r in sel)}
    syms = sorted({r["symbol"] for r in sel})
    s["by_symbol"] = sorted(({"symbol": x, **_summary([r for r in sel if r["symbol"] == x])}
                             for x in syms), key=lambda v: v["net"], reverse=True)
    return s


def _max_drawdown(rows, since_ts, balance_at) -> dict:
    """Plus forte baisse depuis since_ts, dépôts neutralisés :
      usd : baisse du P/L de trading cumulé, sommet -> creux ;
      pct : baisse d'un indice qui enchaîne le rendement de chaque trade sur
            le solde du moment (rendement pondéré dans le temps). Un dépôt fait
            PENDANT une baisse ne la gonfle pas — sinon 142 $ perdus après un
            sommet à 54 $ de solde affichaient -265 %."""
    eq = peak_eq = dd = 0.0
    idx = peak_idx = 1.0
    dd_pct = 0.0
    for r in rows:
        if r["closed_ts"] < since_ts:
            continue
        eq += r["profit"]
        peak_eq = max(peak_eq, eq)
        dd = max(dd, peak_eq - eq)
        before = balance_at(r["closed_ts"])               # solde juste avant ce trade
        if before > 0:
            idx *= 1 + r["profit"] / before
            peak_idx = max(peak_idx, idx)
            dd_pct = max(dd_pct, 100 * (1 - idx / peak_idx))
    return {"usd": round(dd, 2), "pct": round(dd_pct, 1)}


def summary() -> dict:
    ensure_mt5()
    a = mt5.account_info()
    if a is None:
        raise RuntimeError("MT5 account unavailable")
    trades, cash = _history()
    # tous les montants qui ont fait bouger le solde, pour le reconstruire à une date
    events = [(r["closed_ts"], r["profit"]) for r in trades] + cash

    def balance_at(ts):
        return a.balance - sum(x for t, x in events if t >= ts)

    now_l = datetime.now().astimezone()
    today = now_l.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = {
        "today": today,
        "week": today - timedelta(days=today.weekday()),
        "month": today.replace(day=1),
        "d30": now_l - timedelta(days=30),
        "all": datetime(2015, 1, 1, tzinfo=UTC),
    }
    periods = {k: _period_block(trades, v.timestamp(), a.balance, events, cash)
               for k, v in starts.items()}

    # courbe : solde en fin de chaque jour local, 30 derniers jours + maintenant
    curve = []
    for k in range(30, 0, -1):
        day_end = today - timedelta(days=k - 1)            # minuit qui termine le jour
        curve.append({"date": (day_end - timedelta(days=1)).date().isoformat(),
                      "balance": round(balance_at(day_end.timestamp()), 2)})
    curve.append({"date": today.date().isoformat(), "balance": round(a.balance, 2),
                  "equity": round(a.equity, 2)})

    # série en cours (toutes stratégies), du trade le plus récent vers le passé
    streak_kind, streak = None, 0
    for r in reversed(trades):
        k = "win" if r["profit"] > 0 else "loss"
        if streak_kind in (None, k):
            streak_kind, streak = k, streak + 1
        else:
            break

    return {
        "generated": datetime.now(UTC).isoformat(),
        "account": {
            "login": a.login, "demo": a.trade_mode == 0, "currency": a.currency,
            "balance": round(a.balance, 2), "equity": round(a.equity, 2),
            "floating": round(a.profit, 2), "margin": round(a.margin, 2),
            "margin_free": round(a.margin_free, 2),
            "margin_level": round(a.margin_level, 1) if a.margin else None,
            "leverage": a.leverage,
        },
        "periods": periods,
        "curve": curve,
        "risk": _risk(a, trades, balance_at, today),
        "streak": {"kind": streak_kind, "count": streak},
    }


def _risk(a, trades, balance_at, today) -> dict:
    """Risque EN COURS : ce que coûteraient les stops actuels, verrouillé par
    les trails, drawdown depuis le pic suivi par le bot Scalping, limites."""
    positions = []
    for p in mt5.positions_get() or []:
        buy = p.type == mt5.POSITION_TYPE_BUY
        at_sl = None
        if p.sl:
            v = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                                      p.symbol, p.volume, p.price_open, p.sl)
            at_sl = round(v, 2) if v is not None else None
        positions.append({"symbol": _sym(p.symbol), "bot": BOTS.get(p.magic, "other"),
                          "side": "buy" if buy else "sell", "volume": p.volume,
                          "profit": round(p.profit + p.swap, 2), "at_sl": at_sl})
    at_risk = -sum(x["at_sl"] for x in positions if x["at_sl"] is not None and x["at_sl"] < 0)
    locked = sum(x["at_sl"] for x in positions if x["at_sl"] is not None and x["at_sl"] > 0)
    by_bot = {}
    for x in positions:
        b = by_bot.setdefault(x["bot"], {"open": 0, "at_risk": 0.0, "floating": 0.0})
        b["open"] += 1
        b["floating"] = round(b["floating"] + x["profit"], 2)
        if x["at_sl"] is not None and x["at_sl"] < 0:
            b["at_risk"] = round(b["at_risk"] - x["at_sl"], 2)

    out = {
        "open_positions": len(positions),
        "positions": positions,
        "at_risk": round(at_risk, 2),
        "at_risk_pct": round(100 * at_risk / a.balance, 1) if a.balance > 0 else None,
        "locked": round(locked, 2),
        "no_stop": sum(1 for x in positions if x["at_sl"] is None),
        "by_bot": by_bot,
        "max_drawdown_30d": _max_drawdown(trades, (datetime.now().astimezone()
                                                   - timedelta(days=30)).timestamp(), balance_at),
    }
    # pic d'equity et halt de survie du bot Scalping, limites de perte du jour
    try:
        from features.trading.router import scalp_state, SURVIVAL_HALT_PCT
        pk = scalp_state.get("peak_equity")
        if pk:
            out["peak_equity"] = round(pk, 2)
            out["drawdown_from_peak_pct"] = round(100 * max(0.0, pk - a.equity) / pk, 1)
            out["halt_pct"] = SURVIVAL_HALT_PCT
            out["halt_equity"] = round(pk * (1 - SURVIVAL_HALT_PCT / 100), 2)
        st = scalp_state.get("settings") or {}
        out["smc_daily_limit_pct"] = st.get("max_daily_loss_pct")
        out["smc_risk_cap_pct"] = st.get("risk_cap_pct")
    except Exception as e:
        out["smc_state_error"] = str(e)
    try:
        from features.volume_profile.runner import vp_state
        vs = vp_state.get("settings") or {}
        out["vp_daily_limit_pct"] = vs.get("daily_loss_pct")
        out["vp_risk_cap_pct"] = vs.get("risk_cap_pct")
    except Exception as e:
        out["vp_state_error"] = str(e)
    start_ts = today.timestamp()
    out["today_by_bot"] = {b: round(sum(r["profit"] for r in trades
                                        if r["bot"] == b and r["closed_ts"] >= start_ts), 2)
                           for b in ("smc", "vp", "dt", "manual")}
    return out


# ── actualités ───────────────────────────────────────────────────────────────
HEADLINE_FEEDS = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", None),
    ("Cointelegraph", "https://cointelegraph.com/rss", None),
    # FXStreet couvre tout le forex : on ne garde que ce qui touche les symboles tradés
    ("FXStreet", "https://www.fxstreet.com/rss/news",
     ("gold", "xau", "silver", "xag", "fed", "fomc", "powell", "dollar", "usd", "yen", "jpy",
      "boj", "inflation", "cpi", "pce", "nfp", "payroll", "treasury", "yields", "bitcoin",
      "crypto", "ethereum", "rate cut", "rate hike", "tariff")),
)
_HEADLINE_TTL = 900
_hl_cache = {"ts": 0.0, "items": []}
_hl_lock = threading.Lock()


def _headlines() -> list:
    with _hl_lock:
        if time.time() - _hl_cache["ts"] < _HEADLINE_TTL:
            return _hl_cache["items"]
        items = []
        for source, url, keywords in HEADLINE_FEEDS:
            try:
                r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0 CryptoOracle"})
                for it in ET.fromstring(r.content).findall(".//item")[:30]:
                    title = (it.findtext("title") or "").strip()
                    if not title:
                        continue
                    if keywords and not any(k in title.lower() for k in keywords):
                        continue
                    try:
                        ts = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(UTC)
                    except (TypeError, ValueError):
                        continue
                    items.append({"source": source, "title": title,
                                  "link": (it.findtext("link") or "").strip(),
                                  "time": ts.isoformat()})
            except Exception as e:
                print(f"[DASHBOARD] headlines {source} failed: {e}")
        if items:                                  # garder l'ancien cache si tout a échoué
            items.sort(key=lambda x: x["time"], reverse=True)
            _hl_cache.update(ts=time.time(), items=items[:25])
        return _hl_cache["items"]


def _traded_symbols() -> list:
    syms = set()
    try:
        from features.trading.router import scalp_state
        syms.update((scalp_state.get("settings") or {}).get("enabled_symbols") or [])
    except Exception:
        pass
    try:
        from features.volume_profile.runner import vp_state
        syms.update((vp_state.get("settings") or {}).get("symbols") or [])
    except Exception:
        pass
    return sorted(syms)


def news() -> dict:
    """Calendrier économique (impact fort / moyen) pour les devises qui font
    bouger les symboles tradés, + derniers titres crypto / or / dollar."""
    from features.news.calendar import (COUNTRY_CURRENCY, HIGH_AFTER_MIN, _fetch,
                                        _get_currencies, _parse_dt)
    traded = _traded_symbols()
    affects = {}                                   # devise -> symboles tradés concernés
    for s in traded:
        for c in _get_currencies(s):
            affects.setdefault(c, []).append(s)
    now = datetime.now(UTC)
    upcoming, recent = [], []
    for ev in _fetch():
        impact = ev.get("impact", "")
        if impact not in ("High", "Medium"):
            continue
        country = ev.get("country", "")
        cur = COUNTRY_CURRENCY.get(country, country[:3].upper() if country else "")
        dt = _parse_dt(ev)
        if not dt or cur not in affects:
            continue
        row = {"title": ev.get("title", ""), "currency": cur, "impact": impact,
               "time": dt.isoformat(), "forecast": ev.get("forecast") or None,
               "previous": ev.get("previous") or None, "actual": ev.get("actual") or None,
               "symbols": sorted(affects[cur]),
               # fenêtre de pause du bot Scalping (features/news/calendar.is_news_blocked)
               "smc_pause_min": [15, HIGH_AFTER_MIN] if impact == "High" else [10, 5]}
        if dt >= now - timedelta(minutes=10):
            upcoming.append(row)
        elif dt >= now - timedelta(hours=24):
            recent.append(row)
    upcoming.sort(key=lambda r: r["time"])
    recent.sort(key=lambda r: r["time"], reverse=True)
    return {"generated": now.isoformat(), "traded_symbols": traded,
            "currencies": sorted(affects), "upcoming": upcoming, "recent": recent,
            "calendar_note": ("Forex Factory publishes each week's calendar on Sunday — "
                              "on Saturdays the list can be empty."),
            "headlines": _headlines()[:12]}


# ── Espérance par bot (2026-10-10) ───────────────────────────────────────────
# Demande utilisateur après la vidéo "4 concepts mathématiques" : l'espérance
# (résultat moyen par trade, frais compris, sur un GRAND échantillon) est le seul
# chiffre qui dit si un bot gagne de l'argent. Calculée sur les trades RÉELS du
# compte : R = résultat / risque au stop initial (le SL de l'ordre d'entrée).
EXP_BOTS = {BOT_MAGIC: ("smc", "Scalping"), VP_MAGIC: ("vp", "Volume Profile"), DT_MAGIC: ("dt", "Daily Trend")}
# Ce que les tests sur l'historique ont donné (pour comparer au réel)
EXP_TESTED = {
    "smc": "Replays since July: entries no better than random (about -0.03R to -0.08R).",
    "vp": "3-year replay (BTC/ETH, Binance): -0.03R per trade after spread; gold 3 years: +0.02R (not proven).",
    "dt": "Exness daily 2018-2026, long-only BTC+ETH: +1.66R per trade over 91 trades.",
}
SAMPLE_TARGET = 100
_exp_cache = {"t": 0.0, "days": None, "data": None}
_exp_lock = threading.Lock()


def _bootstrap(x, n=2000, seed=11):
    import numpy as np
    rng = np.random.default_rng(seed)
    arr = np.asarray(x, float)
    means = rng.choice(arr, size=(n, len(arr)), replace=True).mean(axis=1)
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def _journal_stops() -> dict:
    """Stop initial noté par les bots eux-mêmes, par ticket (= n° de position).
    Utilisé quand le registre du broker n'a pas de stop : Exness efface SL/TP de
    l'ordre LIMITE une fois exécuté (128 entrées limite sur 128 depuis juillet),
    alors que le bot l'avait envoyé et que la position l'avait (vérifié le
    2026-10-12 : le XAG du jour fermé à son stop). Sans ça, ces trades n'avaient
    pas de R et sortaient de l'espérance."""
    from core.database import get_db
    out = {}
    try:
        conn = get_db()
        for sql in ("SELECT ticket, sl FROM trade_signals WHERE ticket IS NOT NULL AND sl IS NOT NULL",
                    "SELECT ticket, sl FROM vp_log WHERE status='taken' AND ticket IS NOT NULL AND sl IS NOT NULL",
                    "SELECT ticket, sl FROM dt_log WHERE event='entry' AND ticket IS NOT NULL AND sl IS NOT NULL"):
            try:
                for t, s in conn.execute(sql):
                    if s:
                        out[int(t)] = float(s)
            except Exception:
                pass                                    # table absente (base de test)
        conn.close()
    except Exception as e:
        print(f"[EXPECTANCY] journal stops unavailable: {e}")
    return out


def expectancy(days: int = 0) -> dict:
    """Espérance réelle de chaque bot. days = 0 : tout l'historique du compte."""
    with _exp_lock:
        if _exp_cache["data"] is not None and _exp_cache["days"] == days and time.time() - _exp_cache["t"] < 300:
            return _exp_cache["data"]
        ensure_mt5()
        now = datetime.now(UTC)
        start = now - timedelta(days=days) if days else datetime(2015, 1, 1, tzinfo=UTC)
        acct = mt5.account_info()
        deals = mt5.history_deals_get(datetime(2015, 1, 1, tzinfo=UTC), now + timedelta(hours=3)) or []
        net = lambda d: d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
        later = sorted(((d.time, net(d)) for d in deals), key=lambda x: x[0])
        by_pos = {}
        for d in deals:
            if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
                by_pos.setdefault(d.position_id, []).append(d)
        spread_cache = {}
        jstops = _journal_stops()
        rows = {k: [] for k, _ in EXP_BOTS.values()}
        for pid, ds in by_pos.items():
            ins = [d for d in ds if d.entry == mt5.DEAL_ENTRY_IN]
            outs = [d for d in ds if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
            if not ins or not outs or sum(d.volume for d in outs) + 1e-9 < sum(d.volume for d in ins):
                continue
            i = ins[0]
            if i.magic not in EXP_BOTS or i.time < start.timestamp():
                continue
            bot = EXP_BOTS[i.magic][0]
            buy = i.type == mt5.DEAL_TYPE_BUY
            vol = sum(d.volume for d in ins)
            o = next(iter(mt5.history_orders_get(ticket=i.order) or []), None)
            sl0 = o.sl if o is not None else 0.0
            from_journal = False
            if not sl0:                                 # ordre limite exécuté : stop du journal du bot
                js = jstops.get(pid) or jstops.get(i.order)
                if js and ((buy and js < i.price) or (not buy and js > i.price)):
                    sl0, from_journal = js, True
            pnl = sum(net(d) for d in ds)
            costs = sum(d.commission + d.swap + getattr(d, "fee", 0.0) for d in ds)
            risk = None
            if sl0:
                loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                                             i.symbol, vol, i.price, sl0)
                risk = -loss if loss is not None and loss < 0 else None
            if i.symbol not in spread_cache:
                tk = mt5.symbol_info_tick(i.symbol)
                one = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, i.symbol, 1.0, tk.bid, tk.ask) if tk else None
                spread_cache[i.symbol] = one or 0.0
            bal_at = (acct.balance if acct else 0.0) - sum(x for t, x in later if t >= i.time)
            rows[bot].append({"pnl": pnl, "costs": costs, "R": pnl / risk if risk else None,
                              "risk_pct": 100 * risk / bal_at if risk and bal_at > 0 else None,
                              "spread": spread_cache[i.symbol] * vol, "t": i.time,
                              "journal_sl": from_journal and risk is not None})
        out = {}
        for magic, (bot, name) in EXP_BOTS.items():
            rs = rows[bot]
            R = [r["R"] for r in rs if r["R"] is not None]
            n = len(R)
            d = {"bot": bot, "name": name, "trades": len(rs), "trades_with_r": n,
                 "r_from_journal": sum(1 for r in rs if r["journal_sl"]),
                 "sample_target": SAMPLE_TARGET, "tested": EXP_TESTED.get(bot)}
            if rs:
                d.update(first=datetime.fromtimestamp(min(r["t"] for r in rs), UTC).isoformat(),
                         total_usd=round(sum(r["pnl"] for r in rs), 2),
                         expectancy_usd=round(sum(r["pnl"] for r in rs) / len(rs), 2),
                         commission_swap_usd=round(sum(r["costs"] for r in rs), 2),
                         spread_est_usd=round(sum(r["spread"] for r in rs), 2))
                rp = sorted(r["risk_pct"] for r in rs if r["risk_pct"] is not None)
                if rp:                       # 90e centile plutôt que le max : un trade pris sur un solde
                                             # presque nul donnait 700 % et ne disait rien d'utile
                    d.update(risk_pct_median=round(rp[len(rp) // 2], 1),
                             risk_pct_p90=round(rp[min(len(rp) - 1, int(0.9 * len(rp)))], 1))
            if n:
                w = [x for x in R if x > 0]
                l = [x for x in R if x <= 0]
                avg_w = sum(w) / len(w) if w else 0.0
                avg_l = -sum(l) / len(l) if l else 0.0
                payoff = avg_w / avg_l if avg_l > 0 else None
                lo, hi = _bootstrap(R) if n >= 5 else (None, None)
                exp_r = sum(R) / n
                d.update(win_rate=round(100 * len(w) / n, 1), avg_win_r=round(avg_w, 2), avg_loss_r=round(avg_l, 2),
                         payoff=round(payoff, 2) if payoff else None,
                         breakeven_win_rate=round(100 / (1 + payoff), 1) if payoff else None,
                         expectancy_r=round(exp_r, 3),
                         range_low_r=round(lo, 3) if lo is not None else None,
                         range_high_r=round(hi, 3) if hi is not None else None)
                if n < 30:
                    d["verdict"] = "too_early"
                elif hi is not None and hi < 0:
                    d["verdict"] = "losing"
                elif lo is not None and lo > 0:
                    d["verdict"] = "winning"
                else:
                    d["verdict"] = "not_proven"
            else:
                d["verdict"] = "no_trades"
            out[bot] = d
        data = {"generated": now.isoformat(), "days": days, "bots": out,
                "notes": {"r": "1R = what a trade risked at its initial stop. Limit-order trades: the broker "
                               "erases the stop from the filled order, so it is taken from the bot's own journal.",
                          "breakeven": "Breakeven win rate = 1 / (1 + average win / average loss).",
                          "range": "90% range of the expectancy from resampling the trades: "
                                   "the true value is very likely inside it.",
                          "spread": "Spread cost estimated with today's spreads.",
                          "risk": "The video's guideline: 0.25% to 2% of the balance per trade."}}
        _exp_cache.update(t=time.time(), days=days, data=data)
        return data
