"""Le bot Daily Trend — un TROISIÈME BOT sur le même compte MT5 (2026-10-09).

Demande utilisateur : construire le système de suivi de tendance journalier
validé par les études (trend_following_study.py : passé ; robustesse
trend_robustness_study.py : 12/12), risque 2 % par trade modifiable dans l'app.

RÈGLES — exactement celles testées, rien d'optimisé après coup :
  * bougies JOURNALIÈRES Exness (00:00 -> 24:00 UTC), BTC et ETH, ACHATS seulement
    (les ventes n'apportaient rien sur la crypto) ;
  * entrée : clôture du jour au-dessus du plus haut des 20 jours PRÉCÉDENTS ->
    achat au marché juste après la clôture (= ouverture du jour suivant ; testé :
    même résultat qu'à la clôture, la crypto cote 24/7) ;
  * stop de protection : 2 x ATR au moment du signal, posé chez le broker.
    ATR = Wilder 14 jours sur les bougies journalières (features.volume_profile.
    strategy.atr_series, la fonction utilisée par les études) ;
  * sortie : clôture du jour sous le plus bas des 10 jours PRÉCÉDENTS -> vente au
    marché juste après la clôture. Pas d'objectif : on laisse courir la tendance.
    Le jour d'entrée lui-même n'est jugé que par le stop (comme dans le test) ;
  * une position par symbole ; pas de nouvelle entrée sur la bougie pendant
    laquelle une position vient de se fermer (comme dans le test) ;
  * taille : risk_pct % du solde perdu si le stop est touché. Si même le lot
    minimum du broker risque plus que risk_pct, le trade est REFUSÉ (journalisé) :
    jamais de lot forcé au-delà du risque choisi.

INDÉPENDANCE (comme le bot Volume Profile) :
  * son propre minuteur ('dt_scan', toutes les 5 min, main.py), son propre
    marche/arrêt persisté (dt_state.json), DÉSACTIVÉ au premier démarrage ;
  * il ne voit que ses positions (magic DT_MAGIC) ; les règles SMC l'ignorent
    (executor.OTHER_BOT_MAGICS) ; le trail / mi-position SMC ne touche que 234000 ;
  * Stop = plus de NOUVELLE entrée ; les positions ouvertes restent gérées
    (sortie sur le plus bas 10 jours) et protégées par leur stop broker.
  * Le halt -35 % du bot Scalping mesure l'equity du compte : les pertes
    latentes de ce bot y comptent (comme celles du bot VP), mais il n'arrête que
    le bot Scalping.
"""
import json
import math
import os
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from core.database import get_db
from features.trading.executor import SYMBOL_MAP, close_partial
from features.trading.mt5_client import ensure_mt5
from features.trading.risk_manager import DT_MAGIC
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
N_ENTRY = 20          # jours du canal d'entrée
N_EXIT = 10           # jours du canal de sortie
STOP_ATR = 2.0        # stop de protection, en ATR journalier
ALLOWED = ("BTC", "ETH")
DEFAULT_SETTINGS = {"symbols": ["BTC", "ETH"], "risk_pct": 2.0}
RISK_RANGE = (0.1, 5.0)
HISTORY_BARS = 400    # assez pour un ATR de Wilder stabilisé
COMMENT = "DT long"

_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dt_state.json")
_ready = False


class SettingsError(ValueError):
    """Réglage refusé — le message est affiché tel quel dans l'app."""


# ── état persistant ──────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    settings = {**DEFAULT_SETTINGS, **(d.get("settings") or {})}
    return {"enabled": bool(d.get("enabled", False)),        # 1er démarrage : ARRÊTÉ
            "settings": settings,
            "last_bar": d.get("last_bar") or {},            # symbole -> epoch de la dernière bougie traitée
            "last_check": d.get("last_check") or {}}        # symbole -> dernier verdict (affiché dans l'app)


dt_state = _load_state()
dt_state.update(last_scan=None, last_error=None)


def _persist() -> None:
    try:
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k: dt_state[k] for k in ("enabled", "settings", "last_bar", "last_check")}
                      | {"changed_at": datetime.now(UTC).isoformat()}, fh, indent=2)
        os.replace(tmp, _STATE_FILE)
    except OSError as e:
        print(f"[DT] could not persist state: {e}")


def set_enabled(flag: bool) -> dict:
    dt_state["enabled"] = bool(flag)
    _persist()
    print(f"[DT] bot {'STARTED' if flag else 'STOPPED'}")
    return dt_status()


def update_settings(patch: dict) -> dict:
    """Valide puis enregistre les réglages venus de l'app. Tout ou rien."""
    if not isinstance(patch, dict):
        raise SettingsError("settings must be an object")
    new = dict(dt_state["settings"])
    errors = []
    if "symbols" in patch:
        syms = patch["symbols"]
        if not isinstance(syms, list):
            errors.append("symbols must be a list")
        else:
            syms = list(dict.fromkeys(str(x).upper() for x in syms))
            bad = [x for x in syms if x not in ALLOWED]
            if bad:
                errors.append(f"only {', '.join(ALLOWED)} were tested for this bot (not {', '.join(bad)})")
            new["symbols"] = syms
    if "risk_pct" in patch:
        v = patch["risk_pct"]
        lo, hi = RISK_RANGE
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append("risk_pct must be a number")
        elif not lo <= float(v) <= hi:
            errors.append(f"risk_pct must be between {lo:g} and {hi:g} %")
        else:
            new["risk_pct"] = round(float(v), 2)
    if errors:
        raise SettingsError("; ".join(errors))
    dt_state["settings"] = new
    _persist()
    print(f"[DT] settings saved: symbols={new['symbols']} risk={new['risk_pct']}%")
    return dt_status()


# ── journal ──────────────────────────────────────────────────────────────────
def _ensure_ready():
    global _ready
    if _ready:
        return
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dt_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol    TEXT NOT NULL,
            event     TEXT,        -- check | entry | skip | exit | error
            bar_time  TEXT,        -- bougie journalière jugée
            close     REAL, level REAL, atr REAL,
            price     REAL, sl REAL, volume REAL, risk_usd REAL, risk_pct REAL,
            ticket    INTEGER,
            outcome   INTEGER,     -- entrées : 1 gain, 0 perte, NULL ouverte
            profit    REAL,
            closed_at TEXT,
            reason    TEXT
        )""")
    conn.commit()
    conn.close()
    _ready = True


def _log(symbol, event, *, bar_time=None, close=None, level=None, atr=None, price=None, sl=None,
         volume=None, risk_usd=None, risk_pct=None, ticket=None, reason=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO dt_log (timestamp, symbol, event, bar_time, close, level, atr, price, sl, volume, "
        "risk_usd, risk_pct, ticket, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(UTC).isoformat(), symbol, event,
         datetime.fromtimestamp(bar_time, UTC).isoformat() if bar_time else None,
         close, level, atr, price, sl, volume, risk_usd, risk_pct, ticket, reason))
    conn.commit()
    conn.close()


def update_outcomes():
    """Résultat des entrées fermées (sortie 10 jours, stop broker ou manuelle)."""
    conn = get_db()
    rows = list(conn.execute("SELECT id, ticket FROM dt_log WHERE event='entry' "
                             "AND outcome IS NULL AND ticket IS NOT NULL"))
    for r in rows:
        if mt5.positions_get(ticket=r["ticket"]):
            continue
        deals = mt5.history_deals_get(position=r["ticket"]) or []
        outs = [d for d in deals if d.entry == 1]
        if not outs:
            continue
        profit = sum(d.profit + d.commission + d.swap for d in deals)
        last = max(outs, key=lambda d: d.time)
        conn.execute("UPDATE dt_log SET outcome=?, profit=?, closed_at=? WHERE id=?",
                     (1 if profit > 0 else 0, round(profit, 2),
                      datetime.fromtimestamp(last.time, UTC).isoformat(), r["id"]))
    conn.commit()
    conn.close()


# ── décision (fonction pure : testée sur l'historique) ───────────────────────
def decide(bars, position_open_time=None, closed_during_bar=False) -> dict:
    """bars : bougies journalières TERMINÉES (ancienne -> récente), au moins
    N_ENTRY + 1. Juge la dernière. Retourne action enter / exit / hold / none."""
    i = len(bars) - 1
    h, l, c = bars["high"], bars["low"], bars["close"]
    bar_t = int(bars["time"][i])
    atr = float(atr_series(bars)[i])
    out = {"bar_time": bar_t, "close": float(c[i]), "atr": atr}
    if position_open_time is None:
        hi = float(h[i - N_ENTRY:i].max())
        out["level"] = hi
        if closed_during_bar:
            return out | {"action": "none", "why": "a position closed during this bar (as in the test)"}
        if c[i] > hi:
            return out | {"action": "enter", "stop_dist": STOP_ATR * atr,
                          "why": f"close {c[i]:.2f} above the {N_ENTRY}-day high {hi:.2f}"}
        return out | {"action": "none", "why": f"close {c[i]:.2f} below the {N_ENTRY}-day high {hi:.2f}"}
    lo = float(l[i - N_EXIT:i].min())
    out["level"] = lo
    if position_open_time >= bar_t:
        return out | {"action": "hold", "why": "entered during this bar: only the stop applies (as in the test)"}
    if c[i] < lo:
        return out | {"action": "exit", "why": f"close {c[i]:.2f} below the {N_EXIT}-day low {lo:.2f}"}
    return out | {"action": "hold", "why": f"close {c[i]:.2f} above the {N_EXIT}-day low {lo:.2f}"}


def size_trade(balance: float, risk_pct: float, risk_per_lot: float, vmin: float, step: float, vmax: float):
    """Lot pour risquer risk_pct % du solde. None si même le lot minimum risque
    plus (le trade est alors refusé, jamais forcé)."""
    if balance <= 0 or risk_per_lot <= 0:
        return None
    budget = balance * risk_pct / 100.0
    lot = math.floor(budget / risk_per_lot / step + 1e-9) * step
    lot = round(min(lot, vmax), 6)
    return lot if lot >= vmin - 1e-9 else None


# ── exécution ────────────────────────────────────────────────────────────────
def _completed_bars(mt5sym, now_utc):
    bars = mt5.copy_rates_from_pos(mt5sym, mt5.TIMEFRAME_D1, 0, HISTORY_BARS)
    if bars is None or len(bars) < N_ENTRY + 30:
        return None
    return bars[bars["time"] + 86400 <= now_utc.timestamp()]


def _closed_during(mt5sym, bar_t, now_utc) -> bool:
    """Une position DT s'est-elle fermée PENDANT cette bougie par son stop (ou à
    la main) ? Dans le test, une sortie par le stop au jour i interdit une entrée
    jugée sur ce même jour i. La sortie 10 jours du bot, elle, est décidée sur la
    bougie précédente et exécutée juste après minuit, donc DANS celle-ci : elle ne
    doit pas bloquer l'entrée (le test juge l'entrée dès le jour suivant la sortie)."""
    deals = mt5.history_deals_get(datetime.fromtimestamp(bar_t, UTC), now_utc + timedelta(hours=1)) or []
    return any(d.magic == DT_MAGIC and d.symbol == mt5sym and d.entry in (1, 3) and bar_t <= d.time < bar_t + 86400
               and not (d.comment or "").startswith("DT exit") for d in deals)


def _open_long(symbol, mt5sym, stop_dist, risk_pct, d, bar_t):
    si, tick, acct = mt5.symbol_info(mt5sym), mt5.symbol_info_tick(mt5sym), mt5.account_info()
    if si is None or tick is None or acct is None:
        _log(symbol, "error", bar_time=bar_t, reason="no symbol info / tick / account")
        return
    price = tick.ask
    sl = round(price - stop_dist, si.digits)
    loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, mt5sym, 1.0, price, sl)
    risk_per_lot = -loss if loss is not None else 0.0
    lot = size_trade(acct.balance, risk_pct, risk_per_lot, si.volume_min, si.volume_step or 0.01, si.volume_max)
    if lot is None:
        min_risk = risk_per_lot * si.volume_min
        why = (f"account too small: the minimum lot {si.volume_min} risks ${min_risk:.2f} = "
               f"{100 * min_risk / acct.balance:.1f}% of ${acct.balance:.2f} (limit {risk_pct:g}%)")
        print(f"[DT] {symbol}: entry signal skipped - {why}")
        _log(symbol, "skip", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"],
             price=price, sl=sl, reason=why)
        return
    fm = si.filling_mode
    filling = mt5.ORDER_FILLING_IOC if fm & 2 else (mt5.ORDER_FILLING_FOK if fm & 1 else mt5.ORDER_FILLING_RETURN)
    min_dist = max(si.trade_stops_level * si.point, si.point * 10)
    sl = round(min(sl, price - min_dist), si.digits)
    res = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": mt5sym, "volume": float(lot),
                          "type": mt5.ORDER_TYPE_BUY, "price": price, "sl": sl, "tp": 0.0, "deviation": 20,
                          "magic": DT_MAGIC, "comment": COMMENT, "type_time": mt5.ORDER_TIME_GTC,
                          "type_filling": filling})
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        why = f"order refused: {mt5.last_error() if res is None else (res.comment or res.retcode)}"
        print(f"[DT] {symbol}: {why}")
        _log(symbol, "error", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"],
             price=price, sl=sl, volume=lot, reason=why)
        return
    risk_usd = risk_per_lot * lot
    print(f"[DT] {symbol}: BOUGHT {lot} @ {res.price or price} SL {sl} - risk ${risk_usd:.2f} - {d['why']}")
    _log(symbol, "entry", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"],
         price=res.price or price, sl=sl, volume=lot, risk_usd=round(risk_usd, 2),
         risk_pct=round(100 * risk_usd / acct.balance, 2), ticket=res.order, reason=d["why"])


def dt_scan(now_utc: datetime | None = None) -> None:
    ensure_mt5()
    _ensure_ready()
    update_outcomes()
    now_utc = now_utc or datetime.now(UTC)
    s = dt_state["settings"]
    for symbol in ALLOWED:
        mt5sym = SYMBOL_MAP.get(symbol)
        mine = [p for p in (mt5.positions_get(symbol=mt5sym) or []) if p.magic == DT_MAGIC]
        if symbol not in s["symbols"] and not mine:
            continue                                    # symbole retiré et rien à gérer
        bars = _completed_bars(mt5sym, now_utc)
        if bars is None or len(bars) < N_ENTRY + 30:
            continue
        bar_t = int(bars["time"][-1])
        if bar_t <= int(dt_state["last_bar"].get(symbol, 0)):
            continue                                    # bougie déjà jugée
        pos = mine[0] if mine else None
        d = decide(bars, position_open_time=pos.time if pos else None,
                   closed_during_bar=(pos is None and _closed_during(mt5sym, bar_t, now_utc)))
        print(f"[DT] {symbol} {datetime.fromtimestamp(bar_t, UTC):%d/%m}: {d['action']} - {d['why']}")
        dt_state["last_check"][symbol] = {k: d[k] for k in ("action", "why", "close", "level", "atr", "bar_time")}
        if d["action"] == "exit" and pos is not None:
            r = close_partial(pos, pos.volume, "DT exit 10d low")
            _log(symbol, "exit" if r["success"] else "error", bar_time=bar_t, close=d["close"], level=d["level"],
                 atr=d["atr"], price=r.get("price"), volume=pos.volume, ticket=pos.ticket,
                 reason=d["why"] if r["success"] else f"close refused: {r['reason']}")
            if not r["success"]:
                continue                                # on réessaiera au scan suivant
        elif d["action"] == "enter":
            if not dt_state["enabled"] or symbol not in s["symbols"]:
                _log(symbol, "check", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"],
                     reason=f"entry signal, but the bot is stopped or {symbol} is off - not taken")
            else:
                _open_long(symbol, mt5sym, d["stop_dist"], float(s["risk_pct"]), d, bar_t)
        else:
            _log(symbol, "check", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"], reason=d["why"])
        dt_state["last_bar"][symbol] = bar_t
        _persist()


def dt_job() -> None:
    """Minuteur 'dt_scan' (main.py). Tourne même arrêté : gère les SORTIES des
    positions ouvertes ; n'ouvre rien quand le bot est arrêté."""
    try:
        dt_scan()
        dt_state["last_error"] = None
    except Exception as e:
        dt_state["last_error"] = f"{datetime.now(UTC):%H:%M:%S} {e}"
        print(f"[DT] scan error: {e}")
    dt_state["last_scan"] = datetime.now(UTC).isoformat()


# ── état pour l'app ──────────────────────────────────────────────────────────
def dt_status() -> dict:
    ensure_mt5()
    s = dt_state["settings"]
    acct = mt5.account_info()
    bal = acct.balance if acct else 0.0
    now = datetime.now(UTC)
    symbols = {}
    for symbol in ALLOWED:
        mt5sym = SYMBOL_MAP.get(symbol)
        si, tick = mt5.symbol_info(mt5sym), mt5.symbol_info_tick(mt5sym)
        bars = _completed_bars(mt5sym, now)
        info = {"enabled": symbol in s["symbols"], "last_check": dt_state["last_check"].get(symbol)}
        if bars is not None and si is not None and tick is not None:
            i = len(bars) - 1
            atr = float(atr_series(bars)[i])
            loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, mt5sym, si.volume_min, tick.ask, tick.ask - STOP_ATR * atr)
            min_risk = -loss if loss is not None else None
            info.update(price=tick.bid, entry_level=float(bars["high"][i - N_ENTRY + 1:i + 1].max()),
                        exit_level=float(bars["low"][i - N_EXIT + 1:i + 1].min()), atr=round(atr, 2),
                        min_lot=si.volume_min, min_lot_risk_usd=round(min_risk, 2) if min_risk else None,
                        min_lot_risk_pct=round(100 * min_risk / bal, 1) if (min_risk and bal > 0) else None,
                        tradable=bool(min_risk and bal > 0 and 100 * min_risk / bal <= s["risk_pct"]))
        symbols[symbol] = info
    ls = dt_state.get("last_scan")
    pos = [p for p in (mt5.positions_get() or []) if p.magic == DT_MAGIC]
    return {"bot": "daily_trend", "magic": DT_MAGIC, "enabled": dt_state["enabled"], "settings": s,
            "rules": {"entry_days": N_ENTRY, "exit_days": N_EXIT, "stop_atr": STOP_ATR, "atr_days": 14,
                      "side": "long only", "timeframe": "daily (UTC)"},
            "last_scan": ls,
            "last_scan_age_min": round((now - datetime.fromisoformat(ls)).total_seconds() / 60, 1) if ls else None,
            "last_error": dt_state.get("last_error"),
            "account": {"balance": round(bal, 2), "demo": bool(acct and acct.trade_mode == 0)},
            "symbols": symbols,
            "open_positions": [{"symbol": p.symbol, "volume": p.volume, "price_open": p.price_open, "sl": p.sl,
                                "profit": round(p.profit + p.swap, 2),
                                "since": datetime.fromtimestamp(p.time, UTC).isoformat()} for p in pos]}


def dt_history(limit: int = 30) -> dict:
    _ensure_ready()
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT timestamp, symbol, event, bar_time, close, level, price, sl, volume, risk_usd, risk_pct, "
        "ticket, outcome, profit, closed_at, reason FROM dt_log WHERE event IN ('entry','exit','skip','error') "
        "ORDER BY id DESC LIMIT ?", (int(limit),))]
    conn.close()
    return {"bot": "daily_trend", "events": rows}
