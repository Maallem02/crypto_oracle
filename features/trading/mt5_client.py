import MetaTrader5 as mt5
import os
from dotenv import load_dotenv
from core.config import runtime

load_dotenv()

def connect():
    login_id = os.getenv("MT5_LOGIN")
    password  = os.getenv("MT5_PASSWORD")
    server    = os.getenv("MT5_SERVER")

    kwargs = {"timeout": 10000}
    if runtime.mt5_path:
        kwargs["path"] = runtime.mt5_path
    # Passer les credentials directement dans initialize() résout l'erreur -6
    # "Authorization failed" sur les terminaux broker qui exigent l'auth à l'init
    if login_id and password and server:
        kwargs["login"]    = int(login_id)
        kwargs["password"] = password
        kwargs["server"]   = server

    if not mt5.initialize(**kwargs):
        code, msg = mt5.last_error()
        hints = {
            -6:     "Terminal ouvert mais non autorisé — vérifiez MT5_LOGIN/PASSWORD/SERVER dans .env",
            -10005: "IPC timeout — MT5 n'est pas ouvert ou le chemin --mt5-path est incorrect",
        }
        raise Exception(f"MT5 initialize failed ({code}): {msg} → {hints.get(code, 'voir mt5.last_error()')}")

    # ── Si déjà connecté (terminal ouvert + scan en cours) → renvoyer direct ──
    # Appeler mt5.login() sur une session déjà active provoque une erreur 500.
    info = mt5.account_info()
    if info is not None:
        return {
            "balance":  info.balance,
            "equity":   info.equity,
            "currency": info.currency,
            "leverage": info.leverage,
            "server":   info.server,
        }

    # ── Pas encore authentifié → login explicite ─────────────────────────────
    login_id = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server   = os.getenv("MT5_SERVER")

    if not login_id or not password or not server:
        raise Exception(
            "MT5 credentials manquants — vérifiez MT5_LOGIN / MT5_PASSWORD / MT5_SERVER dans .env"
        )

    authorized = mt5.login(
        login=int(login_id),
        password=password,
        server=server,
    )

    if not authorized:
        raise Exception(f"MT5 login failed: {mt5.last_error()}")

    info = mt5.account_info()
    if info is None:
        raise Exception("MT5 connecté mais account_info() retourne None")

    return {
        "balance":  info.balance,
        "equity":   info.equity,
        "currency": info.currency,
        "leverage": info.leverage,
        "server":   info.server,
    }

def disconnect():
    mt5.shutdown()