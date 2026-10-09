"""SURVIVAL halt: deposits / withdrawals rescale the peak instead of counting
as losses. Fake MT5 deals - nothing reaches the broker, risk_state.json and
the settings file are not written."""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.trading.router as R

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


saves = []
R._save_risk_state = lambda d: saves.append(dict(d))
R._persist_settings = lambda d: None

NOW = int(datetime.now(timezone.utc).timestamp())
deals = []
_t = [0]


def deal(minutes_ago, amount, typ=2, ticket=None):
    _t[0] += 1
    ts = NOW - int(minutes_ago * 60)
    return NS(ticket=ticket or 1000 + _t[0], time=ts, time_msc=ts * 1000, type=typ,
              profit=amount, commission=0.0, swap=0.0, fee=0.0)


calls = {"n": 0}


def hist(a, b):
    calls["n"] += 1
    return [d for d in deals if a <= d.time <= b]


R.mt5 = NS(history_deals_get=hist, DEAL_TYPE_BALANCE=2, DEAL_TYPE_CREDIT=3, DEAL_TYPE_BONUS=6)


def setup(peak, since_min_ago, ds, seen=()):
    deals[:] = ds
    R.scalp_state["peak_equity"] = peak
    R.risk_state.clear()
    R.risk_state.update({"login": 262890622, "peak_equity": peak,
                         "flow_since": NOW - since_min_ago * 60, "flow_seen": list(seen)})
    saves.clear()


def dd(eq):
    pk = R.scalp_state["peak_equity"]
    return (eq - pk) / pk * 100


print("THE 04/10 CASE (peak 134.71, ETH +2.59, withdrawal -31.05, then 3 SMC losses)")
setup(134.71, 2000, [deal(1100, 2.59, typ=1), deal(1099, -31.05), deal(300, -7.00, typ=0),
                     deal(250, -6.51, typ=1), deal(248, -6.51, typ=1)])
acct = NS(login=262890622, balance=79.98, equity=79.98)
R._apply_cash_flows(acct)
check("peak rescaled 134.71 * 100/131.05 = 102.79", abs(R.scalp_state["peak_equity"] - 102.79) < 0.01,
      str(R.scalp_state["peak_equity"]))
check(f"drawdown now {dd(79.98):.1f}% -> no halt (was -40.6% -> halt)", dd(79.98) > -R.SURVIVAL_HALT_PCT)
check("persisted: peak + ticket seen", saves and saves[-1]["peak_equity"] == R.scalp_state["peak_equity"]
      and len(saves[-1]["flow_seen"]) == 1)
pk1 = R.scalp_state["peak_equity"]
R._apply_cash_flows(acct)
check("second call -> applied once only", R.scalp_state["peak_equity"] == pk1)

print("\nDRAWDOWN % IS THE SAME JUST BEFORE AND JUST AFTER A FLOW")
setup(200.0, 60, [deal(10, 100.0)])
R._apply_cash_flows(NS(login=1, balance=240.0, equity=240.0))
check(f"deposit 100 while -30%: still {dd(240):.1f}% (a deposit does not hide the loss)",
      abs(dd(240) + 30) < 0.01, str(R.scalp_state["peak_equity"]))
setup(200.0, 60, [deal(10, -100.0)])
R._apply_cash_flows(NS(login=1, balance=40.0, equity=40.0))
check(f"withdraw 100 while -30%: still {dd(40):.1f}% (a plain 'peak - 100' would give -60% = halt)",
      abs(dd(40) + 30) < 0.01, str(R.scalp_state["peak_equity"]))
setup(150.0, 60, [deal(30, -50.0), deal(20, -10.0, typ=0), deal(10, 40.0)])
R._apply_cash_flows(NS(login=1, balance=130.0, equity=130.0))
# 150 bal: -50 -> 100 (x 2/3 -> 100), loss -10 -> 90, +40 -> 130 (x 130/90 -> 144.44)
check("two flows with a trade between -> each one at its own balance",
      abs(R.scalp_state["peak_equity"] - 144.44) < 0.01, str(R.scalp_state["peak_equity"]))

print("\nWHAT IS NOT A FLOW")
setup(120.0, 60, [deal(10, -15.0, typ=0), deal(9, 5.0, typ=1), deal(8, 0.0)])
R._apply_cash_flows(NS(login=1, balance=110.0, equity=110.0))
check("trades and a zero-amount balance deal -> peak unchanged", R.scalp_state["peak_equity"] == 120.0)
setup(120.0, 5, [deal(10, -50.0)])
R._apply_cash_flows(NS(login=1, balance=70.0, equity=70.0))
check("a withdrawal BEFORE the current peak was set -> ignored (already in the base)",
      R.scalp_state["peak_equity"] == 120.0)
setup(120.0, 60, [deal(10, -50.0, ticket=77)], seen=[77])
R._apply_cash_flows(NS(login=1, balance=70.0, equity=70.0))
check("ticket already applied (e.g. before a crash) -> not applied again", R.scalp_state["peak_equity"] == 120.0)
setup(120.0, 60, [deal(10, -20.0, typ=3)])
R._apply_cash_flows(NS(login=1, balance=100.0, equity=100.0))
check("credit removed -> counted as a flow too", abs(R.scalp_state["peak_equity"] - 100.0) < 0.01)

print("\nEDGE CASES")
setup(50.0, 60, [deal(10, 100.0)])
R._apply_cash_flows(NS(login=1, balance=100.0, equity=100.0))
check("deposit into an empty account -> new base = balance", R.scalp_state["peak_equity"] == 100.0)
setup(120.0, 60, [deal(10, -50.0)])
R.risk_state.pop("flow_since")
R._apply_cash_flows(NS(login=1, balance=70.0, equity=70.0))
check("state from before this fix (no flow_since) -> marker set, peak unchanged",
      R.scalp_state["peak_equity"] == 120.0 and abs(R.risk_state.get("flow_since", 0) - NOW) < 5)
setup(120.0, 60, [deal(10, -50.0)])
R.mt5.history_deals_get = lambda a, b: (_ for _ in ()).throw(RuntimeError("terminal busy"))
R._apply_cash_flows(NS(login=1, balance=70.0, equity=70.0))
check("MT5 history error -> peak unchanged, no crash", R.scalp_state["peak_equity"] == 120.0)
R.mt5.history_deals_get = hist
R.scalp_state["peak_equity"] = None
R._apply_cash_flows(NS(login=1, balance=70.0, equity=70.0))
check("no peak yet -> nothing to do", R.scalp_state["peak_equity"] is None)

print("\nRESTART FROM THE APP")
R.mt5.account_info = lambda: NS(login=262890622, balance=79.98, equity=79.98)
setup(134.71, 2000, [deal(1099, -31.05), deal(300, -20.02, typ=1)])
R.start_scalping(R.ScalpingSettings(), source="app")
check("withdrawal while stopped -> peak rescaled, NOT reset to equity (protection kept)",
      abs(R.scalp_state["peak_equity"] - 134.71 * 100 / 131.05) < 0.01, str(R.scalp_state["peak_equity"]))
setup(100.0, 60, [deal(30, -40.0, typ=1)])                 # a real -40% loss, no flow
R.mt5.account_info = lambda: NS(login=262890622, balance=60.0, equity=60.0)
R.start_scalping(R.ScalpingSettings(), source="app")
check("real loss past -35% -> new base as before, marker reset",
      R.scalp_state["peak_equity"] == 60.0 and R.risk_state["flow_seen"] == []
      and abs(R.risk_state["flow_since"] - NOW) < 5)
setup(134.71, 2000, [deal(1099, -31.05)])
R.mt5.account_info = lambda: NS(login=262890622, balance=100.0, equity=100.0)
R.start_scalping(R.ScalpingSettings(), source="watchdog")
check("watchdog restart -> state untouched (the scan applies flows)", R.scalp_state["peak_equity"] == 134.71)
R.scalp_state["running"] = False

print("\nNEW ACCOUNT")
R.risk_state.clear(); R.risk_state.update({"login": 111, "peak_equity": 500.0})
R._bind_risk_state_to_account(222, 70.0)
check("login change -> peak = equity and flow marker set",
      R.risk_state["peak_equity"] == 70.0 and R.risk_state["flow_seen"] == []
      and abs(R.risk_state["flow_since"] - NOW) < 5)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
