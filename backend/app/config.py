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
    telegram_chat_ids: str = ""  # comma-separated chat IDs
    telegram_enabled: bool = True

    @property
    def telegram_chat_id_set(self) -> set[str]:
        """Parse telegram_chat_ids into a set."""
        if not self.telegram_chat_ids:
            return set()
        return {cid.strip() for cid in self.telegram_chat_ids.split(",") if cid.strip()}

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
    vertex_ai_location: str = "global"
    research_llm_provider: str = "gemini"
    research_llm_model: str = "gemini-3.5-flash"
    research_llm_model_pro: str = "gemini-3.1-pro-preview"
    research_agent_timeout_seconds: int = 90
    research_max_concurrent: int = 3

    # Signal Confidence LLM Overlay (Phase 2)
    ai_confidence_enabled: bool = True            # Toggle LLM overlay for signals
    ai_confidence_timeout_seconds: int = 25       # Timeout for LLM call; never blocks signal
    ai_confidence_min_confidence: float = 50.0    # Skip LLM overlay below this raw confidence (saves calls on weak signals)
    ai_confidence_max_concurrency: int = 6        # Cap concurrent overlay LLM calls. gemini-3.5-flash on Vertex global uses Dynamic Shared Quota (no fixed RPM) — latency-bound, not quota-bound. Observed saturation ~11 concurrent → 14% timeouts; 6 sits below that and clears a post-floor candle-close burst within the 25s budget. Tune by timeout rate: lower to 4 if storms persist, raise if signals feel delayed.
    # NOTE: fire_confidence_threshold removed — now per-strategy as min_confidence_to_persist
    # in strategy_configs.parameters JSONB (see services/strategy_params.py)

    # Intraday Hunter agent (discretionary index-options trade SUGGESTER, MANUAL-alert only).
    # Autorun = an 08:45 IST Call 1 (thesis) scheduled task + a Call 2 watcher hooked into the
    # 1m candle-close loop (09:18-09:30). Off → neither fires (the /intraday-hunter page + the
    # manual run-call1/run-call2 endpoints still work). The LLM call uses the `claude` CLI on
    # CLAUDE_CODE_OAUTH_TOKEN (subscription, not API credits) — when absent the calls SKIP safely.
    intraday_hunter_enabled: bool = True
    intraday_hunter_variant: str = "D"  # promoted prompt variant (build_system_prompt); D = C + VIX-regime fix

    # Intraday Hunter v2 (parallel PAPER strategy + learning-loop data capture; v1 untouched).
    # Off → no v2 Call 1/Call 2, no minute log, no ATM±2 capture, no teacher/grade/review jobs.
    # Tunables (basket exit, gates, cadence, Call 2 model) live in
    # strategy_configs.parameters['intraday_hunter_v2'] — see services/intraday_hunter_v2/params.py.
    intraday_hunter_v2_enabled: bool = True
    # Default Call 2 model (text-only, latency target < 45s); the params row `call2_model` overrides.
    intraday_hunter_v2_call2_model: str = "claude-sonnet-5-5"
    # Teacher ingestion runs yt-dlp/ffmpeg/tesseract in-process; off → the scheduled jobs no-op
    # (ingestion can still be pushed from a Mac via POST /intraday-hunter/teacher/ingest).
    ih_teacher_ingest_enabled: bool = True
    # Jev (TypeSafe AI decision model via OpenRouter) — a SHADOW-ONLY arm logged into
    # ih_minute_log.arms.jev; it never trades. Needs OPENROUTER_API_KEY. Never commit the key.
    jev_enabled: bool = False
    jev_model: str = "typesafe/jev-1.13"
    openrouter_api_key: str = ""
    # Optional 5-level DepthUpdate capture for the order-flow features (index futures + ATM CE/PE).
    ih_v2_depth_enabled: bool = False

    # Market Mode
    market_mode: str = "live"  # "live" | "simulated"
    simulator_url: str = "http://localhost:8787"

    # App
    backend_host: str = "0.0.0.0"
    backend_port: int = 8080
    frontend_url: str = "http://localhost:3000"
    app_version: str = "dev"
    deployed_at: str = ""

    model_config = {"env_file": str(_env_file), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
