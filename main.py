from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.database import init_db
from features.auth.router   import router as auth_router
from features.market.router import router as market_router
from features.smc.router    import router as smc_router

app = FastAPI(
    title="CryptoOracle API",
    description="SMC Analysis — Order Blocks · FVG · Liquidity · OTE",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

app.include_router(auth_router)
app.include_router(market_router)
app.include_router(smc_router)

@app.get("/")
def root():
    return {
        "app":     "CryptoOracle",
        "version": "1.0.0",
        "status":  "running",
        "docs":    "/docs",
    }

@app.get("/health")
def health():
    return {"status": "ok"}
