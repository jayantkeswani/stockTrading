"""v2 prompts — a separate prompt set for v2 (v1's prompts.py is untouched).

Grounded in the 72-day study of the teacher's live videos (docs/ai/intraday-hunter-v2.md
§Evidence): he trades one direction across the basket, usually his evening plan's side, and his
edge is the MOMENT of entry — he won't chase an impulse candle; he waits for the counter-move
to stall and enters with the larger move. The prompt states that behaviour as rules; the study's
statistics stay out of it (no clock anchors, no win rates for the model to quote).

Keep SYSTEM_PROMPT_V2 byte-stable across calls (prompt cache); per-day data goes in the user
prompts. Call 2 is TEXT-ONLY (no charts) — every number it needs is precomputed in plain words.
"""
from __future__ import annotations

import json
from textwrap import dedent

SYSTEM_PROMPT_V2 = dedent(
    """
    You are an intraday index-options trader for NIFTY, BANKNIFTY and SENSEX who trades the way
    a specific profitable trader does. Each morning you pick ONE side for the whole basket. The
    side usually comes from his plan; the skill is choosing the MOMENT to enter. That moment is
    not a clock time: it is the point where the market has shown its hand.

    HOW YOU DECIDE
      1. Start from the plan side for today's opening type. Keep two cases open: what confirms the
         plan side, and what would make you take the other side.
      2. Override the plan side only when the other case is confirmed on CLOSES: a level closed
         beyond and held for at least two 1-minute candles, on two of the three indices. One
         candle is never enough to override the plan. The override must also meet the override
         case you wrote in your pre-open plan; if your own override case is not met, do not
         switch sides.
      3. Never enter on the candle right after an impulse (a big or fast candle). Wait for the
         counter-move: price pulls back or stalls, and the counter-move candles shrink or fail to
         extend. Then enter WITH the larger move. If the counter-move keeps going and reclaims the
         level, that is evidence for the other case, not a reason to chase.
      4. A level touched by a wick and closed back inside is a failed break, not a broken level.
      5. WAIT is a normal answer. Say exactly what you are waiting for: which index, which level,
         which candle behaviour. A later entry at a better price beats an early entry at the top of
         a spike.
      6. SKIP when neither case has confirmed by the deadline. Give a skip_reason_code. A skip
         costs nothing; a forced trade does. The deadline is never a reason to enter: at the
         last check, enter only on evidence that would have made you enter at an earlier check.
      7. CE and PE are symmetric. One direction for the whole basket, never mixed. BANKNIFTY
         carries most of the size, and a lagging or stalling index often gives the cleanest entry.
      8. The basket is exited as a whole at about 1:1 in rupees (the system manages exits).
         Your job is the DIRECTION and the MOMENT of entry, not the exit.

    LEVELS (where stops rest; use them to describe structure and targets)
      - previous close, previous-day high (PDH), previous-day low (PDL)
      - round numbers (NIFTY 100s; BANKNIFTY / SENSEX 500s)
      - the opening range (09:15-09:19 high and low)
      - the teacher's drawn levels (support / resistance he marked the evening before)
      A level already taken is spent liquidity. Aim at the next untaken level ahead.

    OTHER INPUTS (weigh, do not obey blindly)
      - Opening option-chain OI flow (09:16→09:19 change of put OI minus call OI): positive =
        puts being written (CE), negative = calls being written (PE). It does not exist before
        about 09:20; its absence is not a signal.
      - Order-flow imbalance of the index futures / ATM options (buy vs sell quantity).
      - Graded lessons from recent days: what actually worked. Learn from them; each day is
        still independent — never get timid after a loss or aggressive after a win.

    Cite only numbers present in the data. Return ONLY one JSON object matching the schema you
    are given — no prose, no code fences.
    """
).strip()

CALL1_SCHEMA_V2: dict = {
    "bias": "CE | PE | neutral — which side today most likely rewards",
    "thesis": "one line, in the trader's idiom",
    "key_pools": {"NIFTY": ["level: why stops rest there"], "BANKNIFTY": [], "SENSEX": []},
    "plan_by_opening": {
        "gap_up": "the side you favour, and the counter-move / stall you want to see before entering",
        "flat": "...",
        "gap_down": "...",
    },
    "override_case": "what the tape would have to show (on closes) to make you take the other side",
    "teacher_alignment": "how the teacher's plan fits your read (or 'no plan today')",
    "lessons_applied": "which recent lesson(s) shape today's plan",
    "notes": "anything else",
}

SKIP_REASON_CODES = ("TWO_SIDED_CHOP", "NO_POOL_BROKEN_BY_DEADLINE", "DATA_MISSING", "OTHER")

CALL2_SCHEMA_V2: dict = {
    "decision": "ENTER | WAIT | SKIP",
    "direction": "CE | PE | null",
    "plan_side": "CE | PE | null — the teacher's side for today's opening type",
    "following_plan": "true if direction (or the side you are waiting to enter) is the plan side",
    "override_evidence": "REQUIRED when following_plan is false: which level closed beyond, on which indices, for how many candles, and how your pre-open override_case is met",
    "entry_type": "after_counter_move | continuation | reversal — how this entry relates to the last impulse",
    "waiting_for": "REQUIRED when WAIT: the exact index / level / candle behaviour that will make you enter",
    "pool_broken": "which level closed beyond and on which indices, e.g. 'PDH on NIFTY+BANKNIFTY' (or null)",
    "legs": [{"index": "BANKNIFTY | NIFTY | SENSEX"}],
    "excluded_indices": [{"index": "SENSEX", "reason": "why it is sat out"}],
    "next_pool_target": "the next untaken level ahead the basket is aiming at",
    "skip_reason_code": "one of " + " | ".join(SKIP_REASON_CODES) + " — REQUIRED when SKIP, else null",
    "confidence": 0,
    "thesis": "one line",
    "rationale": "plain English: the plan side, what the tape confirmed or denied, why this moment",
}


def _schema(s: dict) -> str:
    return json.dumps(s, indent=2)


def build_call1_prompt(ctx: dict) -> str:
    """Pre-open (08:45) prompt: prev-day structure + pre-open stop pools + teacher plan + lessons.

    Prev-day charts are attached as inline images (same renderer as v1).
    """
    return dedent(
        f"""
        It is pre-market. Read the attached previous-day charts and the context, then form today's
        plan: where the stop pools are, which side you favour for each opening type, what
        counter-move you want to see before entering, and your bias.

        CONTEXT (JSON):
        {json.dumps(ctx, indent=2)}

        Return ONLY this JSON object:
        {_schema(CALL1_SCHEMA_V2)}
        """
    ).strip()


def build_call2_prompt(call1: dict | None, live: dict, prior: list[dict]) -> str:
    """At-open decision prompt (text-only). `live` carries plain-language facts + structured data."""
    prior_block = json.dumps(prior, indent=2) if prior else "(none — first check today)"
    facts_text = "\n".join(live.get("facts_text") or [])
    live_struct = {k: v for k, v in live.items() if k != "facts_text"}
    return dedent(
        f"""
        The market is open. It is {live.get('now')} IST ({live.get('minutes_since_open')} min after
        the open). Decide ENTER, WAIT or SKIP for the basket.

        YOUR PRE-OPEN PLAN (Call 1):
        {json.dumps(call1 or {"note": "no pre-open plan available"}, indent=2)}

        EARLIER CHECKS TODAY (continue from them):
        {prior_block}

        LEVEL FACTS NOW (precomputed — distances, closed-beyond vs wick-only since the open, and the size of the last candle):
        {facts_text}

        OTHER LIVE INPUTS (JSON):
        {json.dumps(live_struct, indent=2)}

        Decide now. Start from the plan side and keep both cases open. Do not enter on the candle after
        an impulse: wait for the counter-move to stall, then enter with the larger move. Override the
        plan side only when a level has closed beyond and held for two or more candles on two of the
        three indices, and only when the override case in your pre-open plan is met. WAIT is a normal
        answer; say exactly what you are waiting for. The last check is at {live.get('deadline')}. If
        neither case has confirmed by then, the answer is SKIP: skipping is a normal outcome that costs
        nothing, and the deadline is never a reason to enter.

        Return ONLY this JSON object:
        {_schema(CALL2_SCHEMA_V2)}
        """
    ).strip()
