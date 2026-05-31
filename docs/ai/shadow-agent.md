# Shadow Agent — Signal Accuracy Testing

## Purpose

The shadow agent auto-executes **every** signal the strategy generates as a paper trade tagged `source="SHADOW"`, regardless of whether the signal was flagged `executable=False`. The goal is to measure raw signal accuracy on a ~5-10× larger sample than real trading provides, and to calibrate the post-generation filters (trade window, VIX gate, max-trades-per-day, LLM haircut) with real data.

After ~4 weeks of live shadow execution you can compare:
- Shadow hit rate vs real-trade hit rate → validates or invalidates the filters
- Shadow CE vs PE accuracy → validates Phase 2 soft bias gate in production
- Shadow in-window vs out-of-window accuracy → justifies or relaxes the trade window

## Invariants

1. **Minimal gating.** No `executable` check, no `_final_risk_check`, no `yolo_mode` flag. Gates that DO apply (in order): (a) signal must be PENDING; (b) **open-shadow dedup** — if an OPEN shadow trade already exists for this signal, skip (but CLOSED shadow trades don't block — the signal may evolve across days via Case-2 dedup and each day's conditions deserve a fresh shadow entry); (c) past 3:25 PM IST deadline; (d) symbol on F&O ban list; (e) option/futures contract not resolved; (f) confidence below `min_confidence_for_shadow`; (g) permanent watchlist gate (`shadow_skip_permanent_watchlist` flag). Intentionally does NOT gate on: VIX extreme, drawdown breach, max trades, outside trade window — these blocked signals are still shadow-executed to measure what would have happened.
2. **Always-on.** Triggered on every `_handle_signal` call in `strategy_runner.py`. Shadow execution is naturally bounded to market hours because signal generation is upstream-gated by `is_past_close_deadline`.
3. **Fire-and-forget.** The shadow call is `asyncio.create_task(shadow_execute_signal(...))` — it never blocks the main signal path and exceptions are swallowed.
4. **Paper-only.** `Trade.is_paper = True` always. The shadow agent never submits a broker order.

## Isolation guarantees

Shadow trades and positions are completely isolated from all real-trading data paths:

| Data path | Shadow excluded? |
|-----------|-----------------|
| `GET /trades` (default) | ✅ `WHERE source != 'SHADOW'` |
| `GET /trades/summary` (default) | ✅ |
| `GET /positions` (default) | ✅ `WHERE is_shadow = FALSE` |
| `GET /risk/dashboard` | ✅ all aggregations exclude shadows |
| YOLO `_final_risk_check` trade count | ✅ `WHERE source != 'SHADOW'` |
| YOLO `_final_risk_check` drawdown P&L | ✅ `WHERE source != 'SHADOW'` |
| Active Positions widget | ✅ backend filter |
| WebSocket `trade:open` → store positions slice | ✅ filtered by `is_shadow` flag |
| Main dashboard P&L card (Real mode) | ✅ derived from risk endpoint (shadows excluded by backend) |
| Dashboard in Shadow mode | shows shadow P&L only — completely separate data path |

## How to view shadow data

**Dashboard — Active Positions widget:** use the source pills in the widget header (Manual / profile names / Shadow) to switch views. Selecting Shadow shows open shadow positions with live P&L (polls every 30s) and "Shadow Closed Today" below. The P&L bar above also switches to shadow P&L — closed shadow trades + live unrealized from shadow positions. The drawdown bar is hidden in shadow mode (it's not meaningful for simulated trades).

**Trades page:** use the source pills in the page header, which include a Shadow pill, to filter the view. The table, summary strip, and heatmap recompute from `GET /trades?source=SHADOW` for the selected period.

## DB schema

- `trades.source` — `VARCHAR(20)`, values: `MANUAL | YOLO | SHADOW` (default `MANUAL`, index on column)
- `trades.yolo_profile_id` — `UUID`, FK → `yolo_profiles.id` ON DELETE SET NULL, nullable. YOLO trades carry the profile that triggered them; shadow trades always have `NULL` here (shadow execution is profile-agnostic)
- `positions.is_shadow` — `BOOLEAN` (default `false`, index on column)
- `positions.yolo_profile_id` — `UUID`, FK → `yolo_profiles.id` ON DELETE SET NULL, nullable. Same semantics as `trades.yolo_profile_id` — `NULL` for shadow positions, set for YOLO positions

## Month-end accuracy report (manual)

```sql
-- Hit rate by confidence bucket (shadow trades only)
SELECT
    CASE
        WHEN CAST(s.confidence AS FLOAT) >= 80 THEN '80-100'
        WHEN CAST(s.confidence AS FLOAT) >= 70 THEN '70-79'
        WHEN CAST(s.confidence AS FLOAT) >= 60 THEN '60-69'
        ELSE '<60'
    END AS conf_bucket,
    COUNT(*) AS total,
    SUM(CASE WHEN t.pnl > 0 THEN 1 ELSE 0 END) AS winners,
    ROUND(AVG(t.pnl)::numeric, 2) AS avg_pnl
FROM trades t
JOIN signals s ON s.id = t.signal_id
WHERE t.source = 'SHADOW'
  AND t.status = 'CLOSED'
GROUP BY conf_bucket
ORDER BY conf_bucket DESC;

-- CE vs PE breakdown
SELECT
    s.signal_type,
    COUNT(*) AS total,
    SUM(CASE WHEN t.pnl > 0 THEN 1 ELSE 0 END) AS winners,
    ROUND(AVG(t.pnl)::numeric, 2) AS avg_pnl
FROM trades t
JOIN signals s ON s.id = t.signal_id
WHERE t.source = 'SHADOW'
  AND t.status = 'CLOSED'
GROUP BY s.signal_type;

-- In-window vs out-of-window
SELECT
    s.indicators->>'window_state' AS window_state,
    COUNT(*) AS total,
    SUM(CASE WHEN t.pnl > 0 THEN 1 ELSE 0 END) AS winners,
    ROUND(AVG(t.pnl)::numeric, 2) AS avg_pnl
FROM trades t
JOIN signals s ON s.id = t.signal_id
WHERE t.source = 'SHADOW'
  AND t.status = 'CLOSED'
GROUP BY window_state;
```

## Cost

Zero API cost. Shadow trades are paper-only and run entirely within the existing database + trade monitor infrastructure. Live price lookup (one Fyers REST call per signal if Redis is cold) is shared with the YOLO path — no extra calls.
