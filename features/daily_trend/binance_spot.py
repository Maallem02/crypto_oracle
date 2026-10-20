"""Client REST minimal Binance SPOT — TESTNET uniquement (2026-10-13).

Demande utilisateur : faire tourner le Daily Trend avec PEU d'argent. Sur Exness
le lot minimum interdit tout trade sous ~900$ (ETH) / ~2200$ (BTC) ; en spot on
achète des fractions (ordre minimum 5$), sans swap ni levier. Simulé 2018-2026
avec 200$ : les 91 trades pris, +22.8 %/an, pire baisse -18 % (3 dernières
années : +11 %/an).

SÉCURITÉ :
  * les ORDRES ne partent que vers le TESTNET (argent fictif) : le client refuse
    toute autre adresse — passer en réel demandera un changement de code délibéré ;
  * les BOUGIES viennent du marché RÉEL (données publiques, sans clé) : les
    décisions sont celles qu'on prendrait en réel ;
  * clés lues dans .env (BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET),
    jamais journalisées ni renvoyées à l'app.
"""
import hashlib
import hmac
import os
import time
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from urllib.parse import urlencode

import numpy as np
import requests

TESTNET = "https://testnet.binance.vision"
MARKET_DATA = "https://api.binance.com"          # lecture publique des bougies réelles
KEY_ENV, SECRET_ENV = "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"
TIMEOUT = 20
RATES = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                  ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])   # comme MT5


class BinanceError(RuntimeError):
    def __init__(self, msg, code=None, status=None):
        super().__init__(msg)
        self.code, self.status = code, status


def sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def floor_to(value: float, step: str) -> str:
    """Arrondi VERS LE BAS au pas du broker, écrit comme Binance l'attend
    (pas de notation 1e-05)."""
    st = Decimal(step).normalize()
    q = (Decimal(str(value)) / st).to_integral_value(rounding=ROUND_DOWN) * st
    return format(q.quantize(st) if st.as_tuple().exponent < 0 else q, "f")


def ceil_to(value: float, step: str) -> str:
    """Arrondi VERS LE HAUT au pas du broker (ordre de test : jamais sous le minimum)."""
    st = Decimal(step).normalize()
    q = (Decimal(str(value)) / st).to_integral_value(rounding=ROUND_UP) * st
    return format(q.quantize(st) if st.as_tuple().exponent < 0 else q, "f")


def daily_klines(symbol: str, limit: int = 400, session=None, now_ms: int | None = None):
    """Bougies JOURNALIÈRES TERMINÉES du marché réel (00:00 -> 24:00 UTC), au
    format des rates MT5 (time en secondes = ouverture), pour runner.decide()."""
    s = session or requests
    r = s.get(MARKET_DATA + "/api/v3/klines", params={"symbol": symbol, "interval": "1d", "limit": limit},
              timeout=TIMEOUT)
    r.raise_for_status()
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rows = [k for k in r.json() if int(k[6]) < now_ms]                 # close_time passé = bougie finie
    out = np.zeros(len(rows), dtype=RATES)
    for i, k in enumerate(rows):
        out[i] = (int(k[0]) // 1000, float(k[1]), float(k[2]), float(k[3]), float(k[4]), int(k[8]), 0, 0)
    return out


class SpotClient:
    def __init__(self, base: str = TESTNET, key: str | None = None, secret: str | None = None, session=None):
        if base != TESTNET:
            raise ValueError("this build sends orders to the Binance TESTNET only")
        self.base = base
        self.key = os.environ.get(KEY_ENV, "") if key is None else key
        self.secret = os.environ.get(SECRET_ENV, "") if secret is None else secret
        self.s = session or requests.Session()
        self.offset_ms = 0
        self._rules = {}

    def has_keys(self) -> bool:
        return bool(self.key and self.secret)

    def _req(self, method, path, params=None, signed=False):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed:
            if not self.has_keys():
                raise BinanceError(f"no API keys: add {KEY_ENV} and {SECRET_ENV} to .env, then restart the backend")
            params["timestamp"] = int(time.time() * 1000) + self.offset_ms
            params["recvWindow"] = 10000
            q = urlencode(params)
            url = f"{self.base}{path}?{q}&signature={sign(q, self.secret)}"
            headers["X-MBX-APIKEY"] = self.key
            r = self.s.request(method, url, headers=headers, timeout=TIMEOUT)
        else:
            r = self.s.request(method, self.base + path, params=params, timeout=TIMEOUT)
        try:
            data = r.json()
        except ValueError:
            data = {"msg": (r.text or "")[:200]}
        if r.status_code >= 400:
            code = data.get("code") if isinstance(data, dict) else None
            if code == -1021:                       # horloge décalée : resynchronise pour le prochain appel
                try:
                    self.sync_time()
                except Exception:
                    pass
            msg = data.get("msg") if isinstance(data, dict) else str(data)
            raise BinanceError(f"Binance {r.status_code} {code}: {msg}", code=code, status=r.status_code)
        return data

    # ── public ────────────────────────────────────────────────────────────────
    def sync_time(self) -> int:
        self.offset_ms = int(self._req("GET", "/api/v3/time")["serverTime"]) - int(time.time() * 1000)
        return self.offset_ms

    def symbol_rules(self, symbol: str) -> dict:
        if symbol not in self._rules:
            info = self._req("GET", "/api/v3/exchangeInfo", {"symbol": symbol})["symbols"][0]
            f = {x["filterType"]: x for x in info["filters"]}
            notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            self._rules[symbol] = {"step": f["LOT_SIZE"]["stepSize"], "min_qty": float(f["LOT_SIZE"]["minQty"]),
                                   "tick": f["PRICE_FILTER"]["tickSize"],
                                   "min_notional": float(notional.get("minNotional", 0) or 0),
                                   "order_types": info.get("orderTypes", []), "status": info.get("status")}
        return self._rules[symbol]

    def price(self, symbol: str) -> float:
        return float(self._req("GET", "/api/v3/ticker/price", {"symbol": symbol})["price"])

    # ── compte et ordres (signés) ────────────────────────────────────────────
    def balances(self) -> dict:
        acc = self._req("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)
        return {b["asset"]: {"free": float(b["free"]), "locked": float(b["locked"])} for b in acc.get("balances", [])}

    def order(self, symbol, side, type_, quantity=None, stop_price=None, client_id=None):
        p = {"symbol": symbol, "side": side, "type": type_, "quantity": quantity, "stopPrice": stop_price,
             "newClientOrderId": client_id, "newOrderRespType": "FULL"}
        return self._req("POST", "/api/v3/order", p, signed=True)

    def get_order(self, symbol, order_id):
        return self._req("GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    def get_order_by_client(self, symbol, client_id):
        """Retrouve un ordre par NOTRE identifiant (reprise après un arrêt en plein achat)."""
        return self._req("GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_id}, signed=True)

    def cancel_order(self, symbol, order_id):
        return self._req("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    def order_fees(self, symbol, order_id) -> dict:
        """Frais exacts d'un ordre exécuté (un stop déclenché ne les renvoie pas)."""
        base_fee = quote_fee = 0.0
        for t in self._req("GET", "/api/v3/myTrades", {"symbol": symbol, "orderId": order_id}, signed=True) or []:
            c, asset = float(t.get("commission") or 0), t.get("commissionAsset") or "~"
            if symbol.startswith(asset):
                base_fee += c
            elif symbol.endswith(asset):
                quote_fee += c
        return {"base_fee": base_fee, "quote_fee": quote_fee}


def fill_summary(resp: dict) -> dict:
    """Quantité exécutée, prix moyen, frais en base et en quote d'une réponse FULL."""
    qty = float(resp.get("executedQty") or 0)
    quote = float(resp.get("cummulativeQuoteQty") or 0)
    base_fee = quote_fee = 0.0
    sym = resp.get("symbol", "")
    for f in resp.get("fills") or []:
        c, asset = float(f.get("commission") or 0), f.get("commissionAsset")
        if sym.startswith(asset or "~"):
            base_fee += c
        elif sym.endswith(asset or "~"):
            quote_fee += c
    return {"qty": qty, "quote": quote, "avg": (quote / qty) if qty else 0.0, "base_fee": base_fee,
            "quote_fee": quote_fee}
