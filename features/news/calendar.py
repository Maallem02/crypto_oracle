"""
Economic calendar — news protection + momentum detection.
Data: Forex Factory JSON feed (free, no auth).
Cache: refreshed every 30 min to avoid hammering the API.
"""
import threading
import requests
from datetime import datetime, timedelta, timezone

FF_URL    = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_TTL = 1800   # 30 minutes

_cache: dict = {"events": [], "ts": None}
_lock        = threading.Lock()

# ── Currency / symbol mappings ────────────────────────────────────────────────

COUNTRY_CURRENCY: dict[str, str] = {
    "United States": "USD", "Euro Zone": "EUR", "United Kingdom": "GBP",
    "Japan":         "JPY", "Canada":    "CAD", "Australia":      "AUD",
    "New Zealand":   "NZD", "Switzerland":"CHF","Germany":        "EUR",
    "France":        "EUR", "Italy":     "EUR", "Spain":          "EUR",
    "China":         "CNY",
}

# Currencies that affect each tradable symbol
SYMBOL_CURRENCIES: dict[str, list[str]] = {
    "EURUSD": ["EUR", "USD"],
    "GBPJPY": ["GBP", "JPY"],
    "XAUUSD": ["USD"],      # gold inversely tied to USD strength
    "BTC":    ["USD"],      # macro USD risk-on/off affects crypto
    "ETH":    ["USD"],
    "SOL":    ["USD"],
    "XRP":    ["USD"],
    "BNB":    ["USD"],
    "USDJPY": ["USD", "JPY"],
    "GBPUSD": ["GBP", "USD"],
}

# Indicators where higher actual = bearish for that currency
_NEGATIVE_KW = {
    "unemployment", "jobless", "claims", "deficit",
    "trade balance", "current account",
}


def _get_currencies(symbol: str) -> list[str]:
    # Strip broker suffix (e.g. GBPJPYM → GBPJPY)
    sym = symbol.upper()
    for suffix in ("M", "USD", "USDT"):
        if sym.endswith(suffix) and len(sym) > len(suffix):
            stripped = sym[: -len(suffix)]
            if stripped in SYMBOL_CURRENCIES:
                sym = stripped
                break
    for key, curs in SYMBOL_CURRENCIES.items():
        if key == sym:
            return curs
    # Fallback: split 6-char forex pair
    if len(sym) == 6:
        return [sym[:3], sym[3:]]
    return []


def _fetch() -> list:
    with _lock:
        now = datetime.now(timezone.utc)
        if _cache["ts"] and (now - _cache["ts"]).total_seconds() < CACHE_TTL:
            return _cache["events"]
        try:
            r = requests.get(FF_URL, timeout=8)
            r.raise_for_status()
            _cache["events"] = r.json()
            _cache["ts"]     = now
            print(f"[NEWS] Calendar refreshed — {len(_cache['events'])} events loaded")
        except Exception as e:
            print(f"[NEWS] Fetch failed (using stale cache): {e}")
        return _cache["events"]


def _parse_dt(event: dict) -> datetime | None:
    try:
        dt = datetime.fromisoformat(event["date"])
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_num(s: str) -> float | None:
    if not s:
        return None
    s = s.strip().replace(",", "")
    mult = 1.0
    if s.upper().endswith("K"):   s, mult = s[:-1], 1e3
    elif s.upper().endswith("M"): s, mult = s[:-1], 1e6
    elif s.upper().endswith("B"): s, mult = s[:-1], 1e9
    try:
        return float(s.replace("%", "").strip()) * mult
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def is_news_blocked(symbol: str, before: int = 15, after: int = 10) -> dict:
    """
    Return {blocked, reason} if a HIGH/MEDIUM impact event is imminent or just released
    for any currency that affects this symbol.

    Blocking window: [event_time - before_min, event_time + after_min]
    HIGH impact → full window.  MEDIUM impact → 10 min before / 5 min after.
    """
    events = _fetch()
    now    = datetime.now(timezone.utc)
    curs   = _get_currencies(symbol)
    if not curs:
        return {"blocked": False}

    for ev in events:
        impact = ev.get("impact", "")
        if impact not in ("High", "Medium"):
            continue

        country  = ev.get("country", "")
        currency = COUNTRY_CURRENCY.get(country, country[:3].upper() if country else "")
        if currency not in curs:
            continue

        dt = _parse_dt(ev)
        if not dt:
            continue

        mb = before if impact == "High" else 10
        ma = after  if impact == "High" else 5

        if dt - timedelta(minutes=mb) <= now <= dt + timedelta(minutes=ma):
            mins = (dt - now).total_seconds() / 60
            label = f"in {mins:.0f}min" if mins >= 0 else f"{abs(mins):.0f}min ago"
            return {
                "blocked":  True,
                "reason":   f"{ev.get('title', '?')} ({currency} {impact}) {label}",
                "impact":   impact,
                "currency": currency,
                "event":    ev,
            }

    return {"blocked": False}


def get_news_momentum(symbol: str, lookback: int = 20) -> dict:
    """
    Detect a HIGH-impact event released within the last `lookback` minutes
    and return its implied directional bias for this symbol.

    Direction rules:
      actual > forecast  →  currency BEAT  →  bullish for base, bearish for quote
      For XAUUSD / BTC / ETH: USD beat → bearish (dollar strength = risk-off)
      Negative indicators (unemployment, jobless claims…) are inverted.

    Score boost decays linearly: +15 pts fresh → +5 pts at 20 min.
    """
    events = _fetch()
    now    = datetime.now(timezone.utc)
    curs   = _get_currencies(symbol)
    if not curs:
        return {"detected": False}

    for ev in sorted(events, key=lambda e: e.get("date", ""), reverse=True):
        if ev.get("impact") != "High":
            continue

        actual_s   = (ev.get("actual",   "") or "").strip()
        forecast_s = (ev.get("forecast", "") or "").strip()
        previous_s = (ev.get("previous", "") or "").strip()

        if not actual_s:          # not released yet
            continue

        country  = ev.get("country", "")
        currency = COUNTRY_CURRENCY.get(country, country[:3].upper() if country else "")
        if currency not in curs:
            continue

        dt = _parse_dt(ev)
        if not dt:
            continue

        mins_since = (now - dt).total_seconds() / 60
        if not (0 < mins_since <= lookback):
            continue

        actual = _parse_num(actual_s)
        ref    = _parse_num(forecast_s) or _parse_num(previous_s)
        if actual is None or ref is None:
            continue

        beat = actual > ref

        # Invert for "bad = higher" indicators
        title_low = ev.get("title", "").lower()
        if any(kw in title_low for kw in _NEGATIVE_KW):
            beat = not beat

        # Map beat/miss → symbol direction
        sym = symbol.upper()
        is_inverse = any(s in sym for s in ("XAU", "BTC", "ETH", "SOL"))
        base_cur   = curs[0]

        if is_inverse:
            # USD beat → these assets fall
            direction = "bearish" if beat else "bullish"
        elif currency == base_cur:
            direction = "bullish" if beat else "bearish"
        else:
            direction = "bearish" if beat else "bullish"

        # Score boost: 15 pts at 0 min, decays to 5 pts at 20 min
        boost = round(max(5.0, 15.0 - mins_since * 0.5), 1)

        return {
            "detected":     True,
            "direction":    direction,
            "currency":     currency,
            "title":        ev.get("title", ""),
            "actual":       actual_s,
            "forecast":     forecast_s,
            "minutes_since": round(mins_since, 1),
            "boost":        boost,
        }

    return {"detected": False}
