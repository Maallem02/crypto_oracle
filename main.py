from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from core.database import init_db
from features.auth.router    import router as auth_router
from features.market.router  import router as market_router
from features.smc.router     import router as smc_router
from features.trading.router import router as trading_router, auto_scan, scalp_auto_scan

app = FastAPI(
    title="CryptoOracle API",
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
    scheduler.add_job(auto_scan,       'interval', minutes=15, id='auto_scan')
    scheduler.add_job(scalp_auto_scan, 'interval', minutes=2,  id='scalp_scan')
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
    return {"app": "CryptoOracle", "version": "1.0.0", "status": "running", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok"}