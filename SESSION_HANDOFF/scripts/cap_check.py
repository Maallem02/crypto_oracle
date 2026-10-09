import importlib.util
import sys

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
spec = importlib.util.spec_from_file_location(
    "ex", r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\features\trading\executor.py")
ex = importlib.util.module_from_spec(spec)
sys.modules["ex"] = ex
spec.loader.exec_module(ex)

BAL = 70.0
print("caps at $70:")
for s in ("BTCUSDm", "ETHUSDm", "XAGUSDm", "XAUUSDm", "EURUSDm"):
    p = ex._risk_cap_for(s)
    print(f"  {s:9s} {100*p:5.1f}%  = ${BAL*p:5.2f}")

print("\ntoday's rejected crypto setups vs the new cap:")
fails = 0
for sym, risk in (("ETH", 9.60), ("ETH", 9.54), ("ETH", 7.80), ("BTC", 5.41), ("BTC", 5.12)):
    cap = BAL * ex._risk_cap_for(sym + "USDm")
    ok = risk <= cap
    fails += not ok
    print(f"  {sym} risk ${risk:5.2f}  vs cap ${cap:5.2f}  ->  {'PASSES' if ok else 'STILL BLOCKED'}")
print("\nALL PASS" if not fails else f"\n{fails} still blocked")
