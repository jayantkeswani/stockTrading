# Signal Confidence Agent

**File:** `backend/app/research/agents/signal_confidence.py`

## Purpose
LLM overlay that reviews every VWAP Pullback signal after all deterministic gates have passed. Adjusts confidence ±15 and produces a structured rationale (summary, key supports/risks) so traders know exactly *why* a signal fired.

## When it runs
1. All deterministic gates pass (VWAP proximity, candle pattern, volume filter, intraday bias soft gate, confidence ≥ threshold)
2. Option/futures resolution completes (strike, expiry, fyers symbol known)
3. LLM call fires → confidence updated → signal persisted with `ai_*` fields

**Never blocks a signal.** 8-second timeout; on failure the deterministic score is used as-is.

## Input context sent to the LLM

Complete JSON snapshot including:
- Signal metadata (direction, strike, expiry, entry, SL, target, R:R, lots)
- `intraday_bias` components (score, strength, gap, VWAP slope, global score)
- VWAP (value, distance %, slope)
- Last 3 × 5m candles (OHLCV)
- Previous day (PDH, PDL, PDC, bias)
- CPR (pivot, TC, BC, type)
- OI analysis (PCR, max CE/PE strikes, sentiment, oi_confirmed)
- India VIX
- Global cues (Dow futures %, SP500 %, crude %, USD/INR %, DXY %, US VIX, pre-open gap)
- Deterministic confidence (score, fire threshold, all 10 factor values, rationale)
- Trade window state (time, IN_WINDOW / DEAD_ZONE / OUT_OF_WINDOW)

## System prompt summary

Instructs the LLM to:
1. Explain WHY the signal fired citing actual values
2. Identify 2-4 concrete supports and 2-4 concrete risks with specific values
3. Adjust confidence by -15..+15 (|adj| ≤ 5 for most signals)
4. Produce a one-sentence summary (≤ 200 chars) and a 3-5 sentence rationale

**Key rules:**
- Never invent data; reference only provided fields
- Counter-bias trades → reduce adjustment unless bias is WEAK and reversal_quality > 0.7
- VIX > 22 or global_alignment < 0.3 → major risk
- Pre-open gap > 0.5% opposing direction → major risk
- Narrow CPR + trending VWAP slope → major support

## Output schema

```json
{
  "confidence_adjustment": -15,
  "summary": "PE on NIFTY after -0.45% gap reverses bullish yesterday; VWAP slope -0.12, CE OI +12% at 24400.",
  "rationale": "Intraday bias flipped to MODERATE_BEARISH despite yesterday BULLISH close because today opened with -0.45% gap and global cues are uniformly negative (Dow -0.35%, US VIX 18.2). Price rejected at VWAP 24358 with bearish engulfing on 1.41× avg volume. CE OI +12.3% at 24400 confirms institutional ceiling. R:R 1.61 is acceptable but below ideal 1:2.",
  "key_supports": [
    "Bearish engulfing at VWAP 24358, volume 1.41× avg",
    "CE OI +12.3% at 24400 confirming resistance",
    "Global cues unanimous bearish: Dow -0.35%, US VIX 18.2"
  ],
  "key_risks": [
    "Counter-trend vs yesterday BULLISH close (close_position 0.68)",
    "India VIX 16.4 — elevated options premium",
    "R:R 1.61 below ideal 1:2 threshold"
  ],
  "recommended_action": "PROCEED_WITH_CAUTION",
  "suggested_lot_adjustment": "NONE"
}
```

## Persistence

| Field | Column | Notes |
|---|---|---|
| `summary` | `signals.ai_summary` | String(300), shown as headline in UI |
| `rationale` | `signals.ai_rationale` | Text, shown expandable in UI |
| `confidence_adjustment` | `signals.ai_adjustment` | Numeric(4,1) |
| `recommended_action` | `signals.ai_action` | String(30) |
| `key_supports` | `signals.indicators["ai_key_supports"]` | JSONB list |
| `key_risks` | `signals.indicators["ai_key_risks"]` | JSONB list |

## Cost model

- Model: `gemini-2.5-flash` (same as research agents)
- Input tokens: ~1,000–1,500 (JSON context + system prompt)
- Output tokens: ~300–500
- Cost per signal: ~$0.0003 (≈ ₹0.025)
- At 3 signals/day → <₹1/day

## Failure modes

| Scenario | Behaviour |
|---|---|
| LLM timeout (> 8s) | `Asyncio.TimeoutError` caught → deterministic score used, `ai_*` fields = null |
| Invalid JSON response | Logs warning → fallback SignalConfidence with adj=0 |
| `google_api_key` not set | Skips overlay entirely — no warning spam |
| `ai_confidence_enabled = False` | Skips overlay entirely |
| LLM returns adj that pushes below threshold | Signal still persisted (for study); `executable=False`, `blocked_reason="LLM downgrade"` |

## Tuning notes

- If the LLM consistently gives +0 adjustment, the deterministic composite is well-calibrated.
- If it systematically gives negative adjustments for out-of-window signals, consider adding a window-state penalty to `compute_confidence` instead.
- The `suggested_lot_adjustment` field is available in the schema and can be used to auto-halve lots for borderline signals; it is not yet consumed by the executor or runner.
- System prompt is verbatim in `signal_confidence.py` — edit there to tune behaviour. No abstraction layer.
