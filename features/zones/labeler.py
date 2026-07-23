"""
Zone labeler — extracts ML training examples from historical zone touch events.

For each zone touch in history:
  - Build a feature vector describing the zone + market context at that moment
  - Label: 1 (bounce ≥ 1.5×ATR in expected direction) | 0 (zone broke)
  - Skip ambiguous touches (neither bounce nor break within FORWARD_CANDLES)

Output: zones_dataset.csv — ready for trainer.py
"""

import numpy as np
import pandas as pd
from features.zones.detector import _atr, detect_zones, annotate_zones

FORWARD_CANDLES       = 30    # look-ahead window for outcome labeling
BOUNCE_ATR_MULT       = 1.5   # minimum move to qualify as a bounce
TOUCH_TOLERANCE_ATR   = 0.25  # price can be this many ATRs outside zone and still "touch"
DATASET_PATH          = "zones_dataset.csv"

# Feature columns (must match FEATURE_COLS in trainer.py exactly)
FEATURE_COLS = [
    "zone_type",          # demand=1, supply=-1
    "zone_age_candles",   # H1 candles elapsed since zone was created
    "touch_number",       # which touch this is (1st=1, 2nd=2 …)
    "prior_bounce_rate",  # bounce_count / (touch_count-1) before this touch
    "zone_width_atr",     # (zone_high - zone_low) / ATR — normalized zone size
    "rsi_at_touch",       # RSI(14) when price enters zone
    "approach_speed",     # (close - close[5]) / ATR — momentum direction into zone
    "htf_trend",          # simple H1 trend: bullish=1, bearish=-1, neutral=0
    "hour",               # hour of day (0-23) — session filter proxy
    "day_of_week",        # 0=Mon … 4=Fri
    "atr_ratio",          # ATR / close — volatility context
    "body_ratio",         # abs(open-close) / (high-low+ε) — candle conviction at touch
]


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / (loss + 1e-9)
    return 100 - 100 / (1 + rs)


def _htf_trend_at(df: pd.DataFrame, i: int, lookback: int = 20) -> int:
    """Compare first vs last high/low in a lookback window — simple trend proxy."""
    if i < lookback:
        return 0
    window = df.iloc[i - lookback: i]
    h_start, h_end = float(window["high"].iloc[0]),  float(window["high"].iloc[-1])
    l_start, l_end = float(window["low"].iloc[0]),   float(window["low"].iloc[-1])
    if h_end > h_start and l_end > l_start:
        return 1    # bullish
    if h_end < h_start and l_end < l_start:
        return -1   # bearish
    return 0


def extract_touch_events(
    df:    pd.DataFrame,
    zones: list[dict],
) -> pd.DataFrame:
    """
    Iterate over annotated zones, find every touch event in the DataFrame,
    and produce one ML training row per event.

    Skips events that are neither a clear bounce nor a clear break (ambiguous).
    """
    df   = df.copy().reset_index(drop=True)
    n    = len(df)

    atr_s  = _atr(df)
    rsi_s  = _rsi(df["close"])

    highs  = df["high"].values.astype(float)
    lows   = df["low"].values.astype(float)
    opens  = df["open"].values.astype(float)
    closes = df["close"].values.astype(float)

    has_ts = "timestamp" in df.columns

    rows = []

    for zone in zones:
        created_idx = zone["created_idx"]
        zl          = zone["price_low"]
        zh          = zone["price_high"]
        z_atr       = zone["atr"]
        z_type      = 1 if zone["type"] == "demand" else -1

        if created_idx + 2 >= n - FORWARD_CANDLES:
            continue

        # Vectorized: all candles after creation that overlap zone (+ tolerance)
        tol          = TOUCH_TOLERANCE_ATR * z_atr
        future_h     = highs[created_idx + 1: n - FORWARD_CANDLES]
        future_l     = lows[created_idx  + 1: n - FORWARD_CANDLES]
        touched_mask = (future_l <= zh + tol) & (future_h >= zl - tol)
        rel_idxs     = np.where(touched_mask)[0]

        touch_count  = 0
        bounce_count = 0

        for rel_i in rel_idxs:
            abs_i = created_idx + 1 + int(rel_i)
            if abs_i + FORWARD_CANDLES >= n:
                break

            atr_i = float(atr_s.iloc[abs_i]) if not pd.isna(atr_s.iloc[abs_i]) else z_atr
            rsi_i = float(rsi_s.iloc[abs_i]) if not pd.isna(rsi_s.iloc[abs_i]) else 50.0

            touch_count += 1
            prior_br = bounce_count / max(touch_count - 1, 1) if touch_count > 1 else 0.5

            # ── Outcome: look FORWARD_CANDLES ahead ──────────────────────
            future_h_w = highs[abs_i + 1: abs_i + 1 + FORWARD_CANDLES]
            future_l_w = lows[abs_i  + 1: abs_i + 1 + FORWARD_CANDLES]
            future_c_w = closes[abs_i + 1: abs_i + 1 + FORWARD_CANDLES]

            if zone["type"] == "demand":
                broke  = bool((future_c_w < zl - 0.15 * atr_i).any())
                max_up = float(future_h_w.max()) - zl
                bounced = not broke and max_up >= BOUNCE_ATR_MULT * atr_i
            else:
                broke   = bool((future_c_w > zh + 0.15 * atr_i).any())
                max_dn  = zh - float(future_l_w.min())
                bounced = not broke and max_dn >= BOUNCE_ATR_MULT * atr_i

            if not broke and not bounced:
                continue  # ambiguous — skip

            label = 1 if bounced else 0
            if bounced:
                bounce_count += 1

            # ── Feature vector ────────────────────────────────────────────
            age   = abs_i - created_idx

            # Approach speed: 5-candle momentum normalised by ATR
            if abs_i >= 5:
                speed = (closes[abs_i] - closes[abs_i - 5]) / max(atr_i, 1e-9)
            else:
                speed = 0.0

            htf    = _htf_trend_at(df, abs_i)

            if has_ts:
                ts     = pd.to_datetime(df["timestamp"].iloc[abs_i])
                hour   = int(ts.hour)
                dow    = int(ts.weekday())
            else:
                hour, dow = 0, 0

            c_range    = highs[abs_i] - lows[abs_i]
            body       = abs(opens[abs_i] - closes[abs_i])
            body_ratio = body / max(c_range, 1e-9)

            rows.append({
                "zone_type":         z_type,
                "zone_age_candles":  age,
                "touch_number":      touch_count,
                "prior_bounce_rate": round(prior_br, 3),
                "zone_width_atr":    round((zh - zl) / max(z_atr, 1e-9), 3),
                "rsi_at_touch":      round(rsi_i, 1),
                "approach_speed":    round(float(speed), 3),
                "htf_trend":         htf,
                "hour":              hour,
                "day_of_week":       dow,
                "atr_ratio":         round(atr_i / max(closes[abs_i], 1e-9), 6),
                "body_ratio":        round(float(body_ratio), 3),
                "label":             label,
            })

            if broke:
                break   # zone is invalidated — no more events from it

    return pd.DataFrame(rows, columns=FEATURE_COLS + ["label"])


def build_dataset(
    symbols:     list = None,
    timeframe:   str  = "1h",
    candles:     int  = 20000,
    output_path: str  = DATASET_PATH,
) -> pd.DataFrame:
    """
    Full pipeline per symbol: fetch H1 history → detect zones → annotate → label.
    Concatenates all symbols into one CSV for training.
    """
    from features.market.fetcher import fetch_mt5_candles
    from features.zones.detector import SYMBOLS

    if symbols is None:
        symbols = SYMBOLS

    all_dfs = []

    for sym in symbols:
        print(f"[ZONES-LABEL] {sym}: building labeled dataset …")
        try:
            df    = fetch_mt5_candles(sym, timeframe, limit=candles)
            df    = df.reset_index()
            zones = detect_zones(df, lookback=5)
            zones = annotate_zones(df, zones)

            events         = extract_touch_events(df, zones)
            events["symbol"] = sym.upper()
            all_dfs.append(events)

            n_bounce = int(events["label"].sum())
            n_break  = len(events) - n_bounce
            print(f"[ZONES-LABEL] {sym}: {len(events)} events "
                  f"(bounces={n_bounce}, breaks={n_break})")

        except Exception as e:
            print(f"[ZONES-LABEL] {sym}: error — {e}")

    if not all_dfs:
        print("[ZONES-LABEL] No data collected.")
        return pd.DataFrame()

    dataset = pd.concat(all_dfs, ignore_index=True)
    dataset.to_csv(output_path, index=False)

    total_b = int(dataset["label"].sum())
    total_br = len(dataset) - total_b
    print(f"[ZONES-LABEL] Dataset saved: {len(dataset)} events "
          f"(bounces={total_b} {total_b*100//len(dataset)}%, "
          f"breaks={total_br}) → {output_path}")
    return dataset
