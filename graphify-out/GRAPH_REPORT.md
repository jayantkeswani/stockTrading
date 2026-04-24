# Graph Report - .  (2026-04-24)

## Corpus Check
- 216 files · ~80,000 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2655 nodes · 7881 edges · 77 communities detected
- Extraction: 39% EXTRACTED · 61% INFERRED · 0% AMBIGUOUS · INFERRED: 4805 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Models & Indicators Core|Models & Indicators Core]]
- [[_COMMUNITY_Data Sources & Task Registry|Data Sources & Task Registry]]
- [[_COMMUNITY_Agent Execution & Logging|Agent Execution & Logging]]
- [[_COMMUNITY_API Layer & Auth|API Layer & Auth]]
- [[_COMMUNITY_Futures & Options Resolution|Futures & Options Resolution]]
- [[_COMMUNITY_Architecture & System Docs|Architecture & System Docs]]
- [[_COMMUNITY_Frontend Charts & UI|Frontend Charts & UI]]
- [[_COMMUNITY_Agent API & Market Data|Agent API & Market Data]]
- [[_COMMUNITY_Backtest Harness & Utils|Backtest Harness & Utils]]
- [[_COMMUNITY_Strategy Engine (VWAP & CAN SLIM)|Strategy Engine (VWAP & CAN SLIM)]]
- [[_COMMUNITY_Candle Pattern Detection|Candle Pattern Detection]]
- [[_COMMUNITY_VWAP & Intraday Bias|VWAP & Intraday Bias]]
- [[_COMMUNITY_Global Market & Confidence|Global Market & Confidence]]
- [[_COMMUNITY_CAN SLIM Scoring|CAN SLIM Scoring]]
- [[_COMMUNITY_LLM Signal Confidence|LLM Signal Confidence]]
- [[_COMMUNITY_ORM Models & Trading Config|ORM Models & Trading Config]]
- [[_COMMUNITY_Volume Analysis & Base Patterns|Volume Analysis & Base Patterns]]
- [[_COMMUNITY_Backtest Context Builder & CPR|Backtest Context Builder & CPR]]
- [[_COMMUNITY_Open Interest Analysis|Open Interest Analysis]]
- [[_COMMUNITY_Previous Day Levels|Previous Day Levels]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 99|Community 99]]
- [[_COMMUNITY_Community 100|Community 100]]
- [[_COMMUNITY_Community 119|Community 119]]
- [[_COMMUNITY_Community 120|Community 120]]
- [[_COMMUNITY_Community 121|Community 121]]
- [[_COMMUNITY_Community 122|Community 122]]
- [[_COMMUNITY_Community 123|Community 123]]
- [[_COMMUNITY_Community 124|Community 124]]
- [[_COMMUNITY_Community 125|Community 125]]
- [[_COMMUNITY_Community 126|Community 126]]
- [[_COMMUNITY_Community 131|Community 131]]
- [[_COMMUNITY_Community 132|Community 132]]
- [[_COMMUNITY_Community 133|Community 133]]
- [[_COMMUNITY_Community 134|Community 134]]
- [[_COMMUNITY_Community 135|Community 135]]
- [[_COMMUNITY_Community 136|Community 136]]
- [[_COMMUNITY_Community 137|Community 137]]

## God Nodes (most connected - your core abstractions)
1. `Candle` - 298 edges
2. `MarketContext` - 193 edges
3. `StrategyName` - 182 edges
4. `SignalType` - 172 edges
5. `VWAPResult` - 172 edges
6. `PreviousDayLevels` - 161 edges
7. `InstrumentType` - 139 edges
8. `OIAnalysis` - 139 edges
9. `DayBias` - 134 edges
10. `StrategySignal` - 121 edges

## Surprising Connections (you probably didn't know these)
- `formatINR() - Indian Rupee Currency Formatter` --semantically_similar_to--> `Color: Profit Green (#00e68a)`  [INFERRED] [semantically similar]
  frontend/src/lib/formatters.ts → frontend/CLAUDE.md
- `Next.js 15 - App Router Framework` --conceptually_related_to--> `globe.svg - Globe/Web Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/README.md → frontend/public/globe.svg
- `Next.js 15 - App Router Framework` --conceptually_related_to--> `window.svg - Browser Window/App Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/README.md → frontend/public/window.svg
- `Frontend README - Next.js Project Bootstrap Info` --conceptually_related_to--> `file.svg - Document/File Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/README.md → frontend/public/file.svg
- `Next.js 15 - App Router Framework` --references--> `next.svg - Next.js Wordmark Logo (black, 394x80)`  [INFERRED]
  frontend/README.md → frontend/public/next.svg

## Hyperedges (group relationships)
- **Signal Pipeline Components** — backend_claude_strategy_runner, backend_claude_option_resolver, backend_claude_signal_confidence, backend_claude_shadow_executor, architecture_websocket_manager [EXTRACTED 1.00]
- **Active Trading Strategies** — strategy2_vwap_pullback, strategy4_canslim, strategy1_orb, strategy3_gamma_scalping [EXTRACTED 1.00]
- **Market Context Indicators** — backend_claude_indicators, strategy2_composite_intraday_bias, strategy2_confidence_scoring, strategy2_market_context_fields [INFERRED 0.85]
- **Agent Autonomy Execution Paths** — backend_claude_agent_runner, backend_claude_auto_executor, backend_claude_shadow_executor, backend_claude_trade_monitor [EXTRACTED 1.00]
- **Backtest Pipeline** — backend_claude_backtest_module, backtest_accurate_mode, backtest_fast_mode, backtest_confidence_calibration, backtest_oi_coverage [EXTRACTED 1.00]
- **Shadow Data Viewing Paths** — frontend_claude_shadow_toggle, frontend_claude_active_positions, shadow_agent_doc, shadow_agent_month_end_queries [EXTRACTED 1.00]

## Communities

### Community 0 - "Models & Indicators Core"
Cohesion: 0.06
Nodes (232): Base, BaseStrategy, ExitSignal, MarketContext, Abstract base strategy class.  All strategies must subclass BaseStrategy and imp, Check if an open position should be exited.          Returns ExitSignal if exit, Signal generated by a strategy., Signal to exit a position. (+224 more)

### Community 1 - "Data Sources & Task Registry"
Cohesion: 0.03
Nodes (190): ABC, AgentResult, BaseResearchAgent, Shared context pre-fetched by DataGatherer, passed to all agents.      Prevents, Output from a single research sub-agent., Wrapper that catches unexpected errors and returns a failed result., research(), ResearchContext (+182 more)

### Community 2 - "Agent Execution & Logging"
Cohesion: 0.02
Nodes (155): agent_status(), confirm_action(), AgentLog, AgentRunner, Main agent loop — runs as asyncio background task within FastAPI.  Supports mult, Main monitoring loop., Start the agent monitoring loop., Called when a new executable signal is generated.          Always sends a Telegr (+147 more)

### Community 3 - "API Layer & Auth"
Cohesion: 0.01
Nodes (153): request(), fyers_callback(), fyers_login(), fyers_status(), Fyers OAuth2 authentication endpoints.  Provides browser-based login flow: 1. GE, Redirect the browser to the Fyers OAuth2 authorization page., Handle the OAuth2 callback from Fyers.      Fyers redirects back with either `au, Check whether we have a valid Fyers access token. (+145 more)

### Community 4 - "Futures & Options Resolution"
Cohesion: 0.03
Nodes (81): _fetch_futures_ltp(), find_index_futures_expiry(), _find_nearest_monthly_expiry(), FuturesResolution, _get_lot_size(), _last_dow_of_month(), Futures resolver — translates stock-level signals into tradeable futures contrac, Find the last Thursday of the current month (NSE stock futures expiry).      If (+73 more)

### Community 5 - "Architecture & System Docs"
Cohesion: 0.02
Nodes (106): Backtest Harness (context_builder + harness + exit_simulator), Expiry Schedule Post-SEBI Nov 2024, FastAPI Backend (Python 3.11, port 8080), FeedManager — Tick Aggregator and Candle Builder, Fyers API — Market Data Feed, Phase 2 Intraday Bias (6-factor weighted composite), Design Decision: Decoupled strategy evaluation from candle pipeline, Design Decision: Paper trading first (PAPER_TRADING=true) (+98 more)

### Community 6 - "Frontend Charts & UI"
Cohesion: 0.04
Nodes (92): ChartModal Overlay, PriceChart TradingView Component, ActionableLevels Component, ConfidenceFactorsBar Component (local), Markdown Component, PeriodFilter Component, RecommendationBadge Component, ReportHistory Component (+84 more)

### Community 7 - "Agent API & Market Data"
Cohesion: 0.05
Nodes (70): AgentConfirmRequest, AgentLogResponse, AgentStatusResponse, Toggle YOLO mode — persisted to DB and reflected immediately., YoloToggleRequest, BaseModel, BatchPriceRequest, CandleResponse (+62 more)

### Community 8 - "Backtest Harness & Utils"
Cohesion: 0.04
Nodes (73): main(), Enum, ExitReason, _get_sl_pct(), _make_result(), Exit simulator — determines how a backtest signal resolves.  Two modes:   ACCURA, Simulate trade from entry_ts until exit.      spot_candles_after: [(timestamp, C, _simulate_accurate() (+65 more)

### Community 9 - "Strategy Engine (VWAP & CAN SLIM)"
Cohesion: 0.06
Nodes (30): BasePattern, Detected chart base pattern with breakout level., CANSLIMStrategy, _make_canslim_data(), _make_context(), _make_uptrend_daily_bars(), TestCANSLIMStrategyMeta, TestEvaluate (+22 more)

### Community 10 - "Candle Pattern Detection"
Cohesion: 0.04
Nodes (41): is_bearish_engulfing(), is_bearish_pin_bar(), is_bearish_reversal(), is_bullish_engulfing(), is_bullish_pin_bar(), is_bullish_reversal(), is_doji(), Candlestick pattern detection for entry confirmation. (+33 more)

### Community 11 - "VWAP & Intraday Bias"
Cohesion: 0.04
Nodes (28): average_volume(), Calculate average volume over last N candles., is_blocked_by_bias(), TestAverageVolume, TestIsBlockedByBias, Comprehensive tests for VWAP indicator., A single candle with huge volume should pull VWAP toward its typical price., Price above VWAP -> positive %. (+20 more)

### Community 12 - "Global Market & Confidence"
Cohesion: 0.06
Nodes (28): compute_confidence(), bearish_engulfing_candles(), bullish_engulfing_candles(), make_context(), make_cpr(), make_global_cues(), make_oi(), make_prev_day() (+20 more)

### Community 13 - "CAN SLIM Scoring"
Cohesion: 0.05
Nodes (29): compute_canslim_total(), _interpolate(), CAN SLIM scoring functions — pure, no side effects.  Each function scores a CAN, Score New highs / proximity to 52-week high (0-100).      Full marks: Within 5%, Score Supply/Demand (0-100).      Full marks: Low free float (< 40%), volume sur, Score Leader / Relative Strength (0-100).      Full marks: RS >= 90     Zero: RS, Score Institutional sponsorship (0-100).      Full marks: Both FII and MF holdin, Linear interpolation between 0 and 100.      Returns 0 if value <= zero_at, 100 (+21 more)

### Community 14 - "LLM Signal Confidence"
Cohesion: 0.05
Nodes (32): create_llm_client(), _extract_json(), GeminiClient, Provider-agnostic LLM client for research agents.  Default: Gemini 2.5 Flash via, Factory: create the configured LLM client., Extract JSON from LLM response text.      Handles: clean JSON, markdown code blo, Result from an LLM call with web search grounding., Attempt to repair truncated JSON by finding the last valid position.      Strate (+24 more)

### Community 15 - "ORM Models & Trading Config"
Cohesion: 0.06
Nodes (48): Base, TimestampMixin, DailySummary, DeclarativeBase, FundamentalHistory, CAN SLIM fundamental data models.  StockFundamental: Pre-fetched CAN SLIM scores, Pre-fetched CAN SLIM fundamental data per stock.      One row per F&O eligible s, Quarterly fundamental snapshots for trend analysis.      Used to detect EPS acce (+40 more)

### Community 16 - "Volume Analysis & Base Patterns"
Cohesion: 0.05
Nodes (33): detect_any_base_pattern(), detect_cup_with_handle(), detect_double_bottom(), detect_flat_base(), Chart base pattern detection for CAN SLIM entry timing.  Detects three classical, Detect a flat base pattern.      Criteria:     - Prior uptrend (price rose >= 20, Detect a double bottom (W-shape) pattern.      Criteria:     - Two distinct lows, Try all three pattern detectors, return the first match.      Priority: Cup-with (+25 more)

### Community 17 - "Backtest Context Builder & CPR"
Cohesion: 0.06
Nodes (31): _aggregate_to_5m(), build_historical_context(), _fetch_candles_1m(), _get_global_cues(), _get_india_vix(), _get_oi_analysis(), _get_previous_day_levels(), calculate_cpr() (+23 more)

### Community 18 - "Open Interest Analysis"
Cohesion: 0.06
Nodes (28): analyze_option_chain(), _calculate_max_pain(), is_oi_supporting_direction(), Open Interest analysis for institutional confirmation.  Tracks OI distribution a, Check if OI supports the trade direction.      For CALL: current price should be, Analyze option chain OI data.      Args:         strikes: List of dicts with key, Calculate max pain strike (where total buyer losses are maximum)., _make_strikes() (+20 more)

### Community 19 - "Previous Day Levels"
Cohesion: 0.06
Nodes (26): analyze_previous_day(), is_gap_down(), is_gap_up(), Previous day analysis for directional bias.  Analyzes previous day's OHLC to det, Analyze previous day candle for directional bias.      Bullish: close > open AND, Check if today opened with a gap up (> threshold % above PDC)., Check if today opened with a gap down (> threshold % below PDC)., Tests for previous day analysis indicator. (+18 more)

### Community 20 - "Community 20"
Cohesion: 0.08
Nodes (40): auto_login(), auto_login_and_store(), _compute_app_id_hash(), _exchange_auth_code_for_token(), FyersAutoLoginError, _generate_totp(), _get_auth_code(), _get_reauth_lock() (+32 more)

### Community 21 - "Community 21"
Cohesion: 0.08
Nodes (20): eachDayInRange(), endOfDayIST(), endOfMonthIST(), formatINR(), formatINRCompact(), formatTime(), fromIST(), isoDateIST() (+12 more)

### Community 22 - "Community 22"
Cohesion: 0.12
Nodes (15): compute_rr_ratio(), find_swing_high(), find_swing_low(), select_index_sl_target(), _select_resistance(), _select_support(), _candle(), _cpr() (+7 more)

### Community 23 - "Community 23"
Cohesion: 0.09
Nodes (15): compute_50_dma(), compute_rs_raw_score(), is_above_50_dma(), percentile_rank_rs(), Relative Strength (RS) indicator for CAN SLIM.  Computes IBD-style Relative Stre, Compute raw RS weighted return from daily close prices.      Uses IBD-style weig, Convert raw RS scores to percentile ranks (1-99).      IBD RS Rating ranks a sto, Compute 50-day simple moving average.      Returns None if fewer than 50 data po (+7 more)

### Community 24 - "Community 24"
Cohesion: 0.09
Nodes (31): Convention: All API Calls via lib/api.ts Only, Convention: All Pages are Client Components (use client directive), Convention: All WebSocket Events via useWebSocket.ts, Color: Accent Amber/Gold (#d4a843) - Institutional Highlight, Color: Loss Red (#ff4060), Color: Profit Green (#00e68a), Design Principle: Density First - Tight Padding, Compact Rows, Design Theme: Institutional Terminal - Bloomberg-Inspired Hedge Fund Aesthetic (+23 more)

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (23): BatchEvaluateRequest, evaluate_strategy(), evaluate_strategy_batch(), ManualEvaluateRequest, Manually trigger a single strategy evaluation for a symbol., Manually trigger a strategy evaluation across all its configured symbols., Tests for the strategies API endpoints — evaluate, auto-mode toggle, batch evalu, Unknown strategy name should raise HTTPException. (+15 more)

### Community 26 - "Community 26"
Cohesion: 0.11
Nodes (11): Tests for VIX processor., Exactly VIX_HIGH threshold., Exactly VIX_EXTREME threshold., VIX exactly 14: not < 14 => not LOW => NORMAL., TestClassifyVix, TestGetPositionSizeMultiplier, classify_vix(), get_position_size_multiplier() (+3 more)

### Community 27 - "Community 27"
Cohesion: 0.16
Nodes (19): async_retry(), Async retry helper with exponential backoff and jitter.  Used for safety-critica, Retry an async function with exponential backoff and jitter.      Args:, Decorator that wraps an async function with async_retry.      Retry params are s, with_retry(), _make_flaky(), Tests for the async retry helper., Return an async callable that fails fail_times then succeeds. (+11 more)

### Community 28 - "Community 28"
Cohesion: 0.16
Nodes (17): BrokerError, DataFeedError, DrawdownHalt, InsufficientDataError, MarketClosedError, MaxTradesExceeded, Custom exception hierarchy., Raised when daily drawdown limit is hit. (+9 more)

### Community 29 - "Community 29"
Cohesion: 0.15
Nodes (13): Agent Decision Flow (2s loop), Candle Backfill on Startup (prev day + today), Fyers Authentication Flow (Manual/Auto TOTP), OI Data Flow (APScheduler every 3 min), Price Data Pipeline, Strategy Signal Flow (Auto + Manual paths), Symbol Master Flow (CSV download + Redis cache), System Overview — Indian Options Trading Platform (+5 more)

### Community 30 - "Community 30"
Cohesion: 0.2
Nodes (10): Frontend AGENTS.md - Next.js Agent Usage Warning, Frontend README - Next.js Project Bootstrap Info, Next.js Agent Warning - Breaking Changes vs Training Data, Next.js 15 - App Router Framework, file.svg - Document/File Icon (grey, 16x16), globe.svg - Globe/Web Icon (grey, 16x16), next.svg - Next.js Wordmark Logo (black, 394x80), vercel.svg - Vercel Triangle Logo (white, 1155x1000) (+2 more)

### Community 31 - "Community 31"
Cohesion: 0.29
Nodes (4): AppShell(), subscribeSymbols(), useWebSocket(), loadWatchlist()

### Community 32 - "Community 32"
Cohesion: 0.4
Nodes (2): inPeriod(), key()

### Community 33 - "Community 33"
Cohesion: 0.53
Nodes (4): formatSignalTime(), handleExecuted(), handleReject(), handleWatchlist()

### Community 34 - "Community 34"
Cohesion: 0.47
Nodes (3): formatISTTime(), handleClose(), toggleExpand()

### Community 37 - "Community 37"
Cohesion: 0.4
Nodes (5): AI Trading Agent (MANUAL/SEMI/YOLO autonomy), Design Decision: Single-process asyncio agent within FastAPI, Position Monitor — SL/Target/Time Exit, Telegram Notifications, Agent Autonomy Levels (MANUAL/SEMI/YOLO)

### Community 39 - "Community 39"
Cohesion: 0.5
Nodes (1): add trading_config table  Revision ID: 3f00a98c6835 Revises: e0f67c55d282 Create

### Community 40 - "Community 40"
Cohesion: 0.5
Nodes (1): initial schema  Revision ID: e28ebbb474c4 Revises:  Create Date: 2026-04-15 18:5

### Community 41 - "Community 41"
Cohesion: 0.5
Nodes (1): add trade source and position is_shadow  Revision ID: f1a3c9e27b85 Revises: d79b

### Community 42 - "Community 42"
Cohesion: 0.5
Nodes (1): add auto_mode to strategy_configs  Revision ID: 32d975c5e56e Revises: 8d468bc0be

### Community 43 - "Community 43"
Cohesion: 0.5
Nodes (1): add canslim fundamental data tables and position_type columns  Revision ID: 3525

### Community 44 - "Community 44"
Cohesion: 0.5
Nodes (1): add symbol_map to strategy_configs  Revision ID: bdf125f158c4 Revises: 35259f294

### Community 45 - "Community 45"
Cohesion: 0.5
Nodes (1): add ai fields to signals  Revision ID: d79b95128c64 Revises: 3e647bfdf471 Create

### Community 46 - "Community 46"
Cohesion: 0.5
Nodes (1): add research_reports and research_agent_runs tables  Revision ID: e0f67c55d282 R

### Community 47 - "Community 47"
Cohesion: 0.5
Nodes (1): add executable and blocked_reason to signals  Revision ID: 373982ce98a4 Revises:

### Community 48 - "Community 48"
Cohesion: 0.5
Nodes (1): add lots quantity sizing_meta to signals  Revision ID: b39cb8e83680 Revises: 3f0

### Community 49 - "Community 49"
Cohesion: 0.5
Nodes (1): add global_market_snapshots table  Revision ID: 3e647bfdf471 Revises: b39cb8e836

### Community 50 - "Community 50"
Cohesion: 0.5
Nodes (1): add option resolver fields: instrument_type, index_entry_price, fyers_option_sym

### Community 51 - "Community 51"
Cohesion: 0.5
Nodes (4): Frontend Components (layout, charts, dashboard, research, trades, positions), Frontend Pages (dashboard, trades, signals, research, settings, agent, chart), Frontend Tech Stack (Next.js 15 + TypeScript + Tailwind v4 + Zustand), Zustand Store (prices, positions, signals, scanLogs, risk, agent, research)

### Community 52 - "Community 52"
Cohesion: 0.5
Nodes (4): Tracked Indices: NIFTY BANKNIFTY FINNIFTY SENSEX MIDCPNIFTY, Trading Parameters (10L capital, 5% drawdown, 1.5-2% risk/trade), Research: Critical Warnings (9/10 retail traders lose, 48% win rate means losing streaks), Research: Risk Management Framework for 10 Lakhs

### Community 56 - "Community 56"
Cohesion: 0.67
Nodes (2): BaseSettings, Settings

### Community 57 - "Community 57"
Cohesion: 0.67
Nodes (2): Market constants for Indian stock exchanges., # NOTE: If a holiday falls on a weekend it has no market impact and is not

### Community 72 - "Community 72"
Cohesion: 1.0
Nodes (2): Root Layout, AppShell Component

### Community 74 - "Community 74"
Cohesion: 1.0
Nodes (2): AI Research Agent System (orchestrator + 6 sub-agents), Research Components (ResearchSearch, ResearchProgress, ResearchReport)

### Community 88 - "Community 88"
Cohesion: 1.0
Nodes (1): PostCSS Config

### Community 89 - "Community 89"
Cohesion: 1.0
Nodes (1): ESLint Config

### Community 90 - "Community 90"
Cohesion: 1.0
Nodes (1): Next.js Config

### Community 91 - "Community 91"
Cohesion: 1.0
Nodes (1): Action: updatePrice() - Update Per-Symbol Price in Store

### Community 92 - "Community 92"
Cohesion: 1.0
Nodes (1): Action: addSignal() - Add Signal with Deduplication by ID

### Community 93 - "Community 93"
Cohesion: 1.0
Nodes (1): Action: addPosition() - Add Position with Deduplication by ID

### Community 99 - "Community 99"
Cohesion: 1.0
Nodes (1): Generate text from prompt. Returns raw text response.

### Community 100 - "Community 100"
Cohesion: 1.0
Nodes (1): Generate with web search grounding. Returns text + source citations.

### Community 119 - "Community 119"
Cohesion: 1.0
Nodes (1): Should include all FYERS_SYMBOL_MAP entries except INDIA VIX.

### Community 120 - "Community 120"
Cohesion: 1.0
Nodes (1): Strategy symbols should be merged into the backfill list.

### Community 121 - "Community 121"
Cohesion: 1.0
Nodes (1): NIFTY in both map and strategy config should not duplicate.

### Community 122 - "Community 122"
Cohesion: 1.0
Nodes (1): Valid Fyers option chain data should be parsed into OI rows.

### Community 123 - "Community 123"
Cohesion: 1.0
Nodes (1): Empty option chain should return 0.

### Community 124 - "Community 124"
Cohesion: 1.0
Nodes (1): Missing expiry data should return 0.

### Community 125 - "Community 125"
Cohesion: 1.0
Nodes (1): Strikes with 0 OI should not be persisted.

### Community 126 - "Community 126"
Cohesion: 1.0
Nodes (1): OI fetch should skip when market is closed.

### Community 131 - "Community 131"
Cohesion: 1.0
Nodes (1): How-To: Add a New API Endpoint

### Community 132 - "Community 132"
Cohesion: 1.0
Nodes (1): Frontend Theme — Institutional Terminal (dark, amber accent, monospace)

### Community 133 - "Community 133"
Cohesion: 1.0
Nodes (1): Backend Config (app/config.py — Pydantic Settings)

### Community 134 - "Community 134"
Cohesion: 1.0
Nodes (1): candle_backfill.py — Startup Historical Backfill

### Community 135 - "Community 135"
Cohesion: 1.0
Nodes (1): Research: Previous Day High/Low Breakout Strategy

### Community 136 - "Community 136"
Cohesion: 1.0
Nodes (1): Research: Supertrend + Indicator Combos

### Community 137 - "Community 137"
Cohesion: 1.0
Nodes (1): Research: Recommended Implementation Plan (4 phases)

## Ambiguous Edges - Review These
- `Frontend README - Next.js Project Bootstrap Info` → `file.svg - Document/File Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/public/file.svg · relation: conceptually_related_to
- `Next.js 15 - App Router Framework` → `globe.svg - Globe/Web Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/public/globe.svg · relation: conceptually_related_to
- `Next.js 15 - App Router Framework` → `window.svg - Browser Window/App Icon (grey, 16x16)`  [AMBIGUOUS]
  frontend/public/window.svg · relation: conceptually_related_to

## Knowledge Gaps
- **326 isolated node(s):** `Root Layout`, `AppShell Component`, `Markdown Component`, `WindowBadge Component (local)`, `ToggleSwitch Component (local)` (+321 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 32`** (6 nodes): `cellBg()`, `getMonthsInRange()`, `inPeriod()`, `key()`, `mondayDow()`, `PnLHeatmap.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (4 nodes): `downgrade()`, `add trading_config table  Revision ID: 3f00a98c6835 Revises: e0f67c55d282 Create`, `upgrade()`, `3f00a98c6835_add_trading_config_table.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (4 nodes): `e28ebbb474c4_initial_schema.py`, `downgrade()`, `initial schema  Revision ID: e28ebbb474c4 Revises:  Create Date: 2026-04-15 18:5`, `upgrade()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (4 nodes): `f1a3c9e27b85_add_trade_source_and_position_is_shadow.py`, `downgrade()`, `add trade source and position is_shadow  Revision ID: f1a3c9e27b85 Revises: d79b`, `upgrade()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (4 nodes): `downgrade()`, `add auto_mode to strategy_configs  Revision ID: 32d975c5e56e Revises: 8d468bc0be`, `upgrade()`, `32d975c5e56e_add_auto_mode_to_strategy_configs.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (4 nodes): `downgrade()`, `add canslim fundamental data tables and position_type columns  Revision ID: 3525`, `upgrade()`, `35259f29436b_add_canslim_fundamental_data_tables_and_.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (4 nodes): `bdf125f158c4_add_symbol_map_to_strategy_configs.py`, `downgrade()`, `add symbol_map to strategy_configs  Revision ID: bdf125f158c4 Revises: 35259f294`, `upgrade()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (4 nodes): `d79b95128c64_add_ai_fields_to_signals.py`, `downgrade()`, `add ai fields to signals  Revision ID: d79b95128c64 Revises: 3e647bfdf471 Create`, `upgrade()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (4 nodes): `e0f67c55d282_add_research_reports_and_research_agent_.py`, `downgrade()`, `add research_reports and research_agent_runs tables  Revision ID: e0f67c55d282 R`, `upgrade()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (4 nodes): `downgrade()`, `add executable and blocked_reason to signals  Revision ID: 373982ce98a4 Revises:`, `upgrade()`, `373982ce98a4_add_executable_and_blocked_reason_to_.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (4 nodes): `downgrade()`, `add lots quantity sizing_meta to signals  Revision ID: b39cb8e83680 Revises: 3f0`, `upgrade()`, `b39cb8e83680_add_lots_quantity_sizing_meta_to_signals.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (4 nodes): `downgrade()`, `add global_market_snapshots table  Revision ID: 3e647bfdf471 Revises: b39cb8e836`, `upgrade()`, `3e647bfdf471_add_global_market_snapshots_table.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (4 nodes): `downgrade()`, `add option resolver fields: instrument_type, index_entry_price, fyers_option_sym`, `upgrade()`, `8d468bc0bee4_add_option_resolver_fields_instrument_.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (3 nodes): `config.py`, `BaseSettings`, `Settings`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (3 nodes): `constants.py`, `Market constants for Indian stock exchanges.`, `# NOTE: If a holiday falls on a weekend it has no market impact and is not`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 72`** (2 nodes): `Root Layout`, `AppShell Component`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 74`** (2 nodes): `AI Research Agent System (orchestrator + 6 sub-agents)`, `Research Components (ResearchSearch, ResearchProgress, ResearchReport)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 88`** (1 nodes): `PostCSS Config`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 89`** (1 nodes): `ESLint Config`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 90`** (1 nodes): `Next.js Config`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 91`** (1 nodes): `Action: updatePrice() - Update Per-Symbol Price in Store`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 92`** (1 nodes): `Action: addSignal() - Add Signal with Deduplication by ID`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 93`** (1 nodes): `Action: addPosition() - Add Position with Deduplication by ID`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 99`** (1 nodes): `Generate text from prompt. Returns raw text response.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 100`** (1 nodes): `Generate with web search grounding. Returns text + source citations.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 119`** (1 nodes): `Should include all FYERS_SYMBOL_MAP entries except INDIA VIX.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 120`** (1 nodes): `Strategy symbols should be merged into the backfill list.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 121`** (1 nodes): `NIFTY in both map and strategy config should not duplicate.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 122`** (1 nodes): `Valid Fyers option chain data should be parsed into OI rows.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 123`** (1 nodes): `Empty option chain should return 0.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 124`** (1 nodes): `Missing expiry data should return 0.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 125`** (1 nodes): `Strikes with 0 OI should not be persisted.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 126`** (1 nodes): `OI fetch should skip when market is closed.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 131`** (1 nodes): `How-To: Add a New API Endpoint`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 132`** (1 nodes): `Frontend Theme — Institutional Terminal (dark, amber accent, monospace)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 133`** (1 nodes): `Backend Config (app/config.py — Pydantic Settings)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 134`** (1 nodes): `candle_backfill.py — Startup Historical Backfill`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 135`** (1 nodes): `Research: Previous Day High/Low Breakout Strategy`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 136`** (1 nodes): `Research: Supertrend + Indicator Combos`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 137`** (1 nodes): `Research: Recommended Implementation Plan (4 phases)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Frontend README - Next.js Project Bootstrap Info` and `file.svg - Document/File Icon (grey, 16x16)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Next.js 15 - App Router Framework` and `globe.svg - Globe/Web Icon (grey, 16x16)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Next.js 15 - App Router Framework` and `window.svg - Browser Window/App Icon (grey, 16x16)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `Candle` connect `Models & Indicators Core` to `Agent Execution & Logging`, `API Layer & Auth`, `Backtest Harness & Utils`, `Strategy Engine (VWAP & CAN SLIM)`, `Candle Pattern Detection`, `VWAP & Intraday Bias`, `Global Market & Confidence`, `Volume Analysis & Base Patterns`, `Backtest Context Builder & CPR`, `Community 22`?**
  _High betweenness centrality (0.117) - this node is a cross-community bridge._
- **Why does `StrategyName` connect `Models & Indicators Core` to `Agent Execution & Logging`, `API Layer & Auth`, `Backtest Harness & Utils`, `Strategy Engine (VWAP & CAN SLIM)`, `Community 25`?**
  _High betweenness centrality (0.047) - this node is a cross-community bridge._
- **Why does `OIAnalysis` connect `Models & Indicators Core` to `Agent Execution & Logging`, `Strategy Engine (VWAP & CAN SLIM)`, `Global Market & Confidence`, `Open Interest Analysis`, `Community 22`?**
  _High betweenness centrality (0.034) - this node is a cross-community bridge._
- **Are the 297 inferred relationships involving `Candle` (e.g. with `Abstract base strategy class.  All strategies must subclass BaseStrategy and imp` and `Signal to exit a position.`) actually correct?**
  _`Candle` has 297 INFERRED edges - model-reasoned connections that need verification._