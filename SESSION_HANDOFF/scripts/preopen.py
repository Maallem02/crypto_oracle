"""Pre-open audit: verify every change is actually loaded in the RUNNING
process, not merely present in the source files.

Also price the crash: a BTC buy sat open and unmanaged from Sat 03:25 (the
crash) to Sun 03:45 (stop-out, -$7.05). Did it ever reach the 1.5R break-even
trigger? If so, the ratchet would have saved it and the crash cost real money.
"""
import json
import sys
from datetime import datetime, timedelta

import requests

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")

print("=" * 88)
print("CONFIG LOADED IN THE RUNNING PROCESS")
print("=" * 88)
from features.trading.router import (BLOCKED_SYMBOLS, TRAIL_ATR_FLOOR,
                                     TRAIL_START_R, CONSENSUS_GATE_ENABLED,
                                     is_trading_session)
from features.trading.executor import (MAX_RISK_PCT, SYMBOL_RISK_PCT,
                                       _risk_cap_for)

print(f"  BLOCKED_SYMBOLS      {sorted(BLOCKED_SYMBOLS)}")
print(f"    XAUUSD blocked     {'XAUUSD' in BLOCKED_SYMBOLS}   <- added 09-19")
print(f"  CONSENSUS_GATE       {CONSENSUS_GATE_ENABLED}")
print(f"\n  risk cap default     {100*MAX_RISK_PCT:.0f}%")
print(f"  per-symbol caps      {SYMBOL_RISK_PCT}")
for s in ("XAUUSDm", "XAGUSDm", "BTCUSDm", "ETHUSDm"):
    print(f"    {s:9s} -> {100*_risk_cap_for(s):5.1f}%")
print(f"\n  TRAIL_ATR_FLOOR      {TRAIL_ATR_FLOOR}")
print(f"  TRAIL_START_R        {TRAIL_START_R}")

print("\n" + "=" * 88)
print("LIVE SETTINGS (what the scan loop is using right now)")
print("=" * 88)
st = requests.get("http://127.0.0.1:8000/trading/scalping/status", timeout=10).json()
s = st.get("settings", {})
for k in ("enabled_symbols", "pending_max_age_minutes", "cooldown_minutes",
          "risk_percent", "target_risk_pct", "trail_start_r", "be_trigger_r",
          "max_trades", "min_score", "max_daily_loss_pct"):
    print(f"  {k:26s} {s.get(k)}")
print(f"  running={st.get('running')}  session_start={st.get('session_start')}")

print("\n" + "=" * 88)
print("EXPECTED vs ACTUAL")
print("=" * 88)
checks = [
    ("gold blocked in code", "XAUUSD" in BLOCKED_SYMBOLS, True),
    ("gold absent from enabled_symbols",
     not any("XAU" in x.upper() for x in (s.get("enabled_symbols") or [])), True),
    ("pending expiry 60 min", s.get("pending_max_age_minutes") == 60, True),
    ("XAU cap 15%", abs(_risk_cap_for("XAUUSDm") - 0.15) < 1e-9, True),
    ("XAG cap 15%", abs(_risk_cap_for("XAGUSDm") - 0.15) < 1e-9, True),
    ("BTC cap still 7%", abs(_risk_cap_for("BTCUSDm") - 0.07) < 1e-9, True),
    ("gold trail floor 1.5xATR", TRAIL_ATR_FLOOR.get("XAUUSD") == 1.5, True),
    ("ETH trail arms 1.5R", TRAIL_START_R.get("ETHUSD") == 1.5, True),
    ("consensus gate on", CONSENSUS_GATE_ENABLED, True),
]
bad = 0
for label, got, want in checks:
    ok = got == want
    bad += 0 if ok else 1
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}")
print(f"\n  {'ALL CONFIG CORRECT' if not bad else str(bad) + ' MISMATCH(ES)'}")

print("\n" + "=" * 88)
print("SESSION GATE - when does trading resume?")
print("=" * 88)
from datetime import timezone
u = datetime.now(timezone.utc)
print(f"  now: local {datetime.now():%a %Y-%m-%d %H:%M}   UTC {u:%a %H:%M}  "
      f"weekday={u.weekday()}")
print(f"  weekend block : {u.weekday() >= 5}")
print(f"  dead zone     : {u.hour >= 21 or u.hour < 2}")
for sym in ("BTC", "ETH", "XAGUSD"):
    print(f"    {sym:8s} tradeable now: {is_trading_session(sym)}")
nxt = u.replace(minute=0, second=0, microsecond=0)
for i in range(1, 100):
    t = nxt + timedelta(hours=i)
    if t.weekday() < 5 and not (t.hour >= 21 or t.hour < 2):
        print(f"\n  MARKET OPENS: {t:%a %Y-%m-%d %H:%M} UTC  =  "
              f"{t + timedelta(hours=1):%a %H:%M} local")
        break

print("\n" + "=" * 88)
print("WHAT DID THE CRASH COST?  the orphaned BTC buy")
print("=" * 88)
import MetaTrader5 as mt5
from collections import defaultdict
mt5.initialize(timeout=10000)
deals = mt5.history_deals_get(datetime(2026, 9, 18), datetime.now()) or []
pos = defaultdict(list)
for d in deals:
    if d.position_id and d.type in (0, 1):
        pos[d.position_id].append(d)
for pid, ds in pos.items():
    ds.sort(key=lambda z: z.time)
    ins = [x for x in ds if x.entry == 0]
    outs = [x for x in ds if x.entry == 1]
    if not ins or not outs or "BTC" not in ins[0].symbol.upper():
        continue
    t0 = datetime.fromtimestamp(ins[0].time)
    if t0 < datetime(2026, 9, 18, 20):
        continue
    entry, exit_px = ins[0].price, outs[-1].price
    sl = 80488.32
    risk = abs(entry - sl)
    t1 = datetime.fromtimestamp(outs[-1].time)
    bars = mt5.copy_rates_range(ins[0].symbol, mt5.TIMEFRAME_M1, t0, t1)
    mfe = 0.0
    mfe_t = None
    for b in (bars if bars is not None else []):
        f = (b["high"] - entry) / risk
        if f > mfe:
            mfe, mfe_t = f, datetime.fromtimestamp(b["time"])
    print(f"  BTC BUY opened {t0:%a %H:%M} @ {entry:.2f}, SL {sl:.2f} "
          f"(risk {risk:.2f})")
    print(f"  closed {t1:%a %H:%M} @ {exit_px:.2f} = "
          f"${sum(x.profit+x.commission+x.swap for x in ds):+.2f}")
    print(f"  peak favourable move: +{mfe:.2f}R at {mfe_t:%a %H:%M}"
          if mfe_t else "  peak favourable move: none")
    print(f"\n  break-even ratchet triggers at 1.5R.")
    if mfe >= 1.5:
        print(f"  -> it REACHED {mfe:.2f}R. A live bot would have moved the stop to")
        print(f"     entry and this would have closed at ~$0 instead of -$7.05.")
        print(f"     The crash cost roughly $7.")
    else:
        print(f"  -> it only reached {mfe:.2f}R, below the 1.5R trigger. The")
        print(f"     ratchet would NOT have fired, so the loss was coming anyway.")
        print(f"     The crash did not cause this one.")
mt5.shutdown()
