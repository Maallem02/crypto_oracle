import pandas as pd
import numpy as np
import requests
import ccxt
import time
import threading
import MetaTrader5 as mt5
from datetime import datetime, timedelta

# ── Mapping des symboles ───────────────────────────────────────────────
CRYPTO_SYMBOLS = {
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "SOL": "SOL/USDT",
    "BNB": "BNB/USDT",
    "XRP": "XRP/USDT",
}

FOREX_SYMBOLS = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "GBPJPY": "GBPJPY=X",
    "EURUSD": "EURUSD=X",
    "USDJPY": "USDJPY=X",
}

TIMEFRAME_CCXT = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h",
}

# ── Mapping symboles → MT5 (même feed que l'exécution des trades) ──────
MT5_SYMBOLS = {
    "BTC":    "BTCUSDm",
    "ETH":    "ETHUSDm",
    "SOL":    "SOLUSDm",
    "BNB":    "BNBUSDm",
    "XRP":    "XRPUSDm",
    "XAUUSD": "XAUUSDm",
    "XAGUSD": "XAGUSDm",
    "GBPJPY": "GBPJPYm",
    "EURUSD": "EURUSDm",
    "USDJPY": "USDJPYm",
}

MT5_TIMEFRAMES = {
    "1m":  mt5.TIMEFRAME_M1,
    "3m":  mt5.TIMEFRAME_M3,
    "5m":  mt5.TIMEFRAME_M5,
    "15m": mt5.TIMEFRAME_M15,
    "30m": mt5.TIMEFRAME_M30,
    "1h":  mt5.TIMEFRAME_H1,
    "4h":  mt5.TIMEFRAME_H4,
    "1d":  mt5.TIMEFRAME_D1,
}

def _mt5_init():
    """
    Attache le terminal (idempotent) — délègue à mt5_client.ensure_mt5().

    L'ancienne version ne testait que terminal_info(), qui est non-None même
    quand le terminal est loggé sur le MAUVAIS compte : elle ne réparait
    donc jamais cet état, elle le laissait passer silencieusement.
    """
    from features.trading.mt5_client import ensure_mt5
    ensure_mt5()

# ── Fetch via MT5 (même broker feed que l'exécution des trades) ───────
def fetch_mt5_candles(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    mt5_symbol = MT5_SYMBOLS.get(symbol.upper())
    if not mt5_symbol:
        raise ValueError(f"Symbole MT5 inconnu : {symbol}")

    tf = MT5_TIMEFRAMES.get(timeframe, mt5.TIMEFRAME_M15)

    _mt5_init()
    if not mt5.symbol_select(mt5_symbol, True):
        raise ValueError(f"Impossible de sélectionner {mt5_symbol} dans MT5")

    rates = mt5.copy_rates_from_pos(mt5_symbol, tf, 0, limit)
    if rates is None or len(rates) == 0:
        raise ValueError(f"Pas de données MT5 pour {mt5_symbol} en {timeframe}: {mt5.last_error()}")

    df = pd.DataFrame(rates)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("timestamp", inplace=True)
    df = df.rename(columns={"tick_volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df

TIMEFRAME_YF = {
    "1m":  ("1m",  "1d"),
    "3m":  ("5m",  "2d"),
    "5m":  ("5m",  "5d"),
    "15m": ("15m", "7d"),
    "30m": ("30m", "15d"),
    "1h":  ("1h",  "30d"),
    "4h":  ("1h",  "60d"),
}

exchange = ccxt.binance({"enableRateLimit": True})

# ── Yahoo Finance direct (sans yfinance) ───────────────────────────────
YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

def fetch_yahoo_candles(yf_symbol: str, interval: str, period: str) -> pd.DataFrame:
    """Fetch OHLCV depuis Yahoo Finance API directement (3 tentatives)"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}"
    params = {
        "interval": interval,
        "range":    period,
        "events":   "history",
    }
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=YF_HEADERS, timeout=10)
            resp.raise_for_status()
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2)
    else:
        raise last_err
    data = resp.json()

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    ohlcv      = result["indicators"]["quote"][0]

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(timestamps, unit="s"),
        "open":      ohlcv["open"],
        "high":      ohlcv["high"],
        "low":       ohlcv["low"],
        "close":     ohlcv["close"],
        "volume":    ohlcv["volume"],
    })
    df.set_index("timestamp", inplace=True)
    df.dropna(inplace=True)
    df = df.astype(float)
    return df

# ── Fetch crypto (Binance via ccxt) ───────────────────────────────────
def fetch_crypto_candles(symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
    ccxt_symbol = CRYPTO_SYMBOLS.get(symbol.upper())
    if not ccxt_symbol:
        raise ValueError(f"Symbole crypto inconnu : {symbol}")

    tf       = TIMEFRAME_CCXT.get(timeframe, "15m")
    last_err = None
    for attempt in range(3):
        try:
            ohlcv = exchange.fetch_ohlcv(ccxt_symbol, tf, limit=limit)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2)
    else:
        raise last_err

    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    return df

# ── Fetch forex/metals (Yahoo Finance direct) ─────────────────────────
def fetch_forex_candles(symbol: str, timeframe: str) -> pd.DataFrame:
    yf_symbol          = FOREX_SYMBOLS.get(symbol.upper())
    if not yf_symbol:
        raise ValueError(f"Symbole forex inconnu : {symbol}")

    interval, period = TIMEFRAME_YF.get(timeframe, ("15m", "7d"))
    df = fetch_yahoo_candles(yf_symbol, interval, period)

    if df.empty:
        raise ValueError(f"Pas de données pour {symbol} en {timeframe}")

    # Resample si nécessaire
    resample_map = {"3m": "3min", "4h": "4h"}
    if timeframe in resample_map:
        df = df.resample(resample_map[timeframe]).agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna()

    return df

# ── Cache court-durée des bougies ─────────────────────────────────────
# Un scan demande la MÊME série plusieurs fois : la 1h est récupérée 5 fois
# par symbole et par scan (get_htf_trend 250, get_ema_confluence 250,
# get_obv_bias 30, _ema_macro_trend 60, get_market_regime 50) — chacune par
# une fonction différente qui ignore que les autres viennent de le faire.
# Sur 5 symboles c'était ~65-95 allers-retours IPC par minute pour ~30
# séries réellement distinctes.
#
# TTL proportionnel au timeframe (tf/20, borné 5-60s) : une bougie 1h
# servie pendant 60s est fraîche à 1.7% près, une bougie 1m pendant 5s à
# 8%. On NE cache PAS par "bucket de bougie" : copy_rates_from_pos inclut
# la bougie EN COURS, la figer jusqu'à la clôture rendrait current_price
# obsolète et changerait les décisions de trade.
#
# À noter : le prix d'exécution ne vient jamais d'ici — place_trade()
# ré-ancre systématiquement SL/TP sur le tick live (symbol_info_tick).
_TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}
_candle_cache: dict = {}          # (symbol, tf) -> {"df", "limit", "expires"}
_candle_cache_lock = threading.Lock()


def _cache_ttl(timeframe: str) -> float:
    secs = _TF_SECONDS.get(timeframe)
    if not secs:
        return 0.0                # timeframe inconnu → pas de cache
    return max(5.0, min(60.0, secs / 20.0))


def clear_candle_cache():
    """Vide le cache — utile après un changement de compte/symbole."""
    with _candle_cache_lock:
        _candle_cache.clear()


def candle_cache_stats() -> dict:
    with _candle_cache_lock:
        return {"entries": len(_candle_cache),
                "keys": [f"{s}:{tf}" for s, tf in _candle_cache]}


# ── Point d'entrée unifié ─────────────────────────────────────────────
def fetch_candles(symbol: str, timeframe: str = "15m", limit: int = 200,
                  use_cache: bool = True) -> pd.DataFrame:
    """Candles depuis MT5 — même feed que l'exécution des trades (plus de
    décalage entre l'analyse TA et le prix réel du broker).

    Passer use_cache=False pour forcer un aller-retour MT5 frais.
    """
    symbol = symbol.upper()
    ttl = _cache_ttl(timeframe)
    if not use_cache or ttl <= 0:
        return fetch_mt5_candles(symbol, timeframe, limit)

    key = (symbol, timeframe)
    now = time.monotonic()

    with _candle_cache_lock:
        hit = _candle_cache.get(key)
        # Une entrée ne sert que si elle couvre AU MOINS le nombre de
        # bougies demandé — sinon l'appelant recevrait une série tronquée.
        if hit and hit["expires"] > now and hit["limit"] >= limit:
            return hit["df"].tail(limit).copy()

    # Fetch hors verrou : un appel MT5 lent ne doit pas bloquer les autres
    # threads du scheduler. Deux threads peuvent fetcher la même série en
    # même temps au pire — sans conséquence, le dernier écrit gagne.
    fetch_limit = max(limit, (hit or {}).get("limit", 0))
    df = fetch_mt5_candles(symbol, timeframe, fetch_limit)

    with _candle_cache_lock:
        _candle_cache[key] = {"df": df, "limit": len(df),
                              "expires": time.monotonic() + ttl}

    return df.tail(limit).copy()

# ── Prix actuel ────────────────────────────────────────────────────────
def get_current_price(symbol: str) -> dict:
    symbol = symbol.upper()

    if symbol in CRYPTO_SYMBOLS:
        ticker = exchange.fetch_ticker(CRYPTO_SYMBOLS[symbol])
        return {
            "symbol":     symbol,
            "price":      round(ticker["last"], 5),
            "change_24h": round(ticker.get("percentage", 0), 2),
            "high_24h":   round(ticker.get("high", 0), 5),
            "low_24h":    round(ticker.get("low", 0), 5),
            "volume":     round(ticker.get("quoteVolume", 0), 2),
        }
    elif symbol in FOREX_SYMBOLS:
        yf_symbol = FOREX_SYMBOLS[symbol]
        url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}"
        params = {"interval": "1m", "range": "1d"}
        resp   = requests.get(url, params=params, headers=YF_HEADERS, timeout=10)
        resp.raise_for_status()
        data   = resp.json()

        result  = data["chart"]["result"][0]
        quotes  = result["indicators"]["quote"][0]
        closes  = [c for c in quotes["close"] if c is not None]
        highs   = [h for h in quotes["high"]  if h is not None]
        lows    = [l for l in quotes["low"]   if l is not None]

        current = closes[-1]
        prev    = closes[0]
        change  = round((current - prev) / prev * 100, 2)

        return {
            "symbol":     symbol,
            "price":      round(current, 5),
            "change_24h": change,
            "high_24h":   round(max(highs), 5),
            "low_24h":    round(min(lows), 5),
            "volume":     0,
        }
    else:
        raise ValueError(f"Symbole inconnu : {symbol}")

def get_all_prices() -> list:
    results = []
    for symbol in list(CRYPTO_SYMBOLS.keys()) + list(FOREX_SYMBOLS.keys()):
        try:
            results.append(get_current_price(symbol))
        except Exception as e:
            results.append({"symbol": symbol, "price": 0, "change_24h": 0, "error": str(e)})
    return results
