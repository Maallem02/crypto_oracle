import MetaTrader5 as mt5
import os
from dotenv import load_dotenv

load_dotenv()

def connect():
    if not mt5.initialize():
        raise Exception("MT5 initialize failed")
    
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