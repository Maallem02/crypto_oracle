"""
test_ml.py — Test the PyTorch MLP on realistic trade scenarios.
Run: venv\Scripts\python.exe test_ml.py
"""
import sys
import os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── Runtime bootstrap (same as main.py) ───────────────────────────────────────
from core.config import runtime
runtime.port       = 8000
runtime.db_path    = "crypto_oracle_8000.db"
runtime.ml_db_path = "crypto_oracle_ml.db"
runtime.instance   = "test"
runtime.mt5_path   = None

from features.trading.ml_model import load_model, predict_win_probability

print("Loading model...")
loaded = load_model()
if not loaded:
    print("ERROR: model not found — run train first (it auto-trains on startup)")
    sys.exit(1)

# ── Helper ────────────────────────────────────────────────────────────────────

def make_analysis(bias, structure, rsi, stoch_k, stoch_d, adx, atr_ratio,
                  lg_strength, pd_zone, pd_pct, smc_confluence=None, score=80):
    return {
        "bias": bias,
        "scalping_score": score,
        "adx": adx,
        "atr_ratio": atr_ratio,
        "rsi": rsi,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "liquidity_grab": {"strength": lg_strength},
        "premium_discount": {"zone": pd_zone, "position_pct": pd_pct},
        "structure": {"trend": structure},
        "smc_confluence": smc_confluence,
    }

def test(label, analysis, entry_data, htf_consensus, htf_timeframe="1h",
         extra=None, threshold=0.55):
    prob = predict_win_probability(
        analysis=analysis,
        entry_data=entry_data,
        htf_consensus=htf_consensus,
        htf_timeframe=htf_timeframe,
        extra=extra or {},
    )
    decision = "TAKE" if prob >= threshold else "SKIP"
    bar = "#" * int(prob * 40)
    pad = "." * (40 - len(bar))
    print(f"\n[{decision}] {label}")
    print(f"  Prob:  {bar}{pad}  {prob:.3f}  (threshold {threshold})")
    if extra:
        cl = extra.get("consecutive_losses", 0)
        if cl:
            print(f"  Note:  {cl} consecutive loss(es) on this symbol")
    return prob

# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*65)
print("  SCENARIO TESTS — PyTorch MLP win probability")
print("="*65)

# ── 1. Perfect BUY: everything aligned ────────────────────────────────────────
test(
    "BUY — perfect setup (all confluences aligned)",
    analysis=make_analysis(
        bias="buy", structure="bullish",
        rsi=38, stoch_k=22, stoch_d=20,
        adx=28, atr_ratio=1.1,
        lg_strength=0.85, pd_zone="discount", pd_pct=15,
        smc_confluence="order_block", score=88,
    ),
    entry_data={"rr_ratio": 2.5},
    htf_consensus="bullish",
    extra={"volume_ratio": 1.5, "spread_pct": 0.008, "symbol_winrate": 0.55, "consecutive_losses": 0},
)

# ── 2. Perfect SELL: everything aligned ───────────────────────────────────────
test(
    "SELL — perfect setup (premium zone, bearish HTF, strong momentum)",
    analysis=make_analysis(
        bias="sell", structure="bearish",
        rsi=68, stoch_k=78, stoch_d=75,
        adx=32, atr_ratio=1.3,
        lg_strength=0.80, pd_zone="premium", pd_pct=85,
        smc_confluence="order_block", score=85,
    ),
    entry_data={"rr_ratio": 2.2},
    htf_consensus="bearish",
    extra={"volume_ratio": 1.4, "spread_pct": 0.009, "symbol_winrate": 0.52, "consecutive_losses": 0},
)

# ── 3. Counter-trend BUY: HTF bearish but signal is buy ───────────────────────
test(
    "BUY — counter-trend (HTF bearish, buying into downtrend)",
    analysis=make_analysis(
        bias="buy", structure="bullish",
        rsi=45, stoch_k=40, stoch_d=38,
        adx=24, atr_ratio=1.0,
        lg_strength=0.65, pd_zone="discount", pd_pct=30,
        score=76,
    ),
    entry_data={"rr_ratio": 1.8},
    htf_consensus="bearish",         # HTF says DOWN, signal says UP
    extra={"volume_ratio": 0.9, "spread_pct": 0.010, "symbol_winrate": 0.44, "consecutive_losses": 0},
)

# ── 4. EURUSD SELL in discount zone (like the bad trades last session) ─────────
test(
    "SELL — discount zone (like the 3 bad EURUSD sells at 1.13980-1.14026)",
    analysis=make_analysis(
        bias="sell", structure="bearish",
        rsi=52, stoch_k=55, stoch_d=50,
        adx=18, atr_ratio=0.9,
        lg_strength=0.60, pd_zone="discount", pd_pct=25,  # selling from discount!
        score=77,
    ),
    entry_data={"rr_ratio": 1.5},
    htf_consensus="bearish",
    extra={"volume_ratio": 0.8, "spread_pct": 0.012, "symbol_winrate": 0.46, "consecutive_losses": 1},
)

# ── 5. After 2 consecutive losses (direction lock scenario) ───────────────────
test(
    "BUY — 2 consecutive losses already (high risk, should likely skip)",
    analysis=make_analysis(
        bias="buy", structure="bullish",
        rsi=42, stoch_k=35, stoch_d=32,
        adx=22, atr_ratio=1.0,
        lg_strength=0.70, pd_zone="discount", pd_pct=20,
        score=79,
    ),
    entry_data={"rr_ratio": 2.0},
    htf_consensus="bullish",
    extra={"volume_ratio": 1.1, "spread_pct": 0.009, "symbol_winrate": 0.44, "consecutive_losses": 2},
)

# ── 6. Weak setup: low score, HTF conflict, flat ADX ─────────────────────────
test(
    "BUY — weak setup (low score, HTF conflict, weak ADX)",
    analysis=make_analysis(
        bias="buy", structure="neutral",
        rsi=50, stoch_k=50, stoch_d=48,
        adx=12, atr_ratio=0.7,
        lg_strength=0.40, pd_zone="equilibrium", pd_pct=50,
        score=72,
    ),
    entry_data={"rr_ratio": 1.2},
    htf_consensus="conflict",
    extra={"volume_ratio": 0.7, "spread_pct": 0.015, "symbol_winrate": 0.40, "consecutive_losses": 1},
)

# ── 7. ETH 5m buy — worst performing symbol/tf (34% wr in data) ───────────────
test(
    "BUY ETH 5m — historically worst symbol+tf (34% win rate in training data)",
    analysis=make_analysis(
        bias="buy", structure="bullish",
        rsi=40, stoch_k=28, stoch_d=25,
        adx=26, atr_ratio=1.1,
        lg_strength=0.72, pd_zone="discount", pd_pct=18,
        score=81,
    ),
    entry_data={"rr_ratio": 2.0},
    htf_consensus="bullish",
    extra={"volume_ratio": 1.2, "spread_pct": 0.008, "symbol_winrate": 0.34, "consecutive_losses": 0},
)

# ── 8. XAUUSD 5m sell — best performing symbol (54% wr) ──────────────────────
test(
    "SELL XAUUSD 5m — best performing symbol (54% win rate in training data)",
    analysis=make_analysis(
        bias="sell", structure="bearish",
        rsi=65, stoch_k=72, stoch_d=70,
        adx=30, atr_ratio=1.2,
        lg_strength=0.78, pd_zone="premium", pd_pct=82,
        smc_confluence="fvg", score=83,
    ),
    entry_data={"rr_ratio": 2.3},
    htf_consensus="bearish",
    extra={"volume_ratio": 1.3, "spread_pct": 0.007, "symbol_winrate": 0.54, "consecutive_losses": 0},
)

# ── 9. Good setup but HTF neutral (not conflict, not confirmed) ───────────────
test(
    "SELL — good TA but HTF neutral (no trend confirmation)",
    analysis=make_analysis(
        bias="sell", structure="bearish",
        rsi=60, stoch_k=65, stoch_d=62,
        adx=20, atr_ratio=1.0,
        lg_strength=0.68, pd_zone="premium", pd_pct=75,
        score=78,
    ),
    entry_data={"rr_ratio": 1.8},
    htf_consensus="neutral",
    extra={"volume_ratio": 1.0, "spread_pct": 0.011, "symbol_winrate": 0.49, "consecutive_losses": 0},
)

# ── 10. Very high RR with moderate setup ─────────────────────────────────────
test(
    "BUY — moderate setup but excellent RR ratio (3.5:1)",
    analysis=make_analysis(
        bias="buy", structure="bullish",
        rsi=44, stoch_k=33, stoch_d=30,
        adx=23, atr_ratio=1.05,
        lg_strength=0.65, pd_zone="discount", pd_pct=22,
        score=76,
    ),
    entry_data={"rr_ratio": 3.5},
    htf_consensus="bullish",
    extra={"volume_ratio": 1.1, "spread_pct": 0.009, "symbol_winrate": 0.48, "consecutive_losses": 0},
)

print("\n" + "="*65)
print("  SUMMARY: threshold = 0.55")
print("  Prob >= 0.55 → TAKE  |  Prob < 0.55 → SKIP")
print("="*65 + "\n")
