import pandas as pd
import numpy as np
import requests
import ccxt
import time
import os
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from core.config import runtime

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
    if mt5.terminal_info() is not None:
        return
    kwargs = {"timeout": 10000}
    if runtime.mt5_path:
        kwargs["path"] = runtime.mt5_path
    login_id = os.getenv("MT5_LOGIN")
    password  = os.getenv("MT5_PASSWORD")
    server    = os.getenv("MT5_SERVER")
    if login_id and password and server:
        kwargs["login"]    = int(login_id)
        kwargs["password"] = password
        kwargs["server"]   = server
    mt5.initialize(**kwargs)

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

# ── Point d'entrée unifié ─────────────────────────────────────────────
def fetch_candles(symbol: str, timeframe: str = "15m", limit: int = 200) -> pd.DataFrame:
    """Candles depuis MT5 — même feed que l'exécution des trades (plus de
    décalage entre l'analyse TA et le prix réel du broker)."""
    symbol = symbol.upper()
    return fetch_mt5_candles(symbol, timeframe, limit)

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
