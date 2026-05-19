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
    telegram_enabled: bool = True

    # Trading Config — SEED-ONLY: used once at first startup to populate the
    # trading_config DB table. After seeding, all runtime code reads from the DB
    # via app.services.trading_config.get_trading_config(). Do not read these
    # settings.* fields directly from application logic.
    trading_capital: int = 1_000_000
    max_daily_drawdown_pct: float = 5.0
    max_risk_per_trade_pct: float = 2.0
    max_trades_per_day: int = 3
    paper_trading: bool = True
    yolo_mode: bool = False  # True → seeds autonomy_level as "YOLO"

    # AI Research
    google_api_key: str = ""
    gcp_project_id: str = ""
    vertex_ai_location: str = "asia-south1"
    research_llm_provider: str = "gemini"
    research_llm_model: str = "gemini-3-flash-preview"
    research_agent_timeout_seconds: int = 90
    research_max_concurrent: int = 3

    # Signal Confidence LLM Overlay (Phase 2)
    ai_confidence_enabled: bool = True            # Toggle LLM overlay for signals
    ai_confidence_timeout_seconds: int = 25       # Timeout for LLM call; never blocks signal
    # NOTE: fire_confidence_threshold removed — now per-strategy as min_confidence_to_persist
    # in strategy_configs.parameters JSONB (see services/strategy_params.py)

    # App
    backend_host: str = "0.0.0.0"
    backend_port: int = 8080
    frontend_url: str = "http://localhost:3000"
    app_version: str = "dev"
    deployed_at: str = ""

    model_config = {"env_file": str(_env_file), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
