import argparse
import uvicorn
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

# ── Apply to runtime config before any other import uses it ──────────────────
from core.config import runtime
runtime.port     = args.port
runtime.mt5_path = args.mt5_path
runtime.instance = args.instance or f"instance_{args.port}"
runtime.db_path  = f"crypto_oracle_{args.port}.db"   # one DB per account

print(f"[STARTUP] instance={runtime.instance}  port={runtime.port}  "
      f"mt5_path={runtime.mt5_path or 'auto'}  db={runtime.db_path}")

# ── App imports (after runtime is configured) ─────────────────────────────────
from core.database import init_db
from features.auth.router    import router as auth_router
from features.market.router  import router as market_router
from features.smc.router     import router as smc_router
from features.trading.router import router as trading_router, auto_scan, scalp_auto_scan

app = FastAPI(
    title=f"CryptoOracle API [{runtime.instance}]",
    description="SMC Analysis - Order Blocks · FVG · Liquidity · OTE",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

scheduler = BackgroundScheduler()

@app.on_event("startup")
def startup():
    init_db()
    scheduler.add_job(auto_scan,       'interval', minutes=6, id='auto_scan')
    scheduler.add_job(scalp_auto_scan, 'interval', minutes=1, id='scalp_scan')
    scheduler.start()

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()

app.include_router(auth_router)
app.include_router(market_router)
app.include_router(smc_router)
app.include_router(trading_router)

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
    uvicorn.run("main:app", host="0.0.0.0", port=runtime.port, reload=False)
