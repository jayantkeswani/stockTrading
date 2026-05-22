"""Execution-time utilities shared by all three execution paths (YOLO, Shadow, Manual).

These helpers operate on plain scalars — no ORM imports — so they can be
called from any async context without coupling to session lifecycle.
"""

import logging

logger = logging.getLogger(__name__)


def recompute_sl_target(
    signal_entry: float,
    signal_sl: float,
    signal_target: float | None,
    live_entry: float,
    instrument_type: str,
    signal_type: str,
) -> tuple[float, float | None]:
    """Recompute SL and target from the live entry price at execution time.

    The signal carries SL/target computed at the moment the signal fired.
    Because we fill at the current LTP (not the stale signal premium), the
    risk/reward must be recomputed relative to the actual fill price.

    Rules by instrument type:

    OPTION (always bought long — premium rises when correct):
        Preserves the original SL% and target% relative to entry.
        new_sl     = live_entry × (1 - sl_pct)
        new_target = live_entry × (1 + target_pct)

    FUTURE:
        SL stays at the structural price level (ORB low/high, VWAP band,
        PDH/PDL) — moving it would invalidate the setup's invalidation point.
        Target is recomputed using the original R:R multiplier from live_entry
        so the reward still reflects the intended structure.

    Edge-case fallbacks (return originals unchanged):
        - signal_entry == signal_sl  (degenerate signal, zero risk)
        - new_risk <= 0              (live price already past structural SL)
        - signal_entry or live_entry <= 0
    """
    if signal_entry <= 0 or live_entry <= 0:
        return signal_sl, signal_target

    if instrument_type == "OPTION":
        risk = signal_entry - signal_sl
        if risk <= 0:
            return signal_sl, signal_target

        sl_pct = risk / signal_entry
        new_sl = round(live_entry * (1 - sl_pct), 2)

        if signal_target is None:
            return new_sl, None

        reward = signal_target - signal_entry
        if reward <= 0:
            return new_sl, signal_target

        target_pct = reward / signal_entry
        new_target = round(live_entry * (1 + target_pct), 2)
        return new_sl, new_target

    # --- FUTURE: keep SL at structural level, recompute target ---
    is_long = "BUY" in signal_type  # BUY_FUT → long, SELL_FUT → short

    if is_long:
        orig_risk = signal_entry - signal_sl
        if orig_risk <= 0:
            return signal_sl, signal_target

        new_risk = live_entry - signal_sl
        if new_risk <= 0:
            logger.warning(
                "recompute_sl_target: live_entry %.2f already at/below structural SL %.2f "
                "for %s — using original SL/target",
                live_entry, signal_sl, signal_type,
            )
            return signal_sl, signal_target

        if signal_target is None:
            return signal_sl, None

        orig_rr = (signal_target - signal_entry) / orig_risk
        new_target = round(live_entry + orig_rr * new_risk, 2)
        return signal_sl, new_target

    else:
        # SELL_FUT short: SL is above entry, target is below entry
        orig_risk = signal_sl - signal_entry
        if orig_risk <= 0:
            return signal_sl, signal_target

        new_risk = signal_sl - live_entry
        if new_risk <= 0:
            logger.warning(
                "recompute_sl_target: live_entry %.2f already at/above structural SL %.2f "
                "for %s — using original SL/target",
                live_entry, signal_sl, signal_type,
            )
            return signal_sl, signal_target

        if signal_target is None:
            return signal_sl, None

        orig_rr = (signal_entry - signal_target) / orig_risk
        new_target = round(live_entry - orig_rr * new_risk, 2)
        return signal_sl, new_target
