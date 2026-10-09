"""User restarted the bot on the real account and changed lot sizes.
Check: right account, new code loaded, lot sizes valid for the broker and
affordable under the caps at the CURRENT balance, protections armed, no errors.
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

HERE = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
sys.path.insert(0, HERE)
import MetaTrader5 as mt5

mt5.initialize(timeout=10000)
a = mt5.account_info()
print("=" * 90)
print("ACCOUNT")
print("=" * 90)
print(f"  {a.login}  {a.server}  {'REAL' if a.trade_mode == 2 else 'DEMO'}  "
      f"balance ${a.balance:.2f}  equity ${a.equity:.2f}  margin free ${a.margin_free:.2f}")
for p in (mt5.positions_get() or []):
    print(f"  POS {p.symbol} {'BUY' if p.type == 0 else 'SELL'} vol={p.volume} "
          f"PL={p.profit:+.2f} SL={p.sl} {'(bot)' if p.magic == 234000 else '(manual)'}")
for o in (mt5.orders_get() or []):
    print(f"  ORD {o.symbol} @ {o.price_open} vol={o.volume_current} "
          f"{'(bot)' if o.magic == 234000 else '(manual)'}")

st = requests.get("http://127.0.0.1:8000/trading/scalping/status", timeout=10).json()
s = st.get("settings", {})
ls = st.get("last_scan")
age = (datetime.now() - datetime.fromisoformat(ls)).total_seconds() / 60 if ls else None
print("\n" + "=" * 90)
print("BOT")
print("=" * 90)
print(f"  running={st.get('running')}  stopped_reason={st.get('stopped_reason')}")
print(f"  session_start={st.get('session_start')}   last scan "
      f"{f'{age:.1f} min ago' if age is not None else 'none yet'}")
print(f"  trades_today={st.get('trades_today')}  daily_pnl={st.get('daily_pnl')}  "
      f"peak_equity={st.get('peak_equity')}")
print(f"  enabled_symbols={s.get('enabled_symbols')}")
print(f"  max_daily_loss_pct={s.get('max_daily_loss_pct')}  "
      f"pending_max_age={s.get('pending_max_age_minutes')}  "
      f"pending_entry_enabled={s.get('pending_entry_enabled')}")
print(f"  risk_percent={s.get('risk_percent')}  target_risk_pct={s.get('target_risk_pct')}")

print("\n" + "=" * 90)
print("NEW CODE LOADED IN THE RUNNING PROCESS?")
print("=" * 90)
import subprocess
out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True).stdout
out = out.decode("utf-8", errors="replace")
pid = next((int(l.split()[4]) for l in out.splitlines()
            if len(l.split()) >= 5 and l.split()[3].upper() == "LISTENING"
            and l.split()[1].endswith(":8000")), None)
exe_mtime = datetime.fromtimestamp(os.path.getmtime(
    os.path.join(HERE, "features", "trading", "executor.py")))
print(f"  backend pid {pid}")
try:
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         f"(Get-Process -Id {pid}).StartTime.ToString('yyyy-MM-dd HH:mm:ss')"],
                        capture_output=True, text=True, timeout=20).stdout.strip()
    started = datetime.strptime(ps, "%Y-%m-%d %H:%M:%S")
    print(f"  process started   {started:%H:%M:%S}")
    print(f"  executor.py edited {exe_mtime:%H:%M:%S}  (15% crypto cap)")
    print(f"  -> {'LOADED - process started after the edit' if started > exe_mtime else 'NOT LOADED - process is older than the edit'}")
except Exception as e:
    print(f"  could not read process start time: {e}")

print("\n" + "=" * 90)
print("LOT SIZES vs BROKER RULES and vs THE CAP AT TODAY'S BALANCE")
print("=" * 90)
from features.trading.executor import _risk_cap_for, SYMBOL_MAP
lots = s.get("lot_sizes", {})
# typical stop distance in PRICE units, measured on this week's signals
TYPICAL_STOP = {"BTC": 530.0, "ETH": 19.5, "XAGUSD": 0.19}
bal = a.balance
problems = 0
print(f"  {'sym':7s} {'lot':>6s} {'broker min':>11s} {'step':>6s} {'max':>7s} "
      f"{'cap%':>5s} {'cap$':>7s} {'typical risk$':>14s}  verdict")
for sym in s.get("enabled_symbols", []):
    lot = float(lots.get(sym, 0) or 0)
    mt5sym = SYMBOL_MAP.get(sym.upper(), sym)
    si = mt5.symbol_info(mt5sym)
    if si is None:
        print(f"  {sym:7s}  symbol {mt5sym} NOT FOUND on this account")
        problems += 1
        continue
    cap_pct = _risk_cap_for(mt5sym)
    cap = bal * cap_pct
    risk = lot * si.trade_contract_size * TYPICAL_STOP.get(sym.upper(), 0)
    issues = []
    if lot == 0:
        issues.append("lot 0 = AUTO sizing")
    elif lot < si.volume_min:
        issues.append(f"BELOW broker min {si.volume_min}")
    elif abs(round(lot / si.volume_step) * si.volume_step - lot) > 1e-9:
        issues.append(f"not a multiple of step {si.volume_step}")
    if lot > si.volume_max:
        issues.append("above broker max")
    if lot and risk > cap:
        issues.append(f"typical trade REJECTED (needs ${risk:.2f})")
    verdict = "OK" if not issues else "; ".join(issues)
    problems += 0 if not issues else 1
    print(f"  {sym:7s} {lot:6.2f} {si.volume_min:11.2f} {si.volume_step:6.2f} "
          f"{si.volume_max:7.1f} {100*cap_pct:4.0f}% {cap:7.2f} {risk:14.2f}  {verdict}")
mt5.shutdown()

print("\n" + "=" * 90)
print("PROTECTIONS")
print("=" * 90)
try:
    rs = json.load(open(os.path.join(HERE, "features", "trading", "risk_state.json")))
    pk = rs.get("peak_equity")
    print(f"  risk_state: login {rs.get('login')}  peak ${pk}  day_anchor {rs.get('day_anchor')}")
    print(f"  bound to THIS account: {rs.get('login') == a.login}")
    if pk:
        halt = pk * 0.65
        print(f"  -35% halt fires at ${halt:.2f}   equity ${a.equity:.2f}   "
              f"headroom ${a.equity - halt:.2f}")
except Exception as e:
    print(f"  risk_state unreadable: {e}")

print("\n" + "=" * 90)
print("ERRORS IN THE NEWEST BACKEND LOG")
print("=" * 90)
logs = sorted([f for f in os.listdir(HERE) if f.startswith("backend_8000_") and f.endswith(".log")],
              key=lambda f: os.path.getmtime(os.path.join(HERE, f)))
if logs:
    newest = logs[-1]
    print(f"  {newest}  (modified {datetime.fromtimestamp(os.path.getmtime(os.path.join(HERE, newest))):%H:%M:%S})")
    for suffix in ("", ".err"):
        path = os.path.join(HERE, newest + suffix)
        if not os.path.exists(path):
            continue
        txt = open(path, encoding="utf-8", errors="replace").read().splitlines()
        bad = [l for l in txt if any(k in l for k in
               ("Traceback", "Error", "ERROR", "Exception", "[ERR", "persist error",
                "RISK-STATE", "FATAL"))
               and "DeprecationWarning" not in l and "UserWarning" not in l]
        print(f"  {os.path.basename(path)}: {len(bad)} notable line(s)")
        for l in bad[-8:]:
            print(f"    {l.strip()[:140]}")
print(f"\n  TOTAL LOT/SYMBOL PROBLEMS: {problems}")
