import argparse
import os
import sys
import io
import uvicorn
from dotenv import load_dotenv

# Force UTF-8 output on Windows (prevents UnicodeEncodeError for arrows, emojis, etc.)
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

# ── CLI arguments — must be parsed BEFORE any module that imports runtime ────
parser = argparse.ArgumentParser(description="CryptoOracle backend")
parser.add_argument("--port",      type=int, default=8000,
                    help="HTTP port (default: 8000)")
parser.add_argument("--mt5-path",  type=str, default=None,
                    help="Path to MT5 terminal64.exe (default: auto-detect)")
parser.add_argument("--instance",  type=str, default=None,
                    help="Instance label, e.g. 'account_A' (default: port number)")
args, _ = parser.parse_known_args()   # parse_known_args = safe with uvicorn reload

# ── Load the right .env file for this port ───────────────────────────────────
# Priority: .env.<port>  →  .env  (fallback)
env_file = f".env.{args.port}"
if os.path.exists(env_file):
    load_dotenv(env_file, override=True)
    print(f"[ENV] Loaded {env_file}")
else:
    load_dotenv(".env", override=True)
    print(f"[ENV] {env_file} not found — using .env fallback")

# ── Apply to runtime config before any other import uses it ──────────────────
from core.config import runtime
runtime.port       = args.port
runtime.mt5_path   = args.mt5_path
runtime.instance   = args.instance or f"instance_{args.port}"
runtime.db_path    = f"crypto_oracle_{args.port}.db"  # one DB per account
runtime.ml_db_path = "crypto_oracle_ml.db"            # shared across all accounts

print(f"[STARTUP] instance={runtime.instance}  port={runtime.port}  "
      f"mt5_path={runtime.mt5_path or 'auto'}  db={runtime.db_path}")

# ── App imports (after runtime is configured) ─────────────────────────────────
from core.database import init_db
from features.auth.router    import router as auth_router
from features.market.router  import router as market_router
from features.smc.router     import router as smc_router
from features.trading.router import router as trading_router, auto_scan, scalp_auto_scan, manage_open_positions
from features.zones.router   import router as zones_router

app = FastAPI(
    title=f"CryptoOracle API [{runtime.instance}]",
    description="SMC Analysis - Order Blocks · FVG · Liquidity · OTE",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*", "Authorization", "Content-Type", "ngrok-skip-browser-warning"],
    allow_credentials=False,
    max_age=3600,
)

scheduler = BackgroundScheduler()

def _zone_weekly_retrain():
    """Weekly zone model retrain — runs every Sunday at 02:00 server time."""
    print("[ZONE-SCHEDULE] Weekly retrain starting …")
    try:
        from features.zones.trainer import run_full_pipeline
        from features.zones.scorer  import reload_zone_db
        result = run_full_pipeline()
        reload_zone_db()
        print(f"[ZONE-SCHEDULE] Retrain complete: {result}")
    except Exception as e:
        print(f"[ZONE-SCHEDULE] Retrain failed: {e}")


@app.on_event("startup")
def startup():
    init_db()
    try:
        from features.trading.ml_model import load_model
        load_model()
    except Exception as e:
        print(f"[ML] Startup load skipped: {e}")
    try:
        from features.trading.data_collector import load_scalp_log
        from features.trading.router import scalp_history
        persisted = load_scalp_log(500)
        scalp_history.extend(persisted)
        print(f"[SCALP-LOG] Restored {len(persisted)} signal records from disk")
    except Exception as e:
        print(f"[SCALP-LOG] Restore skipped: {e}")
    try:
        from features.zones.trainer import load_zone_model
        from features.zones.scorer  import reload_zone_db
        load_zone_model()
        reload_zone_db()
    except Exception as e:
        print(f"[ZONE-ML] Startup load skipped: {e}")
    # coalesce=True  → si un tick est manqué, on l'exécute UNE seule fois (pas de rattrapage)
    # misfire_grace_time → tolérance avant de considérer un tick comme manqué (en secondes)
    scheduler.add_job(auto_scan,             'interval', minutes=3,  id='auto_scan',
                      coalesce=True, misfire_grace_time=60)
    scheduler.add_job(scalp_auto_scan,       'interval', minutes=1,  id='scalp_scan',
                      coalesce=True, misfire_grace_time=30)
    scheduler.add_job(manage_open_positions, 'interval', seconds=30, id='position_manager',
                      coalesce=True, misfire_grace_time=15)
    scheduler.add_job(_zone_weekly_retrain,  'cron',     day_of_week='sun', hour=2, minute=0,
                      id='zone_retrain',     coalesce=True, misfire_grace_time=3600)
    scheduler.start()

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()

app.include_router(auth_router)
app.include_router(market_router)
app.include_router(smc_router)
app.include_router(trading_router)
app.include_router(zones_router)

@app.get("/")
def root():
    return {
        "app":      "CryptoOracle",
        "version":  "1.0.0",
        "status":   "running",
        "instance": runtime.instance,
        "port":     runtime.port,
        "docs":     "/docs",
    }

@app.get("/health")
def health():
    return {"status": "ok", "instance": runtime.instance}


if __name__ == "__main__":
    # ── Single-instance guard ─────────────────────────────────────────────
    # Windows allows several processes to bind the SAME port (SO_REUSEADDR),
    # so duplicate launches don't crash — they all run, all trade, and all
    # force MT5 re-logins (terminal flips accounts every few seconds).
    # If something already answers on this port, refuse to start.
    import socket
    _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _probe.settimeout(1.0)
    _already = _probe.connect_ex(("127.0.0.1", runtime.port)) == 0
    _probe.close()
    if _already:
        print(f"[FATAL] Une instance tourne déjà sur le port {runtime.port} — "
              f"fermez-la d'abord (ou utilisez --port différent). Sortie.")
        sys.exit(1)

    uvicorn.run("main:app", host="0.0.0.0", port=runtime.port, reload=False)
