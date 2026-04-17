---
name: build-strategy
description: End-to-end implementation of a new trading strategy — research, plan, code, test, document
user_invocable: true
---

Implement a new trading strategy in the stockTrading project from scratch. The user provides a strategy name or concept (e.g., "RSI Mean Reversion", "Supertrend Breakout", "Pair Trading"). You handle everything: research, architecture decisions, code, tests, docs, database seeding.

## Phase 1: Research (use Agent tool with web search)

Launch 3 parallel research agents:

**Agent 1 — Strategy fundamentals:**
- What is this strategy? Core rules, entry/exit criteria, position sizing
- Quantitative thresholds (e.g., RSI < 30 = oversold)
- What data does it need? (price, volume, fundamentals, options chain, etc.)
- Typical risk parameters (SL%, target%, holding period)

**Agent 2 — Indian market adaptation:**
- Search Reddit, Indian trading forums for how Indian traders use this strategy
- NSE/BSE specific adaptations (F&O stocks, lot sizes, market hours, expiry cycles)
- Which instruments: index options, stock options, stock futures, equity?
- Which symbols work best for this strategy in India?

**Agent 3 — Existing codebase review:**
- Read `CLAUDE.md` (root + backend) for architecture overview
- Read `backend/app/strategies/base.py` for BaseStrategy interface
- Read `backend/app/strategies/strategy_2_vwap_pullback.py` as reference implementation
- Read `backend/app/services/strategy_runner.py` for evaluation pipeline
- Read `backend/app/core/enums.py` and `backend/app/core/constants.py`
- Read `backend/app/agent/trade_monitor.py` for exit handling
- Read `backend/app/agent/auto_executor.py` for execution flow
- Read `backend/app/models/signal.py`, `trade.py`, `position.py` for data models
- Identify what already exists that can be reused vs what's new

## Phase 2: Architecture Decisions

Based on research, determine:

1. **Instrument type**: OPTION, FUTURE, or EQUITY → sets `InstrumentType` and post-processing path
2. **Holding type**: INTRADAY or POSITIONAL → determines if trade_monitor does EOD exit
3. **Signal type**: BUY_CE, BUY_PE, BUY_FUT, SELL_FUT → must match the instrument
4. **Data requirements**: What goes into MarketContext? Do we need new indicators, new external data sources, new periodic tasks?
5. **Trading windows**: Full session (9:15-15:30) or specific windows?
6. **Position sizing**: Risk-based, fixed lots, or custom logic?
7. **Exit rules**: SL%, target%, trailing stop, time-based, invalidation conditions?

If the strategy needs external data (fundamentals, alternative data, etc.):
- Follow the OI snapshot / fundamental_data_task pattern: periodic APScheduler task → fetch → persist to PostgreSQL → strategy_runner reads from DB into MarketContext
- Data sources must be async (use `asyncio.to_thread` for sync libraries)

## Phase 3: Implementation (follow this exact order)

### Step 1: Enums & Constants
**File: `backend/app/core/enums.py`**
- Add strategy name to `StrategyName` enum (e.g., `RSI_MEAN_REVERSION = "rsi_mean_reversion"`)
- Add any new `ExitReason` values if needed

**File: `backend/app/core/constants.py`**
- Add strategy-specific constants (thresholds, SL%, target%, weights, etc.)
- Group under a comment header: `# Strategy N: Name`

### Step 2: New Indicators (if needed)
**Directory: `backend/app/indicators/`**
- Create pure functions (no DB/Redis access, no side effects)
- Input: candle data / price arrays → Output: computed values
- Follow existing patterns: `vwap.py`, `cpr.py`, `relative_strength.py`

### Step 3: External Data Sources (if needed)
**Directory: `backend/app/data_sources/`**
- Client modules for external APIs (yfinance, NSE, etc.)
- All calls async (use `asyncio.to_thread` for sync libraries)
- Rate limiting between requests

**File: `backend/app/tasks/{strategy}_data_task.py`**
- Follow `oi_snapshot_task.py` / `fundamental_data_task.py` pattern exactly
- APScheduler periodic task → fetch → persist to PostgreSQL
- Register in `backend/app/main.py` lifespan (start scheduler + startup fetch)

**File: `backend/app/models/{data_model}.py`** (if new tables needed)
- SQLAlchemy model extending `Base, TimestampMixin`
- Import in `backend/app/models/__init__.py`
- Generate migration: `cd backend && alembic revision --autogenerate -m "description"`
- Run migration: `alembic upgrade head`

### Step 4: Strategy Class
**File: `backend/app/strategies/strategy_N_{name}.py`**

```python
class NewStrategy(BaseStrategy):
    name = StrategyName.NEW_NAME
    holding_type = "INTRADAY"  # or "POSITIONAL"

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        # 1. Check prerequisites (required data exists)
        # 2. Check strategy-specific entry conditions
        # 3. If all pass → return StrategySignal(
        #        strategy_name=self.name,
        #        symbol=ctx.symbol,
        #        signal_type=SignalType.BUY_FUT,  # or BUY_CE/BUY_PE
        #        instrument_type=InstrumentType.FUTURE,  # or OPTION
        #        entry_price=...,
        #        stop_loss=...,
        #        target_price=...,
        #        confidence=...,  # 0-100
        #        reason="Human-readable explanation",
        #        indicators={...},  # Snapshot of all values at signal time
        #    )

    def should_exit(self, ctx, entry_price, stop_loss, target_price) -> ExitSignal | None:
        # Check exit conditions

    def get_position_size(self, capital, risk_pct, entry, sl, lot_size, vix_mul) -> int:
        # Risk-based sizing
```

**Register in `backend/app/strategies/registry.py`:**
- Import the class
- Add to `_STRATEGIES` dict

### Step 5: Extend MarketContext (if needed)
**File: `backend/app/strategies/base.py`**
- Add optional fields with `None` defaults (backward compatible)
- Example: `rsi_14: float | None = None`

### Step 6: Wire into Strategy Runner
**File: `backend/app/services/strategy_runner.py`**

If the strategy needs new data in MarketContext:
- Add a `_get_{data}()` helper method
- Populate in `_build_market_context()` — only for symbols configured for this strategy (check strategy_configs)
- Guard with `if await self._is_{strategy}_symbol(symbol):`

If the strategy uses a new instrument type that needs resolution:
- Add resolution branch in `_evaluate_strategies()` (like `_resolve_option` / `_resolve_futures`)

### Step 7: Agent Changes (if holding_type is POSITIONAL or new exit logic needed)
**File: `backend/app/agent/trade_monitor.py`**
- If POSITIONAL: EOD exit is already gated by `position_type` — no changes needed
- If new exit conditions: add checks in `_check_position()`

**File: `backend/app/agent/auto_executor.py`**
- If new instrument type: handle lot size lookup, option_type, position_type, trading symbol

### Step 8: Tests
Create test files following existing patterns:

- `tests/test_indicators/test_{indicator}.py` — Pure function tests
- `tests/test_strategies/test_{strategy}_scoring.py` — Scoring/evaluation logic
- `tests/test_strategies/test_{strategy}_strategy.py` — evaluate(), should_exit(), get_position_size()
- `tests/test_services/test_{resolver}.py` — If new resolver

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/ -v --tb=short`
All tests must pass (existing 336+ AND new tests).

### Step 9: Seed Database
```python
# Insert strategy_configs row
from app.models.strategy_config import StrategyConfig
config = StrategyConfig(
    strategy_name="strategy_name_enum_value",
    is_active=False,
    auto_mode=False,
    symbols=[],  # User adds via Settings page
    symbol_map={},
    timeframes=["1m"],
    parameters={},
    risk_params={...},  # Strategy-specific defaults
)
```

### Step 10: Documentation (MANDATORY)

**Create:** `docs/strategies/strategy-N-{name}.md`
- Overview, entry/exit rules, data sources, position sizing, configuration, files

**Update:** `CLAUDE.md` (root)
- Add to Strategies list
- Update directory map if new packages created
- Update test coverage counts

**Update:** `backend/CLAUDE.md`
- Add strategy to strategy engine section
- Add new indicators to indicators section
- Add new data sources / tasks
- Add new models

**Update:** `ARCHITECTURE.md`
- Add strategy to strategy_configs table example
- Add data flow if new data pipeline

**Update:** `frontend/src/lib/constants.ts`
- Add label to `STRATEGY_LABELS`

**Update:** Project memories if significant architectural decisions were made

## Critical Rules

1. **symbol_map**: When the strategy uses stock symbols, the `symbol_map` on `strategy_configs` stores `short_name → fyers_symbol`. Never reconstruct Fyers symbols from short names.
2. **No breaking changes**: All existing tests must still pass. New MarketContext fields must have `None` defaults.
3. **Data pipeline pattern**: External data → periodic task → PostgreSQL table → strategy_runner reads into MarketContext. Same as OI snapshots.
4. **Pure indicators**: Indicator functions in `backend/app/indicators/` must be pure — no DB, no Redis, no side effects.
5. **Run /test-runner** before declaring done.
6. **Run /update-docs** as final verification.

## Example Invocation

User: `/build-strategy Supertrend Breakout for index options`

The agent should:
1. Research Supertrend indicator and breakout strategies
2. Determine it's an INTRADAY OPTION strategy using Supertrend(10,3) indicator
3. Create `backend/app/indicators/supertrend.py`
4. Create `backend/app/strategies/strategy_5_supertrend.py`
5. Wire into strategy_runner, registry
6. Write tests, seed DB, update docs
7. Run tests, verify everything passes
