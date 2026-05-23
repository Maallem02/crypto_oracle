import MetaTrader5 as mt5
import os
from dotenv import load_dotenv
from core.config import runtime

load_dotenv()

def connect():
    kwargs = {"path": runtime.mt5_path} if runtime.mt5_path else {}
    if not mt5.initialize(**kwargs):
        raise Exception(f"MT5 initialize failed: {mt5.last_error()}")
    
    authorized = mt5.login(
        login=int(os.getenv("MT5_LOGIN")),
        password=os.getenv("MT5_PASSWORD"),
        server=os.getenv("MT5_SERVER"),
    )
    
    if not authorized:
        raise Exception(f"MT5 login failed: {mt5.last_error()}")
    
    info = mt5.account_info()
    return {
        "balance":  info.balance,
        "equity":   info.equity,
        "currency": info.currency,
        "leverage": info.leverage,
        "server":   info.server,
    }

def disconnect():
    mt5.shutdown()