"""Half at +1R for the scalping bot — fake MT5, nothing reaches the broker."""
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
os.chdir(r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend")
from dotenv import load_dotenv
load_dotenv(".env")
import features.trading.router as R
import features.trading.executor as ex

fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


class FakeMT5:
    TRADE_ACTION_SLTP, TRADE_ACTION_DEAL = 6, 1
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    ORDER_TIME_GTC = 0
    DEAL_ENTRY_IN = 0

    def __init__(self):
        self.pos, self.px, self.sent, self.orig = {}, {}, [], {}
        self.lots = {"BTCUSDm": (0.01, 0.01), "ETHUSDm": (0.1, 0.01), "XAGUSDm": (0.01, 0.01)}

    def add(self, ticket, sym, typ, vol, entry, sl, tp, magic=234000):
        self.pos[ticket] = NS(ticket=ticket, symbol=sym, type=typ, volume=vol, price_open=entry,
                              sl=sl, tp=tp, magic=magic, comment="", profit=0.0, swap=0.0)
        self.orig[ticket] = vol

    def positions_get(self, symbol=None, ticket=None):
        if ticket is not None:
            p = self.pos.get(ticket)
            return [p] if p else []
        return list(self.pos.values())

    def symbol_info_tick(self, s):
        return NS(bid=self.px[s], ask=self.px[s])

    DIGITS = {"BTCUSDm": 2, "ETHUSDm": 2, "XAGUSDm": 3}

    def symbol_info(self, s):
        mn, st = self.lots[s]
        d = self.DIGITS[s]
        return NS(volume_min=mn, volume_step=st, filling_mode=2, digits=d, point=10 ** -d)

    def history_deals_get(self, position=None, *a, **k):
        return [NS(entry=0, volume=self.orig[position])] if position in self.orig else []

    def last_error(self):
        return (0, "")

    def order_send(self, r):
        self.sent.append(dict(r))
        p = self.pos.get(r["position"])
        if r["action"] == self.TRADE_ACTION_SLTP:
            # the broker rounds to the symbol's digits, like the real one
            p.sl, p.tp = round(r["sl"], self.DIGITS[p.symbol]), r["tp"]
        elif r["action"] == self.TRADE_ACTION_DEAL:
            p.volume = round(p.volume - r["volume"], 8)
            if p.volume <= 1e-9:
                del self.pos[r["position"]]
        return NS(retcode=10009, price=r.get("price"), volume=r.get("volume"), comment="done")


F = FakeMT5()
R.mt5 = ex.mt5 = F
R._mt5_init = ex._mt5_init = lambda: None
R.cancel_stale_pending_orders = lambda *a, **k: []
R._track_excursion = lambda ticket, sym, entry, sl, moved, is_buy: R._excursion_cache.setdefault(
    ticket, {"risk": abs(entry - sl), "mfe_r": 0, "mae_r": 0})
R.scalp_state["settings"] = {"trail_start_r": 1.0, "be_trigger_r": 1.5, "partial_close_r": 1.0,
                             "pending_max_age_minutes": 0}


def run(sym, price):
    F.px[sym] = price
    F.sent.clear()
    R.manage_open_positions()
    return list(F.sent)


print("BTC BUY 0.02 @100, SL 90 (1R = 10), TP 125")
F.add(1, "BTCUSDm", 0, 0.02, 100.0, 90.0, 125.0)
s = run("BTCUSDm", 108.0)
check("+0.8R: nothing happens", not s)
s = run("BTCUSDm", 110.5)
deal = [x for x in s if x["action"] == 1]
sltp = [x for x in s if x["action"] == 6]
check("+1.05R: half closed (0.01, a SELL on this position)",
      len(deal) == 1 and deal[0]["volume"] == 0.01 and deal[0]["type"] == 1 and deal[0]["position"] == 1)
check("... and the rest's stop moved to entry + 0.05R = 100.5", sltp and sltp[0]["sl"] == 100.5 and sltp[0]["tp"] == 125.0,
      str(sltp))
check("rest = 0.01 lot", F.pos[1].volume == 0.01)
s = run("BTCUSDm", 118.0)
check("+1.8R later: NO trail on the rest, no second half", not s, str(s))
check("stop still 100.5, target still 125", F.pos[1].sl == 100.5 and F.pos[1].tp == 125.0)

print("\nbackend restart (memory lost) with the rest still open")
R._partial_done.clear(); R._orig_volume_cache.clear(); R._excursion_cache.clear()
R._excursion_cache[1] = {"risk": 10.0, "mfe_r": 0, "mae_r": 0}     # DB gives the original risk back
s = run("BTCUSDm", 119.0)
check("volume 0.01 < opened 0.02 -> recognised as already halved, nothing sent", not s, str(s))

print("\nETH SELL 0.5 @100, SL 110, TP 80")
F.add(2, "ETHUSDm", 1, 0.5, 100.0, 110.0, 80.0)
F.px["BTCUSDm"] = 119.0
s = run("ETHUSDm", 89.5)
deal = [x for x in s if x["action"] == 1 and x["position"] == 2]
sltp = [x for x in s if x["action"] == 6 and x["position"] == 2]
check("+1.05R: 0.25 closed with a BUY", deal and deal[0]["volume"] == 0.25 and deal[0]["type"] == 0, str(deal))
check("rest stop -> 99.5 (entry - 0.05R)", sltp and sltp[0]["sl"] == 99.5, str(sltp))

print("\nprice falls back below entry BEFORE the rest's stop could be set")
F.add(3, "ETHUSDm", 0, 0.4, 200.0, 190.0, 230.0)
F.orig[3] = 0.8                                  # half already taken (volume 0.4 of 0.8)
F.pos[3].sl = 190.0                              # stop never moved (e.g. crash in between)
F.px["ETHUSDm"] = 89.5
del F.pos[2]
s = run("ETHUSDm", 199.0)
deal = [x for x in s if x["action"] == 1 and x["position"] == 3]
check("rest closed at market at ~entry (where the stop would have taken it)",
      deal and deal[0]["volume"] == 0.4 and 3 not in F.pos, str(s))

print("\nXAG BUY 0.01 (minimum lot, cannot be split) -> old trail")
F.add(4, "XAGUSDm", 0, 0.01, 50.0, 49.0, 53.0)
s = run("XAGUSDm", 51.2)
check("+1.2R: no partial close", not [x for x in s if x["action"] == 1])
check("trail moved the stop instead (old behaviour)", [x for x in s if x["action"] == 6 and x["position"] == 4],
      str(s))

print("\nswitched off (partial_close_r = 0) -> old trail on a splittable lot")
R.scalp_state["settings"]["partial_close_r"] = 0
F.add(5, "BTCUSDm", 1, 0.02, 100.0, 110.0, 70.0)
F.px["XAGUSDm"] = 51.2
s = run("BTCUSDm", 88.0)
check("no partial close", not [x for x in s if x["action"] == 1 and x["position"] == 5])
check("trail/BE handled it", [x for x in s if x["action"] == 6 and x["position"] == 5], str(s))
R.scalp_state["settings"]["partial_close_r"] = 1.0

print("\nVolume Profile position (magic 234100) at +2R")
F.pos.clear()
F.add(6, "BTCUSDm", 0, 0.02, 100.0, 90.0, 120.0, magic=234100)
s = run("BTCUSDm", 120.0 - 0.1)
check("untouched", not s, str(s))

print("\n30/09 bug: BTC SELL, entry stop not on a 2-decimal price (83430.36 - 11.1035)")
F.pos.clear()
F.add(7, "BTCUSDm", 1, 0.02, 83430.36, 83652.43, 81000.0)
s = run("BTCUSDm", 83182.91)
sltp = [x for x in s if x["action"] == 6]
check("half closed + stop sent already rounded to 83419.26", sltp and sltp[0]["sl"] == 83419.26, str(sltp))
s = run("BTCUSDm", 83250.0)
check("next check: NOTHING sent (was: same request every 30 s)", not s, str(s))
s = run("BTCUSDm", 83300.0)
check("and the one after: still nothing", not s, str(s))

print("\nsplit_half_volume")
check("0.02 BTC -> 0.01", ex.split_half_volume("BTCUSDm", 0.02) == 0.01)
check("0.5 ETH -> 0.25", ex.split_half_volume("ETHUSDm", 0.5) == 0.25)
check("0.45 ETH -> 0.22 (step 0.01, rest 0.23)", ex.split_half_volume("ETHUSDm", 0.45) == 0.22)
check("0.15 ETH -> None (0.07 < min 0.1)", ex.split_half_volume("ETHUSDm", 0.15) is None)
check("0.01 XAG -> None", ex.split_half_volume("XAGUSDm", 0.01) is None)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
