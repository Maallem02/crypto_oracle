"""Daily Trend SPOT — les règles du bot Daily Trend sur Binance SPOT, TESTNET (2026-10-13).

Pourquoi : avec peu d'argent, le lot minimum Exness interdit tout trade Daily
Trend (ETH ~900$, BTC ~2200$ pour 2 %). En spot on achète des fractions (ordre
minimum 5$), sans swap ni levier. Simulé 2018-2026 avec 200$ : 91/91 trades
pris, +22.8 %/an, pire baisse -18 % (3 dernières années : +11 %/an).

MÊMES RÈGLES que features/daily_trend/runner.py (decide() — prouvée identique au
code de l'étude) : bougies journalières RÉELLES de Binance (00:00 UTC), achat au
marché quand la clôture dépasse le plus haut 20 jours, stop 2 x ATR14, vente au
marché quand la clôture passe sous le plus bas 10 jours, ACHATS seulement, BTC
et ETH, plafond crypto commun (max_crypto_risk_pct).

SPOT :
  * capital_usd = l'argent CONFIÉ au bot (le compte testnet contient bien plus) ;
    équité du bot = capital + gains/pertes réalisés + latents ; il n'achète
    jamais pour plus que son argent libre ni que l'USDT libre du compte ;
  * taille = risk_pct % de l'équité perdu si le stop est touché, quantité
    arrondie VERS LE BAS au pas Binance ; trade sauté si < minimum (5$) ;
  * le stop est un ordre STOP_LOSS posé CHEZ BINANCE juste après l'achat (vente
    au marché s'il est touché). Si le stop ne peut pas être posé, la position est
    revendue aussitôt : jamais de position sans stop ;
  * reprise : l'achat est noté AVANT l'envoi (identifiant client) ; après un arrêt
    en plein achat, l'ordre est retrouvé chez Binance et le stop posé.

SÉCURITÉ : ordres vers le TESTNET uniquement (binance_spot refuse le reste) ;
ARRÊTÉ au premier démarrage ; Stop = plus d'entrée, mais stops et sorties restent
gérés ; aucune clé dans les journaux ni dans l'app.
"""
import json
import os
import threading
from datetime import datetime, timezone

from core.database import get_db
from features.daily_trend import runner as DT
from features.daily_trend.binance_spot import (TESTNET, BinanceError, SpotClient, ceil_to, daily_klines,
                                               fill_summary, floor_to)
from features.volume_profile.strategy import atr_series

UTC = timezone.utc
PAIRS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
DEFAULT_SETTINGS = {"symbols": ["BTC", "ETH"], "risk_pct": 2.0, "max_crypto_risk_pct": 3.0, "capital_usd": 200.0}
CAPITAL_RANGE = (20.0, 100000.0)
HISTORY_BARS = 400
CASH_MARGIN = 0.995              # marge pour le prix qui bouge entre le calcul et l'exécution
FEE_RATE = 0.001                 # frais Binance spot standard par côté : comptés dans la taille, pour
                                 # qu'un stop touché ne perde pas plus que risk_pct (achat + vente)
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dt_spot_state.json")
_lock = threading.RLock()
_client = None
_ready = False


class SettingsError(ValueError):
    """Réglage refusé — message affiché tel quel dans l'app."""


def client() -> SpotClient:
    global _client
    if _client is None:
        _client = SpotClient()               # TESTNET, clés lues dans .env au démarrage du backend
    return _client


# ── état persistant ──────────────────────────────────────────────────────────
def _load_state() -> dict:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    return {"enabled": bool(d.get("enabled", False)),                       # 1er démarrage : ARRÊTÉ
            "settings": {**DEFAULT_SETTINGS, **(d.get("settings") or {})},
            "positions": d.get("positions") or {},                          # symbole -> position ouverte
            "pending": d.get("pending") or {},                              # achat envoyé, pas encore noté
            "realized_usd": float(d.get("realized_usd") or 0.0),
            "dust": {k: float(v) for k, v in (d.get("dust") or {}).items()},   # miettes < 1 pas, par coin
            "last_bar": d.get("last_bar") or {},
            "last_exit": d.get("last_exit") or {},                          # symbole -> {time, kind}
            "last_check": d.get("last_check") or {}}


state = _load_state()
state.update(last_scan=None, last_error=None)


def _persist() -> None:
    keys = ("enabled", "settings", "positions", "pending", "realized_usd", "dust", "last_bar", "last_exit",
            "last_check")
    try:
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k: state[k] for k in keys} | {"changed_at": datetime.now(UTC).isoformat()}, fh, indent=2)
        os.replace(tmp, _STATE_FILE)
    except OSError as e:
        print(f"[DTS] could not persist state: {e}")


def set_enabled(flag: bool) -> dict:
    with _lock:
        state["enabled"] = bool(flag)
        _persist()
    print(f"[DTS] bot {'STARTED' if flag else 'STOPPED'} (Binance TESTNET)")
    return spot_status()


def update_settings(patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise SettingsError("settings must be an object")
    new, errors = dict(state["settings"]), []
    if "symbols" in patch:
        syms = patch["symbols"]
        if not isinstance(syms, list):
            errors.append("symbols must be a list")
        else:
            syms = list(dict.fromkeys(str(x).upper() for x in syms))
            bad = [x for x in syms if x not in PAIRS]
            if bad:
                errors.append(f"only BTC and ETH were tested for this bot (not {', '.join(bad)})")
            new["symbols"] = syms
    for key, (lo, hi) in (("risk_pct", DT.RISK_RANGE), ("max_crypto_risk_pct", DT.CAP_RANGE),
                          ("capital_usd", CAPITAL_RANGE)):
        if key in patch:
            v = patch[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errors.append(f"{key} must be a number")
            elif not lo <= float(v) <= hi:
                errors.append(f"{key} must be between {lo:g} and {hi:g}")
            else:
                new[key] = round(float(v), 2)
    if errors:
        raise SettingsError("; ".join(errors))
    with _lock:
        state["settings"] = new
        _persist()
    print(f"[DTS] settings saved: {new}")
    return spot_status()


# ── journal ──────────────────────────────────────────────────────────────────
def _ensure_ready():
    global _ready
    if _ready:
        return
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dt_spot_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol    TEXT NOT NULL,
            event     TEXT,        -- check | entry | skip | exit | stop | error | warn
            bar_time  TEXT,
            close     REAL, level REAL, atr REAL,
            price     REAL, sl REAL, qty REAL, cost_usd REAL, risk_usd REAL, risk_pct REAL,
            order_id  INTEGER,
            outcome   INTEGER,     -- entrées : 1 gain, 0 perte, NULL ouverte
            profit    REAL,
            closed_at TEXT,
            reason    TEXT
        )""")
    conn.commit()
    conn.close()
    _ready = True


def _log(symbol, event, *, bar_time=None, close=None, level=None, atr=None, price=None, sl=None, qty=None,
         cost_usd=None, risk_usd=None, risk_pct=None, order_id=None, reason=None):
    _ensure_ready()
    conn = get_db()
    conn.execute(
        "INSERT INTO dt_spot_log (timestamp, symbol, event, bar_time, close, level, atr, price, sl, qty, cost_usd, "
        "risk_usd, risk_pct, order_id, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(UTC).isoformat(), symbol, event,
         datetime.fromtimestamp(bar_time, UTC).isoformat() if bar_time else None,
         close, level, atr, price, sl, qty, cost_usd, risk_usd, risk_pct, order_id, reason))
    conn.commit()
    conn.close()


def _close_journal(entry_order_id, profit, closed_at):
    _ensure_ready()
    conn = get_db()
    conn.execute("UPDATE dt_spot_log SET outcome=?, profit=?, closed_at=? WHERE event='entry' AND order_id=?",
                 (1 if profit > 0 else 0, round(profit, 4), closed_at, entry_order_id))
    conn.commit()
    conn.close()


# ── argent du bot ────────────────────────────────────────────────────────────
def _equity(prices: dict) -> float:
    s = state["settings"]
    eq = float(s["capital_usd"]) + state["realized_usd"]
    for sym, p in state["positions"].items():
        px = prices.get(sym)
        eq += (px * p["qty"] - p["cost_usd"]) if px else 0.0
    return eq


def _free_cash() -> float:
    """Argent du bot pas encore investi (au prix d'achat) : pas de levier."""
    s = state["settings"]
    return float(s["capital_usd"]) + state["realized_usd"] - sum(p["cost_usd"] for p in state["positions"].values())


def size_order(equity, risk_pct, cap_pct, open_risk_usd, stop_dist, price, cash, step, min_qty, min_notional):
    """-> (quantité texte | None, risque % retenu, raison). Fonction pure (testée)."""
    open_pct = 100.0 * open_risk_usd / equity if equity > 0 else 100.0
    r = DT.risk_left(risk_pct, cap_pct, open_pct)
    if r < DT.MIN_LEFT_PCT:
        return None, 0.0, f"crypto risk cap full: {open_pct:.2f}% already at risk, cap {cap_pct:g}%"
    qty = (r / 100.0) * equity / (stop_dist + 2 * FEE_RATE * price)   # perte au stop + frais aller-retour
    note = ""
    if qty * price > cash * CASH_MARGIN:
        qty, note = max(0.0, cash * CASH_MARGIN / price), " (cut to the cash available: no leverage)"
    q = floor_to(qty, step)
    if float(q) < min_qty or float(q) * price < min_notional:
        return None, r, (f"too small: {q} = ${float(q) * price:.2f} is under Binance's minimum order "
                         f"(${min_notional:g}) — capital ${equity:.2f}, risk {r:.2f}%{note}")
    return q, r, note.strip()


# ── ordres ───────────────────────────────────────────────────────────────────
def _take_dust(sym, received, step) -> str:
    """Coins reçus + miettes des achats précédents -> quantité vendable (au pas
    Binance) ; le reste (< 1 pas) est gardé et vendu avec le prochain ordre. Sans
    ça, les frais Binance pris EN COIN laissaient ~1 pas bloqué à chaque achat
    (~0.8$ en BTC, ~0.25$ en ETH), compté comme une perte (2026-10-16)."""
    total = received + float(state["dust"].get(sym, 0.0))
    net = floor_to(total, step)
    state["dust"][sym] = round(max(0.0, total - float(net)), 12)
    return net


def _place_stop(sym, pair, qty, stop, tag) -> int:
    o = client().order(pair, "SELL", "STOP_LOSS", quantity=qty, stop_price=stop, client_id=tag)
    return int(o["orderId"])


def _record_entry(sym, pair, resp, d, bar_t, stop_dist, risk_pct_used, client_id, eq=None, note=""):
    rules = client().symbol_rules(pair)
    fs = fill_summary(resp)
    net = _take_dust(sym, fs["qty"] - fs["base_fee"], rules["step"])
    cost = fs["quote"] + fs["quote_fee"]
    stop = floor_to(fs["avg"] - stop_dist, rules["tick"])
    try:
        stop_id = _place_stop(sym, pair, net, stop, f"dtsS-{sym}-{bar_t}")
    except BinanceError as e:
        why = f"stop could not be placed ({e}) - bought back out at market: never hold without a stop"
        print(f"[DTS] {sym}: {why}")
        try:
            client().order(pair, "SELL", "MARKET", quantity=net)
        except BinanceError as e2:
            why += f"; the sale failed too ({e2}) - CHECK THE TESTNET ACCOUNT"
        _log(sym, "error", bar_time=bar_t, price=fs["avg"], qty=float(net), reason=why)
        return
    risk_usd = cost - float(stop) * float(net)
    eq = eq or (float(state["settings"]["capital_usd"]) + state["realized_usd"])
    t_ms = resp.get("transactTime") or resp.get("updateTime") or resp.get("time")
    state["positions"][sym] = {"pair": pair, "qty": float(net), "qty_text": net, "entry": fs["avg"],
                               "cost_usd": round(cost, 6), "stop": float(stop), "stop_id": stop_id,
                               "entry_id": int(resp["orderId"]), "client_id": client_id,
                               "t_in": int(t_ms) // 1000 if t_ms else int(datetime.now(UTC).timestamp()),
                               "bar_time": bar_t, "risk_usd": round(risk_usd, 6)}
    state["pending"].pop(sym, None)
    _persist()
    msg = (f"BOUGHT {net} {sym} @ {fs['avg']:.2f} (${cost:.2f}), stop {stop} at Binance - risk ${risk_usd:.2f} "
           f"= {100 * risk_usd / eq:.2f}% of ${eq:.2f}")
    print(f"[DTS] {sym}: {msg}")
    _log(sym, "entry", bar_time=bar_t, close=d.get("close"), level=d.get("level"), atr=d.get("atr"),
         price=fs["avg"], sl=float(stop), qty=float(net), cost_usd=round(cost, 4), risk_usd=round(risk_usd, 4),
         risk_pct=round(100 * risk_usd / eq, 2), order_id=int(resp["orderId"]),
         reason=d.get("why", "") + (f"; risk {risk_pct_used:.2f}%" if risk_pct_used else "") + (f" {note}" if note else ""))


def _enter(sym, d, bar_t):
    s, c, pair = state["settings"], client(), PAIRS[sym]
    rules = c.symbol_rules(pair)
    price = c.price(pair)
    prices = {x: (price if x == sym else c.price(PAIRS[x])) for x in set(state["positions"]) | {sym}}
    eq = _equity(prices)
    usdt = c.balances().get("USDT", {}).get("free", 0.0)
    q, r, why = size_order(eq, float(s["risk_pct"]), float(s["max_crypto_risk_pct"]),
                           sum(p["risk_usd"] for p in state["positions"].values()), d["stop_dist"], price,
                           min(_free_cash(), usdt), rules["step"], rules["min_qty"], rules["min_notional"])
    if q is None:
        print(f"[DTS] {sym}: entry signal skipped - {why}")
        _log(sym, "skip", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"], price=price, reason=why)
        return
    cid = f"dts-{sym}-{bar_t}"
    state["pending"][sym] = {"client_id": cid, "bar_time": bar_t, "stop_dist": d["stop_dist"], "risk": r,
                             "d": {k: d[k] for k in ("close", "level", "atr", "why")}}
    _persist()                                          # noté AVANT l'envoi : reprise possible
    try:
        resp = c.order(pair, "BUY", "MARKET", quantity=q, client_id=cid)
    except BinanceError as e:
        state["pending"].pop(sym, None)
        _persist()
        print(f"[DTS] {sym}: buy refused - {e}")
        _log(sym, "error", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"], price=price,
             reason=f"buy refused: {e}")
        return
    _record_entry(sym, pair, resp, d, bar_t, d["stop_dist"], r, cid, eq=eq, note=why)


def _close(sym, kind, exit_avg, proceeds, t_out, reason):
    p = state["positions"].pop(sym)
    pnl = proceeds - p["cost_usd"]
    state["realized_usd"] = round(state["realized_usd"] + pnl, 6)
    state["last_exit"][sym] = {"time": int(t_out), "kind": kind}
    _persist()
    r_mult = pnl / p["risk_usd"] if p["risk_usd"] > 0 else None
    msg = f"{kind.upper()} - sold {p['qty_text']} @ {exit_avg:.2f}: {pnl:+.2f}$" + (f" ({r_mult:+.2f}R)" if r_mult is not None else "")
    print(f"[DTS] {sym}: {msg}")
    _log(sym, "stop" if kind == "stop" else "exit", price=exit_avg, qty=p["qty"], order_id=p["entry_id"],
         reason=f"{reason}; {msg}")
    _close_journal(p["entry_id"], pnl, datetime.fromtimestamp(int(t_out), UTC).isoformat())


def _check_stops(now_ts):
    """Stops exécutés chez Binance -> positions fermées ; stop disparu -> reposé."""
    c = client()
    for sym, p in list(state["positions"].items()):
        try:
            o = c.get_order(p["pair"], p["stop_id"])
        except BinanceError as e:
            if e.code == -2013:                          # ordre introuvable (remise à zéro du testnet ?)
                o = {"status": "MISSING"}
            else:
                raise
        st = o.get("status")
        if st == "FILLED":
            fees = c.order_fees(p["pair"], p["stop_id"])
            qty, quote = float(o["executedQty"]), float(o["cummulativeQuoteQty"])
            _close(sym, "stop", quote / qty if qty else p["stop"], quote - fees["quote_fee"],
                   int(o.get("updateTime", now_ts * 1000)) // 1000, "stop hit at Binance")
        elif st in ("CANCELED", "EXPIRED", "REJECTED", "MISSING"):
            try:
                p["stop_id"] = _place_stop(sym, p["pair"], p["qty_text"], floor_to(p["stop"], c.symbol_rules(p["pair"])["tick"]),
                                           f"dtsR-{sym}-{now_ts}")
                _persist()
                _log(sym, "warn", sl=p["stop"], qty=p["qty"], reason=f"the stop order was {st.lower()} - placed again")
            except BinanceError as e:
                if e.code == -2010:                      # solde insuffisant : la position n'existe plus
                    _close(sym, "lost", p["entry"], 0.0, now_ts,
                           f"stop {st.lower()} and the coins are gone (testnet reset?) - counted as fully lost")
                else:
                    _log(sym, "error", reason=f"stop {st.lower()} and could not be placed again: {e}")


def _recover_pending():
    """Achat envoyé avant un arrêt du backend : le retrouver chez Binance."""
    c = client()
    for sym, pe in list(state["pending"].items()):
        try:
            o = c.get_order_by_client(PAIRS[sym], pe["client_id"])
        except BinanceError as e:
            if e.code == -2013:                          # jamais arrivé chez Binance
                state["pending"].pop(sym, None)
                _persist()
                continue
            raise
        if o.get("status") == "FILLED" and sym not in state["positions"]:
            o["fills"] = []                              # frais relus à part
            fees = c.order_fees(PAIRS[sym], o["orderId"])
            o["fills"] = [{"commission": fees["base_fee"], "commissionAsset": sym},
                          {"commission": fees["quote_fee"], "commissionAsset": "USDT"}]
            o["symbol"] = PAIRS[sym]
            _log(sym, "warn", reason="buy found at Binance after a restart - recorded and stop placed")
            _record_entry(sym, PAIRS[sym], o, pe["d"], pe["bar_time"], pe["stop_dist"], pe["risk"], pe["client_id"])
        else:
            state["pending"].pop(sym, None)
            _persist()


def _exit(sym, d, bar_t):
    c, p = client(), state["positions"][sym]
    try:
        c.cancel_order(p["pair"], p["stop_id"])
    except BinanceError as e:
        o = c.get_order(p["pair"], p["stop_id"])
        if o.get("status") == "FILLED":                  # le stop est passé avant : déjà vendu
            _check_stops(int(datetime.now(UTC).timestamp()))
            return
        raise e
    try:
        resp = c.order(p["pair"], "SELL", "MARKET", quantity=p["qty_text"])
    except BinanceError as e:                            # stop annulé mais vente refusée : on repose le stop
        p["stop_id"] = _place_stop(sym, p["pair"], p["qty_text"], floor_to(p["stop"], c.symbol_rules(p["pair"])["tick"]),
                                   f"dtsR-{sym}-{bar_t}")
        _persist()
        _log(sym, "error", bar_time=bar_t, reason=f"10-day exit refused ({e}) - stop placed again, retry next scan")
        return
    fs = fill_summary(resp)
    _close(sym, "channel", fs["avg"], fs["quote"] - fs["quote_fee"],
           int(resp.get("transactTime", 0)) // 1000 or int(datetime.now(UTC).timestamp()), d["why"])


# ── ordre de test (bouton de l'app, 2026-10-16) ──────────────────────────────
TEST_ORDER_USD = 6.0
TEST_STOP_PCT = 5.0


def test_order(symbol: str = "BTC") -> dict:
    """Bouton « Test order » : vérifie toute la chaîne d'ordres sur le TESTNET avec
    ~6$ : achat au marché, STOP_LOSS posé chez Binance, stop relu, stop annulé,
    revente au marché. N'entre PAS dans les comptes du bot (capital, positions,
    résultat). En cas d'échec en route : le stop est annulé et les coins revendus.
    Refusé si le bot tient déjà ce coin (pour ne jamais toucher sa position)."""
    sym = str(symbol).upper()
    if sym not in PAIRS:
        raise SettingsError(f"test order: only {', '.join(PAIRS)}")
    with _lock:
        c, pair = client(), PAIRS[sym]
        if not c.has_keys():
            raise SettingsError("no API keys: add BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET to .env, "
                                "then restart the backend")
        if sym in state["positions"] or sym in state["pending"]:
            raise SettingsError(f"the bot holds a {sym} position: no test order, so it can never touch it")
        steps, tag = [], int(datetime.now(UTC).timestamp())

        def step(name, ok, detail=""):
            steps.append({"step": name, "ok": bool(ok), "detail": detail})
            print(f"[DTS] test order {sym} - {name}: {'ok' if ok else 'FAILED'} {detail}")

        buy = sold = None
        net = stop_id = None
        stop_live = False
        try:
            rules = c.symbol_rules(pair)
            price = c.price(pair)
            qty = ceil_to(max(TEST_ORDER_USD, rules["min_notional"] * 1.2) / price, rules["step"])
            buy = fill_summary(c.order(pair, "BUY", "MARKET", quantity=qty, client_id=f"dtsT-{sym}-{tag}"))
            dust_before = float(state["dust"].get(sym, 0.0))
            net = _take_dust(sym, buy["qty"] - buy["base_fee"], rules["step"])
            _persist()
            step("1. Market buy", True, f"bought {buy['qty']:g} {sym} at {buy['avg']:.2f} for ${buy['quote']:.2f}")
            stop = floor_to(buy["avg"] * (1 - TEST_STOP_PCT / 100), rules["tick"])
            stop_id = _place_stop(sym, pair, net, stop, f"dtsTS-{sym}-{tag}")
            stop_live = True
            step("2. Stop placed at Binance", True, f"STOP_LOSS sell {net} at {stop} ({TEST_STOP_PCT:g}% below)")
            st = c.get_order(pair, stop_id).get("status")
            step("3. Stop read back from Binance", st == "NEW", f"status {st}")
            c.cancel_order(pair, stop_id)
            stop_live = False
            step("4. Stop cancelled", True)
            sold = fill_summary(c.order(pair, "SELL", "MARKET", quantity=net, client_id=f"dtsTX-{sym}-{tag}"))
            step("5. Market sell", True, f"sold {sold['qty']:g} {sym} at {sold['avg']:.2f} for ${sold['quote']:.2f}")
        except BinanceError as e:
            step("Failed", False, str(e))
            if stop_live:                                   # nettoyage : rien ne doit rester ouvert
                try:
                    c.cancel_order(pair, stop_id)
                    step("Clean-up: stop cancelled", True)
                except BinanceError as e2:
                    step("Clean-up: stop could not be cancelled", False, f"{e2} - CHECK THE TESTNET ACCOUNT")
            if buy and sold is None and net and float(net) > 0:
                try:
                    sold = fill_summary(c.order(pair, "SELL", "MARKET", quantity=net))
                    step("Clean-up: coins sold back", True, f"at {sold['avg']:.2f} for ${sold['quote']:.2f}")
                except BinanceError as e2:
                    step("Clean-up: coins could not be sold back", False, f"{e2} - CHECK THE TESTNET ACCOUNT")
        cost = None
        if buy and sold:                                # les miettes gardées ne sont pas un coût
            kept = float(state["dust"].get(sym, 0.0)) - dust_before
            cost = (buy["quote"] + buy["quote_fee"]) - (sold["quote"] - sold["quote_fee"]) - kept * sold["avg"]
            if kept > 0:
                step("Leftover kept", True, f"{kept:.8f} {sym} (~${kept * sold['avg']:.2f}) under Binance's minimum "
                                            f"step - sold together with the next order")
        ok = bool(steps) and all(s["ok"] for s in steps)
        summary = ("test order OK: buy, stop at Binance, cancel, sell all worked" if ok else
                   "test order FAILED: " + "; ".join(f"{s['step']} {s['detail']}" for s in steps if not s["ok"]))
        if cost is not None:
            summary += f" (cost ${cost:.4f}: spread + fees, test money)"
        _log(sym, "test", price=buy["avg"] if buy else None, qty=float(net) if net else None, reason=summary)
        return {"ok": ok, "symbol": sym, "steps": steps, "cost_usd": None if cost is None else round(cost, 4),
                "summary": summary, "time": datetime.now(UTC).isoformat()}


# ── le scan (toutes les 5 min, main.py) ──────────────────────────────────────
def spot_scan(now_utc: datetime | None = None):
    now_utc = now_utc or datetime.now(UTC)
    now_ts = int(now_utc.timestamp())
    with _lock:
        if not client().has_keys():
            raise BinanceError("no API keys: add BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET to .env, "
                               "then restart the backend")
        _recover_pending()
        _check_stops(now_ts)
        s = state["settings"]
        due = (now_ts // 86400) * 86400 - 86400               # ouverture de la dernière bougie terminée
        entries = []
        for sym, pair in PAIRS.items():
            pos = state["positions"].get(sym)
            if sym not in s["symbols"] and not pos:
                continue
            if int(state["last_bar"].get(sym, 0)) >= due:
                continue                                       # bougie du jour déjà jugée
            bars = daily_klines(pair, HISTORY_BARS, now_ms=now_ts * 1000)
            if len(bars) < DT.N_ENTRY + 30:
                continue
            bar_t = int(bars["time"][-1])
            if bar_t <= int(state["last_bar"].get(sym, 0)):
                continue
            le = state["last_exit"].get(sym) or {}
            closed_during = (pos is None and le.get("kind") in ("stop", "lost")
                             and bar_t <= int(le.get("time", 0)) < bar_t + 86400)
            d = DT.decide(bars, position_open_time=pos["t_in"] if pos else None, closed_during_bar=closed_during)
            print(f"[DTS] {sym} {datetime.fromtimestamp(bar_t, UTC):%d/%m}: {d['action']} - {d['why']}")
            state["last_check"][sym] = {k: d[k] for k in ("action", "why", "close", "level", "atr", "bar_time")}
            if d["action"] == "exit" and pos is not None:
                _exit(sym, d, bar_t)
            elif d["action"] == "enter":
                if not state["enabled"] or sym not in s["symbols"]:
                    _log(sym, "check", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"],
                         reason=f"entry signal, but the bot is stopped or {sym} is off - not taken")
                else:
                    entries.append((sym, d, bar_t))
                    state["last_bar"][sym] = bar_t             # noté avant l'achat : jamais deux fois
                    _persist()
                    continue
            else:
                _log(sym, "check", bar_time=bar_t, close=d["close"], level=d["level"], atr=d["atr"], reason=d["why"])
            state["last_bar"][sym] = bar_t
            _persist()
        for sym, d, bar_t in entries:                          # après les sorties, BTC puis ETH
            _enter(sym, d, bar_t)


def spot_job():
    if not client().has_keys():                         # pas de clés : signalé dans l'app, rien d'autre
        state["last_error"] = ("no API keys: add BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET "
                               "to .env, then restart the backend")
        state["last_scan"] = datetime.now(UTC).isoformat()
        return
    try:
        spot_scan()
        state["last_error"] = None
    except Exception as e:
        state["last_error"] = f"{datetime.now(UTC):%H:%M:%S} {e}"
        print(f"[DTS] scan error: {e}")
    state["last_scan"] = datetime.now(UTC).isoformat()


# ── état pour l'app ──────────────────────────────────────────────────────────
_bars_cache = {}


def _bars(pair, now_ts):
    day = now_ts // 86400
    if _bars_cache.get(pair, (None,))[0] != day:
        _bars_cache[pair] = (day, daily_klines(pair, HISTORY_BARS, now_ms=now_ts * 1000))
    return _bars_cache[pair][1]


def spot_status() -> dict:
    s, c = state["settings"], client()
    now = datetime.now(UTC)
    now_ts = int(now.timestamp())
    out = {"bot": "daily_trend_spot", "exchange": "Binance", "mode": "TESTNET", "base_url": TESTNET,
           "keys": c.has_keys(), "enabled": state["enabled"], "settings": s,
           "last_scan": state.get("last_scan"), "last_error": state.get("last_error"),
           "rules": {"entry_days": DT.N_ENTRY, "exit_days": DT.N_EXIT, "stop_atr": DT.STOP_ATR, "atr_days": 14,
                     "side": "long only (spot)", "candles": "Binance real-market daily (UTC)"}}
    ls = state.get("last_scan")
    out["last_scan_age_min"] = round((now - datetime.fromisoformat(ls)).total_seconds() / 60, 1) if ls else None
    prices, symbols, err = {}, {}, None
    for sym, pair in PAIRS.items():
        info = {"enabled": sym in s["symbols"], "last_check": state["last_check"].get(sym)}
        try:
            prices[sym] = c.price(pair)
            bars = _bars(pair, now_ts)
            i = len(bars) - 1
            atr = float(atr_series(bars)[i])
            info.update(price=prices[sym], atr=round(atr, 2),
                        entry_level=float(bars["high"][i - DT.N_ENTRY + 1:i + 1].max()),
                        exit_level=float(bars["low"][i - DT.N_EXIT + 1:i + 1].min()),
                        stop_pct=round(100 * DT.STOP_ATR * atr / prices[sym], 2))
        except Exception as e:
            err = str(e)
        symbols[sym] = info
    eq = _equity(prices)
    for sym, info in symbols.items():                 # taille qu'aurait le prochain achat
        if "atr" in info and eq > 0:
            stop_dist = DT.STOP_ATR * info["atr"]
            qty = (float(s["risk_pct"]) / 100) * eq / (stop_dist + 2 * FEE_RATE * info["price"])
            info["next_order_usd"] = round(min(qty * info["price"], max(_free_cash(), 0.0)), 2)
            info["next_risk_usd"] = round(eq * float(s["risk_pct"]) / 100, 2)
    usdt = None
    if c.has_keys():
        try:
            usdt = c.balances().get("USDT", {}).get("free")
        except Exception as e:
            err = str(e)
    out.update(prices=prices, symbols=symbols, connection_error=err, usdt_free=usdt,
               equity=round(eq, 2), capital=s["capital_usd"], realized_usd=round(state["realized_usd"], 2),
               free_cash=round(_free_cash(), 2), dust={k: v for k, v in state["dust"].items() if v},
               open_risk_usd=round(sum(p["risk_usd"] for p in state["positions"].values()), 2),
               positions=[{"symbol": sym, "qty": p["qty"], "entry": round(p["entry"], 2), "stop": p["stop"],
                           "cost_usd": round(p["cost_usd"], 2),
                           "value_usd": round(p["qty"] * prices[sym], 2) if sym in prices else None,
                           "pnl_usd": round(p["qty"] * prices[sym] - p["cost_usd"], 2) if sym in prices else None,
                           "since": datetime.fromtimestamp(p["t_in"], UTC).isoformat()}
                          for sym, p in state["positions"].items()])
    return out


def spot_history(limit: int = 30) -> dict:
    _ensure_ready()
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT timestamp, symbol, event, bar_time, price, sl, qty, cost_usd, risk_usd, risk_pct, outcome, profit, "
        "closed_at, reason FROM dt_spot_log WHERE event != 'check' ORDER BY id DESC LIMIT ?", (int(limit),))]
    conn.close()
    return {"bot": "daily_trend_spot", "events": rows}
