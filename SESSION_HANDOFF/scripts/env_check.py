"""Why doesn't ensure_mt5() log the terminal back into the demo account?

ensure_mt5() only re-logs when MT5_LOGIN, MT5_PASSWORD and MT5_SERVER are all
present. Report which keys dotenv actually PARSES and whether each is
non-empty. Never print the password value.
"""
from dotenv import dotenv_values

v = dotenv_values(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend\.env")
for k in ("MT5_LOGIN", "MT5_SERVER", "MT5_PASSWORD"):
    val = v.get(k)
    if val is None:
        print(f"  {k:13s} NOT PARSED")
    elif k == "MT5_PASSWORD":
        print(f"  {k:13s} present, {'non-empty' if val.strip() else 'EMPTY'} (value hidden)")
    else:
        print(f"  {k:13s} = {val!r}")
odd = [k for k in v if k.strip() != k or "MT5" in k and k not in
       ("MT5_LOGIN", "MT5_SERVER", "MT5_PASSWORD", "MT5_PATH")]
if odd:
    print(f"  keys parsed with stray characters: {odd}")
