"""
test_cnn.py — Test CNN candle model + MLP ensemble on live market data.

Run: venv\Scripts\python.exe test_cnn.py
MT5 must be open.
"""
import sys
import warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.config import runtime
runtime.port       = 8000
runtime.db_path    = "crypto_oracle_8000.db"
runtime.ml_db_path = "crypto_oracle_ml.db"
runtime.instance   = "cnn_test"
runtime.mt5_path   = None

import MetaTrader5 as mt5
from features.trading.router import _mt5_init, get_htf_trend, get_extra_features
from features.trading.ml_model import load_model, predict_decision
from features.trading.cnn_model import load_cnn, predict_cnn, get_cnn_info
from features.market.fetcher import fetch_candles
from features.smc.engine import run_scalping_analysis

_mt5_init()
load_model()
cnn_loaded = load_cnn()

print("\n" + "="*65)
print("  ENSEMBLE TEST — MLP (TA features) + CNN (candle patterns)")
print(f"  CNN model: {'LOADED' if cnn_loaded else 'NOT FOUND — run train_cnn.py first'}")
print("="*65)

SYMBOLS  = ["BTC", "ETH", "EURUSD"]
SCAN_TFS = ["15m", "5m"]
HTF_TFS  = ["5m", "1h"]

for symbol in SYMBOLS:
    # Resolve tick
    tick_sym = None
    for c in [symbol, symbol + "USDm", symbol + "USDT", symbol + "USD"]:
        t = mt5.symbol_info_tick(c)
        if t:
            price = (t.bid + t.ask) / 2
            tick_sym = c
            break
    if not tick_sym:
        print(f"\n[{symbol}] Cannot get tick — skipping")
        continue

    print(f"\n{'─'*65}")
    print(f"  {symbol}  ({tick_sym})  price={price:.5g}")
    print(f"{'─'*65}")

    htf_result = get_htf_trend(symbol, HTF_TFS)
    consensus  = htf_result.get("consensus", "neutral")
    print(f"  HTF consensus: {consensus.upper()}  {htf_result.get('trends', {})}")

    # CNN on live candles (independent of SMC analysis)
    cnn = predict_cnn(symbol)
    if cnn["ready"]:
        print(f"\n  CNN (50×1h candles):")
        print(f"    action={cnn['action'].upper():<10} conf={cnn['confidence']:.2f}"
              f"  buy={cnn['probs']['buy']:.2f}  sell={cnn['probs']['sell']:.2f}"
              f"  no_trade={cnn['probs']['no_trade']:.2f}")
    else:
        print(f"\n  CNN: not ready (train first with train_cnn.py)")

    for tf in SCAN_TFS:
        print(f"\n  {tf}:")
        try:
            df       = fetch_candles(symbol, tf, limit=200)
            analysis = run_scalping_analysis(df, symbol)
            bias     = analysis.get("bias", "unknown") or "none"
            ta_score = analysis.get("scalping_score", 0)
            print(f"    SMC bias={bias.upper():<5}  score={ta_score}")

            entry_data = {"entry": price, "rr_ratio": analysis.get("rr_ratio") or 2.0}
            extra      = get_extra_features(symbol, analysis, price)
            htf_tf_str = HTF_TFS[0]

            # Full ensemble call (MLP + CNN)
            dec = predict_decision(
                analysis, entry_data, consensus, htf_tf_str, extra, symbol=symbol
            )
            a, conf = dec["action"], dec["confidence"]
            p       = dec["probs"]
            cnn_r   = dec.get("cnn") or {}

            print(f"    MLP buy={p['buy']:.2f}  sell={p['sell']:.2f}  no_trade={p['no_trade']:.2f}")
            if cnn_r.get("ready"):
                cnn_a = cnn_r["action"]
                cnn_c = cnn_r["confidence"]
                agree = "✓ AGREE" if cnn_a == a else ("~ neutral" if cnn_a == "no_trade" else "✗ CONFLICT")
                print(f"    CNN {cnn_a.upper():<10} conf={cnn_c:.2f}  [{agree}]")

            if a == "no_trade":
                verdict = "NO TRADE"
            elif a == bias:
                verdict = f"TRADE ✓  ({a.upper()})"
            else:
                verdict = f"BLOCKED  (ensemble={a.upper()} vs SMC={bias.upper()})"

            print(f"    ► {verdict}  (final_conf={conf:.2f})")

        except Exception as e:
            print(f"    ERROR: {e}")
            import traceback; traceback.print_exc()

print(f"\n{'='*65}")
info = get_cnn_info()
print(f"  CNN status: trained={info.get('trained')} "
      f"samples={info.get('samples','?')} "
      f"accuracy={info.get('accuracy_pct','?')}%")
print(f"{'='*65}\n")
