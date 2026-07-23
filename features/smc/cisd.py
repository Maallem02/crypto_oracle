import pandas as pd


def detect_cisd(df: pd.DataFrame, min_run: int = 2, max_age: int = 3) -> dict:
    """
    Change in State of Delivery (CISD) — ICT concept.

    Price is "delivered" candle by candle in one direction (bearish = close < open).
    A CISD is the candle that breaks out of that delivery by closing through the
    opposite side of the previous delivery candle.

    Bullish CISD:
      - At least `min_run` consecutive bearish candles (close < open)
      - Followed by a candle that closes ABOVE the previous candle's high
      → Delivery flipped bullish — strong reversal confirmation

    Bearish CISD:
      - At least `min_run` consecutive bullish candles (close > open)
      - Followed by a candle that closes BELOW the previous candle's low
      → Delivery flipped bearish

    Checks the last `max_age` candles as candidates.
    Freshness matters: age=1 (current candle) is strongest signal.
    """
    if len(df) < min_run + max_age + 2:
        return {"detected": False, "type": None, "description": "Not enough data"}

    for age in range(1, max_age + 1):
        # cisd_candle: the candidate that broke the delivery
        # pre_window:  the candles before it (delivery run)
        if age == 1:
            cisd_candle = df.iloc[-1]
            pre_window  = df.iloc[-(min_run + age + 1):-1]
        else:
            cisd_candle = df.iloc[-age]
            pre_window  = df.iloc[-(min_run + age + 1):-age]

        if len(pre_window) < min_run:
            continue

        prev_candle = pre_window.iloc[-1]   # candle immediately before CISD

        # Count consecutive bearish delivery ending at prev_candle
        bearish_run = 0
        for i in range(len(pre_window) - 1, -1, -1):
            c = pre_window.iloc[i]
            if float(c["close"]) < float(c["open"]):
                bearish_run += 1
            else:
                break

        # Count consecutive bullish delivery ending at prev_candle
        bullish_run = 0
        for i in range(len(pre_window) - 1, -1, -1):
            c = pre_window.iloc[i]
            if float(c["close"]) > float(c["open"]):
                bullish_run += 1
            else:
                break

        cisd_close = float(cisd_candle["close"])
        prev_high  = float(prev_candle["high"])
        prev_low   = float(prev_candle["low"])

        # ── Bullish CISD ──────────────────────────────────────────────────
        if bearish_run >= min_run and cisd_close > prev_high:
            # Validate: candles after CISD (if any) haven't re-established bearish delivery
            if age > 1:
                post = df.iloc[-age + 1:]  # candles after CISD up to now
                if not post.empty:
                    # If any post-CISD candle closes below CISD candle's low → stale
                    if (post["close"] < float(cisd_candle["low"])).any():
                        continue

            strength = round((cisd_close - prev_high) / prev_high * 100, 4)
            return {
                "detected":     True,
                "type":         "bullish",
                "age":          age,
                "run_length":   bearish_run,
                "broke_level":  round(prev_high, 5),
                "cisd_close":   round(cisd_close, 5),
                "strength":     strength,
                "description":  (
                    f"Bullish CISD (age={age}): closed {cisd_close:.4f} above "
                    f"bearish delivery high {prev_high:.4f} "
                    f"after {bearish_run} bearish candles"
                ),
            }

        # ── Bearish CISD ──────────────────────────────────────────────────
        if bullish_run >= min_run and cisd_close < prev_low:
            # Validate: candles after CISD haven't re-established bullish delivery
            if age > 1:
                post = df.iloc[-age + 1:]
                if not post.empty:
                    if (post["close"] > float(cisd_candle["high"])).any():
                        continue

            strength = round((prev_low - cisd_close) / prev_low * 100, 4)
            return {
                "detected":     True,
                "type":         "bearish",
                "age":          age,
                "run_length":   bullish_run,
                "broke_level":  round(prev_low, 5),
                "cisd_close":   round(cisd_close, 5),
                "strength":     strength,
                "description":  (
                    f"Bearish CISD (age={age}): closed {cisd_close:.4f} below "
                    f"bullish delivery low {prev_low:.4f} "
                    f"after {bullish_run} bullish candles"
                ),
            }

    return {"detected": False, "type": None, "description": "No CISD detected"}
