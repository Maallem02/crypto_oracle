import pandas as pd
import numpy as np


def detect_liquidity_grab(df: pd.DataFrame, lookback: int = 10) -> dict:
    """
    Détecte un Liquidity Grab (Stop Hunt) parmi deux patterns :

    Pattern A — Wick Rejection (plus fréquent) :
      - UNE seule bougie passe sous/au-dessus d'un swing level
        ET ferme de l'autre côté → le rejet se passe EN UNE bougie
      - Inclut la bougie actuelle (LG en cours de formation)

    Pattern B — Sweep + Recovery (pattern classique) :
      - Une bougie sweep le niveau (low < swing_low)
      - Une bougie SUIVANTE ferme au-dessus (recovery)

    Les deux patterns sont renvoyés si détectés.
    Priority : Pattern A (plus frais) > Pattern B.

    Paramètres de qualité :
      - recovery_pct >= 0.015% : filtre les effleurements
      - Fenêtre max : 6 bougies pour rester "frais"

    Bullish ET bearish sont TOUJOURS calculés (plus de court-circuit). Le
    primaire reste bullish par défaut si les deux existent (comportement
    historique inchangé), mais le second est exposé via "alt_type"/"alt" —
    permet à l'engine de basculer vers la direction alignée avec la
    tendance réelle au lieu de rejeter purement un signal contre-tendance.
    """
    if len(df) < lookback + 5:
        return {"detected": False, "type": None}

    recent = df.tail(lookback + 5)

    # ── Swing lows / highs ────────────────────────────────────────────────────
    swing_lows  = []
    swing_highs = []

    for i in range(2, len(recent) - 2):
        row, prev, nxt = recent.iloc[i], recent.iloc[i - 1], recent.iloc[i + 1]
        if row['low']  < prev['low']  and row['low']  < nxt['low']:
            swing_lows.append(float(row['low']))
        if row['high'] > prev['high'] and row['high'] > nxt['high']:
            swing_highs.append(float(row['high']))

    if not swing_lows and not swing_highs:
        return {"detected": False, "type": None}

    current_price = float(df['close'].iloc[-1])
    # Fenêtre large — inclut la bougie actuelle
    window = df.iloc[-7:]   # 7 bougies (dont l'actuelle)

    # ──────────────────────────────────────────────────────────────────────────
    # BULLISH LG
    # ──────────────────────────────────────────────────────────────────────────
    best_bullish = None

    for candidate_low in sorted(swing_lows, key=lambda x: abs(x - current_price)):

        # ── Pattern A : Wick Rejection ────────────────────────────────────────
        #    bougie.low < candidate_low  ET  bougie.close > candidate_low
        wick_candles = window[
            (window['low']   < candidate_low) &
            (window['close'] > candidate_low)
        ]
        if not wick_candles.empty:
            wick_c       = wick_candles.iloc[-1]          # la plus récente
            sweep_low    = float(wick_c['low'])
            recovery_pct = (float(wick_c['close']) - candidate_low) / candidate_low * 100
            # La bougie actuelle doit toujours être au-dessus du niveau
            if current_price > candidate_low and recovery_pct >= 0.015:
                best_bullish = {
                    "pattern":       "wick_rejection",
                    "grabbed_level": round(candidate_low, 5),
                    "sweep_low":     round(sweep_low, 5),
                    "strength":      round(abs(sweep_low - candidate_low) / candidate_low * 100, 4),
                    "recovery_pct":  round((current_price - candidate_low) / candidate_low * 100, 4),
                }
                break   # meilleur candidat trouvé, arrêter

        # ── Pattern B : Sweep + Recovery ─────────────────────────────────────
        #    une bougie sweep (low < level) puis la bougie actuelle est au-dessus
        if best_bullish is None:
            swept = window.iloc[:-1][window.iloc[:-1]['low'] < candidate_low]  # exclut la bougie actuelle
            recovery_pct = (current_price - candidate_low) / candidate_low * 100
            if not swept.empty and current_price > candidate_low and recovery_pct >= 0.015:
                sweep_c   = swept.iloc[-1]
                sweep_low = float(sweep_c['low'])
                best_bullish = {
                    "pattern":       "sweep_recovery",
                    "grabbed_level": round(candidate_low, 5),
                    "sweep_low":     round(sweep_low, 5),
                    "strength":      round(abs(sweep_low - candidate_low) / candidate_low * 100, 4),
                    "recovery_pct":  round(recovery_pct, 4),
                }
                break

    # ──────────────────────────────────────────────────────────────────────────
    # BEARISH LG
    # ──────────────────────────────────────────────────────────────────────────
    best_bearish = None

    for candidate_high in sorted(swing_highs, key=lambda x: abs(x - current_price)):

        # ── Pattern A : Wick Rejection ────────────────────────────────────────
        wick_candles = window[
            (window['high']  > candidate_high) &
            (window['close'] < candidate_high)
        ]
        if not wick_candles.empty:
            wick_c        = wick_candles.iloc[-1]
            sweep_high    = float(wick_c['high'])
            recovery_pct  = (candidate_high - float(wick_c['close'])) / candidate_high * 100
            if current_price < candidate_high and recovery_pct >= 0.015:
                best_bearish = {
                    "pattern":       "wick_rejection",
                    "grabbed_level": round(candidate_high, 5),
                    "sweep_high":    round(sweep_high, 5),
                    "strength":      round(abs(sweep_high - candidate_high) / candidate_high * 100, 4),
                    "recovery_pct":  round((candidate_high - current_price) / candidate_high * 100, 4),
                }
                break

        # ── Pattern B : Sweep + Recovery ─────────────────────────────────────
        if best_bearish is None:
            swept        = window.iloc[:-1][window.iloc[:-1]['high'] > candidate_high]
            recovery_pct = (candidate_high - current_price) / candidate_high * 100
            if not swept.empty and current_price < candidate_high and recovery_pct >= 0.015:
                sweep_c    = swept.iloc[-1]
                sweep_high = float(sweep_c['high'])
                best_bearish = {
                    "pattern":       "sweep_recovery",
                    "grabbed_level": round(candidate_high, 5),
                    "sweep_high":    round(sweep_high, 5),
                    "strength":      round(abs(sweep_high - candidate_high) / candidate_high * 100, 4),
                    "recovery_pct":  round(recovery_pct, 4),
                }
                break

    # ── Combine: bullish is primary by default, bearish exposed as alt ────────
    if best_bullish and best_bearish:
        return {
            "detected": True,
            "type":     "bullish",
            **best_bullish,
            "description": (
                f"Bullish LG ({best_bullish['pattern']}): "
                f"swept {best_bullish['grabbed_level']} "
                f"→ +{best_bullish['recovery_pct']}% recovery"
            ),
            "alt_type": "bearish",
            "alt": {
                "detected": True,
                "type":     "bearish",
                **best_bearish,
                "description": (
                    f"Bearish LG ({best_bearish['pattern']}): "
                    f"swept {best_bearish['grabbed_level']} "
                    f"→ -{best_bearish['recovery_pct']}% recovery"
                ),
            },
        }

    if best_bullish:
        return {
            "detected": True,
            "type":     "bullish",
            **best_bullish,
            "description": (
                f"Bullish LG ({best_bullish['pattern']}): "
                f"swept {best_bullish['grabbed_level']} "
                f"→ +{best_bullish['recovery_pct']}% recovery"
            ),
            "alt_type": None,
            "alt": None,
        }

    if best_bearish:
        return {
            "detected": True,
            "type":     "bearish",
            **best_bearish,
            "description": (
                f"Bearish LG ({best_bearish['pattern']}): "
                f"swept {best_bearish['grabbed_level']} "
                f"→ -{best_bearish['recovery_pct']}% recovery"
            ),
            "alt_type": None,
            "alt": None,
        }

    return {"detected": False, "type": None, "alt_type": None, "alt": None,
            "description": "No liquidity grab detected"}


def get_scalping_entry(df: pd.DataFrame, grab: dict, structure_trend: str) -> dict | None:
    """
    Entrée marché immédiate après un LG confirmé.
    SL = derrière la mèche de sweep.
    TP = 1.5 × risque (recalculé par ATR dans run_scalping_analysis).
    """
    if not grab.get("detected"):
        return None

    current_price = float(df['close'].iloc[-1])
    grabbed_level = grab["grabbed_level"]
    grab_type     = grab["type"]

    if grab_type == "bullish":
        action    = "buy"
        entry     = current_price
        sweep_low = grab.get("sweep_low", grabbed_level)
        sl        = round(min(float(sweep_low), grabbed_level) * 0.9995, 5)
        risk      = abs(entry - sl)
        tp1       = round(entry + risk * 2.5, 5)
        tp2       = round(entry + risk * 4.0, 5)
    else:
        action     = "sell"
        entry      = current_price
        sweep_high = grab.get("sweep_high", grabbed_level)
        sl         = round(max(float(sweep_high), grabbed_level) * 1.0005, 5)
        risk       = abs(sl - entry)
        tp1        = round(entry - risk * 2.5, 5)
        tp2        = round(entry - risk * 4.0, 5)

    rr = round(abs(tp1 - entry) / risk, 2) if risk > 0 else 0

    return {
        "action":      action,
        "entry":       round(entry, 5),
        "sl":          sl,
        "tp1":         tp1,
        "tp2":         tp2,
        "rr_ratio":    rr,
        "grab_level":  grabbed_level,
        "description": grab.get("description", ""),
    }
