import pandas as pd
import numpy as np
import requests
import ccxt
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
}

TIMEFRAME_CCXT = {
    "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h",
}

TIMEFRAME_YF = {
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
    """Fetch OHLCV depuis Yahoo Finance API directement"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_symbol}"
    params = {
        "interval": interval,
        "range":    period,
        "events":   "history",
    }
    resp = requests.get(url, params=params, headers=YF_HEADERS, timeout=10)
    resp.raise_for_status()
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

    tf    = TIMEFRAME_CCXT.get(timeframe, "15m")
    ohlcv = exchange.fetch_ohlcv(ccxt_symbol, tf, limit=limit)

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

    # Resample 4h depuis 1h si nécessaire
    if timeframe == "4h":
        df = df.resample("4h").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna()

    return df

# ── Point d'entrée unifié ─────────────────────────────────────────────
def fetch_candles(symbol: str, timeframe: str = "15m", limit: int = 200) -> pd.DataFrame:
    symbol = symbol.upper()
    if symbol in CRYPTO_SYMBOLS:
        return fetch_crypto_candles(symbol, timeframe, limit)
    elif symbol in FOREX_SYMBOLS:
        return fetch_forex_candles(symbol, timeframe)
    else:
        raise ValueError(f"Symbole inconnu : {symbol}")

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
