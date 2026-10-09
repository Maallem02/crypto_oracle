"""Les trois setups Volume Profile, traduits en règles vérifiables.

Source : vidéo https://youtu.be/Xa2fB097ajs (fournie le 2026-09-24).
Les niveaux (POC / VAH / VAL) viennent TOUJOURS de la session précédente ;
on les trade pendant la session courante (voir runner.py).

La vidéo décrit des setups visuels. Chaque mot vague y est remplacé par un
seuil explicite, en multiple d'ATR(14) de l'unité de temps des signaux (M15),
pour s'adapter à la volatilité de chaque symbole :

  1. REBOND POC (poc_bounce) — seulement si la session précédente s'est
     terminée HORS de la zone de valeur.
       Short : le prix remonte VERS le POC par en dessous, et la bougie de
               confirmation est soit
                 - une mèche de rejet : ouverte sous le POC, plus haut
                   >= POC (tolérance POC_TOL), clôture sous le POC, bougie
                   rouge, mèche haute >= WICK_MIN de l'amplitude ;
                 - un avalement baissier au POC.
       Long  : miroir, le prix redescend vers le POC par au-dessus.
       SL    : juste au-delà du POC / de l'extrême de la confirmation.

  2. RÉINTÉGRATION (va_reversal) — seulement si la session précédente s'est
     terminée DANS la zone de valeur.
       Long  : une bougie a CLÔTURÉ sous la VAL, puis la bougie suivante
               CLÔTURE de nouveau dans la zone (une mèche ne suffit pas).
       Short : miroir au-dessus de la VAH.
       SL    : au-delà du plus bas (plus haut) de l'excursion hors zone.
       Répétable dans la même session.

  3. CASSURE (va_breakout) — session précédente dans ou hors zone.
       1) cassure NETTE : clôture au-delà de VAH/VAL d'au moins BRK_SIG ATR ;
       2) repli qui revient près du niveau (à moins de PB_NEAR ATR) SANS
          clôturer à plus de PB_DEEP de la hauteur de la zone à l'intérieur
          (sinon le setup est invalidé) ;
       3) cassure de structure : clôture au-delà de l'extrême d'avant le repli.
       SL    : au-delà de l'extrême du repli.

Commun : entrée à la clôture de la bougie déclencheuse, objectif 2R, SL fixe.
Un stop plus serré que MIN_RISK ATR (le spread le mangerait) ou plus large
que MAX_RISK ATR est signalé comme bloqué plutôt que tradé.
"""
from dataclasses import dataclass, field

import numpy as np

RR = 2.0            # objectif fixe de la vidéo
SL_BUF = 0.10       # marge au-delà de la structure, en ATR
POC_TOL = 0.10      # tolérance de contact avec le POC, en ATR
WICK_MIN = 0.50     # mèche de rejet >= 50 % de l'amplitude de la bougie
BRK_SIG = 0.50      # cassure "nette" : clôture >= 0.5 ATR au-delà du niveau
PB_NEAR = 0.50      # le repli doit revenir à moins de 0.5 ATR du niveau
PB_DEEP = 0.25      # ... sans clôturer à plus de 25 % de la zone à l'intérieur
MIN_RISK = 0.25     # stop minimum, en ATR
MAX_RISK = 4.0      # stop maximum, en ATR


@dataclass
class Signal:
    setup: str                 # poc_bounce | va_reversal | va_breakout
    direction: str             # buy | sell
    entry: float
    sl: float
    tp: float
    trigger_time: int          # epoch de la bougie déclencheuse
    reason: str
    blocked: str | None = None # renseigné si le setup existe mais n'est pas tradable
    detail: dict = field(default_factory=dict)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)


def atr_series(bars, period: int = 14) -> np.ndarray:
    """ATR de Wilder, aligné sur `bars`."""
    h = np.asarray(bars["high"], dtype=float)
    l = np.asarray(bars["low"], dtype=float)
    c = np.asarray(bars["close"], dtype=float)
    n = len(c)
    if n == 0:
        return np.zeros(0)
    prev = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    out = np.empty(n)
    out[0] = tr[0]
    for i in range(1, n):
        if i < period:
            out[i] = tr[:i + 1].mean()
        else:
            out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _finalize(setup, direction, entry, sl, atr_now, t, reason, detail) -> Signal:
    risk = (entry - sl) if direction == "buy" else (sl - entry)
    tp = entry + RR * risk if direction == "buy" else entry - RR * risk
    blocked = None
    if risk <= 0:
        blocked = "stop on the wrong side of entry"
    elif risk < MIN_RISK * atr_now:
        blocked = f"stop too tight: {risk / atr_now:.3f} ATR < {MIN_RISK}"
    elif risk > MAX_RISK * atr_now:
        blocked = f"stop too wide: {risk / atr_now:.3f} ATR > {MAX_RISK}"
    return Signal(setup=setup, direction=direction, entry=float(entry), sl=float(sl),
                  tp=float(tp), trigger_time=int(t), reason=reason, blocked=blocked,
                  detail={**detail, "atr": float(atr_now),
                          "risk_atr": float(risk / atr_now) if atr_now else None})


def detect_poc_bounce(bars, atr, prof) -> Signal | None:
    if prof.closed_inside or len(bars) < 2:
        return None
    O, H, L, C = (np.asarray(bars[k], dtype=float) for k in ("open", "high", "low", "close"))
    n = len(C)
    o, h, l, c = O[-1], H[-1], L[-1], C[-1]
    po, ph, pl, pc = O[-2], H[-2], L[-2], C[-2]
    a = float(atr[-1])
    P, tol = prof.poc, POC_TOL * a
    rng = h - l
    if rng <= 0 or a <= 0:
        return None
    t = int(bars["time"][-1])

    # short : le POC fait résistance, le prix arrive par en dessous
    upper_wick = h - max(o, c)
    rej_s = (o < P and h >= P - tol and c < P and c < o and upper_wick >= WICK_MIN * rng)
    eng_s = (pc > po and c < o and o >= pc and c <= po and po < P
             and max(h, ph) >= P - tol and c < P)
    if rej_s or eng_s:
        top = max(h, ph) if eng_s else h
        return _finalize("poc_bounce", "sell", c, max(top, P) + SL_BUF * a, a, t,
                         "bearish engulfing at POC" if eng_s else "rejection wick at POC",
                         {"poc": P})

    # long : le POC fait support, le prix arrive par au-dessus
    lower_wick = min(o, c) - l
    rej_l = (o > P and l <= P + tol and c > P and c > o and lower_wick >= WICK_MIN * rng)
    eng_l = (pc < po and c > o and o <= pc and c >= po and po > P
             and min(l, pl) <= P + tol and c > P)
    if rej_l or eng_l:
        bot = min(l, pl) if eng_l else l
        return _finalize("poc_bounce", "buy", c, min(bot, P) - SL_BUF * a, a, t,
                         "bullish engulfing at POC" if eng_l else "rejection wick at POC",
                         {"poc": P})
    return None


def detect_va_reversal(bars, atr, prof) -> Signal | None:
    if not prof.closed_inside or len(bars) < 2:
        return None
    H = np.asarray(bars["high"], dtype=float)
    L = np.asarray(bars["low"], dtype=float)
    C = np.asarray(bars["close"], dtype=float)
    n = len(C)
    a = float(atr[-1])
    vah, val = prof.vah, prof.val
    t = int(bars["time"][-1])
    if a <= 0 or not (val <= C[-1] <= vah):
        return None                       # la bougie déclencheuse doit CLÔTURER dedans

    if C[-2] < val:                       # retour par le bas -> achat
        k = n - 2
        while k - 1 >= 0 and C[k - 1] < val:
            k -= 1
        swing = float(L[k:n].min())
        return _finalize("va_reversal", "buy", C[-1], swing - SL_BUF * a, a, t,
                         f"closed back inside after {n - 1 - k} bar(s) below VAL",
                         {"val": val, "vah": vah, "swing": swing})
    if C[-2] > vah:                       # retour par le haut -> vente
        k = n - 2
        while k - 1 >= 0 and C[k - 1] > vah:
            k -= 1
        swing = float(H[k:n].max())
        return _finalize("va_reversal", "sell", C[-1], swing + SL_BUF * a, a, t,
                         f"closed back inside after {n - 1 - k} bar(s) above VAH",
                         {"val": val, "vah": vah, "swing": swing})
    return None


def _breakout(bars, atr, prof, d) -> Signal | None:
    buy = d == "buy"
    H = np.asarray(bars["high"], dtype=float)
    L = np.asarray(bars["low"], dtype=float)
    C = np.asarray(bars["close"], dtype=float)
    T = bars["time"]
    n = len(C)
    level = prof.vah if buy else prof.val
    deep = (prof.vah - PB_DEEP * prof.height) if buy else (prof.val + PB_DEEP * prof.height)
    state, ext, pb = "wait", None, None
    for i in range(n):
        a = float(atr[i])
        c, h, l = C[i], H[i], L[i]
        if state == "wait":
            if (c > level + BRK_SIG * a) if buy else (c < level - BRK_SIG * a):
                state, ext = "broken", (h if buy else l)
            continue
        if state == "broken":
            ext = max(ext, h) if buy else min(ext, l)
            near = (l <= level + PB_NEAR * a) if buy else (h >= level - PB_NEAR * a)
            if near:
                if (c < deep) if buy else (c > deep):
                    state, ext, pb = "wait", None, None      # repli trop profond
                else:
                    state, pb = "pullback", (l if buy else h)
            continue
        # state == "pullback"
        if (c < deep) if buy else (c > deep):
            state, ext, pb = "wait", None, None              # repli trop profond
            continue
        pb = min(pb, l) if buy else max(pb, h)
        if (c > ext) if buy else (c < ext):                  # cassure de structure
            if i == n - 1:
                return _finalize("va_breakout", d, c,
                                 (pb - SL_BUF * a) if buy else (pb + SL_BUF * a),
                                 a, int(T[i]),
                                 f"break of structure beyond {ext:.5g} after a held pullback",
                                 {"level": level, "leg_extreme": float(ext),
                                  "pullback_extreme": float(pb)})
            # cassure déjà passée : la tendance continue, il faudra un NOUVEAU repli
            state, ext, pb = "broken", (h if buy else l), None
    return None


def detect_va_breakout(bars, atr, prof) -> Signal | None:
    if len(bars) < 3:
        return None
    return _breakout(bars, atr, prof, "buy") or _breakout(bars, atr, prof, "sell")


_DETECTORS = (("va_breakout", detect_va_breakout),
              ("va_reversal", detect_va_reversal),
              ("poc_bounce", detect_poc_bounce))


def detect(bars, atr, prof, enabled: dict | None = None) -> Signal | None:
    """Premier setup déclenché par la DERNIÈRE bougie terminée de `bars`.

    `enabled` : {nom_du_setup: bool} réglé depuis l'app. Un setup désactivé
    n'est même pas évalué, pour qu'il ne puisse pas masquer un setup actif.

    Les trois se recoupent rarement sur une même bougie : rebond POC et
    réintégration s'excluent (session précédente hors / dans la zone), et une
    cassure de structure clôture hors de la zone là où une réintégration
    clôture dedans.
    """
    for name, fn in _DETECTORS:
        if enabled is not None and not enabled.get(name, True):
            continue
        s = fn(bars, atr, prof)
        if s is not None:
            return s
    return None
