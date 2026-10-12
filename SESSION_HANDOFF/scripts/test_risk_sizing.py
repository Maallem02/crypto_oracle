"""Percentage-risk sizing (2026-10-10) for the Scalping and Volume Profile bots.
Fake MT5 only: nothing reaches the broker; vp_state.json, the scalping settings
file and the database are not touched."""
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.trading.executor as EX
import features.trading.router as RT
import features.volume_profile.runner as VP

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


ACCT = {"balance": 5000.0}
SYMS = {"BTCUSDm": dict(volume_min=0.01, volume_step=0.01, volume_max=200, value=1.0),       # $1 per point per lot
        "ETHUSDm": dict(volume_min=0.1, volume_step=0.01, volume_max=2000, value=1.0),
        "USDJPYm": dict(volume_min=0.01, volume_step=0.01, volume_max=200, value=632.0)}     # ~$632 per 1.000 yen per lot


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1

    def symbol_info(self, s):
        x = SYMS.get(s)
        return None if x is None else NS(**{k: v for k, v in x.items() if k != "value"})

    def account_info(self):
        return NS(balance=ACCT["balance"], equity=ACCT["balance"], login=1)

    def order_calc_profit(self, t, s, vol, a, b):
        return ((b - a) if t == 0 else (a - b)) * vol * SYMS[s]["value"]


EX.mt5 = FakeMT5()

print("1. size_by_risk (shared helper)")
lot, info = EX.size_by_risk("BTCUSDm", "buy", 82000.0, 81000.0, 1.0)
check("$5,000, 1%, BTC buy, stop $1,000 away -> 0.05 lot ($50)", lot == 0.05, str(info))
lot, info = EX.size_by_risk("BTCUSDm", "sell", 82000.0, 82700.0, 2.0)
check("$5,000, 2%, BTC sell, stop $700 above -> 0.14 lot ($98 <= $100)", lot == 0.14, f"lot {lot}")
lot, info = EX.size_by_risk("USDJPYm", "buy", 158.0, 157.8, 1.0)
check("USDJPY uses MT5's own $ value (0.200 yen x $632/yen/lot) -> 0.39 lot", lot == 0.39, f"lot {lot}")
ACCT["balance"] = 100.0
lot, info = EX.size_by_risk("BTCUSDm", "buy", 82000.0, 81000.0, 1.0)
check("$100, 1%, BTC stop $1,000 -> REFUSED (min lot would risk $10 = 10%)", lot is None, info.get("reason", ""))
check("... the reason names the account size", "account too small" in info.get("reason", ""))
lot, info = EX.size_by_risk("BTCUSDm", "buy", 82000.0, 82100.0, 1.0)
check("stop on the wrong side -> refused", lot is None, info.get("reason", ""))
ACCT["balance"] = 5000.0

print("\n2. Scalping settings")
d = RT.ScalpingSettings().dict()
check("defaults: sizing 'risk', 1% per trade", d["sizing_mode"] == "risk" and d["risk_per_trade_pct"] == 1.0)
for bad in ({"sizing_mode": "lots"}, {"risk_per_trade_pct": 6}, {"risk_per_trade_pct": 0.05}):
    try:
        RT.ScalpingSettings(**bad)
        check(f"{bad} refused", False)
    except Exception as e:
        check(f"{bad} refused", True, str(e).splitlines()[0])
check("an app that does not send the new fields gets the safe defaults",
      RT.ScalpingSettings(**{"lot_sizes": {"BTC": 0.02}}).dict()["sizing_mode"] == "risk")

print("\n3. Scalping helpers")
risk = {"sizing_mode": "risk", "risk_per_trade_pct": 1.0, "pending_entry_enabled": True,
        "target_risk_pct": 7.0, "risk_cap_pct": 30}
fixed = {"target_risk_pct": 7.0, "risk_cap_pct": 30, "pending_entry_enabled": True}
check("risk mode: cap = 1% + 10% margin, limit entries target 1%",
      abs(RT._smc_risk_cap(risk) - 0.011) < 1e-12 and RT._smc_target_risk(risk) == 1.0)
check("settings without the key keep the OLD behaviour (cap 30%, target 7%)",
      abs(RT._smc_risk_cap(fixed) - 0.30) < 1e-12 and RT._smc_target_risk(fixed) == 7.0)
RT.size_by_risk = EX.size_by_risk
lot, min_only, skip = RT._smc_risk_lot(risk, "BTC", "buy", 82000.0, 81000.0)
check("$5,000: BTC buy sized to 0.05 lot, market entry allowed", (lot, min_only, skip) == (0.05, False, None))
ACCT["balance"] = 100.0
lot, min_only, skip = RT._smc_risk_lot(risk, "BTC", "buy", 82000.0, 81000.0)
check("$100: BTC BUY -> minimum lot, LIMIT ENTRY ONLY (closer to the stop)", (lot, min_only, skip) == (0.01, True, None))
lot, min_only, skip = RT._smc_risk_lot(risk, "BTC", "sell", 82000.0, 83000.0)
check("$100: BTC SELL -> skipped (no limit entries for sells)", lot == 0 and skip and "account too small" in skip, skip)
lot, min_only, skip = RT._smc_risk_lot({**risk, "pending_entry_enabled": False}, "BTC", "buy", 82000.0, 81000.0)
check("$100: BTC buy with limit entries OFF -> skipped", lot == 0 and bool(skip))
ACCT["balance"] = 5000.0

print("\n4. Volume Profile scan in risk mode (fake broker)")
placed, logged = [], []
VP._persist = lambda: None
PROF = NS(poc=100.0, vah=110.0, val=90.0, closed_inside=True)
VP.ensure_mt5 = VP._ensure_ready = VP.update_outcomes = lambda: None
VP.previous_session_profile = lambda s, d: ("2026-10-09", PROF)
VP._levels_logged.add("BTC|2026-10-09")
VP.session_bars = lambda s, n: ([1, 2, 3], [1, 1, 1])
VP.is_news_blocked = lambda s: {"blocked": False}
VP._log = lambda sym, status, **k: logged.append((status, k.get("reason")))
VP.live_risk_problem = lambda sig, px: None
VP.earlier_on_edge = lambda *a: 0
VP.size_by_risk = EX.size_by_risk


class VPMT5(FakeMT5):
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1

    def positions_get(self, symbol=None):
        return []

    def symbol_info_tick(self, s):
        return NS(ask=100.0, bid=100.0)


VP.mt5 = VPMT5()
VP.place_trade = lambda **k: (placed.append(k), {"success": True, "ticket": 1, "price": 100, "sl": 0,
                                                   "tp": 0, "volume": k["fixed_lot"]})[1]
cnt = {"t": 4_000_000}


def fake_detect(*a, **k):
    cnt["t"] += 900
    return NS(setup="va_reversal", direction="buy", entry=100.0, sl=90.0, tp=120.0, trigger_time=cnt["t"],
              reason="test", blocked=None, detail={"atr": 5.0}, risk=10.0)


VP.detect = fake_detect
SYMS["BTCUSDm"]["value"] = 1.0
base = {"symbols": ["BTC"], "lot_sizes": {"BTC": 0.02}, "risk_cap_pct": 30.0, "daily_loss_pct": 0.0,
        "setups": {"va_reversal": True}, "max_per_symbol": 1, "same_direction": False, "news_pause": False,
        "min_room_r": 0, "news_close_before_min": 0, "max_spread_pct": 0, "first_per_edge": False}


def scan(settings):
    placed.clear(); logged.clear()
    VP.vp_state["settings"] = settings
    VP.vp_scan()


ACCT["balance"] = 5000.0
scan({**base, "sizing_mode": "risk", "risk_pct": 1.0})
check("risk mode, $5,000, 1%, stop 10 points -> 5 lots ($50), not the app's 0.02",
      placed and placed[0]["fixed_lot"] == 5.0, str(placed[0]["fixed_lot"] if placed else None))
check("... and the broker-side cap is 1% + 10% margin, not the old 30%",
      placed and abs(placed[0]["risk_cap_pct"] - 0.011) < 1e-12)
ACCT["balance"] = 0.5
scan({**base, "sizing_mode": "risk", "risk_pct": 1.0})
check("risk mode, tiny balance -> no order, 'account too small' journalled",
      not placed and any("account too small" in (r or "") for _, r in logged), str(logged[-1:]))
ACCT["balance"] = 5000.0
scan(dict(base))
check("settings without sizing_mode (old file / tests) -> old fixed lot 0.02, cap 30%",
      placed and placed[0]["fixed_lot"] == 0.02 and abs(placed[0]["risk_cap_pct"] - 0.30) < 1e-12)

print("\n5. Volume Profile settings")
VP.available_symbols = lambda: {"BTC": {"min_lot": 0.01, "lot_step": 0.01, "max_lot": 10}}
VP.vp_status = lambda: {"ok": True}
VP.vp_state["settings"] = {**base, "sizing_mode": "risk", "risk_pct": 1.0}
check("default sizing_mode = risk, risk_pct = 1%", VP.DEFAULT_SETTINGS["sizing_mode"] == "risk"
      and VP.DEFAULT_SETTINGS["risk_pct"] == 1.0)
for bad in ({"risk_pct": 7}, {"risk_pct": "1"}, {"sizing_mode": "lots"}):
    try:
        VP.update_settings(bad)
        check(f"{bad} refused", False)
    except VP.SettingsError as e:
        check(f"{bad} refused", True, str(e))
VP.update_settings({"risk_pct": 0.5})
check("0.5% accepted", VP.vp_state["settings"]["risk_pct"] == 0.5)
VP.update_settings({"symbols": ["BTC"], "lot_sizes": {}})
check("risk mode: a symbol without a lot size is accepted (lots unused)", VP.vp_state["settings"]["symbols"] == ["BTC"])

print("\n6. MANUAL LOT PER SYMBOL (2026-10-12): empty = the bot chooses, a typed lot is used as is")
print("  Scalping")
d = RT.ScalpingSettings(manual_lots={"btc": 0.05, "ETH": 0, "XAGUSD": None}).dict()
check("lots typed in the app are kept, empty / 0 = auto (key dropped), symbols upper-cased",
      d["manual_lots"] == {"BTC": 0.05}, str(d["manual_lots"]))
check("an app that does not send manual_lots -> all auto; the OLD lot_sizes do NOT come back",
      RT.ScalpingSettings(lot_sizes={"BTC": 0.02}).dict()["manual_lots"] == {})
for bad in ({"BTC": -0.01}, {"BTC": "0.02"}, {"BTC": True}, ["BTC", 0.02]):
    try:
        RT.ScalpingSettings(manual_lots=bad)
        check(f"manual_lots {bad} refused", False)
    except Exception as e:
        check(f"manual_lots {bad} refused", True, str(e).splitlines()[0])
s_risk = {**risk, "lot_sizes": {"BTC": 0.02, "ETH": 0.5}, "risk_cap_pct": None, "manual_lots": {"BTC": 0.07}}
sz = RT._smc_sizing(s_risk, "BTC")
check("BTC has a manual lot -> this signal uses 0.07 lot, fixed (not risk %) sizing",
      not RT._smc_risk_mode(sz) and sz["lot_sizes"]["BTC"] == 0.07)
check("... broker-side safety = the per-symbol caps (risk_cap None -> 15% crypto / 7% forex), "
      "limit entries aim at target_risk_pct 7%", RT._smc_risk_cap(sz) is None and RT._smc_target_risk(sz) == 7.0)
check("... the global settings are NOT modified", s_risk["sizing_mode"] == "risk" and s_risk["lot_sizes"]["BTC"] == 0.02)
sz = RT._smc_sizing(s_risk, "ETH")
check("ETH has no manual lot -> risk % sizing (1%), its old 0.5 lot is ignored",
      sz is s_risk and RT._smc_risk_mode(sz) and RT._smc_target_risk(sz) == 1.0)


class RTMT5(FakeMT5):
    pass


RT.mt5 = RTMT5()
check("broker check: 0.05 BTC accepted", RT._check_manual_lots({"BTC": 0.05}) == [])
for lots, word in (({"BTC": 0.005}, "below the broker minimum"), ({"BTC": 0.015}, "not a multiple"),
                   ({"BTC": 300}, "above the broker maximum"), ({"ETH": 0.05}, "below the broker minimum")):
    e = RT._check_manual_lots(lots)
    check(f"broker check: {lots} -> '{word}'", len(e) == 1 and word in e[0], str(e))
RT._mt5_init = lambda: None
RT._persist_settings = lambda d: (_ for _ in ()).throw(AssertionError("must not save"))
try:
    RT.start_scalping(RT.ScalpingSettings(manual_lots={"BTC": 0.005}), source="app")
    check("Start from the app with a lot under the broker minimum -> refused with a readable message", False)
except Exception as e:
    check("Start from the app with a lot under the broker minimum -> refused, nothing saved or started",
          getattr(e, "status_code", None) == 400 and "below the broker minimum" in str(getattr(e, "detail", "")),
          str(getattr(e, "detail", e)))

print("  Volume Profile")
VP.vp_state["settings"] = {**base, "sizing_mode": "risk", "risk_pct": 1.0, "manual_lots": {}}
check("default: no manual lot (all auto)", VP.DEFAULT_SETTINGS["manual_lots"] == {})
VP.update_settings({"manual_lots": {"btc": 0.05}})
check("BTC lot 0.05 saved", VP.vp_state["settings"]["manual_lots"] == {"BTC": 0.05})
for bad, word in (({"BTC": 0.005}, "minimum"), ({"BTC": 0.015}, "multiple"), ({"BTC": 50}, "maximum"),
                  ({"XAUUSD": 0.01}, "does not offer"), ({"BTC": "0.05"}, "positive number")):
    try:
        VP.update_settings({"manual_lots": bad})
        check(f"VP manual lot {bad} refused", False)
    except VP.SettingsError as e:
        check(f"VP manual lot {bad} refused ({word})", word in str(e), str(e))
check("a refused lot leaves the saved 0.05 unchanged", VP.vp_state["settings"]["manual_lots"] == {"BTC": 0.05})
ACCT["balance"] = 5000.0
scan({**base, "sizing_mode": "risk", "risk_pct": 1.0, "manual_lots": {"BTC": 0.3}})
check("VP scan, BTC manual lot 0.3 -> the order uses 0.3 (risk % would give 5)",
      placed and placed[0]["fixed_lot"] == 0.3, str(placed[0]["fixed_lot"] if placed else None))
check("... with the VP safety cap (risk_cap_pct 30%), not the 1% risk cap",
      placed and abs(placed[0]["risk_cap_pct"] - 0.30) < 1e-12)
scan({**base, "sizing_mode": "risk", "risk_pct": 1.0, "manual_lots": {}})
check("VP scan, lot box emptied -> back to the bot's choice (5 lots at 1%)", placed and placed[0]["fixed_lot"] == 5.0)
VP.vp_state["settings"] = {**base, "sizing_mode": "risk", "risk_pct": 1.0, "manual_lots": {"BTC": 0.05}}
VP.update_settings({"manual_lots": {"BTC": None}})
check("sending an empty lot removes it (auto again)", VP.vp_state["settings"]["manual_lots"] == {})

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
