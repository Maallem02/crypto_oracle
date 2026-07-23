"""
test_live_eth.py — Feed live ETH market data into the ML decision model.
Run: venv\Scripts\python.exe test_live_eth.py
MT5 must be open and logged in.
"""
import sys, warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Bootstrap ─────────────────────────────────────────────────────────────────
from core.config import runtime
runtime.port       = 8000
runtime.db_path    = "crypto_oracle_8000.db"
runtime.ml_db_path = "crypto_oracle_ml.db"
runtime.instance   = "live_test"
runtime.mt5_path   = None

import MetaTrader5 as mt5
from features.trading.router import _mt5_init, get_htf_trend, get_extra_features
from features.trading.ml_model import load_model, predict_decision
from features.market.fetcher import fetch_candles
from features.smc.engine import run_scalping_analysis

# ── Init ──────────────────────────────────────────────────────────────────────
_mt5_init()
load_model()

SYMBOL   = "ETH"
HTF_TFS  = ["5m", "1h"]
SCAN_TFS = ["15m", "5m"]

# Resolve broker tick symbol (e.g. ETH → ETHUSDm)
def _get_tick(sym):
    for candidate in [sym, sym + "USDm", sym + "USDT", sym + "USD", sym + "USDm"]:
        t = mt5.symbol_info_tick(candidate)
        if t:
            return t, candidate
    return None, None

tick, tick_sym = _get_tick(SYMBOL)
if not tick:
    print(f"ERROR: Cannot get tick for {SYMBOL} — is MT5 running?")
    sys.exit(1)

price = (tick.bid + tick.ask) / 2
print(f"\n{'='*60}")
print(f"  LIVE ANALYSIS — {SYMBOL}   current price: {price:.2f}")
print(f"{'='*60}")

# ── HTF consensus (same as the scanner) ───────────────────────────────────────
print(f"\n[1] Computing HTF consensus ({HTF_TFS}) ...")
htf_result  = get_htf_trend(SYMBOL, HTF_TFS)
consensus   = htf_result.get("consensus", "neutral")
htf_details = htf_result.get("trends", {})
print(f"    {htf_details}  →  consensus = {consensus.upper()}")

# ── Per-timeframe analysis + ML decision ──────────────────────────────────────
print(f"\n[2] Running scalping analysis on {SCAN_TFS} ...\n")

results = []

for tf in SCAN_TFS:
    print(f"  ── {tf} ─────────────────────────────────────────────")
    try:
        df       = fetch_candles(SYMBOL, tf, limit=200)
        analysis = run_scalping_analysis(df, SYMBOL)
        bias     = analysis.get("bias", "unknown")
        ta_score = analysis.get("scalping_score", 0)
        rsi      = analysis.get("rsi", 0)
        stoch_k  = analysis.get("stoch_k", 0)
        adx      = analysis.get("adx", 0)
        pd       = analysis.get("premium_discount", {}) or {}
        pd_zone  = pd.get("zone", "?")
        pd_pct   = pd.get("position_pct", 0) or 0
        struct   = (analysis.get("structure") or {}).get("trend", "?")

        print(f"  SMC signal : {bias.upper() if bias else 'NONE'}")
        print(f"  TA score   : {ta_score}")
        print(f"  Structure  : {struct}  |  PD zone: {pd_zone} ({pd_pct:.0f}%)")
        print(f"  RSI={rsi:.1f}  Stoch={stoch_k:.1f}  ADX={adx:.1f}")

        # Entry data (simplified — no actual SL/TP calc needed for model)
        entry_data = {
            "entry":    price,
            "rr_ratio": analysis.get("rr_ratio") or 2.0,
        }

        # Extra features from live DB
        extra = get_extra_features(SYMBOL, analysis, price)
        print(f"  Symbol WR  : {extra.get('symbol_winrate', '?')}  "
              f"ConsecLosses: {extra.get('consecutive_losses', 0)}")

        # ── ML decision ───────────────────────────────────────────────────────
        dec    = predict_decision(analysis, entry_data, consensus, "1h", extra)
        action = dec["action"]
        conf   = dec["confidence"]
        p      = dec["probs"]

        print(f"\n  ┌── ML DECISION ──────────────────────────────────────────")
        print(f"  │  Action    : {action.upper()}")
        print(f"  │  Confidence: {conf:.2f} ({conf*100:.0f}%)")
        print(f"  │  buy={p['buy']:.2f}  sell={p['sell']:.2f}  no_trade={p['no_trade']:.2f}")

        # Would this trade be taken?
        if action == "no_trade":
            verdict = "NO TRADE — model sees unfavourable conditions"
        elif action == bias:
            verdict = f"TRADE APPROVED — model agrees with SMC ({action.upper()})"
        else:
            verdict = f"BLOCKED — ML says {action.upper()}, SMC says {(bias or '?').upper()} (conflict)"

        print(f"  │  Verdict   : {verdict}")
        print(f"  └─────────────────────────────────────────────────────────\n")

        results.append({
            "tf": tf, "smc_bias": bias, "ml_action": action,
            "confidence": conf, "ta_score": ta_score, "consensus": consensus
        })

    except Exception as e:
        print(f"  ERROR on {tf}: {e}")
        import traceback; traceback.print_exc()

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"{'='*60}")
print(f"  SUMMARY for {SYMBOL} @ {price:.2f}")
print(f"  HTF consensus: {consensus.upper()}")
print()
for r in results:
    smc = (r["smc_bias"] or "none").upper()
    ml  = r["ml_action"].upper()
    agree = "✓" if smc.lower() == r["ml_action"] else "✗"
    print(f"  {r['tf']:>3}  SMC={smc:<5}  ML={ml:<9}  conf={r['confidence']:.2f}  {agree}")
print(f"{'='*60}\n")
