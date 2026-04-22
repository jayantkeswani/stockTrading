from pathlib import Path

from pydantic_settings import BaseSettings

# Find .env: check backend/.env first, then root .env
_backend_dir = Path(__file__).resolve().parent.parent
_env_file = _backend_dir / ".env"
if not _env_file.exists():
    _env_file = _backend_dir.parent / ".env"


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading"
    database_url_sync: str = "postgresql://trader:trader_dev_123@localhost:5433/stocktrading"

    # Redis
    redis_url: str = "redis://localhost:6380/0"

    # Fyers API
    fyers_app_id: str = ""
    fyers_secret_key: str = ""
    fyers_redirect_uri: str = "http://127.0.0.1:8080/api/v1/auth/fyers/callback"

    # Fyers Login Credentials (for auto-login)
    fyers_username: str = ""
    fyers_pin: str = ""
    fyers_totp_secret: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Trading Config
    trading_capital: int = 1_000_000  # 10 Lakhs INR in paise... no, in rupees
    max_daily_drawdown_pct: float = 5.0
    max_risk_per_trade_pct: float = 2.0
    max_trades_per_day: int = 3
    paper_trading: bool = True

    # Agent
    yolo_mode: bool = False

    # AI Research
    google_api_key: str = ""
    research_llm_provider: str = "gemini"
    research_llm_model: str = "gemini-2.5-flash"
    research_agent_timeout_seconds: int = 90
    research_max_concurrent: int = 3

    # App
    backend_host: str = "0.0.0.0"
    backend_port: int = 8080
    frontend_url: str = "http://localhost:3000"

    model_config = {"env_file": str(_env_file), "env_file_encoding": "utf-8"}


settings = Settings()
