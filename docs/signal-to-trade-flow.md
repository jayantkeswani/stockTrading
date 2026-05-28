# Signal-to-Trade Flow

End-to-end flow from signal generation through trade execution to position monitoring. This is the core pipeline — every trade (manual, YOLO, shadow) passes through it.

---

## Phase 1: Signal Generation (strategy_runner.py)

### 1.1 Trigger

Two entry points, both converge on the same downstream flow:

- **Auto mode** (`on_candle_close`) — called by `feed_manager` on every 1-minute candle close. Only runs during market hours.
- **Manual scan** (`evaluate_manual`) — called by `POST /api/v1/strategies/evaluate/batch`. Builds a synthetic candle from current price.

### 1.2 Pre-evaluation guardrails

Before any strategy runs, two checks set the initial `executable` flag:

1. **Hard guardrail** — past 3:15 PM IST? If yes, abort entirely (no signal generated).
2. **Regulatory check** (`_check_regulatory_limits`) — is the symbol on the NSE F&O ban list? If yes, `executable=False`, `blocked_reason="F&O ban list"`.

### 1.3 Per-strategy evaluation

For each active strategy configured for this symbol:

1. **Load strategy params** from `strategy_configs` table (JSONB `parameters` column).
2. **Per-strategy risk limits** (`_check_strategy_risk_limits`) — two checks that can set `executable=False`:
   - **Trading window** — strategy defines allowed time windows (e.g., VWAP Pullback: 9:20-14:45). Outside the window? `blocked_reason="Outside trade window"`.
   - **VIX threshold** — India VIX above strategy's `vix_extreme` / `max_vix` param? `blocked_reason="VIX extreme"`.
3. **`strategy.evaluate(ctx)`** — pure function, returns `StrategySignal` or `None`. Signal carries: entry, SL, target, confidence, reason, indicators dict. No lots, no sizing — those happen at execution time.

### 1.4 The `executable` flag

`executable` is a boolean that flows from strategy_runner through to the persisted signal. It answers: **"Is this signal structurally tradeable?"**

Sources that set `executable=False`:

| Source | Reason | Where checked |
|--------|--------|---------------|
| F&O ban list | Legally blocked | `_check_regulatory_limits` |
| Outside trading window | Strategy time filter | `_check_strategy_risk_limits` |
| VIX extreme | Volatility too high | `_check_strategy_risk_limits` |
| Option resolution failed | No valid contract | `_resolve_option` |
| Futures resolution failed | No valid contract | `_resolve_futures` |
| Confidence below threshold | Weak signal | Confidence gating (step 1.6) |

**Who reads it:**

- **YOLO executor** — checks `signal.executable` as gate #1. Won't auto-trade a structurally blocked signal regardless of its own confidence check.
- **Shadow executor** — does NOT check `executable`. Intentionally shadows blocked signals to measure what would have happened.
- **Manual execute** — ignores it. User can override (warnings shown, not enforced).
- **Frontend UI** — greyed-out EXEC button, displays `blocked_reason`.

**Why it exists alongside confidence gating:** Confidence says "this opportunity is weak." Executable says "this opportunity is structurally impossible to trade" (no contract, banned, wrong time). They serve different purposes. The one overlap is confidence — when confidence < execution threshold, `executable` is set to False so the UI shows the blocked reason.

---

## Phase 2: Instrument Resolution (strategy_runner.py)

Immediately after `evaluate()` returns a signal, the contract is resolved. This is where the signal goes from "NIFTY CE" to "NSE:NIFTY26MAY24800CE at 322.15".

### 2.1 Option resolution (`_resolve_option`)

For `instrument_type=OPTION` signals:

1. **Strike selection** — ATM or 1-strike ITM based on delta target.
2. **Expiry selection** — weekly for NIFTY/SENSEX, monthly for others.
3. **Symbol lookup** — finds the Fyers symbol in the symbol master.
4. **Premium fetch** — Redis cache first, then Fyers REST `/quotes` fallback.
5. **SL/target recomputation** — based on option premium (not index price).
6. **WS subscription** — subscribes to the resolved option symbol so ticks start flowing immediately. This happens BEFORE shadow/YOLO create the trade.

If resolution fails (no symbol found, no premium), `executable=False` and `blocked_reason="Option premium unavailable"`.

### 2.2 Futures resolution (`_resolve_futures`)

For `instrument_type=FUTURE` signals:

1. **Contract lookup** — nearest-month futures (last Thursday expiry).
2. **LTP fetch** — live price for the futures contract.
3. **SL/target adjustment** — proportionally adjusted from spot to futures price.
4. **WS subscription** — same as options, subscribes before trade creation.

If resolution fails, `executable=False` and `blocked_reason="Futures contract unavailable"`.

---

## Phase 3: AI Overlay + Dedup + Persist (strategy_runner.py)

After resolution, three sequential steps before the signal reaches executors:

### 3.0 Per-symbol concurrency guard

`_evaluate_strategies` uses a per-symbol busy flag (`_evaluating_symbols` set). If the symbol is already being evaluated by a prior candle's asyncio task, the new evaluation is skipped entirely. This prevents concurrent candle evaluations for the same symbol from racing on signal persist / YOLO execution (e.g., Task A's YOLO reading stale signal data while Task B's Case-2 dedup overwrites it). Different symbols are never blocked.

### 3.1 Dedup skip check (`_is_dedup_skip`)

Quick DB check: if an identical PENDING signal already exists (same strategy, symbol, direction, entry, SL, target), skip the AI overlay and DB write entirely. Saves Gemini API calls on noise.

### 3.2 AI confidence overlay (`_run_ai_confidence_overlay`)

Sequential `await` — **blocks until complete or 25s timeout**. Calls Gemini with the signal's full indicator context + up to 5 prior signals today for the same symbol+strategy. Returns an adjustment (-30 to +30) applied to the signal's confidence score.

This 25-second window between resolve (WS subscription) and execution gives Fyers time to start delivering ticks.

### 3.3 Confidence gating

```python
cfg = await get_trading_config()
if executable and signal.confidence < cfg.min_confidence_for_execution:
    executable = False
    blocked_reason = "Confidence below threshold"
```

Only downgrades `executable` — never upgrades. If the signal was already blocked (F&O ban, no contract), this doesn't change anything.

### 3.4 Signal handling (`_handle_signal`)

1. **Open position check** — skip if a non-shadow position already exists for this symbol + direction.
2. **Dedup** (`_dedup_signal`):
   - **Case 1** (noise): identical PENDING signal exists → skip entirely.
   - **Case 2** (meaningful change): PENDING signal exists but values changed → archive old to `signal_history`, update in place, re-fire shadow + YOLO.
   - **Case 3** (acted on): prior signal was EXECUTED → create new signal.
3. **Persist** (`_persist_signal`) — writes to `signals` table. The signal's `executable` and `blocked_reason` are stored on the row. Gated by `min_confidence_to_persist` — below this threshold, the signal is discarded entirely.
4. **Notify agent runner** (`on_new_signal`) — sends Telegram notification (only if confidence >= execution threshold), triggers YOLO execution if in YOLO mode.
5. **Fire shadow** (`shadow_execute_signal`) — fire-and-forget via `asyncio.create_task()`.

---

## Phase 4: Trade Execution

Three independent consumers. The signal is persisted in DB — executors read it fresh.

### 4.1 YOLO executor (`auto_executor.py`)

Called by `agent_runner.on_new_signal()` only when YOLO mode is active.

**Gate order** (all must pass):

1. Signal is PENDING + `executable=True`
2. Confidence >= `min_confidence_for_execution` (from `trading_config`)
3. Per-strategy `yolo_enabled=True` (from `strategy_configs`)
4. Not a permanent watchlist signal (if `yolo_skip_permanent_watchlist=True`)
5. No existing open YOLO trade for this signal_id

**Per-profile execution** — after signal-level gates pass, shared computation runs once (VIX, lot sizing, live price, SL/target recomputation, margin). Then for each active uncapped YOLO profile:

1. Position dedup scoped to `yolo_profile_id` — no existing open position for this symbol+direction in this profile
2. `_final_risk_check(session, symbol, profile_id, profile_cap)` — drawdown, max trades, profit cap all scoped by profile
3. **Create Trade + Position** — `Trade(source="YOLO", yolo_profile_id=profile.id)`, `Position(yolo_profile_id=profile.id)`.
4. **Broadcast** `trade:open` (with `yolo_profile_id`) + `agent:auto_executed` per trade.

Single DB commit for all profiles. Telegram notification sent once (not per profile).

### 4.2 Shadow executor (`shadow_executor.py`)

Called via `asyncio.create_task()` — fire-and-forget, never blocks the caller.

**Gate order** (intentionally lighter than YOLO):

1. Signal is PENDING or EXECUTED (allows shadow mirroring of YOLO-executed signals)
2. No OPEN shadow trade for this signal (closed shadows don't block — allows fresh shadow on Case-2 re-fire)
3. Not past 3:15 PM close deadline
4. Not on F&O ban list
5. Contract resolved (has `fyers_option_symbol` or `fyers_futures_symbol`)
6. Per-strategy `shadow_enabled=True` (from `strategy_configs`)
7. Confidence >= `min_confidence_for_shadow` (from `trading_config`)
8. Not a permanent watchlist signal (if `shadow_skip_permanent_watchlist=True`)

**Key differences from YOLO:** No `executable` check, no capital gates, no drawdown/max-trades check. Always 1 lot. Shadows blocked signals to measure what would have happened.

**Execution:** Same pattern — fresh LTP via `get_live_price()`, `recompute_sl_target()`, create `Trade(source="SHADOW")` + `Position(is_shadow=True)`.

### 4.3 Manual execution (`signals.py` API)

User clicks EXEC in the UI → `POST /api/v1/signals/{id}/execute`.

- Ignores `executable` flag — user can override.
- Fresh LTP, same `recompute_sl_target()` logic.
- Lot sizing via `compute_lots_for_manual()` (same as YOLO logic, no blocking gates — warnings shown instead).

---

## Phase 5: Trade Monitoring (`trade_monitor.py`)

The `agent_runner` calls `monitor_positions()` every 500ms. Iterates ALL open positions (shadow + real).

### 5.1 Price sourcing

For each position, the monitor tries to get a current price:

1. **Redis cache** (`price:{fyers_option_symbol}`) — populated by WS ticks via `feed_manager.process_tick()`. This is the primary, real-time source.
2. **REST fallback** (`_fetch_option_price_rest`) — calls `fetch_option_premium()` which does Redis check → Fyers REST `/quotes`. Retries 2x with 0.5s delay.
3. **No price available** — see grace period below.

### 5.2 Grace period (5 minutes)

If both Redis and REST return no price (or `ltp=0`), the monitor checks the position's age:

- **< 5 minutes old** → skip this check cycle. WS subscription may not have delivered ticks yet, and Fyers REST may return `ltp=0` for freshly-listed option contracts in early session.
- **>= 5 minutes old, shadow** → close with `ExitReason.STALE_DATA`, PnL zeroed (entry_price used as exit).
- **>= 5 minutes old, non-shadow** → no action (returns None, will retry next cycle).

### 5.3 Exit condition checks

Once a valid price is obtained:

1. **Per-profile profit cap** (`_check_profit_cap`) — iterates each active YOLO profile. For each, computes realized + unrealized net PnL scoped to that profile. If PnL >= profile's `profit_cap`, closes only that profile's open positions with `ExitReason.PROFIT_CAP` and sends a per-profile Telegram notification. Other profiles continue trading.
2. **SL hit** — direction-aware. Uses `ExitReason.TRAILING_SL` if stop_loss was trailed (differs from original), else `ExitReason.AGENT_SL`. Auto-closes in all modes (MANUAL, SEMI, YOLO).
3. **Target hit** — YOLO/shadow: auto-close with `ExitReason.AGENT_PROFIT`. SEMI: request user confirmation via Telegram.
4. **Time exit** — past 3:15 PM for INTRADAY positions: close with `ExitReason.TIME_EXIT`.
5. **Trailing SL update** — POSITIONAL always trails; INTRADAY trails when `trailing_sl_enabled=True`. Breakeven at `trailing_sl_breakeven_pct`, progressive trail when `trailing_sl_trail_pct` is set. SL only moves favorably.
6. **Futures expiry roll** — 3 days before expiry: close old contract, open next month.

### 5.4 Position close flow

`_close_position()`:

1. Compute brokerage charges via `brokerage_calculator.compute_charges()`.
2. Update Trade: exit_price, exit_reason, PnL, net_pnl, charges_json, status=CLOSED.
3. Delete Position row.
4. Create AgentLog entry.
5. Broadcast `trade:close` + `agent:action` via WebSocket.
6. Send Telegram notification (SL hit, profit booked, time exit, etc.).

---

## Confidence Threshold Ladder

```
min_confidence_to_persist       Signal discarded, not saved to DB
          |
min_confidence_for_shadow       Signal saved, visible in UI, shadow trade created
          |
min_confidence_for_execution    Signal saved + shadow + YOLO trade created
```

Cross-field validation enforces: `persist < shadow <= execution`. All three are global settings in the `trading_config` singleton row.

---

## Data Flow: Where Prices Come From

| Stage | Price source | Writes to |
|-------|-------------|-----------|
| Option/futures resolution | Fyers REST `/quotes` | Signal object (in-memory) |
| WS subscription (at resolve) | Fyers WebSocket | Redis `price:{symbol}` via feed_manager |
| Executor fresh LTP | `get_live_price()`: Redis → Fyers REST | Trade entry_price (Postgres) |
| Trade monitor | Redis `price:{symbol}` → REST fallback | Position.current_price (Postgres) |
| Candle persistence | WS ticks aggregated by feed_manager | `market_data_1m` (Postgres) |

Key: only WS ticks write to Redis price cache. REST calls in the monitor are consumed in-memory and not cached.
