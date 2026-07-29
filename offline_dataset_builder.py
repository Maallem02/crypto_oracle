"""Offline training-dataset builder (Phase 1).

Replays historical M5 candles through the live signal engine
(run_scalping_analysis) bar by bar and labels every directional signal
with a UNIFORM bracket: entry = bar close, SL = 1.5*ATR14, TP = 2.5R,
conservative same-bar double-touch = loss. Samples (features + label)
are appended to offline_dataset.sqlite; the run is resumable per
symbol/timeframe via the meta table.

Usage:
  python offline_dataset_builder.py --symbol BTC --months 6 --max-eval 15000
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import warnings
from contextlib import redirect_stdout
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from features.smc.engine import run_scalping_analysis  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offline_dataset.sqlite")
MT5_SYMBOLS = {"BTC": "BTCUSDm", "ETH": "ETHUSDm", "SOL": "SOLUSDm", "BNB": "BNBUSDm",
               "XRP": "XRPUSDm", "XAUUSD": "XAUUSDm", "XAGUSD": "XAGUSDm",
               "EURUSD": "EURUSDm", "USDJPY": "USDJPYm", "GBPJPY": "GBPJPYm"}
WINDOW = 100          # bars the live scan feeds the engine
SL_ATR = 1.5
RR = 2.5
LABEL_HORIZON = 864   # 3 days of M5 bars

RSI_RE   = re.compile(r"RSI ([\d.]+)")
STOCH_RE = re.compile(r"Stoch (\d+)")
PD_RE    = re.compile(r"\((?:deep_)?(?:premium|discount|equilibrium)?\s?([\d.]+)%\)")
LG_RE    = re.compile(r"LG (bullish|bearish) \((\w+), str=([\d.]+)\)")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, symbol TEXT, tf TEXT, bias TEXT,
        score REAL, should_scalp INTEGER, veto TEXT,
        lg_pattern TEXT, lg_strength REAL,
        had_cisd INTEGER, had_choch_confirm INTEGER,
        had_ob INTEGER, had_fvg INTEGER, ema50_aligned INTEGER,
        rsi REAL, stoch REAL, pd_pos REAL, adx REAL, atr REAL,
        hour INTEGER, dow INTEGER, macro4h TEXT,
        entry REAL, risk REAL,
        label INTEGER, r_outcome REAL, mfe_r REAL, mae_r REAL, bars_held INTEGER,
        UNIQUE(ts, symbol, tf))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS meta (
        symbol TEXT, tf TEXT, last_ts TEXT, PRIMARY KEY(symbol, tf))""")
    return conn


def fetch_full(symbol: str, months: int) -> pd.DataFrame:
    import MetaTrader5 as mt5
    if not mt5.initialize(timeout=15000):
        raise RuntimeError(f"mt5 init failed: {mt5.last_error()}")
    m = MT5_SYMBOLS[symbol]
    mt5.symbol_select(m, True)
    rates = mt5.copy_rates_range(m, mt5.TIMEFRAME_M5,
                                 datetime.now() - timedelta(days=months * 31),
                                 datetime.now())
    mt5.shutdown()
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"no M5 history for {m}")
    df = pd.DataFrame(rates)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("timestamp", inplace=True)
    df = df.rename(columns={"tick_volume": "volume"})
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def atr14(df: pd.DataFrame) -> np.ndarray:
    tr = np.maximum(df["high"] - df["low"],
         np.maximum((df["high"] - df["close"].shift()).abs(),
                    (df["low"] - df["close"].shift()).abs()))
    return tr.rolling(14).mean().values


def macro4h_series(df: pd.DataFrame) -> pd.Series:
    """EMA20-slope trend proxy on resampled 4H, mapped to each M5 bar."""
    h4 = df["close"].resample("4h").last().dropna()
    ema = h4.ewm(span=20).mean()
    trend = pd.Series("neutral", index=h4.index)
    rising = ema > ema.shift(2)
    trend[(h4 > ema) & rising] = "bullish"
    trend[(h4 < ema) & ~rising] = "bearish"
    return trend.reindex(df.index, method="ffill").fillna("neutral")


def classify_veto(conds: str) -> str:
    if "COUNTER-TREND REJECTED" in conds:
        return "counter_trend"
    if "P/D REJECTED" in conds:
        return "pd"
    if "No Liquidity Grab" in conds:
        return "no_lg"
    return ""


def label_bracket(highs, lows, i, bias, entry, risk):
    sl = entry - risk if bias == "buy" else entry + risk
    tp = entry + risk * RR if bias == "buy" else entry - risk * RR
    mfe = mae = 0.0
    end = min(i + 1 + LABEL_HORIZON, len(highs))
    for j in range(i + 1, end):
        hi, lo = highs[j], lows[j]
        f = (hi - entry) / risk if bias == "buy" else (entry - lo) / risk
        a = (entry - lo) / risk if bias == "buy" else (hi - entry) / risk
        mfe, mae = max(mfe, f), max(mae, a)
        hit_sl = lo <= sl if bias == "buy" else hi >= sl
        hit_tp = hi >= tp if bias == "buy" else lo <= tp
        if hit_sl:                     # conservative: stop first on double-touch
            return 0, -1.0, mfe, -mae, j - i
        if hit_tp:
            return 1, RR, mfe, -mae, j - i
    return None, None, mfe, -mae, end - 1 - i


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="5m")
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--max-eval", type=int, default=20000)
    args = ap.parse_args()
    symbol = args.symbol.upper()

    t_start = time.time()
    df = fetch_full(symbol, args.months)
    highs, lows, closes = df["high"].values, df["low"].values, df["close"].values
    atr = atr14(df)
    m4h = macro4h_series(df).values
    idx_ts = df.index

    conn = get_db()
    row = conn.execute("SELECT last_ts FROM meta WHERE symbol=? AND tf=?",
                       (symbol, args.tf)).fetchone()
    start_i = WINDOW
    if row and row[0]:
        pos = idx_ts.searchsorted(pd.Timestamp(row[0]), side="right")
        start_i = max(WINDOW, int(pos))
    end_i = len(df) - 1            # last bar kept for labeling only
    print(f"{symbol} {args.tf}: {len(df)} bars {idx_ts[0]} -> {idx_ts[-1]} | "
          f"resume at bar {start_i}/{end_i}", flush=True)

    devnull = io.StringIO()
    n_eval = n_saved = 0
    batch = []
    last_ts_done = None

    for i in range(start_i, end_i):
        if n_eval >= args.max_eval:
            break
        n_eval += 1
        window = df.iloc[i - WINDOW + 1: i + 1]
        try:
            devnull.seek(0); devnull.truncate(0)
            with redirect_stdout(devnull):
                a = run_scalping_analysis(window.copy(), symbol, macro_trend=m4h[i])
        except Exception:
            continue
        last_ts_done = idx_ts[i]
        bias = a.get("bias")
        if bias not in ("buy", "sell"):
            continue
        risk = SL_ATR * atr[i]
        if not np.isfinite(risk) or risk <= 0:
            continue
        conds = " | ".join(str(c) for c in (a.get("conditions") or []))
        lg = LG_RE.search(conds)
        rsi_m, st_m, pd_m = RSI_RE.search(conds), STOCH_RE.search(conds), PD_RE.search(conds)
        label, r_out, mfe, mae, held = label_bracket(highs, lows, i, bias, closes[i], risk)
        batch.append((
            idx_ts[i].isoformat(), symbol, args.tf, bias,
            float(a.get("scalping_score") or 0), int(bool(a.get("should_scalp"))),
            classify_veto(conds),
            lg.group(2) if lg else None, float(lg.group(3)) if lg else None,
            int("CISD bullish" in conds or "CISD bearish" in conds),
            int("confirms LG direction" in conds),
            int("OB inside aligned" in conds or "OB approaching aligned" in conds),
            int("FVG inside aligned" in conds or "FVG approaching aligned" in conds),
            int("EMA50 aligned" in conds),
            float(rsi_m.group(1)) if rsi_m else None,
            float(st_m.group(1)) if st_m else None,
            float(pd_m.group(1)) if pd_m else None,
            float(a.get("adx") or 0), float(a.get("atr") or 0),
            int(idx_ts[i].hour), int(idx_ts[i].dayofweek), str(m4h[i]),
            float(closes[i]), float(risk),
            label, r_out, float(mfe), float(mae), int(held),
        ))
        if len(batch) >= 500:
            conn.executemany("INSERT OR IGNORE INTO samples VALUES "
                             "(NULL," + ",".join("?" * 29) + ")", batch)
            conn.commit()
            n_saved += len(batch)
            batch = []
            print(f"  bar {i}/{end_i} saved={n_saved} "
                  f"({(time.time()-t_start)/60:.1f} min)", flush=True)

    if batch:
        conn.executemany("INSERT OR IGNORE INTO samples VALUES "
                         "(NULL," + ",".join("?" * 29) + ")", batch)
        n_saved += len(batch)
    if last_ts_done is not None:
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?,?)",
                     (symbol, args.tf, last_ts_done.isoformat()))
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM samples WHERE symbol=?", (symbol,)).fetchone()[0]
    done = last_ts_done == idx_ts[end_i - 1] if last_ts_done is not None else False
    print(f"DONE-CHUNK {symbol}: evaluated={n_eval} saved={n_saved} total_in_db={total} "
          f"| finished={done} | {(time.time()-t_start)/60:.1f} min", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
