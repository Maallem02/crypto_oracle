"""Le bot Volume Profile — un SECOND BOT sur le même compte MT5.

Demande utilisateur du 2026-09-24 : "pense-le comme un autre bot utilisant la
stratégie volume profile, qui travaille séparément de la stratégie actuelle,
sur le même compte MT5". Il n'a donc AUCUN lien avec le bot SMC :

  - son propre minuteur (job 'vp_scan' dans main.py, chaque minute) — il ne
    passe pas par la boucle de scan SMC ;
  - son propre marche/arrêt, persisté dans vp_state.json : le bouton
    Start/Stop de l'onglet Scalping pilote le bot SMC SEULEMENT. Endpoints :
        GET  /trading/vp/status   POST /trading/vp/start   POST /trading/vp/stop
  - ses propres réglages — symboles, lots, risque max par trade, perte
    journalière, setups actifs — modifiables dans l'onglet "Vol. Profile" de
    l'app (POST /trading/vp/settings), sans lien avec ceux du bot Scalping ;
  - ses propres positions : jusqu'à max_per_symbol positions VP par symbole
    (réglage de l'app, 1 à 5 ; même sens autorisé ou non via same_direction).
    Il ne voit ni les positions SMC ni les manuelles, et le bot SMC ne voit
    pas les siennes (voir executor.place_trade) ;
  - ses propres heures : aucune zone morte ni blocage du week-end (ce sont des
    mesures faites sur les trades SMC) — il trade dès que le marché cote ;
  - son propre journal : table vp_log, jamais scalp_log (qui nourrit les
    pauses de série, le breaker directionnel et le ML du bot SMC) ;
  - ses propres sorties : SL fixe + TP 2R comme dans la vidéo. Le trail et le
    break-even du bot SMC ne touchent que le magic 234000.

Identification : magic VP_MAGIC (234100), commentaire "VP-POC buy",
"VP-REV sell", "VP-BRK buy"...

PROTECTIONS PROPRES : risque max par trade (risk_cap_pct), une perte
journalière optionnelle (daily_loss_pct, désactivée par défaut) qui ne compte
QUE les trades VP, et depuis le 2026-09-30 une pause autour des annonces
économiques (news_pause, activée par défaut — mêmes fenêtres que le bot SMC). Pas de halt de drawdown : celui du bot SMC ne compte que
le bot SMC et n'arrête que lui.

Seules règles d'EXÉCUTION communes, dans place_trade, qui ne lient pas les deux
bots : rejet d'un signal périmé (prix parti > 3x le risque), distance minimale
des stops exigée par le broker, et pour les métaux la fenêtre de rollover
(23:45-00:30) et l'ouverture du lundi avant 02:00, où le spread explose.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from core.database import get_db
from features.news.calendar import is_news_blocked, upcoming_high_event
from features.trading.executor import SYMBOL_MAP, close_partial, place_trade
from features.trading.mt5_client import ensure_mt5
from features.trading.risk_manager import VP_MAGIC
from features.volume_profile.profile import compute_profile
from features.volume_profile.strategy import (MAX_RISK, MIN_RISK, RR, atr_series, detect,
                                              detect_va_reversal)

# ── réglages du bot Volume Profile — modifiables depuis l'app ────────────────
# Valeurs par défaut au tout premier démarrage uniquement. Ensuite tout vient de
# vp_state.json, écrit par POST /trading/vp/settings (onglet VP de l'app), et
# s'applique au scan suivant (< 1 min), sans redémarrage ni arrêt du bot.
SETUPS = ("poc_bounce", "va_reversal", "va_breakout")
DEFAULT_SETTINGS = {
    "symbols": ["BTC", "ETH", "XAGUSD"],                       # ceux du replay
    "lot_sizes": {"BTC": 0.02, "ETH": 0.50, "XAGUSD": 0.01},   # copiés de l'app le 2026-09-24
    "risk_cap_pct": 15.0,     # un trade VP ne peut risquer plus de X % du solde
    "daily_loss_pct": 0.0,    # 0 = désactivé ; sinon plus de nouveau trade VP
                              # jusqu'à minuit UTC une fois la perte VP du jour atteinte
    "setups": {s: True for s in SETUPS},
    # Positions VP simultanées par symbole. Demande du 2026-09-25 ("plus d'un
    # trade sur le même symbole"). Replay 60 j, re-entry + breakout, 2R fixe :
    # 1 = +1.0R, 1 achat + 1 vente = -6.4R, max 2 = -15.8R, max 3 = -26.9R -
    # chaque place en plus a coûté. Défaut 2 sans même sens = 1 achat + 1
    # vente, la version la moins coûteuse (le cas de l'achat VAL du 25/09
    # pendant qu'une vente était ouverte). Modifiable dans l'app.
    "max_per_symbol": 2,
    "same_direction": False,  # True = plusieurs positions VP du même sens
    # Pause autour des annonces économiques (2026-09-30, demande utilisateur
    # après un BTC stoppé 5 min après Core PCE + PIB US). Même calendrier et
    # mêmes fenêtres que le bot Scalping (features/news/calendar) : impact fort
    # 15 min avant -> 18 min après (10 avant le 02/10), moyen 10 -> 5, pour les devises qui font
    # bouger le symbole. Un signal pendant la pause est ABANDONNÉ (pas différé :
    # entrer après l'annonce sur un signal d'avant n'a plus de sens). Les
    # positions ouvertes gardent leur stop et leur objectif.
    "news_pause": True,
    # Filtre "zone de valeur trop étroite" (2026-10-01, demande utilisateur
    # après l'étude gagnants/perdants). Pour une RÉINTÉGRATION : distance entre
    # l'entrée et le bord OPPOSÉ de la zone de valeur, en R (risque du trade).
    # Sous min_room_r, le trade est ignoré : la zone est étroite par rapport au
    # stop, l'objectif 2R tombe loin hors de la "juste valeur".
    # Rejeu 201 trades (9 semaines, 5 symboles, 1/symbole, 2R) :
    #     actuel            +56.1R  DD 11.2R   moitiés +41.2 / +14.9
    #     bord opposé >= 1R +61.2R  DD  6.9R   moitiés +36.4 / +24.8
    # Le groupe "< 1R" était à ~0 dans les deux moitiés (+2.0 / -0.2R, n=43).
    # Preuve modérée (meilleur sur la 2e moitié, un peu moins sur la 1re) :
    # test en réel. 0 = désactivé. Ne touche pas les cassures (pas de bord opposé).
    "min_room_r": 1.0,
    # Fermeture des positions VP AVANT une annonce à impact fort (2026-10-02,
    # demande utilisateur). NFP du 02/10 : la pause news bloquait bien les
    # NOUVEAUX trades, mais 2 positions ouvertes avant la fenêtre (argent 11:15,
    # USDJPY 12:30) ont été stoppées par le pic de 13:30 : -35 $, dont 6.88 $
    # de glissement au-delà du stop. X minutes avant l'annonce (pour une devise
    # du symbole), la position est fermée au marché. 0 = désactivé.
    "news_close_before_min": 15,
    # Filtre spread (2026-10-08, demande utilisateur après la ré-étude des
    # filtres). Spread (ask - bid) au moment de l'ordre, en % de la distance
    # au stop au prix live. Au-delà de max_spread_pct, le trade est ignoré : le
    # spread mange une trop grosse part du risque. Sur ce compte le spread est
    # quasi fixe (BTC ~10, ETH ~1, XAU ~0.24, USDJPY ~0.01 hors rollover), donc
    # le filtre écarte surtout les stops serrés d'ETH et d'USDJPY.
    # Rejeu 442 réintégrations 01/08 -> 08/10 (4 symboles live, room >= 1R,
    # 1/symbole, 2R) : actuel +33R DD 14R ; < 10 % +36R DD 9R ; moitiés
    # +23 / +13R contre +24 / +9R. Les signaux écartés : -5R, négatifs dans
    # les deux moitiés. Stable de 6 % à 20 %. 0 = désactivé.
    "max_spread_pct": 10.0,
    # Premier signal par bord (2026-10-08, même étude). Une réintégration
    # n'est prise que si c'est la PREMIÈRE du jour UTC sur ce bord (achat à la
    # VAL, vente à la VAH) ; les suivantes sont ignorées, prises ou non. Ne
    # rapporte pas plus (+33R, comme sans) mais réduit l'exposition : DD 14R ->
    # 8R, série perdante 11 -> 8. Avec le filtre spread : +38R, DD 5R, série 5
    # (2e moitié seule : +8R DD 5R contre +9R DD 11R). Ne touche pas les autres setups.
    "first_per_edge": True,
}
NEWS_CLOSE_RANGE = (0, 60)
MIN_ROOM_RANGE = (0.0, 5.0)
MAX_SPREAD_RANGE = (0.0, 50.0)
RISK_CAP_RANGE = (1.0, 50.0)
DAILY_LOSS_RANGE = (0.0, 50.0)
MAX_PER_SYMBOL_RANGE = (1, 5)

SIGNAL_TF = mt5.TIMEFRAME_M15         # bougies des signaux (vidéo : intraday)
SIGNAL_TF_SEC = 15 * 60
PROFILE_TF = mt5.TIMEFRAME_M1         # bougies du profil de la session précédente
SETUP_CODE = {"poc_bounce": "POC", "va_reversal": "REV", "va_breakout": "BRK"}

UTC = timezone.utc
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vp_state.json")
_profile_cache: dict = {}             # (symbole mt5, jour) -> (jour précédent, Profile)
_handled: set = set()                 # signaux déjà traités (dédoublonnage)
_levels_logged: set = set()
_ready = False


class SettingsError(ValueError):
    """Réglage refusé — le message est affiché tel quel dans l'app."""


def _copy(obj):
    return json.loads(json.dumps(obj))


# ── état persistant : marche/arrêt + réglages, indépendants du bot SMC ───────
def _load_state() -> tuple:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    settings = _copy(DEFAULT_SETTINGS)
    saved = d.get("settings") or {}
    for k in ("symbols", "lot_sizes", "risk_cap_pct", "daily_loss_pct",
              "max_per_symbol", "same_direction", "news_pause", "min_room_r",
              "news_close_before_min", "max_spread_pct", "first_per_edge"):
        if k in saved:
            settings[k] = saved[k]
    settings["setups"] = {**DEFAULT_SETTINGS["setups"], **(saved.get("setups") or {})}
    return bool(d.get("enabled", True)), settings    # 1er démarrage : actif


_enabled, _settings = _load_state()
vp_state = {"enabled": _enabled, "settings": _settings,
            "last_scan": None, "last_error": None, "paused": None}


def _persist() -> None:
    try:
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"enabled": vp_state["enabled"], "settings": vp_state["settings"],
                       "changed_at": datetime.now(UTC).isoformat()}, fh, indent=2)
        os.replace(tmp, _STATE_FILE)                  # écriture atomique
    except OSError as e:
        print(f"[VP] could not persist state: {e}")


def set_enabled(flag: bool) -> dict:
    vp_state["enabled"] = bool(flag)
    _persist()
    print(f"[VP] bot {'STARTED' if flag else 'STOPPED'}")
    return vp_status()


def available_symbols() -> dict:
    """Symboles que le bot VP peut trader : connus du code ET cotés par le
    broker du compte connecté, avec les contraintes de lot du broker."""
    ensure_mt5()
    out = {}
    for sym, mt5sym in SYMBOL_MAP.items():
        si = mt5.symbol_info(mt5sym)
        if si is not None:
            out[sym] = {"min_lot": si.volume_min, "lot_step": si.volume_step,
                        "max_lot": si.volume_max}
    return out


def update_settings(patch: dict) -> dict:
    """Valide puis enregistre un changement de réglages venu de l'app.
    Tout ou rien : à la moindre erreur rien n'est modifié."""
    if not isinstance(patch, dict):
        raise SettingsError("settings must be an object")
    avail = available_symbols()
    new = _copy(vp_state["settings"])
    errors = []

    if "symbols" in patch:
        syms = patch["symbols"]
        if not isinstance(syms, list):
            errors.append("symbols must be a list")
        else:
            syms = list(dict.fromkeys(str(x).upper() for x in syms))
            bad = [x for x in syms if x not in avail]
            if bad:
                errors.append(f"not available on this broker: {', '.join(bad)}")
            new["symbols"] = syms

    if "lot_sizes" in patch:
        lots = patch["lot_sizes"]
        if not isinstance(lots, dict):
            errors.append("lot_sizes must be an object")
        else:
            for k, v in lots.items():
                ku = str(k).upper()
                info = avail.get(ku)
                if info is None:
                    errors.append(f"lot size for a symbol this broker does not offer: {ku}")
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    errors.append(f"{ku}: lot size must be a number")
                    continue
                step = info["lot_step"] or 0.01
                v = round(round(v / step) * step, 6)          # aligné sur le pas du broker
                if v < info["min_lot"] - 1e-9:
                    errors.append(f"{ku}: {v} is below the broker minimum of {info['min_lot']}")
                elif v > info["max_lot"]:
                    errors.append(f"{ku}: {v} is above the broker maximum of {info['max_lot']}")
                else:
                    new["lot_sizes"][ku] = v

    for key, (lo, hi) in (("risk_cap_pct", RISK_CAP_RANGE),
                          ("daily_loss_pct", DAILY_LOSS_RANGE)):
        if key in patch:
            try:
                v = float(patch[key])
            except (TypeError, ValueError):
                errors.append(f"{key} must be a number")
                continue
            if not lo <= v <= hi:
                errors.append(f"{key} must be between {lo:g} and {hi:g}")
            else:
                new[key] = round(v, 1)

    if "max_per_symbol" in patch:
        v = patch["max_per_symbol"]
        lo, hi = MAX_PER_SYMBOL_RANGE
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
            errors.append("max_per_symbol must be a whole number")
        elif not lo <= int(v) <= hi:
            errors.append(f"max_per_symbol must be between {lo} and {hi}")
        else:
            new["max_per_symbol"] = int(v)

    if "same_direction" in patch:
        if not isinstance(patch["same_direction"], bool):
            errors.append("same_direction must be true or false")
        else:
            new["same_direction"] = patch["same_direction"]

    if "news_pause" in patch:
        if not isinstance(patch["news_pause"], bool):
            errors.append("news_pause must be true or false")
        else:
            new["news_pause"] = patch["news_pause"]

    if "min_room_r" in patch:
        v = patch["min_room_r"]
        lo, hi = MIN_ROOM_RANGE
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append("min_room_r must be a number (0 = off)")
        elif not lo <= float(v) <= hi:
            errors.append(f"min_room_r must be between {lo:g} and {hi:g}")
        else:
            new["min_room_r"] = round(float(v), 2)

    if "max_spread_pct" in patch:
        v = patch["max_spread_pct"]
        lo, hi = MAX_SPREAD_RANGE
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append("max_spread_pct must be a number (0 = off)")
        elif not lo <= float(v) <= hi:
            errors.append(f"max_spread_pct must be between {lo:g} and {hi:g}")
        else:
            new["max_spread_pct"] = round(float(v), 1)

    if "first_per_edge" in patch:
        if not isinstance(patch["first_per_edge"], bool):
            errors.append("first_per_edge must be true or false")
        else:
            new["first_per_edge"] = patch["first_per_edge"]

    if "news_close_before_min" in patch:
        v = patch["news_close_before_min"]
        lo, hi = NEWS_CLOSE_RANGE
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
            errors.append("news_close_before_min must be a whole number of minutes (0 = off)")
        elif not lo <= int(v) <= hi:
            errors.append(f"news_close_before_min must be between {lo} and {hi}")
        else:
            new["news_close_before_min"] = int(v)

    if "setups" in patch:
        st = patch["setups"]
        if not isinstance(st, dict) or any(k not in SETUPS for k in st):
            errors.append(f"setups must only contain {', '.join(SETUPS)}")
        else:
            new["setups"].update({k: bool(v) for k, v in st.items()})

    missing = [s for s in new["symbols"] if not new["lot_sizes"].get(s)]
    if missing:
        errors.append(f"set a lot size for: {', '.join(missing)}")
    if errors:
        raise SettingsError("; ".join(errors))

    vp_state["settings"] = new
    _persist()
    print(f"[VP] settings saved: symbols={new['symbols']} lots={new['lot_sizes']} "
          f"risk cap {new['risk_cap_pct']}% daily loss {new['daily_loss_pct']}% "
          f"max {new['max_per_symbol']}/symbol same-direction={new['same_direction']} "
          f"news pause={new.get('news_pause', True)} min room={new.get('min_room_r', 1.0)}R "
          f"news close={new.get('news_close_before_min', 15)}min "
          f"max spread={new.get('max_spread_pct', 10.0)}% "
          f"first per edge={new.get('first_per_edge', True)} "
          f"setups={new['setups']}")
    return vp_status()


def vp_status() -> dict:
    ls = vp_state.get("last_scan")
    age = ((datetime.now(UTC) - datetime.fromisoformat(ls)).total_seconds() / 60
           if ls else None)
    s = vp_state["settings"]
    open_pos = [p for p in (mt5.positions_get() or []) if p.magic == VP_MAGIC]
    return {"bot": "volume_profile", "magic": VP_MAGIC,
            "enabled": vp_state["enabled"], "last_scan": ls,
            "last_scan_age_min": round(age, 1) if age is not None else None,
            "last_error": vp_state.get("last_error"),
            "paused": vp_state.get("paused"),
            "settings": s,
            "available_symbols": available_symbols(),
            # champs à plat conservés pour l'app déjà installée
            "symbols": s["symbols"],
            "lot_sizes": {k: s["lot_sizes"].get(k) for k in s["symbols"]},
            "risk_cap_pct": s["risk_cap_pct"],
            "open_positions": [{"symbol": p.symbol, "side": "buy" if p.type == 0 else "sell",
                                "volume": p.volume, "profit": round(p.profit, 2),
                                "comment": p.comment} for p in open_pos]}


def vp_job() -> None:
    """Point d'entrée du minuteur 'vp_scan' (main.py). Une erreur ici ne peut
    rien casser d'autre : c'est un job séparé du scan SMC."""
    if not vp_state["enabled"]:
        return
    try:
        vp_scan()
        vp_state["last_error"] = None
    except Exception as e:
        vp_state["last_error"] = f"{datetime.now(UTC):%H:%M:%S} {e}"
        print(f"[VP] scan error: {e}")
    vp_state["last_scan"] = datetime.now(UTC).isoformat()


# ── journal ──────────────────────────────────────────────────────────────────
def _ensure_ready():
    """Crée vp_log et recharge les signaux déjà traités (survit aux redémarrages)."""
    global _ready
    if _ready:
        return
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vp_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            setup       TEXT,        -- levels | poc_bounce | va_reversal | va_breakout
            direction   TEXT,
            status      TEXT,        -- levels | taken | rejected
            reason      TEXT,
            trigger_bar TEXT,
            entry REAL, sl REAL, tp REAL,
            poc REAL, vah REAL, val REAL,
            prev_inside INTEGER,
            ticket      INTEGER,
            outcome     INTEGER,     -- 1 gain, 0 perte, NULL encore ouvert
            profit      REAL,
            closed_at   TEXT,
            data        TEXT
        )""")
    conn.commit()
    since = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    for r in conn.execute("SELECT symbol, setup, direction, trigger_bar, status FROM vp_log "
                          "WHERE timestamp >= ?", (since,)):
        if r["status"] == "levels":
            _levels_logged.add(f"{r['symbol']}|{r['trigger_bar']}")
        elif r["trigger_bar"]:
            _handled.add(f"{r['symbol']}|{r['setup']}|{r['direction']}|{r['trigger_bar']}")
    conn.close()
    _ready = True


def _log(symbol, status, *, setup=None, direction=None, reason=None, sig=None,
         prof=None, prev_day=None, ticket=None, fill=None):
    entry = sl = tp = None
    data = {}
    if sig is not None:
        entry, sl, tp = sig.entry, sig.sl, sig.tp
        data = {"reason": sig.reason, **sig.detail}
    if fill:                                   # prix réellement obtenus
        entry, sl, tp = fill.get("price", entry), fill.get("sl", sl), fill.get("tp", tp)
        data["volume"] = fill.get("volume")
    if prev_day is not None:
        data["profile_session"] = str(prev_day)
    trig = (datetime.fromtimestamp(sig.trigger_time, UTC).isoformat()
            if sig is not None else (str(prev_day) if status == "levels" else None))
    conn = get_db()
    conn.execute(
        "INSERT INTO vp_log (timestamp, symbol, setup, direction, status, reason, trigger_bar, "
        "entry, sl, tp, poc, vah, val, prev_inside, ticket, data) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(UTC).isoformat(), symbol,
         setup or (sig.setup if sig else None), direction or (sig.direction if sig else None),
         status, reason, trig, entry, sl, tp,
         prof.poc if prof else None, prof.vah if prof else None, prof.val if prof else None,
         (1 if prof.closed_inside else 0) if prof else None,
         ticket, json.dumps(data, default=str)))
    conn.commit()
    conn.close()


# ── données ──────────────────────────────────────────────────────────────────
def previous_session_profile(mt5sym: str, today):
    """Profil de la dernière journée UTC terminée qui a des cotations
    (lundi : vendredi pour les métaux, dimanche pour le crypto).
    Heure serveur Exness = UTC exactement (vérifié le 2026-09-24)."""
    key = (mt5sym, today)
    if key in _profile_cache:
        return _profile_cache[key]
    for back in range(1, 6):
        d = today - timedelta(days=back)
        start = datetime(d.year, d.month, d.day, tzinfo=UTC)
        bars = mt5.copy_rates_range(mt5sym, PROFILE_TF, start,
                                    start + timedelta(days=1) - timedelta(seconds=1))
        if bars is not None and len(bars) >= 60:
            prof = compute_profile(bars)
            if prof is not None:
                _profile_cache[key] = (d, prof)
                return d, prof
    return None, None                          # pas mis en cache : on réessaiera


def session_bars(mt5sym: str, now_utc: datetime):
    """Bougies M15 TERMINÉES de la session courante + ATR aligné (ATR calculé
    sur 4 jours pour avoir assez d'historique, y compris un lundi)."""
    day_start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=UTC)
    bars = mt5.copy_rates_range(mt5sym, SIGNAL_TF, day_start - timedelta(days=4), now_utc)
    if bars is None or len(bars) == 0:
        return None, None
    bars = bars[bars["time"] + SIGNAL_TF_SEC <= now_utc.timestamp()]   # bougie en cours exclue
    atr = atr_series(bars)
    today = bars["time"] >= day_start.timestamp()
    return bars[today], atr[today]


# ── résultats ────────────────────────────────────────────────────────────────
def update_outcomes():
    """Renseigne gain/perte des trades VP clôturés, depuis l'historique MT5."""
    conn = get_db()
    rows = list(conn.execute("SELECT id, ticket FROM vp_log WHERE status='taken' "
                             "AND outcome IS NULL AND ticket IS NOT NULL"))
    for r in rows:
        if mt5.positions_get(ticket=r["ticket"]):
            continue                                        # encore ouvert
        deals = mt5.history_deals_get(position=r["ticket"]) or []
        outs = [d for d in deals if d.entry == 1]
        if not outs:
            continue
        profit = sum(d.profit + d.commission + d.swap for d in deals)
        last = max(outs, key=lambda d: d.time)
        exit_kind = ("tp" if "[tp" in (last.comment or "").lower() else
                     "sl" if "[sl" in (last.comment or "").lower() else
                     "news" if "news" in (last.comment or "").lower() else "other")
        conn.execute("UPDATE vp_log SET outcome=?, profit=?, closed_at=?, "
                     "data=json_set(COALESCE(data,'{}'), '$.exit', ?) WHERE id=?",
                     (1 if profit > 0 else 0, round(profit, 2),
                      datetime.fromtimestamp(last.time, UTC).isoformat(), exit_kind, r["id"]))
    conn.commit()
    conn.close()


# ── scan ─────────────────────────────────────────────────────────────────────
def live_risk_problem(sig, price: float) -> str | None:
    """Le stop VP est STRUCTUREL (il ne bouge pas avec le prix) : le risque
    réel dépend donc du prix au moment de l'ordre, pas de la clôture de la
    bougie. Mêmes bornes que la stratégie (MIN_RISK..MAX_RISK x ATR 15 min)."""
    atr = float(sig.detail.get("atr") or 0)
    risk = (price - sig.sl) if sig.direction == "buy" else (sig.sl - price)
    if risk <= 0:
        return "price already beyond the structural stop"
    if atr > 0 and risk < MIN_RISK * atr:
        return (f"price ran toward the stop before entry: only {risk / atr:.2f} ATR "
                f"left (< {MIN_RISK}) - the spread would eat it")
    if atr > 0 and risk > MAX_RISK * atr:
        return (f"price ran away from the stop before entry: {risk / atr:.2f} ATR "
                f"(> {MAX_RISK}) - entry no longer at the level")
    return None


def close_before_news(minutes: int) -> list:
    """Ferme au marché chaque position VP dont le symbole a une annonce à
    impact fort dans les `minutes` prochaines minutes. -> tickets fermés."""
    rev = {v: k for k, v in SYMBOL_MAP.items()}
    closed = []
    for p in mt5.positions_get() or []:
        if p.magic != VP_MAGIC:
            continue
        sym = rev.get(p.symbol, p.symbol)
        try:
            ev = upcoming_high_event(sym, minutes)
        except Exception as e:                         # calendrier indisponible : on ne ferme pas
            print(f"[VP-NEWS] calendar unavailable ({e}) — {p.symbol} left open")
            continue
        if not ev:
            continue
        res = close_partial(p, p.volume, "VP news close")
        if res["success"]:
            closed.append(p.ticket)
            print(f"[VP-NEWS] {p.symbol} #{p.ticket} closed {ev['minutes']:.0f} min before "
                  f"{ev['title']} ({ev['currency']} High) — P/L {p.profit:+.2f}")
        else:
            print(f"[VP-NEWS-ERR] {p.symbol} #{p.ticket}: {res['reason']}")
    return closed


def room_to_far_edge(sig, prof) -> float:
    """Réintégration : distance entrée -> bord OPPOSÉ de la zone de valeur,
    en R. Achat (retour par la VAL) : VAH - entrée ; vente : entrée - VAL.
    Mêmes valeurs que le rejeu (entrée = clôture de la bougie, risque du signal)."""
    if sig.risk <= 0:
        return 0.0
    far = (prof.vah - sig.entry) if sig.direction == "buy" else (sig.entry - prof.val)
    return far / sig.risk


def spread_share_pct(sig, tick) -> float | None:
    """Spread (ask - bid) en % de la distance au stop au prix live (achat :
    ask - SL ; vente : SL - bid). Même mesure que le rejeu (entrée au prix
    payé, spread compris). None si le prix a déjà dépassé le stop."""
    live = tick.ask if sig.direction == "buy" else tick.bid
    risk = (live - sig.sl) if sig.direction == "buy" else (sig.sl - live)
    if risk <= 0:
        return None
    return 100.0 * (tick.ask - tick.bid) / risk


def earlier_on_edge(bars, atr, prof, sig) -> int:
    """Nombre de réintégrations du MÊME bord (même sens) déjà données par le
    détecteur dans la session courante, avant la bougie de `sig`. Recalculé
    sur les bougies du jour plutôt que lu dans vp_log : même compte que le
    rejeu (signaux non bloqués, qu'ils aient été pris ou non) et insensible
    aux redémarrages."""
    n = 0
    for k in range(2, len(bars) + 1):
        if int(bars["time"][k - 1]) >= sig.trigger_time:
            break
        s = detect_va_reversal(bars[:k], atr[:k], prof)
        if s is not None and not s.blocked and s.direction == sig.direction:
            n += 1
    return n


def vp_pnl_today(now_utc: datetime) -> float:
    """Résultat réalisé du bot VP SEUL depuis minuit UTC (gains, pertes,
    commissions et swaps de ses deals — magic 234100 uniquement)."""
    start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=UTC)
    deals = mt5.history_deals_get(start, now_utc + timedelta(hours=1)) or []
    return float(sum(d.profit + d.commission + d.swap for d in deals if d.magic == VP_MAGIC))


def vp_scan() -> None:
    ensure_mt5()
    _ensure_ready()
    update_outcomes()
    now_utc = datetime.now(UTC)
    today = now_utc.date()
    s = vp_state["settings"]              # relu à chaque scan : un réglage de l'app
                                          # s'applique au scan suivant

    # ── fermeture avant annonce forte (voir DEFAULT_SETTINGS) ────────────────
    # AVANT la perte journalière : une protection doit tourner même si le bot
    # est en pause pour la journée.
    close_before = int(s.get("news_close_before_min", 15) or 0)
    if close_before > 0:
        close_before_news(close_before)

    # ── perte journalière du bot VP (optionnelle, 0 = désactivée) ────────────
    # Ne compte que les trades VP. Une fois atteinte, plus de NOUVEAU trade VP
    # jusqu'à minuit UTC ; les positions ouvertes gardent leur SL/TP.
    dl = float(s.get("daily_loss_pct") or 0)
    if dl > 0:
        pnl = vp_pnl_today(now_utc)
        acct = mt5.account_info()
        ref = (acct.balance - pnl) if acct else 0.0          # solde en début de journée VP
        if ref > 0 and pnl <= -dl / 100.0 * ref:
            msg = (f"daily loss limit reached: VP lost ${-pnl:.2f} today "
                   f"({100 * -pnl / ref:.1f}% >= {dl:g}%) - no new VP trade until 00:00 UTC")
            if vp_state.get("paused") != msg:
                print(f"[VP] {msg}")
            vp_state["paused"] = msg
            return
    vp_state["paused"] = None

    enabled_setups = s.get("setups") or {}
    lots = s.get("lot_sizes") or {}
    cap = float(s.get("risk_cap_pct") or 15.0) / 100.0
    max_pos = int(s.get("max_per_symbol") or 1)
    same_dir = bool(s.get("same_direction"))

    for symbol in s.get("symbols") or []:
        su = symbol.upper()
        mt5sym = SYMBOL_MAP.get(su)
        if not mt5sym:
            continue

        prev_day, prof = previous_session_profile(mt5sym, today)
        if prof is None:
            continue
        lk = f"{symbol}|{prev_day}"
        if lk not in _levels_logged:
            _levels_logged.add(lk)
            _log(symbol, "levels", setup="levels", prof=prof, prev_day=prev_day,
                 reason=(f"POC {prof.poc:.5g} VAH {prof.vah:.5g} VAL {prof.val:.5g} — "
                         f"session closed {'INSIDE' if prof.closed_inside else 'OUTSIDE'} "
                         f"the value area"))
            print(f"[VP] {symbol} levels from {prev_day}: POC {prof.poc:.5g} "
                  f"VAH {prof.vah:.5g} VAL {prof.val:.5g} "
                  f"({'inside' if prof.closed_inside else 'outside'})")

        bars, atr = session_bars(mt5sym, now_utc)
        if bars is None or len(bars) < 2:
            continue
        sig = detect(bars, atr, prof, enabled=enabled_setups)
        if sig is None:
            continue
        key = (f"{symbol}|{sig.setup}|{sig.direction}|"
               f"{datetime.fromtimestamp(sig.trigger_time, UTC).isoformat()}")
        if key in _handled:
            continue
        _handled.add(key)

        tag = f"[VP-{SETUP_CODE[sig.setup]}] {symbol} {sig.direction}"
        if sig.blocked:
            print(f"{tag}: setup found but not traded — {sig.blocked}")
            _log(symbol, "rejected", sig=sig, prof=prof, prev_day=prev_day, reason=sig.blocked)
            continue
        # zone de valeur trop étroite (voir DEFAULT_SETTINGS["min_room_r"])
        min_room = float(s.get("min_room_r", 1.0) or 0)
        if min_room > 0 and sig.setup == "va_reversal":
            room = room_to_far_edge(sig, prof)
            if room < min_room:
                why = (f"value area too narrow: far edge {room:.2f}R away "
                       f"(< {min_room:g}R)")
                print(f"{tag}: not taken — {why}")
                _log(symbol, "rejected", sig=sig, prof=prof, prev_day=prev_day, reason=why)
                continue
        # premier signal par bord (voir DEFAULT_SETTINGS["first_per_edge"])
        if s.get("first_per_edge", True) and sig.setup == "va_reversal":
            before = earlier_on_edge(bars, atr, prof, sig)
            if before > 0:
                edge = "buy at VAL" if sig.direction == "buy" else "sell at VAH"
                why = (f"not the first re-entry on this edge today: #{before + 1} "
                       f"{edge} (first-per-edge filter)")
                print(f"{tag}: not taken — {why}")
                _log(symbol, "rejected", sig=sig, prof=prof, prev_day=prev_day, reason=why)
                continue
        # pause annonces économiques (voir DEFAULT_SETTINGS["news_pause"])
        if s.get("news_pause", True):
            try:
                nb = is_news_blocked(su)
            except Exception as e:                     # calendrier indisponible : pas de pause
                nb = {"blocked": False}
                print(f"{tag}: news calendar unavailable ({e}) — no pause applied")
            if nb.get("blocked"):
                why = f"news pause: {nb.get('reason')}"
                print(f"{tag}: not taken — {why}")
                _log(symbol, "rejected", sig=sig, prof=prof, prev_day=prev_day, reason=why)
                continue
        # le bot VP ne regarde QUE ses propres positions
        mine = [p for p in (mt5.positions_get(symbol=mt5sym) or []) if p.magic == VP_MAGIC]
        side = mt5.POSITION_TYPE_BUY if sig.direction == "buy" else mt5.POSITION_TYPE_SELL
        why = None
        if len(mine) >= max_pos:
            why = (f"the Volume Profile bot already has {len(mine)} position(s) "
                   f"on this symbol (max {max_pos})")
        elif not same_dir and any(p.type == side for p in mine):
            why = (f"the Volume Profile bot already has a {sig.direction} on this symbol "
                   f"(same-direction stacking is off)")
        if why:
            print(f"{tag}: {why}")
            _log(symbol, "rejected", sig=sig, prof=prof, prev_day=prev_day, reason=why)
            continue

        # stop structurel conservé : vérifier le risque au prix LIVE
        tick = mt5.symbol_info_tick(mt5sym)
        if tick is not None:
            live = tick.ask if sig.direction == "buy" else tick.bid
            why = live_risk_problem(sig, live)
            if why:
                print(f"{tag}: not taken — {why}")
                _log(symbol, "rejected", sig=sig, prof=prof, prev_day=prev_day, reason=why)
                continue
            # spread trop gros pour ce stop (voir DEFAULT_SETTINGS["max_spread_pct"])
            max_spread = float(s.get("max_spread_pct", 10.0) or 0)
            share = spread_share_pct(sig, tick) if max_spread > 0 else None
            if share is not None and share >= max_spread - 1e-9:    # limite incluse (arrondis)
                why = (f"spread too large for this stop: {tick.ask - tick.bid:.5g} = "
                       f"{share:.1f}% of the stop distance (>= {max_spread:g}%)")
                print(f"{tag}: not taken — {why}")
                _log(symbol, "rejected", sig=sig, prof=prof, prev_day=prev_day, reason=why)
                continue

        res = place_trade(
            symbol=symbol, action=sig.direction, entry=sig.entry, sl=sig.sl, tp1=sig.tp,
            confidence=0.5, fixed_lot=float(lots.get(su, 0) or 0),
            magic=VP_MAGIC, comment=f"VP-{SETUP_CODE[sig.setup]} {sig.direction}",
            risk_cap_pct=cap,
            keep_sl=True, rr=RR,           # SL au niveau de structure, TP = 2R du prix réel
            max_per_symbol=max_pos,
        )
        if res.get("success"):
            print(f"{tag}: TAKEN ticket {res.get('ticket')} @ {res.get('price')} "
                  f"SL {res.get('sl')} TP {res.get('tp')} — {sig.reason}")
            _log(symbol, "taken", sig=sig, prof=prof, prev_day=prev_day,
                 reason=sig.reason, ticket=res.get("ticket"), fill=res)
        else:
            print(f"{tag}: order refused — {res.get('reason')}")
            _log(symbol, "rejected", sig=sig, prof=prof, prev_day=prev_day,
                 reason=f"order refused: {res.get('reason')}")


# ── statistiques ─────────────────────────────────────────────────────────────
def vp_stats(days: int = 30) -> dict:
    """Résultats de la stratégie VP seule, par setup."""
    _ensure_ready()
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    conn = get_db()
    taken = [dict(r) for r in conn.execute(
        "SELECT * FROM vp_log WHERE status='taken' AND timestamp >= ?", (since,))]
    rejected = [dict(r) for r in conn.execute(
        "SELECT setup, reason FROM vp_log WHERE status='rejected' AND timestamp >= ?", (since,))]
    conn.close()

    def summary(rows):
        closed = [r for r in rows if r["outcome"] is not None]
        wins = [r for r in closed if r["outcome"] == 1]
        gp = sum(r["profit"] for r in wins)
        gl = abs(sum(r["profit"] for r in closed if r["outcome"] == 0))
        return {"trades": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
                "wins": len(wins),
                "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
                "net_profit": round(sum(r["profit"] or 0 for r in closed), 2),
                "profit_factor": round(gp / gl, 2) if gl else None}

    by_setup = {s: summary([r for r in taken if r["setup"] == s]) for s in SETUP_CODE}
    reasons: dict = {}
    for r in rejected:
        k = (r["reason"] or "").split(":")[0][:60]
        reasons[k] = reasons.get(k, 0) + 1
    return {"bot": "volume_profile", "magic": VP_MAGIC, "days": days,
            "all": summary(taken), "by_setup": by_setup, "rejected_reasons": reasons}


def vp_history(limit: int = 50) -> dict:
    """Derniers trades du bot VP, pour l'onglet Volume Profile de l'app."""
    _ensure_ready()
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT timestamp, symbol, setup, direction, reason, entry, sl, tp, ticket, "
        "outcome, profit, closed_at, data FROM vp_log WHERE status='taken' "
        "ORDER BY id DESC LIMIT ?", (int(limit),))]
    conn.close()
    for r in rows:
        try:
            r["exit"] = json.loads(r.pop("data") or "{}").get("exit")
        except ValueError:
            r["exit"] = None
        r["state"] = ("open" if r["outcome"] is None else
                      "win" if r["outcome"] == 1 else "loss")
        r["setup_code"] = SETUP_CODE.get(r["setup"], r["setup"])
    return {"bot": "volume_profile", "trades": rows}
