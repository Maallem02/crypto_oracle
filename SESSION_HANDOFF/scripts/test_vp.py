"""Unit tests for the volume profile strategy, on synthetic bars where the
right answer is known in advance. Includes the cases that must NOT fire."""
import sys

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from features.volume_profile.profile import Profile, compute_profile
from features.volume_profile import strategy as S

DT = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
      ("close", "f8"), ("tick_volume", "i8")]
fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   ' + extra) if extra else ''}")


def bars(rows, t0=1_700_000_000, step=900):
    """rows: (open, high, low, close[, volume])"""
    out = []
    for i, r in enumerate(rows):
        o, h, l, c = r[:4]
        v = r[4] if len(r) > 4 else 100
        out.append((t0 + i * step, o, h, l, c, v))
    return np.array(out, dtype=DT)


def prof(poc=100.0, vah=105.0, val=95.0, inside=True):
    return Profile(poc=poc, vah=vah, val=val, low=85, high=115, last_close=100 if inside else 112,
                   closed_inside=inside, bars=100, total_volume=1e4)


ATR1 = lambda n: np.full(n, 1.0)

print("=" * 78)
print("PROFILE")
print("=" * 78)
rows = [(100, 100.4, 99.6, 100, 5000)] * 40          # heavy volume around 100
rows += [(90, 90.5, 89.5, 90, 50)] * 5                # thin volume far below
rows += [(110, 110.5, 109.5, 110, 50)] * 5            # thin volume far above
rows += [(100, 100.3, 99.7, 100, 5000)]               # session closes at 100
p = compute_profile(bars(rows, step=60))
check("POC sits on the heavy-volume price (~100)", abs(p.poc - 100) < 0.5, f"POC={p.poc:.3f}")
check("value area contains the POC", p.val <= p.poc <= p.vah, f"VAL={p.val:.3f} VAH={p.vah:.3f}")
check("value area excludes the thin extremes", p.val > 90.5 and p.vah < 109.5)
check("session closing at 100 = INSIDE", p.closed_inside is True)
rows2 = rows[:-1] + [(110, 110.5, 109.5, 110.2, 50)]
p2 = compute_profile(bars(rows2, step=60))
check("session closing at 110 = OUTSIDE", p2.closed_inside is False)
vol_in = 0.0

print("\n" + "=" * 78)
print("SETUP 2 - VALUE AREA REVERSAL (prev session INSIDE)")
print("=" * 78)
b = bars([(100, 100.5, 99.5, 100), (98, 98.5, 97, 97.5), (96, 96.2, 93.8, 94),
          (94, 94.5, 92.5, 93), (93, 97.5, 92.8, 97.2)])
s = S.detect_va_reversal(b, ATR1(len(b)), prof(inside=True))
check("closed below VAL then back inside -> BUY", s is not None and s.direction == "buy")
if s:
    check("stop below the excursion low (92.5) minus buffer", abs(s.sl - (92.5 - 0.1)) < 1e-9,
          f"SL={s.sl}")
    check("target is 2R", abs((s.tp - s.entry) - 2 * (s.entry - s.sl)) < 1e-9)
b = bars([(100, 100.5, 99.5, 100), (103, 106.5, 102.8, 106.2), (106, 107.8, 105.5, 107),
          (107, 107.2, 104.1, 104.4)])
s = S.detect_va_reversal(b, ATR1(len(b)), prof(inside=True))
check("closed above VAH then back inside -> SELL", s is not None and s.direction == "sell")
b = bars([(100, 100.5, 99.5, 100), (96, 96.2, 93.8, 94), (94, 95.6, 93.5, 94.6)])
check("wick back inside but CLOSE still below VAL -> nothing",
      S.detect_va_reversal(b, ATR1(len(b)), prof(inside=True)) is None)
b = bars([(100, 100.5, 99.5, 100), (96, 96.2, 93.8, 94), (94, 97.5, 93.5, 97.2)])
check("same move but prev session OUTSIDE -> nothing",
      S.detect_va_reversal(b, ATR1(len(b)), prof(inside=False)) is None)

print("\n" + "=" * 78)
print("SETUP 1 - POC BOUNCE (prev session OUTSIDE)")
print("=" * 78)
b = bars([(97, 98, 96.5, 97.8), (98, 98.9, 97.9, 98.8), (99.3, 100.5, 98.8, 99.0)])
s = S.detect_poc_bounce(b, ATR1(len(b)), prof(inside=False))
check("up into POC, red rejection wick, close below -> SELL",
      s is not None and s.direction == "sell", s.reason if s else "")
if s:
    check("stop above the wick high (100.5) + buffer", abs(s.sl - 100.6) < 1e-9, f"SL={s.sl}")
b = bars([(103, 103.5, 102, 102.2), (102, 102.2, 100.3, 100.6), (100.4, 102.4, 100.1, 102.3)])
s = S.detect_poc_bounce(b, ATR1(len(b)), prof(inside=False))
check("down onto POC, bullish engulfing -> BUY",
      s is not None and s.direction == "buy", s.reason if s else "")
check("same rejection but prev session INSIDE -> nothing",
      S.detect_poc_bounce(bars([(97, 98, 96.5, 97.8), (98, 98.9, 97.9, 98.8),
                                (99.3, 100.5, 98.8, 99.0)]),
                          ATR1(3), prof(inside=True)) is None)
b = bars([(97, 98, 96.5, 97.8), (98, 98.9, 97.9, 98.8), (99, 100.5, 98.8, 99.0)])
check("doji rejection (close = open, not red) -> nothing",
      S.detect_poc_bounce(b, ATR1(len(b)), prof(inside=False)) is None)
b = bars([(97, 98, 96.5, 97.8), (98, 98.9, 97.9, 98.8), (98.5, 98.9, 98.4, 98.6)])
check("never reached the POC -> nothing",
      S.detect_poc_bounce(b, ATR1(len(b)), prof(inside=False)) is None)

print("\n" + "=" * 78)
print("SETUP 3 - VALUE AREA BREAKOUT (VAH 105, deep limit 102.5, ATR 1)")
print("=" * 78)
b = bars([(104, 104.5, 103.5, 104), (104.5, 106.2, 104.4, 106),     # break: close > 105.5
          (106, 107.4, 105.9, 107), (107, 107.1, 105.2, 105.6),     # pullback near VAH
          (105.6, 106.4, 105.3, 106.2), (106.2, 107.9, 106, 107.7)])  # close > 107.4 = BOS
s = S.detect_va_breakout(b, ATR1(len(b)), prof(inside=True))
check("break, held pullback, higher high -> BUY",
      s is not None and s.direction == "buy", s.reason if s else "")
if s:
    check("stop below the pullback low (105.2) minus buffer", abs(s.sl - 105.1) < 1e-9,
          f"SL={s.sl}")
b = bars([(104, 104.5, 103.5, 104), (104.5, 106.2, 104.4, 106), (106, 107.4, 105.9, 107),
          (107, 107.1, 102, 102.2),                                  # closes below 102.5
          (102.2, 106.4, 102, 106.2), (106.2, 107.9, 106, 107.7)])
check("pullback closed too deep inside -> invalidated, nothing",
      S.detect_va_breakout(b, ATR1(len(b)), prof(inside=True)) is None)
b = bars([(104, 104.5, 103.5, 104), (104.5, 105.4, 104.4, 105.3),    # only +0.3 ATR: not significant
          (105.3, 107.4, 105.2, 107), (107, 107.1, 105.2, 105.6), (105.6, 107.9, 105.3, 107.7)])
s = S.detect_va_breakout(b, ATR1(len(b)), prof(inside=True))
check("breakout only 0.3 ATR beyond VAH is not 'significant' on that bar",
      s is None or s.detail.get("leg_extreme", 0) >= 107.4, "")
b = bars([(96, 96.5, 95.5, 96), (95.5, 95.6, 93.8, 94),              # close < 94.5 = break down
          (94, 94.1, 92.6, 93), (93, 94.8, 92.9, 94.4),              # pullback near VAL
          (94.4, 94.6, 93.5, 93.7), (93.7, 93.9, 92.2, 92.4)])       # close < 92.6 = BOS
s = S.detect_va_breakout(b, ATR1(len(b)), prof(inside=False))
check("mirror: break below VAL, held pullback, lower low -> SELL",
      s is not None and s.direction == "sell", s.reason if s else "")

print("\n" + "=" * 78)
print("RISK SANITY")
print("=" * 78)
b = bars([(100, 100.5, 99.5, 100), (96, 96.2, 94.95, 94.97), (94.97, 95.2, 94.96, 95.0)])
s = S.detect_va_reversal(b, ATR1(len(b)), prof(inside=True))
check("stop only 0.15 ATR away -> found but BLOCKED (spread would eat it)",
      s is not None and s.blocked is not None and "tight" in s.blocked,
      s.blocked if s else "")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
