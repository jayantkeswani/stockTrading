# Execution Concurrency Model

How the signal-to-trade pipeline handles concurrency — what blocks, what's fire-and-forget, and why.

---

## Pipeline Overview

```
Tick (Fyers WS)
  → Redis price cache (price:{symbol}, 24h TTL)
  → WS broadcast to frontend
  → In-memory candle aggregation
      ↓ (on minute boundary)
      ├─ DB persist candle (create_task, fire-and-forget)
      └─ Strategy evaluation (create_task, fire-and-forget)
            → Dedup skip check (DB read)
            → AI confidence overlay (Gemini LLM, 1-25s)
            → Full dedup (DB read)
            → Persist signal (DB write, awaited — returns signal_id)
            → Broadcast signal:new (awaited — ordering guarantee)
            → YOLO executor (create_task, fire-and-forget)
            → Shadow executor (create_task, fire-and-forget)

Trade Monitor (separate 500ms loop)
  → Redis price read per open position
  → SL/target/trailing/time exit checks
```

---

## Tick Ingestion

Fyers SDK runs in a background thread. Each tick bridges to the asyncio event loop via `loop.call_soon_threadsafe`. The tick callback (`process_tick`) does three things:

1. **Redis write** — `cache_price(symbol, data)` with 24h TTL. Also writes under Fyers alias.
2. **WS broadcast** — frontend gets real-time price updates.
3. **Candle aggregation** — in-memory accumulation until minute boundary.

Ticks are NOT written to the database. Only completed 1-minute candles are persisted.

---

## Candle Close: Persist vs Evaluate

When a minute boundary is detected, `_emit_candle()` launches two independent tasks:

```python
asyncio.create_task(_persist_candle(...))              # fire-and-forget
asyncio.create_task(_run_auto_strategy_evaluation(...))  # fire-and-forget
```

There is **no ordering guarantee** between candle persistence and strategy evaluation. They race concurrently. Strategy evaluation does NOT require the candle to be in the DB — it reads from in-memory buffers.

---

## Strategy Evaluation: Sequential Chain

Within a single symbol's evaluation, everything runs sequentially:

| Step | Mechanism | Latency |
|------|-----------|---------|
| `strategy.evaluate(ctx)` | awaited | <1ms (pure computation) |
| `_resolve_option()` / `_resolve_futures()` | awaited | 5-50ms (Redis + possible REST) |
| `_is_dedup_skip()` | awaited | ~1ms (indexed DB read) |
| `_run_ai_confidence_overlay()` | awaited | **1-25s** (Gemini API) |
| `_dedup_signal()` | awaited | ~1ms (indexed DB read) |
| `_persist_signal()` | awaited | ~2-5ms (DB write) |
| `_broadcast_signal()` | awaited | ~1ms (WS send) |
| `agent_runner.on_new_signal()` [YOLO] | **create_task** | 0ms (non-blocking) |
| `shadow_execute_signal()` | **create_task** | 0ms (non-blocking) |

**The AI overlay dominates the critical path.** Everything else combined is <100ms.

### Why AI blocks the pipeline

The AI overlay mutates `signal.confidence` in place before persist. The final confidence value determines:
- Whether the signal is persisted at all (`min_confidence_to_persist`)
- Whether `executable=False` (confidence below execution threshold)
- Whether YOLO and shadow will accept it (their own confidence gates)

Moving AI off the critical path would require persisting with a provisional confidence and updating later — adding complexity for questionable benefit since the LLM is the quality filter.

---

## Post-Persist: Fire-and-Forget Executors

After `_persist_signal()` returns `signal_record.id`, both executors are launched as detached tasks:

```python
asyncio.create_task(agent_runner.on_new_signal(signal_record.id))  # YOLO
asyncio.create_task(shadow_execute_signal(signal_record.id))        # Shadow
```

### Why fire-and-forget works

1. **Both read the signal from DB by ID** — persist has already committed, so the row exists.
2. **Neither's result affects the caller** — no downstream code depends on trade creation.
3. **Both have top-level try/except** — exceptions are logged, never propagated to the event loop.
4. **They don't race with each other** — shadow only dedupes against shadow trades/positions; YOLO only dedupes against non-shadow. They operate on completely independent data partitions.

### Why `_persist_signal` must be awaited

Every downstream consumer needs `signal_record.id` (the DB-assigned UUID). Without it:
- Broadcast can't include the signal ID for frontend
- YOLO can't query the signal from DB
- Shadow can't query the signal from DB

### Why `_broadcast_signal` is awaited before executors

Ordering guarantee: frontend receives `signal:new` before `trade:open`. If broadcast were fire-and-forget and YOLO ran first, the UI would see a trade for a signal it hasn't received yet.

---

## Per-Symbol Concurrency Guard

`_evaluating_symbols: set[str]` prevents the same symbol from being evaluated concurrently by two candle tasks:

```python
if symbol in self._evaluating_symbols:
    return  # skip, not queue
self._evaluating_symbols.add(symbol)
try:
    ...
finally:
    self._evaluating_symbols.discard(symbol)
```

This is a **skip-not-queue** design. If the prior candle's evaluation is still in-flight (e.g., waiting on Gemini), the new candle's evaluation is dropped entirely. Different symbols are never blocked from each other.

---

## Trade Monitor

The monitor runs as a completely separate background loop, decoupled from the tick/signal pipeline:

```python
while self._running:
    await monitor_positions(session, yolo_mode=cfg.yolo_mode)
    await asyncio.sleep(0.5)  # 500ms interval
```

### How it checks prices

Each iteration:
1. `SELECT * FROM positions` — all open positions (YOLO + shadow + manual), unfiltered.
2. For each position: `GET price:{symbol}` from Redis — single key read, no pipelining.
3. Compare against SL/target/time exit conditions.

### Why polling (not tick-driven)

- **Simplicity** — no pub/sub wiring, no per-position subscription management.
- **Bounded load** — with ~3-6 open positions, each iteration is 3-6 Redis GETs + 1 DB query. At 500ms that's ~12 Redis ops/sec — trivial.
- **Worst-case latency** — 500ms. For options moving 5-10% in seconds, this is acceptable. A tick-driven design would be faster but adds complexity (filtering relevant ticks, managing subscriptions as positions open/close).

### What the monitor handles

| Check | Exit Reason | Applies to |
|-------|-------------|------------|
| Price <= SL (long) / >= SL (short) | `AGENT_SL` or `TRAILING_SL` | All |
| Price >= target (long) / <= target (short) | `AGENT_PROFIT` | YOLO + shadow auto-close; SEMI requests confirmation |
| Past 3:15 PM (intraday) | `TIME_EXIT` | All intraday |
| Realized + unrealized >= profit cap | `PROFIT_CAP` | Non-shadow only |
| No price for 5+ minutes (shadow) | `STALE_DATA` | Shadow only |
| 3 days before futures expiry | Roll to next month | Positional futures |

---

## Case-2 Dedup: Re-firing Executors

When a signal is updated (Case-2 dedup: same symbol/strategy but meaningful change in entry/SL/target/confidence):

```python
# Archive old → update signal in place
await self._broadcast_signal(..., event="signal:updated")
asyncio.create_task(shadow_execute_signal(dedup_result.id))    # re-fire shadow
asyncio.create_task(agent_runner.on_new_signal(dedup_result.id))  # re-fire YOLO
```

Both executors have their own dedup gates — shadow checks for open shadow trade per signal_id (closed shadows don't block, allowing re-fire), YOLO checks for open non-shadow position per symbol+direction.

---

## Summary: What's Sequential, What's Parallel

```
SEQUENTIAL (within one symbol's evaluation):
  evaluate → resolve → dedup_skip → AI overlay → dedup → persist → broadcast

PARALLEL (fire-and-forget after broadcast):
  ├─ YOLO (create_task)
  └─ Shadow (create_task)

PARALLEL (across symbols):
  Symbol A evaluation ║ Symbol B evaluation ║ Symbol C evaluation

INDEPENDENT (separate loop):
  Trade Monitor (500ms poll) — never blocks or is blocked by signal pipeline
```
