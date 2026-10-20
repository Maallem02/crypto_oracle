"""Daily Trend SPOT (features/daily_trend/spot_runner.py + binance_spot.py).
A FAKE Binance exchange: nothing reaches Binance (not even the testnet), the
real dt_spot_state.json and the database are not touched. Part 6 reads REAL
Binance daily candles (public data, no key) for an end-to-end replay that must
reproduce the tested study's trades exactly."""
import copy
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import numpy as np
import requests

BK = r"C:\Users\MSI\Desktop\loumi\crypt\crypto_oracle-backend"
sys.path.insert(0, BK)
import features.daily_trend.binance_spot as B
import features.daily_trend.spot_runner as SR
from features.daily_trend import audit as AU

UTC = timezone.utc
fails = 0


def check(label, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}{('   -> ' + extra) if extra else ''}")


class FakeSpot:
    """Fake Binance: market orders fill at the current price, STOP_LOSS sells wait
    and trigger when a played candle trades through them."""
    RULES = {"BTCUSDT": {"step": "0.00001000", "min_qty": 1e-5, "tick": "0.01000000", "min_notional": 5.0,
                         "order_types": ["MARKET", "STOP_LOSS"], "status": "TRADING"},
             "ETHUSDT": {"step": "0.00010000", "min_qty": 1e-4, "tick": "0.01000000", "min_notional": 5.0,
                         "order_types": ["MARKET", "STOP_LOSS"], "status": "TRADING"}}

    def __init__(self, fee=0.001, keys=True):
        self.fee, self.keys = fee, keys
        self.px = {"BTCUSDT": 100.0, "ETHUSDT": 100.0}
        self.usdt = 10_000.0
        self.orders, self.calls, self.next_id, self.t_ms = {}, [], 1000, 1_790_000_000_000
        self.fail_stop = None

    def has_keys(self):
        return self.keys

    def symbol_rules(self, pair):
        return self.RULES[pair]

    def price(self, pair):
        return self.px[pair]

    def balances(self):
        return {"USDT": {"free": self.usdt, "locked": 0.0}}

    def _new(self, **o):
        self.next_id += 1
        o.update(orderId=self.next_id, updateTime=self.t_ms, transactTime=self.t_ms)
        self.orders[self.next_id] = o
        return o

    def order(self, pair, side, type_, quantity=None, stop_price=None, client_id=None):
        self.calls.append((side, type_, pair, quantity, stop_price))
        q = float(quantity)
        if type_ == "MARKET":
            px = self.px[pair]
            base_fee = q * self.fee if side == "BUY" else 0.0
            quote_fee = q * px * self.fee if side == "SELL" else 0.0
            self.usdt += (-1 if side == "BUY" else 1) * q * px - quote_fee
            return self._new(symbol=pair, side=side, type=type_, status="FILLED", executedQty=str(q),
                             cummulativeQuoteQty=str(q * px), clientOrderId=client_id,
                             fills=[{"price": str(px), "qty": str(q), "commission": str(base_fee or quote_fee),
                                     "commissionAsset": pair[:3] if side == "BUY" else "USDT"}])
        if type_ == "STOP_LOSS":
            if self.fail_stop:
                raise B.BinanceError(self.fail_stop, code=-2010)
            if float(stop_price) >= self.px[pair]:
                raise B.BinanceError("Stop price would trigger immediately.", code=-2010)
            return self._new(symbol=pair, side=side, type=type_, status="NEW", origQty=str(q), executedQty="0",
                             cummulativeQuoteQty="0", stopPrice=str(stop_price), clientOrderId=client_id)
        raise ValueError(type_)

    def get_order(self, pair, oid):
        if oid not in self.orders:
            raise B.BinanceError("Order does not exist.", code=-2013)
        return dict(self.orders[oid])

    def get_order_by_client(self, pair, cid):
        for o in self.orders.values():
            if o.get("clientOrderId") == cid:
                return dict(o)
        raise B.BinanceError("Order does not exist.", code=-2013)

    def cancel_order(self, pair, oid):
        o = self.orders.get(oid)
        if o is None or o["status"] != "NEW":
            raise B.BinanceError("Unknown order sent.", code=-2011)
        o["status"] = "CANCELED"
        return o

    def order_fees(self, pair, oid):
        o = self.orders[oid]
        return {"base_fee": 0.0, "quote_fee": float(o["cummulativeQuoteQty"]) * self.fee}

    def play_bar(self, pair, o, h, l, t_open, entered_this_bar):
        """Stops of this pair triggered by a candle (the study's rule: the stop price
        on the entry day, otherwise the worse of the stop and the open)."""
        for oid, od in self.orders.items():
            if od["symbol"] == pair and od["type"] == "STOP_LOSS" and od["status"] == "NEW":
                stop = float(od["stopPrice"])
                if l <= stop:
                    fill = stop if entered_this_bar else min(stop, o)
                    q = float(od["origQty"])
                    od.update(status="FILLED", executedQty=str(q), cummulativeQuoteQty=str(q * fill),
                              updateTime=(t_open + 3600) * 1000)
                    self.usdt += q * fill * (1 - self.fee)


def fresh(settings=None, enabled=True, fake=None):
    SR._persist = lambda: None
    SR._ensure_ready = lambda: None
    SR._close_journal = lambda *a: None
    SR.state.clear()
    SR.state.update(enabled=enabled, settings={**SR.DEFAULT_SETTINGS, **(settings or {})}, positions={}, pending={},
                    realized_usd=0.0, dust={}, last_bar={}, last_exit={}, last_check={}, last_scan=None,
                    last_error=None)
    SR._client = fake or FakeSpot()
    logged.clear()
    return SR._client


logged = []
SR._log = lambda sym, ev, **k: logged.append((sym, ev, k.get("reason")))
DT_ = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
DAY0 = 1_780_000_000 - 1_780_000_000 % 86400


def make_bars(closes, w=1.0):
    b = np.zeros(len(closes), dtype=DT_)
    prev = closes[0]
    for i, c in enumerate(closes):
        b[i] = (DAY0 + 86400 * i, prev, max(prev, c) + w, min(prev, c) - w, c, 1, 0, 0)
        prev = c
    return b


print("1. SETTINGS")
fresh()
check("defaults: BTC + ETH, 2% risk, 3% crypto cap, $200 capital, OFF on first start",
      SR.DEFAULT_SETTINGS == {"symbols": ["BTC", "ETH"], "risk_pct": 2.0, "max_crypto_risk_pct": 3.0,
                              "capital_usd": 200.0} and SR._load_state.__defaults__ is None)
SR.spot_status = lambda: {"ok": True}
for bad in ({"capital_usd": 5}, {"capital_usd": "200"}, {"risk_pct": 6}, {"symbols": ["BTC", "SOL"]}):
    try:
        SR.update_settings(bad)
        check(f"{bad} refused", False)
    except SR.SettingsError as e:
        check(f"{bad} refused", True, str(e))
SR.update_settings({"capital_usd": 150, "risk_pct": 1.5})
check("capital $150 and risk 1.5% accepted", SR.state["settings"]["capital_usd"] == 150 and SR.state["settings"]["risk_pct"] == 1.5)

print("\n2. POSITION SIZE (spot: fractions, no leverage, Binance minimum $5)")
q, r, why = SR.size_order(200, 2, 3, 0, 4500, 83000, 200, "0.00001000", 1e-5, 5)
check("$200, 2%, BTC 83,000 with a $4,500 stop + 0.1% fees each way -> 0.00085 BTC (= $71, loss at the stop "
      "incl. fees $3.96 <= $4)", q == "0.00085" and abs(r - 2) < 1e-9
      and float(q) * (4500 + 2 * 0.001 * 83000) <= 4.0, f"{q} {why}")
q, r, why = SR.size_order(200, 2, 3, 4.0, 4500, 83000, 200, "0.00001000", 1e-5, 5)
check("BTC already risks $4 (2%): the next one gets the 1% left of the 3% cap", q == "0.00042" and abs(r - 1) < 1e-9, q)
q, r, why = SR.size_order(200, 2, 3, 6.0, 4500, 83000, 200, "0.00001000", 1e-5, 5)
check("3% already at risk -> skipped, 'crypto risk cap full'", q is None and "cap full" in why, why)
q, r, why = SR.size_order(100, 2, 3, 0, 5, 2500, 100, "0.00010000", 1e-4, 5)
check("a very tight $5 stop would need $500 of ETH with $100 -> cut to the cash (no leverage)",
      q == "0.0398" and "cut to the cash" in why, f"{q} {why}")
q, r, why = SR.size_order(10, 2, 3, 0, 4500, 83000, 10, "0.00001000", 1e-5, 5)
check("$10 capital -> order of $3.32 is under Binance's $5 minimum -> skipped", q is None and "minimum order" in why, why)

print("\n3. ENTRY, STOP AT BINANCE, EXITS (fake exchange)")
flat = [100.0] * 60
up = make_bars(flat + [101, 102, 103, 112])                  # last close breaks the 20-day high
SR.daily_klines = lambda pair, limit=400, session=None, now_ms=None: STATE_BARS[pair]
STATE_BARS = {"BTCUSDT": up, "ETHUSDT": make_bars(flat + [100.2])}
NOW = datetime.fromtimestamp(int(up["time"][-1]) + 86400 + 300, UTC)
fx = fresh()
fx.px["BTCUSDT"] = 112.0
fx.t_ms = int(NOW.timestamp() * 1000)
SR.spot_scan(NOW)
d = SR.DT.decide(up)
pos = SR.state["positions"].get("BTC")
buy = [c for c in fx.calls if c[0] == "BUY"]
stop = [c for c in fx.calls if c[1] == "STOP_LOSS"]
check("breakout -> one market BUY of BTC, nothing on ETH (no breakout)", len(buy) == 1 and buy[0][2] == "BTCUSDT", str(fx.calls))
check("the 0.1% fee taken in BTC is deducted: the stop sells exactly what we hold",
      pos and abs(pos["qty"] - float(B.floor_to(float(buy[0][3]) * 0.999, "0.00001000"))) < 1e-12, str(pos))
check("STOP_LOSS placed at Binance at entry - 2 ATR", stop and abs(float(stop[0][4]) - float(B.floor_to(112.0 - d["stop_dist"], "0.01"))) < 1e-9,
      str(stop))
check("loss at the stop incl. both fees <= 2% of the $200 capital ($4)",
      pos and 3.5 < pos["risk_usd"] + pos["stop"] * pos["qty"] * 0.001 <= 4.0 + 1e-6, f"{pos['risk_usd'] if pos else None}")
check("journal: 'entry' with the size and the stop", any(ev == "entry" for _, ev, _ in logged))
n_calls = len(fx.calls)
SR.spot_scan(NOW)
check("scanned again the same day -> no second order", len(fx.calls) == n_calls)

# stop hit during the next candle
fx.play_bar("BTCUSDT", 112.0, 113.0, 100.0, int(up["time"][-1]) + 86400, entered_this_bar=True)
STATE_BARS["BTCUSDT"] = make_bars(flat + [101, 102, 103, 112, 101])
SR.spot_scan(datetime.fromtimestamp(int(STATE_BARS["BTCUSDT"]["time"][-1]) + 86400 + 300, UTC))
check("stop triggered at Binance -> position closed, loss realized, 'stop' journalled",
      "BTC" not in SR.state["positions"] and SR.state["realized_usd"] < 0 and any(ev == "stop" for _, ev, _ in logged),
      f"realized {SR.state['realized_usd']:.2f}")

# channel exit
fx = fresh()
fx.px["BTCUSDT"] = 112.0
fx.t_ms = int(NOW.timestamp() * 1000)
STATE_BARS["BTCUSDT"] = up
SR.spot_scan(NOW)
down = make_bars(flat + [101, 102, 103, 112, 111, 110, 109, 108, 107, 106, 105, 104, 103, 102, 101, 95])
STATE_BARS["BTCUSDT"] = down
fx.px["BTCUSDT"] = 95.0
SR.spot_scan(datetime.fromtimestamp(int(down["time"][-1]) + 86400 + 300, UTC))
canc = [o for o in fx.orders.values() if o["type"] == "STOP_LOSS" and o["status"] == "CANCELED"]
sells = [c for c in fx.calls if c[0] == "SELL" and c[1] == "MARKET"]
check("close under the 10-day low -> stop cancelled, BTC sold at market, P&L realized",
      canc and sells and "BTC" not in SR.state["positions"] and any(ev == "exit" for _, ev, _ in logged),
      f"realized {SR.state['realized_usd']:.2f}")

print("\n4. SAFETY")
fx = fresh()
fx.px["BTCUSDT"] = 112.0
STATE_BARS["BTCUSDT"] = up
fx.fail_stop = "Stop price would trigger immediately."
SR.spot_scan(NOW)
check("the stop cannot be placed -> the coins are sold straight back: never a position without a stop",
      "BTC" not in SR.state["positions"] and [c[:2] for c in fx.calls] == [("BUY", "MARKET"), ("SELL", "STOP_LOSS"), ("SELL", "MARKET")]
      and any(ev == "error" and "never hold without a stop" in (r or "") for _, ev, r in logged), str(fx.calls))
fx = fresh(enabled=False)
fx.px["BTCUSDT"] = 112.0
SR.spot_scan(NOW)
check("bot STOPPED + breakout -> no order, 'not taken' journalled",
      not fx.calls and any("stopped" in (r or "") for _, _, r in logged))
fx = fresh(fake=FakeSpot(keys=False))
SR.spot_job()
check("no API keys -> no call to the exchange, the app is told what to add to .env",
      not fx.calls and "BINANCE_TESTNET_API_KEY" in (SR.state["last_error"] or ""))
try:
    B.SpotClient(base="https://api.binance.com", key="x", secret="y")
    check("orders to the REAL Binance are impossible in this build", False)
except ValueError:
    check("orders to the REAL Binance are impossible in this build", True)

print("\n5. RECOVERY")
fx = fresh()
fx.px["BTCUSDT"] = 112.0
cid = f"dts-BTC-{int(up['time'][-1])}"
fx.order("BUYUSDT".replace("BUY", "BTC"), "BUY", "MARKET", quantity="0.03000", client_id=cid)
fx.calls.clear()
SR.state["pending"]["BTC"] = {"client_id": cid, "bar_time": int(up["time"][-1]), "stop_dist": 6.0, "risk": 2.0,
                              "d": {"close": 112.0, "level": 104.0, "atr": 3.0, "why": "breakout"}}
SR.state["last_bar"] = {"BTC": int(up["time"][-1]), "ETH": int(up["time"][-1])}
SR.spot_scan(NOW)
check("backend stopped right after a buy -> the order is found at Binance, position recorded, stop placed",
      "BTC" in SR.state["positions"] and fx.calls and fx.calls[0][1] == "STOP_LOSS" and not SR.state["pending"], str(fx.calls))
sid = SR.state["positions"]["BTC"]["stop_id"]
fx.orders.pop(sid)                                            # testnet reset: the stop vanished
fx.calls.clear()
SR.spot_scan(NOW)
check("the stop order disappeared (testnet reset) -> placed again", fx.calls and fx.calls[0][1] == "STOP_LOSS"
      and SR.state["positions"]["BTC"]["stop_id"] != sid)

print("\n7. TEST ORDER BUTTON (fake exchange)")
fx = fresh()
fx.px["BTCUSDT"] = 83000.0
res = SR.test_order("BTC")
kinds = [c[:2] for c in fx.calls]
stop_orders = [o for o in fx.orders.values() if o["type"] == "STOP_LOSS"]
check("5 steps, all OK: buy, stop placed, stop read back, stop cancelled, sell",
      res["ok"] and [s["step"][:2] for s in res["steps"]][:5] == ["1.", "2.", "3.", "4.", "5."], str(res["steps"]))
check("orders sent: market BUY, STOP_LOSS sell, market SELL - the stop ends CANCELLED",
      kinds == [("BUY", "MARKET"), ("SELL", "STOP_LOSS"), ("SELL", "MARKET")]
      and stop_orders and stop_orders[0]["status"] == "CANCELED", str(kinds))
buy_qty = float(fx.calls[0][3])
check(f"the test buy is at least Binance's $5 minimum (bought {buy_qty:g} BTC = ${buy_qty * 83000:.2f})",
      5.0 <= buy_qty * 83000 <= 7.0)
check("the stop is 5% under the buy price", abs(float(fx.calls[1][4]) - 83000 * 0.95) < 0.02, fx.calls[1][4])
check("the bot's own money and positions are untouched",
      SR.state["positions"] == {} and SR.state["realized_usd"] == 0.0 and SR.state["pending"] == {})
check("true cost = the two 0.1% fees only (~$0.013); the leftover under Binance's step is NOT counted as lost",
      res["cost_usd"] is not None and 0 < res["cost_usd"] < 0.02 and any(ev == "test" and "OK" in (r or "") for _, ev, r in logged),
      f"cost ${res['cost_usd']}")
dust1 = SR.state["dust"].get("BTC", 0.0)
check("the fee taken in BTC leaves 0.00000992 BTC (0.00008 x 0.999 - 0.00007): kept in the ledger and reported",
      abs(dust1 - 0.00000992) < 1e-12 and any(s["step"] == "Leftover kept" for s in res["steps"]), f"{dust1:.10f}")
res2 = SR.test_order("BTC")
sold2 = float([c for c in fx.calls if c[:2] == ("SELL", "MARKET")][-1][3])
check("next order sells the leftover too (0.00008 instead of 0.00007): nothing piles up",
      res2["ok"] and sold2 == 0.00008 and SR.state["dust"]["BTC"] < 0.00001, f"sold {sold2:g}, dust {SR.state['dust']['BTC']:.10f}")
fx = fresh()
fx.px["BTCUSDT"] = 83000.0
fx.fail_stop = "Filter failure: PERCENT_PRICE_BY_SIDE"
res = SR.test_order("BTC")
check("stop refused by Binance -> reported as FAILED, the bought coins are sold back (nothing left open)",
      not res["ok"] and any(s["step"].startswith("Clean-up: coins sold back") and s["ok"] for s in res["steps"])
      and [c[:2] for c in fx.calls][-1] == ("SELL", "MARKET")
      and not [o for o in fx.orders.values() if o["type"] == "STOP_LOSS" and o["status"] == "NEW"],
      str([s["step"] for s in res["steps"]]))
fx = fresh()
SR.state["positions"]["BTC"] = {"risk_usd": 4.0, "cost_usd": 70.0, "qty": 0.001, "t_in": 0}
try:
    SR.test_order("BTC")
    check("the bot holds BTC -> test order refused, so it can never touch the position", False)
except SR.SettingsError as e:
    check("the bot holds BTC -> test order refused, so it can never touch the position", not fx.calls, str(e))
fx = fresh(fake=FakeSpot(keys=False))
try:
    SR.test_order("BTC")
    check("no API keys -> test order refused", False)
except SR.SettingsError as e:
    check("no API keys -> test order refused", not fx.calls, str(e))

print("\n6. REAL BINANCE HISTORY (public candles): the spot bot, run day by day with a fake exchange,"
      "\n   must make the SAME trades as the tested study code")


def binance_daily(pair, start_ms):
    out, t = [], start_ms
    while True:
        r = requests.get(B.MARKET_DATA + "/api/v3/klines", params={"symbol": pair, "interval": "1d", "startTime": t,
                                                                   "limit": 1000}, timeout=30).json()
        out += r
        if len(r) < 1000:
            break
        t = int(r[-1][0]) + 86400_000
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    rows = [k for k in out if int(k[6]) < now_ms]
    b = np.zeros(len(rows), dtype=DT_)
    for i, k in enumerate(rows):
        b[i] = (int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), 1, 0, 0)
    return b


HIST = {p: binance_daily(p, int(datetime(2018, 1, 1, tzinfo=UTC).timestamp() * 1000)) for p in ("BTCUSDT", "ETHUSDT")}
fx = fresh(settings={"capital_usd": 200.0})
made = []
real_close = SR._close


def spy_close(sym, kind, exit_avg, proceeds, t_out, reason):
    p = SR.state["positions"][sym]
    made.append((sym, p["t_in"] // 86400 * 86400, int(t_out) // 86400 * 86400 - (86400 if kind == "channel" else 0), kind))
    return real_close(sym, kind, exit_avg, proceeds, t_out, reason)


SR._close = spy_close
SR.daily_klines = lambda pair, limit=400, session=None, now_ms=None: VIEW[pair]
times = HIST["BTCUSDT"]["time"]
idx = {p: {int(t): i for i, t in enumerate(HIST[p]["time"])} for p in HIST}
VIEW = {}
skips = 0
for k in range(60, len(times) - 1):
    t_open = int(times[k])
    for p in HIST:                                            # the candle k is played: stops may trigger
        i = idx[p].get(t_open)
        if i is None:
            continue
        b = HIST[p][i]
        sym = p[:3]
        pos = SR.state["positions"].get(sym)
        fx.play_bar(p, float(b["open"]), float(b["high"]), float(b["low"]), t_open,
                    entered_this_bar=bool(pos and t_open <= pos["t_in"] < t_open + 86400))
    for p in HIST:                                            # after its close, the bot sees candles up to k
        i = idx[p].get(t_open)
        VIEW[p] = HIST[p][max(0, i - 399):i + 1] if i is not None else VIEW.get(p, HIST[p][:1])
        nxt = HIST[p][i + 1] if (i is not None and i + 1 < len(HIST[p])) else None
        fx.px[p] = float(nxt["open"]) if nxt is not None else float(HIST[p][i]["close"])
    fx.t_ms = (t_open + 86400 + 300) * 1000
    n_log = len(logged)
    SR.spot_scan(datetime.fromtimestamp(t_open + 86400 + 300, UTC))
    skips += sum(1 for _, ev, _ in logged[n_log:] if ev in ("skip", "error"))
SR._close = real_close
for p in HIST:
    sym = p[:3]
    ref = [(t["t0"], t["t1"], t["kind"]) for t in AU.reference_trades(HIST[p], 0.0, 0.0)
           if t["t0"] >= int(times[61])]
    mine = [(a, b, k) for s, a, b, k in made if s == sym]
    n = min(len(ref), len(mine))
    same = ref[:n] == mine[:n]
    first = next(((x, y) for x, y in zip(ref, mine) if x != y), None)
    check(f"{sym} {datetime.fromtimestamp(int(HIST[p]['time'][0]), UTC):%Y}->{datetime.fromtimestamp(int(HIST[p]['time'][-1]), UTC):%Y}: "
          f"study {len(ref)} trades, spot bot {len(mine)} closed, identical days and exit types: {same}",
          same and abs(len(ref) - len(mine)) <= 1, f"first difference {first}")
eq = SR._equity({p[:3]: float(HIST[p]["close"][-1]) for p in HIST})
check(f"no trade skipped or refused with $200 (skips/errors: {skips})", skips == 0)
print(f"  info: $200 -> ${eq:.0f} on Binance real prices (fake fills at the next open, fee 0.1%, no swap)")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
