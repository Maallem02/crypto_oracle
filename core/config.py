from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings:
    SECRET_KEY: str = "crypto_oracle_super_secret_key_change_in_prod"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Assets supportés
    CRYPTO_ASSETS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
    FOREX_ASSETS  = ["XAUUSD", "XAGUSD", "GBPJPY"]
    ALL_ASSETS    = CRYPTO_ASSETS + FOREX_ASSETS

    # Timeframes supportés
    TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h"]

settings = Settings()
