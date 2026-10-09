"""HTF conflict block in the real scan loop. Fake MT5, fake trend reader,
fake journal - nothing reaches the broker, the DB or risk_state.json."""
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
import features.trading.router as R
import features.trading.data_collector as DC
import features.news.calendar as CAL

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


class Reached(Exception):
    """raised by the first call AFTER the conflict gate = the symbol went on to analysis"""


journal = []
DC.save_rejection = lambda e: journal.append(e)
CAL.is_news_blocked = lambda s, *a, **k: {"blocked": False}
R._mt5_init = lambda: None
R._bind_risk_state_to_account = lambda *a: None
R._apply_cash_flows = lambda a: None
R._save_risk_state = lambda d: None
R.get_daily_pnl_pct = lambda *a: 0.0
R.check_and_update_outcomes = R.check_symbol_loss_streaks = R.maybe_retrain = lambda *a, **k: None
R.update_direction_breaker = lambda **k: None
R.is_trading_session = lambda s: True
R.mt5 = NS(account_info=lambda: NS(login=262890622, equity=1000.0, balance=1000.0),
           positions_get=lambda **k: [])


def boom(*a, **k):
    raise Reached()


R.get_ema_confluence = boom
trend = {"v": None}
R.get_htf_trend = lambda sym, tfs: trend["v"]

settings = dict(R.scalp_state["settings"])
settings.update(enabled_symbols=["BTC"], max_daily_trades=0, max_daily_loss_pct=0, htf_timeframe=["5m", "1h"])


def scan(trends, consensus):
    trend["v"] = {"trends": trends, "consensus": consensus}
    R.scalp_state.update(running=True, settings=settings, peak_equity=1000.0,
                         last_scan_date=R.datetime.now().date().isoformat())
    R.scalp_cooldowns.clear(); R.symbol_paused_until.clear()
    journal.clear()
    try:
        R._scalp_auto_scan_inner()
    except Reached:
        return True, list(journal)
    return False, list(journal)


print("CONFLICT BLOCK ON (as deployed)")
check("constant is ON", R.HTF_CONFLICT_BLOCK is True)
went, j = scan({"5m": "bullish", "1h": "bearish"}, "conflict")
check("5m bullish / 1h bearish -> symbol skipped before analysis", not went)
check("   ... and the skip is journaled as htf_conflict",
      any(e["reason"].startswith("htf_conflict") and e["symbol"] == "BTC" for e in j), str(j[-1:]))
went, _ = scan({"5m": "bearish", "1h": "bullish"}, "conflict")
check("5m bearish / 1h bullish -> skipped", not went)
for cons, tr in (("bullish", {"5m": "bullish", "1h": "bullish"}), ("bearish", {"5m": "bearish", "1h": "bearish"}),
                 ("neutral", {"5m": "neutral", "1h": "neutral"}), ("bullish", {"5m": "neutral", "1h": "bullish"})):
    went, j = scan(tr, cons)
    check(f"{cons:8s} {tr} -> goes on to analysis", went)
    check("   ... and nothing journaled as htf_conflict", not any(e["reason"].startswith("htf_conflict") for e in j))

print("\nSWITCH OFF (rollback = one constant)")
R.HTF_CONFLICT_BLOCK = False
went, j = scan({"5m": "bullish", "1h": "bearish"}, "conflict")
check("conflict with the constant OFF -> goes on (old observation-only behaviour)", went)
R.HTF_CONFLICT_BLOCK = True

print("\nOTHER GATES STILL BEFORE IT")
R.mt5 = NS(account_info=lambda: NS(login=262890622, equity=1000.0, balance=1000.0),
           positions_get=lambda **k: [NS(magic=234000, symbol="BTCUSDm")])
went, j = scan({"5m": "bullish", "1h": "bullish"}, "bullish")
check("BTC position already open -> skipped by the 1-per-symbol rule (unchanged)",
      not went and any(e["reason"] == "max_positions 1/1" for e in j))

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
