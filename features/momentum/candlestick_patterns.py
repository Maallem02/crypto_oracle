"""
Candlestick reversal pattern detectors — the M1-level confirmation trigger
for the M1 confirmation entry strategy (see m1_signal.py). Each function
checks the LAST candle(s) of the given DataFrame and returns True/False.
"""


def _body(df, i):
    return abs(df['close'].iloc[i] - df['open'].iloc[i])


def _range(df, i):
    return df['high'].iloc[i] - df['low'].iloc[i]


def _is_bullish(df, i):
    return df['close'].iloc[i] > df['open'].iloc[i]


def _is_bearish(df, i):
    return df['close'].iloc[i] < df['open'].iloc[i]


def bullish_engulfing(df) -> bool:
    """Bearish candle followed by a bullish candle whose body fully engulfs it."""
    if len(df) < 2:
        return False
    i = len(df) - 1
    prev, cur = i - 1, i
    if not _is_bearish(df, prev) or not _is_bullish(df, cur):
        return False
    return (df['open'].iloc[cur] <= df['close'].iloc[prev] and
            df['close'].iloc[cur] >= df['open'].iloc[prev])


def bearish_engulfing(df) -> bool:
    """Bullish candle followed by a bearish candle whose body fully engulfs it."""
    if len(df) < 2:
        return False
    i = len(df) - 1
    prev, cur = i - 1, i
    if not _is_bullish(df, prev) or not _is_bearish(df, cur):
        return False
    return (df['open'].iloc[cur] >= df['close'].iloc[prev] and
            df['close'].iloc[cur] <= df['open'].iloc[prev])


def three_white_soldiers(df) -> bool:
    """Three consecutive bullish candles, each closing higher, small upper wicks."""
    if len(df) < 3:
        return False
    idxs = [len(df) - 3, len(df) - 2, len(df) - 1]

    for j in idxs:
        if not _is_bullish(df, j):
            return False
    for k in range(1, 3):
        if df['close'].iloc[idxs[k]] <= df['close'].iloc[idxs[k - 1]]:
            return False
        if df['open'].iloc[idxs[k]] < df['open'].iloc[idxs[k - 1]]:
            return False
    for j in idxs:
        rng = _range(df, j)
        if rng <= 0:
            return False
        upper_wick = df['high'].iloc[j] - max(df['open'].iloc[j], df['close'].iloc[j])
        if upper_wick / rng > 0.3:
            return False
    return True


def three_black_crows(df) -> bool:
    """Three consecutive bearish candles, each closing lower, small lower wicks."""
    if len(df) < 3:
        return False
    idxs = [len(df) - 3, len(df) - 2, len(df) - 1]

    for j in idxs:
        if not _is_bearish(df, j):
            return False
    for k in range(1, 3):
        if df['close'].iloc[idxs[k]] >= df['close'].iloc[idxs[k - 1]]:
            return False
        if df['open'].iloc[idxs[k]] > df['open'].iloc[idxs[k - 1]]:
            return False
    for j in idxs:
        rng = _range(df, j)
        if rng <= 0:
            return False
        lower_wick = min(df['open'].iloc[j], df['close'].iloc[j]) - df['low'].iloc[j]
        if lower_wick / rng > 0.3:
            return False
    return True


def hammer(df) -> bool:
    """Small body near the top, long lower wick (>=2x body), little upper wick."""
    if len(df) < 1:
        return False
    i = len(df) - 1
    rng = _range(df, i)
    if rng <= 0:
        return False
    body       = _body(df, i)
    lower_wick = min(df['open'].iloc[i], df['close'].iloc[i]) - df['low'].iloc[i]
    upper_wick = df['high'].iloc[i] - max(df['open'].iloc[i], df['close'].iloc[i])
    return lower_wick >= body * 2 and upper_wick <= body * 0.5 and body / rng < 0.4


def shooting_star(df) -> bool:
    """Small body near the bottom, long upper wick (>=2x body), little lower wick."""
    if len(df) < 1:
        return False
    i = len(df) - 1
    rng = _range(df, i)
    if rng <= 0:
        return False
    body       = _body(df, i)
    upper_wick = df['high'].iloc[i] - max(df['open'].iloc[i], df['close'].iloc[i])
    lower_wick = min(df['open'].iloc[i], df['close'].iloc[i]) - df['low'].iloc[i]
    return upper_wick >= body * 2 and lower_wick <= body * 0.5 and body / rng < 0.4


# Strength tiers — used to size the confidence bonus when one fires.
STRONG_BULLISH = {"bullish_engulfing", "three_white_soldiers"}
STRONG_BEARISH = {"bearish_engulfing", "three_black_crows"}
WEAK_BULLISH   = {"hammer"}
WEAK_BEARISH   = {"shooting_star"}


def detect_bullish_pattern(df) -> str | None:
    if bullish_engulfing(df):     return "bullish_engulfing"
    if three_white_soldiers(df):  return "three_white_soldiers"
    if hammer(df):                return "hammer"
    return None


def detect_bearish_pattern(df) -> str | None:
    if bearish_engulfing(df):     return "bearish_engulfing"
    if three_black_crows(df):     return "three_black_crows"
    if shooting_star(df):         return "shooting_star"
    return None
