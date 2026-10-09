"""Dashboard service against the live demo account (read-only) + consistency checks."""
import os
import sys

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
os.chdir(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from dotenv import load_dotenv
load_dotenv(".env")
from features.dashboard import service as S

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


d = S.summary()
a = d["account"]
print(f"account {a['login']} demo={a['demo']} balance {a['balance']} equity {a['equity']} "
      f"floating {a['floating']} margin level {a['margin_level']}")
for k in ("today", "week", "month", "d30", "all"):
    p = d["periods"][k]
    print(f"  {k:6s} trades {p['trades']:4d} WR {p['win_rate']}% net {p['net']:+9.2f} "
          f"({p['return_pct']}% of {p['balance_start']}) PF {p['profit_factor']} "
          f"deposits {p['deposits']:+.2f} best {p['best']} worst {p['worst']}")
    print(f"         by bot: " + ", ".join(f"{b} {v['trades']}tr {v['net']:+.2f}"
                                          for b, v in p["by_bot"].items()))
allp = d["periods"]["all"]
check("all-time: trading net + deposits == balance (to the cent)",
      abs(allp["net"] + allp["deposits"] - a["balance"]) < 0.02,
      f"{allp['net']} + {allp['deposits']} vs {a['balance']}")
check("all-time balance_start ~ 0", abs(allp["balance_start"]) < 0.02, str(allp["balance_start"]))
w, t = d["periods"]["week"], d["periods"]["today"]
check("today's trades <= week's", t["trades"] <= w["trades"])
check("by_symbol sums to the period net (30d)",
      abs(sum(x["net"] for x in d["periods"]["d30"]["by_symbol"]) - d["periods"]["d30"]["net"]) < 0.05)
print("  per symbol 30d:", ", ".join(f"{x['symbol']} {x['net']:+.2f}" for x in d["periods"]["d30"]["by_symbol"]))
c = d["curve"]
check("curve: 31 points, last = balance now", len(c) == 31 and c[-1]["balance"] == a["balance"])
print(f"  curve {c[0]['date']} {c[0]['balance']} ... {c[-2]['date']} {c[-2]['balance']} -> now {c[-1]['balance']}")
r = d["risk"]
print(f"  risk: {r['open_positions']} open, at risk ${r['at_risk']} ({r['at_risk_pct']}%), locked ${r['locked']}, "
      f"no stop {r['no_stop']}, max DD 30d {r['max_drawdown_30d']}")
print(f"        peak {r.get('peak_equity')} dd {r.get('drawdown_from_peak_pct')}% halt at {r.get('halt_equity')} "
      f"| SMC daily {r.get('smc_daily_limit_pct')} cap {r.get('smc_risk_cap_pct')} "
      f"| VP daily {r.get('vp_daily_limit_pct')} cap {r.get('vp_risk_cap_pct')}")
print(f"        by bot {r['by_bot']}  today {r['today_by_bot']}  streak {d['streak']}")
for x in r["positions"]:
    print(f"        {x['bot']:6s} {x['symbol']} {x['side']} {x['volume']} P/L {x['profit']:+.2f} at-SL {x['at_sl']}")

n = S.news()
print(f"\nnews: traded {n['traded_symbols']} -> currencies {n['currencies']}")
print(f"  upcoming {len(n['upcoming'])}, recent 24h {len(n['recent'])}, headlines {len(n['headlines'])}")
for e in n["upcoming"][:5] + n["recent"][:5]:
    print(f"   {e['time'][:16]} {e['currency']} {e['impact']:6s} {e['title'][:40]:40s} "
          f"f={e['forecast']} p={e['previous']} -> {e['symbols']}")
for h in n["headlines"][:6]:
    print(f"   [{h['source']}] {h['time'][:16]} {h['title'][:80]}")
check("news has headlines", len(n["headlines"]) > 0)
print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
