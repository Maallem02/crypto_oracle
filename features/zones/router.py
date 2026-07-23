"""
Zones API — trigger training pipeline and query zone model status.

Endpoints:
  POST /zones/train          — run full pipeline (detect → label → train) in background
  GET  /zones/status         — model metadata + zone database summary
  GET  /zones/nearby         — query nearest zones for a symbol/price (debug)
"""

import threading
from fastapi import APIRouter, BackgroundTasks, Query

router = APIRouter(prefix="/zones", tags=["zones"])

_training_status: dict = {"running": False, "last_result": None, "error": None}


def _run_pipeline_bg(symbols: list | None, candles: int):
    global _training_status
    _training_status["running"] = True
    _training_status["error"]   = None
    try:
        from features.zones.trainer  import run_full_pipeline
        from features.zones.scorer   import reload_zone_db
        result = run_full_pipeline(symbols=symbols, candles=candles)
        reload_zone_db()
        _training_status["last_result"] = result
        print(f"[ZONE-PIPELINE] Completed: {result}")
    except Exception as e:
        _training_status["error"] = str(e)
        print(f"[ZONE-PIPELINE] Error: {e}")
    finally:
        _training_status["running"] = False


@router.post("/train")
def train_zones(
    background_tasks: BackgroundTasks,
    candles: int = Query(default=20000, ge=1000, le=50000,
                         description="H1 candles per symbol (20000 ≈ 2.3 years)"),
    symbols: str = Query(default="", description="Comma-separated symbols, empty = all 6"),
):
    """
    Launch the full zone training pipeline in the background.
    Returns immediately — poll /zones/status to track progress.

    Timeline: ~60–120 s for 6 symbols × 20 000 candles.
    """
    if _training_status["running"]:
        return {"status": "already_running", "message": "Training is already in progress"}

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] or None
    background_tasks.add_task(_run_pipeline_bg, sym_list, candles)

    return {
        "status":  "started",
        "symbols": sym_list or "all",
        "candles": candles,
        "message": "Training started in background — poll /zones/status",
    }


@router.get("/status")
def zones_status():
    """Return zone model metadata and zone database summary."""
    from features.zones.trainer  import get_zone_model_info
    from features.zones.detector import load_zone_database
    import os

    model_info = get_zone_model_info()

    db = load_zone_database()
    db_summary = {sym: len(zones) for sym, zones in db.items()}
    broken  = {sym: sum(1 for z in zones if z.get("broken"))  for sym, zones in db.items()}
    touches = {sym: sum(z.get("touch_count",  0) for z in zones) for sym, zones in db.items()}

    return {
        "training_running": _training_status["running"],
        "last_error":       _training_status["error"],
        "model":            model_info,
        "database": {
            "zones_per_symbol":   db_summary,
            "total_zones":        sum(db_summary.values()),
            "broken_per_symbol":  broken,
            "total_touches":      touches,
            "db_file_exists":     os.path.exists("zone_database.json"),
            "model_file_exists":  os.path.exists("zone_model.pkl"),
        },
    }


@router.get("/nearby")
def nearby_zones(
    symbol: str  = Query(..., description="e.g. EURUSD"),
    price:  float = Query(..., description="Current or entry price"),
    bias:   str  = Query(..., description="buy or sell"),
    atr:    float = Query(default=0.0, description="Current ATR (0 = auto 0.1% of price)"),
    limit:  int  = Query(default=5, ge=1, le=20, description="Max zones to return"),
):
    """Debug endpoint: return the N nearest zones for a symbol/price/bias."""
    from features.zones.detector import load_zone_database

    db    = load_zone_database()
    zones = db.get(symbol.upper(), [])
    if not zones:
        return {"symbol": symbol, "zones": [], "message": "No zone database for this symbol"}

    target = "demand" if bias == "buy" else "supply"
    atr    = atr if atr > 0 else price * 0.001
    prox   = 2.0 * atr

    result = []
    for z in zones:
        if z["type"] != target:
            continue
        if z.get("broken"):
            continue

        if bias == "buy":
            dist = price - z["price_high"]
        else:
            dist = z["price_low"] - price

        if dist < -atr or dist > prox:
            continue

        tc = z.get("touch_count",  0)
        bc = z.get("bounce_count", 0)
        result.append({
            **z,
            "distance":    round(dist, 6),
            "bounce_rate": round(bc / max(tc, 1), 2),
        })

    result.sort(key=lambda x: abs(x["distance"]))
    return {
        "symbol":      symbol.upper(),
        "price":       price,
        "bias":        bias,
        "zones_found": len(result),
        "zones":       result[:limit],
    }


@router.post("/reload")
def reload_zones():
    """
    Hot-reload zone database and model from disk into the running bot.
    Call this after running rebuild_zones.py (no restart needed).
    """
    from features.zones.trainer import load_zone_model
    from features.zones.scorer  import reload_zone_db
    from features.zones.trainer import get_zone_model_info
    import os

    ok_model = load_zone_model()
    reload_zone_db()

    info = get_zone_model_info()
    return {
        "status":      "reloaded",
        "model_ok":    ok_model,
        "trained_at":  info.get("trained_at"),
        "samples":     info.get("samples"),
        "accuracy_pct": info.get("accuracy_pct"),
        "auc_pct":     info.get("auc_pct"),
        "db_exists":   os.path.exists("zone_database.json"),
    }
