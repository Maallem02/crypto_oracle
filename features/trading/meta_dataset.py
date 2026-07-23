"""
Meta-dataset builder — joins scalp_log (decision-time reasoning) with
trade_signals (outcome) on ticket number, producing a flat, labeled
dataset ready for training a future meta-model.

This is a different layer than ml_model.py or the zone model: those learn
from raw indicators / historical zones. This one learns from the bot's
OWN meta-decisions — which boosts fired, whether a CHoCH switch happened,
whether the LG pattern was wick_rejection vs sweep_recovery — to answer
"when should we trust the existing scoring stack, and when not?"

Not enough volume yet to train on (needs ~200+ closed, logged trades,
same threshold ml_model.py uses) — this just keeps the dataset assembled
and growing in the right shape for whenever it is.
"""
import json
import re
from datetime import datetime
from core.database import get_db

MIN_SAMPLES = 200   # same bar ml_model.py uses before activating a filter

# Boolean flags extracted from the free-text "conditions" list logged at
# decision time — these capture which subsystems fired, not just scores.
_FLAG_PATTERNS = {
    "had_choch_switch":   re.compile(r"switching LG"),
    "had_choch_confirm":  re.compile(r"confirms LG direction"),
    "had_choch_reject":   re.compile(r"COUNTER-TREND REJECTED"),
    "had_cisd":           re.compile(r"CISD (bullish|bearish)"),
    "had_ob_confluence":  re.compile(r"OB (inside|approaching) aligned"),
    "had_fvg_confluence": re.compile(r"FVG (inside|approaching) aligned"),
    "stoch_cross":        re.compile(r"Stoch cross"),
    "ema50_aligned":      re.compile(r"EMA50 aligned"),
}


def _conditions_text(conditions) -> str:
    if isinstance(conditions, list):
        return " | ".join(conditions)
    return str(conditions or "")


def _extract_flags(conditions) -> dict:
    text = _conditions_text(conditions)
    return {name: bool(pat.search(text)) for name, pat in _FLAG_PATTERNS.items()}


def _extract_lg_meta(conditions) -> dict:
    """Pull LG pattern + strength from text like
    'LG bearish (wick_rejection, str=0.073) +30.7pts'."""
    text = _conditions_text(conditions)
    m = re.search(r"LG (bullish|bearish) \((\w+), str=([\d.]+)\)", text)
    if not m:
        return {"lg_pattern": None, "lg_strength_parsed": None}
    return {"lg_pattern": m.group(2), "lg_strength_parsed": float(m.group(3))}


def build_meta_dataset() -> list:
    """
    Join scalp_log decisions with trade_signals outcomes on ticket.
    Returns one row per CLOSED trade — open trades have no outcome yet
    and are skipped. Cheap query, no caching, safe to call repeatedly.
    """
    conn = get_db()
    # outcome/profit live on scalp_log itself now — covers ALL strategies
    # (lg_primary, support_resistance, m1_momentum) uniformly. The old
    # trade_signals join only ever had rows for lg_primary, so S/R and M1
    # trades silently never appeared in any stats query.
    log_rows = conn.execute(
        "SELECT ticket, data, outcome, profit, closed_at FROM scalp_log "
        "WHERE ticket IS NOT NULL AND outcome IS NOT NULL"
    ).fetchall()

    dataset = []
    for ticket, data_json, outcome, profit, closed_at in log_rows:
        entry = json.loads(data_json)

        conditions = entry.get("conditions")
        htf        = entry.get("htf_trends") or {}

        try:
            ts = datetime.fromisoformat(entry.get("timestamp"))
            hour, dow = ts.hour, ts.weekday()
        except Exception:
            hour, dow = None, None

        row = {
            "ticket":        ticket,
            "timestamp":     entry.get("timestamp"),
            "symbol":        entry.get("symbol"),
            "timeframe":     entry.get("timeframe"),
            "action":        entry.get("action"),
            "signal_type":   entry.get("signal_type", "lg_primary"),
            "score":         entry.get("score"),
            "blended_score": entry.get("blended_score"),
            "ml_win_prob":   entry.get("ml_win_prob"),
            "news_boost":    entry.get("news_boost"),
            "zone_boost":    entry.get("zone_boost"),
            "ema_boost":     entry.get("ema_boost"),
            "rr":            entry.get("rr"),
            "htf_consensus": entry.get("htf_consensus"),
            "htf_5m":        htf.get("5m"),
            "htf_30m":       htf.get("30m"),
            "htf_1h":        htf.get("1h"),
            "hour":          hour,
            "day_of_week":   dow,
            **_extract_flags(conditions),
            **_extract_lg_meta(conditions),
            "outcome":       outcome,
            "profit":        profit,
            "closed_at":     closed_at,
        }
        dataset.append(row)

    conn.close()
    dataset.sort(key=lambda r: r["ticket"])
    return dataset


MIN_TRADES_FOR_VERDICT = 15   # below this, expectancy is statistical noise


def _risk_dollars(symbol: str, action: str, volume: float,
                  entry: float, sl: float, profit, outcome) -> float | None:
    """Dollar value of 1R (entry→SL distance) for the executed volume.
    Primary: MT5 order_calc_profit (handles JPY/exotic conversions).
    Fallback: |profit| for trades closed exactly at SL (outcome=0)."""
    try:
        import MetaTrader5 as mt5
        from features.trading.executor import SYMBOL_MAP, _mt5_init
        mt5sym = SYMBOL_MAP.get((symbol or "").upper())
        if mt5sym and entry and sl and volume:
            _mt5_init()
            otype = mt5.ORDER_TYPE_BUY if action == "buy" else mt5.ORDER_TYPE_SELL
            p = mt5.order_calc_profit(otype, mt5sym, float(volume), float(entry), float(sl))
            if p is not None and abs(p) > 1e-9:
                return abs(p)
    except Exception:
        pass
    if outcome == 0 and profit is not None and abs(profit) > 1e-9:
        return abs(profit)   # SL hit → loss ≈ 1R by definition
    return None


def _r_bucket() -> dict:
    return {"trades": 0, "wins": 0, "losses": 0, "sum_r": 0.0,
            "sum_win_r": 0.0, "sum_loss_r": 0.0, "unmeasured": 0}


def _r_add(bucket: dict, r: float | None, outcome: int):
    bucket["trades"] += 1
    if outcome == 1:
        bucket["wins"] += 1
    else:
        bucket["losses"] += 1
    if r is None:
        bucket["unmeasured"] += 1
        return
    bucket["sum_r"] += r
    if r >= 0:
        bucket["sum_win_r"] += r
    else:
        bucket["sum_loss_r"] += r


def _r_final(bucket: dict) -> dict:
    n = bucket["trades"] - bucket["unmeasured"]
    wins, losses = bucket["wins"], bucket["losses"]
    out = {
        "trades": bucket["trades"],
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / bucket["trades"] * 100, 1) if bucket["trades"] else 0.0,
        "expectancy_R": round(bucket["sum_r"] / n, 2) if n else None,
        "total_R": round(bucket["sum_r"], 2) if n else None,
        "avg_win_R": round(bucket["sum_win_r"] / wins, 2) if wins and n else None,
        "avg_loss_R": round(bucket["sum_loss_r"] / losses, 2) if losses and n else None,
    }
    if bucket["unmeasured"]:
        out["unmeasured_trades"] = bucket["unmeasured"]
    if bucket["trades"] < MIN_TRADES_FOR_VERDICT:
        out["verdict"] = f"insufficient data ({bucket['trades']}/{MIN_TRADES_FOR_VERDICT} trades)"
    elif out["expectancy_R"] is None:
        out["verdict"] = "no measurable trades"
    elif out["expectancy_R"] >= 0.15:
        out["verdict"] = "positive — keep"
    elif out["expectancy_R"] >= -0.05:
        out["verdict"] = "breakeven — watch"
    else:
        out["verdict"] = "negative — review this rule/strategy"
    return out


def compute_expectancy_stats(since: str = None) -> dict:
    """Expectancy in R (profit ÷ dollar risked at entry) per strategy,
    per symbol, and per rule-cohort. The one number that answers
    'is the math positive?' — win rate alone can't (38% @ RR2.5 beats
    71% @ RR0.4)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT data, outcome, profit FROM scalp_log "
        "WHERE ticket IS NOT NULL AND outcome IS NOT NULL"
    ).fetchall()
    conn.close()

    overall = _r_bucket()
    by_strategy: dict = {}
    by_symbol: dict = {}
    cohorts = {
        "4h_pullback_waiver":  _r_bucket(),   # entered via 'Counter-trend waived — 4H pullback'
        "1h_master_conflict":  _r_bucket(),   # entered while 5m/1h were in conflict (1H-master rule)
        "widened_pd_band":     _r_bucket(),   # entered in the 65-80% / 20-35% P/D zone opened for with-4H trades
    }

    for data_json, outcome, profit in rows:
        try:
            d = json.loads(data_json)
        except Exception:
            continue
        if since and (d.get("timestamp") or "") < since:
            continue
        res    = d.get("result") or {}
        text   = _conditions_text(d.get("conditions"))
        symbol = d.get("symbol")
        risk = _risk_dollars(symbol, d.get("action"), res.get("volume"),
                             res.get("price") or d.get("entry"),
                             res.get("sl") or d.get("sl"), profit, outcome)
        r = round(profit / risk, 2) if (risk and profit is not None) else None

        _r_add(overall, r, outcome)
        _r_add(by_strategy.setdefault(d.get("signal_type", "lg_primary"), _r_bucket()), r, outcome)
        _r_add(by_symbol.setdefault(symbol or "?", _r_bucket()), r, outcome)

        if "Counter-trend waived" in text:
            _r_add(cohorts["4h_pullback_waiver"], r, outcome)
        if d.get("htf_consensus") == "conflict":
            _r_add(cohorts["1h_master_conflict"], r, outcome)
        m = re.search(r"premium BUY \((?:deep_)?premium (\d+)%\)", text) if d.get("action") == "buy" \
            else re.search(r"discount SELL \((?:deep_)?discount (\d+)%\)", text)
        if m:
            pct = int(m.group(1))
            if (d.get("action") == "buy" and 65 < pct <= 80) or \
               (d.get("action") == "sell" and 20 <= pct < 35):
                _r_add(cohorts["widened_pd_band"], r, outcome)

    return {
        "unit": "R = profit ÷ dollars risked at entry (SL distance × volume)",
        "how_to_read": "expectancy_R > 0 → profitable math; breakeven is 0. "
                       "A -1R loss and a +2.5R win are one full SL and one full TP.",
        "overall": _r_final(overall),
        "by_strategy": {k: _r_final(b) for k, b in by_strategy.items()},
        "by_symbol": {k: _r_final(b) for k, b in by_symbol.items()},
        "rule_cohorts": {k: _r_final(b) for k, b in cohorts.items() if b["trades"]},
    }


def get_meta_dataset_stats() -> dict:
    """Readiness check — how much labeled meta-data exists so far."""
    data = build_meta_dataset()
    wins = sum(1 for r in data if r["outcome"] == 1)
    return {
        "labeled_samples":    len(data),
        "wins":               wins,
        "losses":             len(data) - wins,
        "win_rate_pct":       round(wins / len(data) * 100, 1) if data else 0.0,
        "ready_for_training": len(data) >= MIN_SAMPLES,
        "progress":           f"{min(len(data), MIN_SAMPLES)}/{MIN_SAMPLES}",
    }


def export_meta_dataset_csv(path: str = "meta_dataset.csv") -> int:
    """Write the current meta-dataset to CSV. Returns row count written."""
    import csv
    data = build_meta_dataset()
    if not data:
        return 0
    cols = list(data[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(data)
    return len(data)


def evaluate_gate_rejections(reason_contains: str, lookback_candles: int = 8,
                              timeframe: str = "5m") -> dict:
    """
    For every PERSISTED rejection matching `reason_contains` (e.g. "M1
    momentum"), check what price actually did over the following
    `lookback_candles` candles — did it move in the direction the gate
    favored (a correct block) or in the original LG direction (a good
    trade that got blocked)?

    Needs rejection_log data accumulated AFTER this logging was added —
    historical rejections before that point were never persisted.
    """
    import pandas as pd
    from features.trading.data_collector import load_rejections
    from features.market.fetcher import fetch_candles

    rejections = load_rejections(limit=2000, reason_contains=reason_contains)
    results = []

    for r in rejections:
        symbol = r.get("symbol")
        bias   = r.get("bias")
        price0 = r.get("price")
        ts     = r.get("ts")
        if not symbol or not bias or price0 is None or not ts:
            continue

        try:
            df = fetch_candles(symbol, timeframe, limit=150)
            df = df.reset_index()
            ts_dt  = pd.to_datetime(ts)
            future = df[df["timestamp"] > ts_dt].head(lookback_candles)
            if future.empty:
                continue

            final_price = float(future["close"].iloc[-1])
            moved = final_price - float(price0)

            # bias = the direction that got BLOCKED. If price moved AGAINST
            # that blocked direction, the gate made the right call.
            gate_correct = moved < 0 if bias == "buy" else moved > 0

            results.append({
                "symbol": symbol, "bias_blocked": bias, "ts": ts,
                "price_at_rejection": price0, "price_after": final_price,
                "moved": round(moved, 5), "gate_correct": gate_correct,
            })
        except Exception:
            continue

    correct = sum(1 for r in results if r["gate_correct"])
    return {
        "reason_filter":  reason_contains,
        "total_checked":  len(results),
        "gate_correct":   correct,
        "gate_wrong":     len(results) - correct,
        "accuracy_pct":   round(correct / len(results) * 100, 1) if results else 0.0,
        "details":        results,
    }
