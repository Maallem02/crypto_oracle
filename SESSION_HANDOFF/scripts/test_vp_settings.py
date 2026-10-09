"""VP settings from the app: validation, all-or-nothing, persistence, setup
toggles, daily loss limit. Live state file and MT5 are NOT touched."""
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import numpy as np

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.volume_profile.runner as R
from features.volume_profile import strategy as S
from features.volume_profile.profile import Profile

SCRATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vp_state_test.json")
R._STATE_FILE = SCRATCH
R.ensure_mt5 = lambda: True
LOT_RULES = {"BTCUSDm": (0.01, 0.01, 200), "ETHUSDm": (0.10, 0.01, 2000),
             "XAGUSDm": (0.01, 0.01, 200), "XAUUSDm": (0.01, 0.01, 200)}   # min, step, max
deals = []
R.mt5 = NS(
    symbol_info=lambda m: (NS(volume_min=LOT_RULES[m][0], volume_step=LOT_RULES[m][1],
                              volume_max=LOT_RULES[m][2]) if m in LOT_RULES else None),
    positions_get=lambda **k: [],
    history_deals_get=lambda a, b: deals,
    account_info=lambda: NS(balance=100.0),
)
fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


def reset():
    R.vp_state["settings"] = R._copy(R.DEFAULT_SETTINGS)
    if os.path.exists(SCRATCH):
        os.remove(SCRATCH)


def rejected(patch):
    before = R._copy(R.vp_state["settings"])
    try:
        R.update_settings(patch)
        return False, "accepted", before
    except R.SettingsError as e:
        return True, str(e), before


print("=" * 80)
print("AVAILABLE SYMBOLS = known to the bot AND offered by the broker")
print("=" * 80)
av = R.available_symbols()
check("gold offered to VP (only blocked for the Scalping bot)", "XAUUSD" in av)
check("symbols the broker lacks are not offered (SOL, GBPJPY...)", "SOL" not in av and "GBPJPY" not in av,
      f"{sorted(av)}")

print("\n" + "=" * 80)
print("VALID CHANGES")
print("=" * 80)
reset()
st = R.update_settings({"symbols": ["btc", "XAUUSD"], "lot_sizes": {"XAUUSD": 0.03},
                        "risk_cap_pct": 10, "daily_loss_pct": 20,
                        "setups": {"poc_bounce": False}})
s = R.vp_state["settings"]
check("symbols updated (and upper-cased)", s["symbols"] == ["BTC", "XAUUSD"], f"{s['symbols']}")
check("new lot saved, old lots kept for re-enabling later",
      s["lot_sizes"]["XAUUSD"] == 0.03 and s["lot_sizes"]["ETH"] == 0.5)
check("risk cap and daily loss saved", s["risk_cap_pct"] == 10 and s["daily_loss_pct"] == 20)
check("POC bounce off, the other setups untouched",
      s["setups"] == {"poc_bounce": False, "va_reversal": True, "va_breakout": True})
saved = json.load(open(SCRATCH))
check("written to disk", saved["settings"]["symbols"] == ["BTC", "XAUUSD"])
en, reloaded = R._load_state()
check("survives a restart (re-read from disk)", reloaded == s)
R.update_settings({"lot_sizes": {"BTC": 0.123}})
check("lot rounded to the broker step (0.123 -> 0.12)",
      R.vp_state["settings"]["lot_sizes"]["BTC"] == 0.12, f"{R.vp_state['settings']['lot_sizes']['BTC']}")

print("\n" + "=" * 80)
print("REFUSED CHANGES - and nothing is modified (all or nothing)")
print("=" * 80)
reset()
for label, patch, needle in [
    ("ETH lot below the broker minimum (0.05 < 0.10)", {"lot_sizes": {"ETH": 0.05}}, "minimum"),
    ("symbol the broker does not offer", {"symbols": ["BTC", "SOL"]}, "not available"),
    ("enabling a symbol with no lot size", {"symbols": ["BTC", "XAUUSD"]}, "set a lot size"),
    ("risk cap 80% (max 50%)", {"risk_cap_pct": 80}, "between"),
    ("daily loss -5%", {"daily_loss_pct": -5}, "between"),
    ("unknown setup name", {"setups": {"magic_setup": True}}, "setups must"),
    ("lot that is not a number", {"lot_sizes": {"BTC": "abc"}}, "number"),
]:
    was_rejected, msg, before = rejected(patch)
    check(label, was_rejected and needle in msg and R.vp_state["settings"] == before, msg[:70])
was_rejected, msg, before = rejected({"risk_cap_pct": 12, "lot_sizes": {"ETH": 0.05}})
check("one bad field rejects the whole change (cap stays 15, not 12)",
      was_rejected and R.vp_state["settings"]["risk_cap_pct"] == 15.0)

print("\n" + "=" * 80)
print("SETUP SWITCHES REACH THE STRATEGY")
print("=" * 80)
DT = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"), ("tick_volume", "i8")]
b = np.array([(1_700_000_000 + i * 900, *r, 100) for i, r in enumerate(
    [(104, 104.5, 103.5, 104), (104.5, 106.2, 104.4, 106), (106, 107.4, 105.9, 107),
     (107, 107.1, 105.2, 105.6), (105.6, 106.4, 105.3, 106.2), (106.2, 107.9, 106, 107.7)])], dtype=DT)
p = Profile(poc=100, vah=105, val=95, low=85, high=115, last_close=100, closed_inside=True,
            bars=100, total_volume=1e4)
a = np.full(len(b), 1.0)
check("breakout ON  -> breakout detected", (S.detect(b, a, p, {"va_breakout": True}) or NS(setup=None)).setup == "va_breakout")
check("breakout OFF -> not detected", S.detect(b, a, p, {"va_breakout": False}) is None)

print("\n" + "=" * 80)
print("VP DAILY LOSS LIMIT - counts VP trades only")
print("=" * 80)
now = datetime.now(timezone.utc)
deals[:] = [NS(magic=234100, profit=-15.0, commission=0.0, swap=0.0),
            NS(magic=234000, profit=-50.0, commission=0.0, swap=0.0)]   # a big SMC loss
check("VP P/L today ignores the Scalping bot's -$50", R.vp_pnl_today(now) == -15.0)
R.vp_state["settings"]["daily_loss_pct"] = 10.0
R._ensure_ready = lambda: None
R.update_outcomes = lambda: None
R.vp_scan()
check("VP lost 13% of its day-start balance with a 10% limit -> paused",
      R.vp_state["paused"] is not None and "daily loss" in R.vp_state["paused"],
      (R.vp_state["paused"] or "")[:75])
R.vp_state["settings"]["daily_loss_pct"] = 0.0
R.vp_state["settings"]["symbols"] = []
R.vp_scan()
check("limit set to 0 (off)                                  -> not paused",
      R.vp_state["paused"] is None)

if os.path.exists(SCRATCH):
    os.remove(SCRATCH)
print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
