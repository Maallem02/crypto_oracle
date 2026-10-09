"""Profil de volume d'une session terminée : POC, VAH, VAL.

Réglages de la vidéo source : 60 lignes de prix, zone de valeur à 70 %.

Volume utilisé : tick_volume. Sur ces CFD (BTCUSDm, ETHUSDm, XAGUSDm chez
Exness) real_volume vaut toujours 0 — vérifié le 2026-09-24. Le tick volume
est l'approximation standard du volume sur le forex / CFD.

Répartition : chaque bougie (M1 par défaut) répartit son volume
UNIFORMÉMENT sur la plage [low, high] qu'elle a couverte, au prorata du
chevauchement avec chaque ligne. Sans données tick par prix, c'est la
méthode habituelle ; en M1 l'erreur est négligeable à l'échelle d'une ligne
(1/60 de la plage de la journée).
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class Profile:
    poc: float            # prix au volume le plus élevé (centre de la ligne)
    vah: float            # haut de la zone de valeur (70 % du volume)
    val: float            # bas de la zone de valeur
    low: float            # plus bas de la session
    high: float           # plus haut de la session
    last_close: float     # clôture de la dernière bougie de la session
    closed_inside: bool   # la session s'est-elle terminée DANS la zone de valeur ?
    bars: int
    total_volume: float

    @property
    def height(self) -> float:
        return self.vah - self.val


def compute_profile(bars, rows: int = 60, value_area: float = 0.70) -> Profile | None:
    """bars : tableau structuré MT5 (champs high, low, close, tick_volume)."""
    if bars is None or len(bars) < 2:
        return None
    lows = np.asarray(bars["low"], dtype=float)
    highs = np.asarray(bars["high"], dtype=float)
    vols = np.asarray(bars["tick_volume"], dtype=float)
    lo, hi = float(lows.min()), float(highs.max())
    if hi <= lo or vols.sum() <= 0:
        return None

    step = (hi - lo) / rows
    hist = np.zeros(rows)
    for l, h, v in zip(lows, highs, vols):
        if v <= 0:
            continue
        if h <= l:                                   # bougie sans amplitude
            hist[min(int((l - lo) / step), rows - 1)] += v
            continue
        i0 = min(int((l - lo) / step), rows - 1)
        i1 = min(int((h - lo) / step), rows - 1)
        for i in range(i0, i1 + 1):
            r_lo = lo + i * step
            overlap = min(h, r_lo + step) - max(l, r_lo)
            if overlap > 0:
                hist[i] += v * overlap / (h - l)

    poc_i = int(hist.argmax())

    # Zone de valeur, méthode CBOT : on part du POC et on ajoute à chaque pas
    # le côté (deux lignes au-dessus contre deux lignes en dessous) qui porte
    # le plus de volume, jusqu'à atteindre 70 % du total.
    target = hist.sum() * value_area
    up = dn = poc_i
    acc = hist[poc_i]
    while acc < target and (up < rows - 1 or dn > 0):
        above = hist[up + 1:up + 3].sum() if up < rows - 1 else -1.0
        below = hist[max(dn - 2, 0):dn].sum() if dn > 0 else -1.0
        if above >= below:
            n = min(2, rows - 1 - up)
            acc += hist[up + 1:up + 1 + n].sum()
            up += n
        else:
            n = min(2, dn)
            acc += hist[dn - n:dn].sum()
            dn -= n

    vah = lo + (up + 1) * step
    val = lo + dn * step
    poc = lo + (poc_i + 0.5) * step
    last_close = float(bars["close"][-1])
    return Profile(poc=poc, vah=vah, val=val, low=lo, high=hi,
                   last_close=last_close,
                   closed_inside=bool(val <= last_close <= vah),
                   bars=len(bars), total_volume=float(hist.sum()))
