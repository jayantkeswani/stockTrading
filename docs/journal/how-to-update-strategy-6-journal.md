# How to update the Strategy 6 diagnosis journal

The journal is `docs/journal/strategy-6-journal.html` — a running, agent-maintained
log of S6's live (paper) behaviour. **Goal:** after ~2–3 weeks of honest daily
entries, the *patterns* (not any one session) drive concrete, backtested changes to
the strategy. Open it directly in a browser (`file://…/docs/journal/strategy-6-journal.html`).

## When to add an entry

Once per S6 **trading day**, ideally **after the 15:30 IST close** so the full
session is captured. (Reading prod for analysis is fine during market hours — it's
read-only — but a mid-session entry only sees a partial day; note it if so.)

## Rules (read before writing)

- **S6 only** (`strategy_name = 'breakout_retest'`).
- **Work at the SIGNAL level, not the trade level.** Each signal fans out to up to 3
  YOLO profile books (S6-Full / S6-ORB / S6-Full-15K), so raw trade counts are ~3×
  inflated. Dedupe by `(symbol, entry_time)`.
- **Be honest about sample size and one-winner days.** Always fill "Net ex-top winner"
  — if the day is green only because of a single signal, say so.
- **"%-to-tgt before SL" uses the entry→exit window ONLY** (how far price moved toward
  target *before* the stop). Do NOT use a whole-day/whole-window max — that includes
  post-stop dead-cat bounces and overstates the case (this exact mistake was made on
  Day 1 and corrected).
- **Don't propose live parameter changes off one day.** Log hypotheses; change only
  after a backtest on the S6 replay.

## Data to pull (prod, read-only)

Prod API base: `http://8.231.84.44/api/v1`. Prod DB (read-only SELECTs): via SSH.

### 1. Today's S6 closed trades + signal-level outcome (API)

```bash
PROD=http://8.231.84.44/api/v1
# IST-midnight of the day, e.g. 2026-06-04T00:00:00+05:30
curl -s "$PROD/trades?status=CLOSED&closed_since=2026-06-04T00:00:00%2B05:30&limit=500" -o /tmp/t.json
python3 -c "
import json; from collections import defaultdict, Counter
ts=json.load(open('/tmp/t.json')); ts=ts if isinstance(ts,list) else ts.get('trades',ts)
s6=[t for t in ts if t.get('strategy_name')=='breakout_retest']
sig=defaultdict(lambda:{'r':[],'p':0.0})
for t in s6:
    g=sig[(t['symbol'],t.get('entry_time'))]; g['r'].append(t.get('exit_reason'))
    g['p']+=float(t.get('net_pnl') or t.get('pnl') or 0)
print('%d trades -> %d signals'%(len(s6),len(sig)))
for (sym,_),g in sorted(sig.items(),key=lambda x:-x[1]['p']):
    print(f\"{sym:<12}{dict(Counter(g['r']))}  net={g['p']:.0f}\")
print('net all:', sum(g['p'] for g in sig.values()))
"
```

### 2. "%-to-target before SL" — the key whipsaw/noise check (prod candles, SSH)

This is the corrected query: max favorable price in the **entry→exit window** per
stopped signal, as a fraction of the entry→target distance. (LONG version; mirror
`max(high)`→`min(low)` and the ratio for SHORT signals.)

```bash
ssh -i ~/.ssh/st-deploy deploy@8.231.84.44 "docker exec -i st-postgres psql -U trader -d stocktrading" <<'SQL'
WITH s6sl AS (
  SELECT DISTINCT ON (t.symbol) t.symbol, t.side, t.entry_price, t.target_price, t.stop_loss,
         t.fyers_option_symbol, t.entry_time, t.exit_time
  FROM trades t
  WHERE t.strategy_name='breakout_retest' AND t.exit_reason IN ('AGENT_SL','SL_HIT')
    AND (t.entry_time AT TIME ZONE 'Asia/Kolkata')::date = (now() AT TIME ZONE 'Asia/Kolkata')::date
  ORDER BY t.symbol, t.entry_time)
SELECT s.symbol, round(s.entry_price,2) entry, round(s.stop_loss,2) sl, round(s.target_price,2) tgt,
       round((s.entry_price-s.stop_loss)/s.entry_price*100,2) AS sl_dist_pct,
       round((max(m.high)-s.entry_price)/NULLIF(s.target_price-s.entry_price,0)*100) AS pct_to_tgt_before_sl
FROM s6sl s
LEFT JOIN market_data_1m m ON m.symbol = s.fyers_option_symbol
  AND m.timestamp >= s.entry_time AND m.timestamp <= s.exit_time
GROUP BY s.symbol,s.side,s.entry_price,s.stop_loss,s.target_price,s.entry_time,s.exit_time
ORDER BY pct_to_tgt_before_sl DESC NULLS LAST;
SQL
```

### 3. (optional) Whole-window excursion — did price reach target *after* the stop

Use the hold-analysis endpoint with `scenario:"best"` on the stopped trade IDs to see
post-stop behaviour (true whipsaws). Keep this DISTINCT from the before-SL number.

```bash
curl -s -X POST "$PROD/trades/hold-analysis" -H "Content-Type: application/json" \
  -d '{"trade_ids": ["…"], "scenario": "best"}'
```

## Where to edit the HTML (two markers)

1. **Metrics row** — copy the commented `<!-- METRICS ROW: … -->` template inside
   `<tbody>` and insert the filled row as the **first** row (newest on top).
2. **Daily entry** — copy the structure of the latest `<article class="day">` and add
   the new one **directly below** the `▼▼▼ ADD NEW DAILY ENTRY DIRECTLY BELOW THIS LINE ▼▼▼`
   marker (newest first). Fill: context / volume / outcome meta, the signal table,
   Diagnosis, Hypotheses, Actions.
3. **Themes** — when a finding repeats across days, update the cumulative
   "Recurring themes & open questions" list (mark `[open]` → `[confirmed]` /
   `[resolved]` as evidence accrues).

Class helpers: `pos` (green), `neg` (red), `warn` (amber), `muted`/`note` (dim), `num`
(right-aligned numbers).

## The 2–3 week review

After ~10–15 sessions: read the metrics table + themes top-to-bottom and write a
**change proposal** — e.g. "stop-outs persistently hit at <20% of the way to target
with SL dist <0.3% → widen the retest-swing buffer." Then **validate it on the S6
replay** (`scripts/replay_strategy6.py` → `analyze_strategy5_signal_accuracy.py
--strategy breakout_retest`, plus an SL-buffer sweep) before changing anything live.
Record the decision as a final journal entry.
