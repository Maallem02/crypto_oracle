"""
Zone detector — identifies supply/demand zones from MT5 historical data.

Zone definition (SMC-style):
  DEMAND zone at swing low:  price_low=wick low, price_high=body top (max open/close)
  SUPPLY zone at swing high: price_low=body bottom (min open/close), price_high=wick high

Build once (run_full_pipeline or build_zone_database), then scorer.py queries at runtime.
"""

import json
import os
import numpy as np
import pandas as pd
from datetime import datetime

ZONE_DB_PATH = "zone_database.json"

SYMBOLS = ["EURUSD", "GBPJPY", "XAUUSD", "BTC", "ETH", "SOL"]

# Minimum historical touches for a zone to be included in runtime scoring
MIN_TOUCHES_FOR_SCORING = 2


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def detect_zones(df: pd.DataFrame, lookback: int = 5) -> list[dict]:
    """
    Detect all supply/demand zones from OHLCV DataFrame.
    Reuses detect_swings() from structure.py for consistency with the engine.

    Returns list of zone dicts (touch_count=0 at this stage — call annotate_zones next).
    """
    from features.smc.structure import detect_swings

    df = df.copy()
    # Ensure integer index and timestamp column
    if not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
    else:
        df = df.reset_index(drop=True)

    if "timestamp" not in df.columns:
        df["timestamp"] = df.index

    atr_series = _atr(df)
    df_sw      = detect_swings(df, lookback)

    zones = []
    for i in range(len(df_sw)):
        atr_val = atr_series.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        # ── DEMAND: formed at a swing low ────────────────────────────────
        if df_sw["swing_low"].iloc[i]:
            body_top = max(float(df["open"].iloc[i]), float(df["close"].iloc[i]))
            wick_bot = float(df["low"].iloc[i])
            if body_top > wick_bot:               # skip doji (no zone body)
                zones.append({
                    "type":         "demand",
                    "price_low":    round(wick_bot,  6),
                    "price_high":   round(body_top,  6),
                    "atr":          round(float(atr_val), 6),
                    "created_idx":  i,
                    "created_at":   str(df["timestamp"].iloc[i]),
                    "touch_count":  0,
                    "bounce_count": 0,
                    "broken":       False,
                })

        # ── SUPPLY: formed at a swing high ───────────────────────────────
        if df_sw["swing_high"].iloc[i]:
            body_bot = min(float(df["open"].iloc[i]), float(df["close"].iloc[i]))
            wick_top = float(df["high"].iloc[i])
            if wick_top > body_bot:
                zones.append({
                    "type":         "supply",
                    "price_low":    round(body_bot,  6),
                    "price_high":   round(wick_top,  6),
                    "atr":          round(float(atr_val), 6),
                    "created_idx":  i,
                    "created_at":   str(df["timestamp"].iloc[i]),
                    "touch_count":  0,
                    "bounce_count": 0,
                    "broken":       False,
                })

    return zones


def annotate_zones(df: pd.DataFrame, zones: list[dict]) -> list[dict]:
    """
    For each zone, scan the candles AFTER it was created and record:
    - touch_count  : how many times price entered the zone
    - bounce_count : how many times price bounced ≥1× ATR in expected direction
    - broken       : True if price ever closed decisively through the zone

    Uses vectorized NumPy for the initial touch detection (fast), then a small
    Python loop only over actual touch events.
    """
    atr_series = _atr(df)
    highs  = df["high"].values.astype(float)
    lows   = df["low"].values.astype(float)
    closes = df["close"].values.astype(float)
    n      = len(df)

    for zone in zones:
        idx   = zone["created_idx"]
        zl    = zone["price_low"]
        zh    = zone["price_high"]
        z_atr = zone["atr"]

        if idx + 2 >= n:
            continue

        # Vectorized: find all candles after creation that overlap with zone
        future_h = highs[idx + 1:]
        future_l = lows[idx  + 1:]
        touched  = (future_l <= zh) & (future_h >= zl)
        rel_idxs = np.where(touched)[0]

        touch_count  = 0
        bounce_count = 0
        broken       = False

        for rel_i in rel_idxs:
            abs_i = idx + 1 + int(rel_i)
            atr_i = float(atr_series.iloc[abs_i]) if not pd.isna(atr_series.iloc[abs_i]) else z_atr

            touch_count += 1

            # ── Break check ───────────────────────────────────────────────
            c = closes[abs_i]
            if zone["type"] == "demand" and c < zl - 0.15 * atr_i:
                broken = True
                break
            if zone["type"] == "supply" and c > zh + 0.15 * atr_i:
                broken = True
                break

            # ── Bounce check: 5 candles forward ──────────────────────────
            end_j = min(abs_i + 6, n)
            if abs_i + 1 >= n:
                continue

            if zone["type"] == "demand":
                move = float(highs[abs_i + 1:end_j].max()) - zl if abs_i + 1 < n else 0
            else:
                move = zh - float(lows[abs_i + 1:end_j].min()) if abs_i + 1 < n else 0

            if move >= 1.0 * atr_i:
                bounce_count += 1

        zone["touch_count"]  = touch_count
        zone["bounce_count"] = bounce_count
        zone["broken"]       = broken

    return zones


def build_zone_database(
    symbols:   list  = None,
    timeframe: str   = "1h",
    candles:   int   = 20000,
) -> dict:
    """
    Pull H1 history from MT5 for each symbol, detect and annotate all zones,
    and save the database to ZONE_DB_PATH (zone_database.json).

    Returns summary dict {symbol: zone_count}.
    Typical run: ~30 s for 6 symbols × 20 000 candles.
    """
    from features.market.fetcher import fetch_mt5_candles

    if symbols is None:
        symbols = SYMBOLS

    db      = {}
    summary = {}

    for sym in symbols:
        print(f"[ZONES] {sym}: fetching {candles} × {timeframe} candles …")
        try:
            df = fetch_mt5_candles(sym, timeframe, limit=candles)
            df = df.reset_index()

            zones = detect_zones(df, lookback=5)
            print(f"[ZONES] {sym}: {len(zones)} raw zones → annotating …")

            zones = annotate_zones(df, zones)

            # Keep only zones with ≥ MIN_TOUCHES_FOR_SCORING interactions
            # (zones with 0 touches are unreachable price levels — useless)
            zones = [z for z in zones if z["touch_count"] >= MIN_TOUCHES_FOR_SCORING]

            db[sym.upper()]      = zones
            summary[sym.upper()] = len(zones)
            bounces = sum(z["bounce_count"] for z in zones)
            broken  = sum(1 for z in zones if z["broken"])
            print(f"[ZONES] {sym}: {len(zones)} zones kept | "
                  f"total bounces={bounces} | broken={broken}")

        except Exception as e:
            print(f"[ZONES] {sym}: error — {e}")
            db[sym.upper()]      = []
            summary[sym.upper()] = 0

    payload = {"built_at": datetime.now().isoformat(), "zones": db}
    with open(ZONE_DB_PATH, "w") as f:
        json.dump(payload, f)

    total = sum(summary.values())
    print(f"[ZONES] Database saved → {ZONE_DB_PATH}  ({total} zones total)")
    return summary


def load_zone_database() -> dict:
    """Load zone database from disk. Returns {SYMBOL: [zone_dicts]}."""
    if not os.path.exists(ZONE_DB_PATH):
        return {}
    try:
        with open(ZONE_DB_PATH) as f:
            data = json.load(f)
        return data.get("zones", {})
    except Exception as e:
        print(f"[ZONES] Failed to load DB: {e}")
        return {}
