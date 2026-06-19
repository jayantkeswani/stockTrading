"""mplfinance chart renderers for the Intraday Hunter agent.

Renders the two image types Claude reads in its calls:
  - prev-day chart per index (Call 1)            -> render_prev_day_chart()
  - live opening chart 09:15->now per index      -> render_opening_chart()

Both annotate the previous-day CLOSE pivot, PDH/PDL, and any round-number levels in
the trader's red-resistance / green-support style. Pure rendering: callers pass candle
data + levels; the service layer does the DB fetch. Headless (Agg) — no browser.
"""
from __future__ import annotations

import os
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # headless; must precede pyplot/mplfinance import
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

# A clean, high-contrast style — Claude reads these, so legibility beats theme-matching.
_STYLE = mpf.make_mpf_style(base_mpf_style="charles", gridstyle=":", facecolor="white")

PIVOT_COLOR = "#1f77b4"      # previous-day close (the trader's key level)
RESISTANCE_COLOR = "#d62728"  # red — overhead supply / PDH / rejection highs
SUPPORT_COLOR = "#2ca02c"     # green — support / PDL
ROUND_COLOR = "#999999"       # round numbers (soft)


def _to_frame(candles: list[dict]) -> pd.DataFrame:
    """candles: list of {ts: datetime|iso str, open, high, low, close, volume}."""
    if not candles:
        raise ValueError("no candles to render")
    rows = []
    for c in candles:
        ts = c["ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        rows.append(
            {
                "Date": ts,
                "Open": float(c["open"]),
                "High": float(c["high"]),
                "Low": float(c["low"]),
                "Close": float(c["close"]),
                "Volume": float(c.get("volume", 0) or 0),
            }
        )
    df = pd.DataFrame(rows).set_index("Date").sort_index()
    return df


def _build_hlines(levels: dict | None) -> dict | None:
    """levels: {prev_close: float, pdh: float, pdl: float, round: [float, ...]}.

    Returns an mplfinance `hlines` dict (prices + matched colors), or None.
    """
    if not levels:
        return None
    prices: list[float] = []
    colors: list[str] = []

    def add(val, color):
        if val is None:
            return
        prices.append(float(val))
        colors.append(color)

    add(levels.get("prev_close"), PIVOT_COLOR)
    add(levels.get("pdh"), RESISTANCE_COLOR)
    add(levels.get("pdl"), SUPPORT_COLOR)
    for r in levels.get("round", []) or []:
        add(r, ROUND_COLOR)

    if not prices:
        return None
    return {"hlines": prices, "colors": colors, "linestyle": "--", "linewidths": 0.9}


def _render(
    candles: list[dict],
    title: str,
    out_path: str,
    levels: dict | None = None,
    show_volume: bool = False,
) -> str:
    """Core renderer. Returns out_path. show_volume off by default (index spot volume ~0)."""
    df = _to_frame(candles)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    kwargs = dict(
        type="candle",
        style=_STYLE,
        title=title,
        ylabel="",
        volume=show_volume,
        figratio=(16, 9),
        figscale=1.1,
        tight_layout=True,
        savefig=dict(fname=out_path, dpi=120, bbox_inches="tight"),
    )
    hlines = _build_hlines(levels)
    if hlines:
        kwargs["hlines"] = hlines
    mpf.plot(df, **kwargs)
    return out_path


def render_prev_day_chart(
    index_name: str, candles: list[dict], levels: dict, out_dir: str, trading_date: str
) -> str:
    """Previous-day 1m chart for one index (Call 1). `levels` annotates close/PDH/PDL/round."""
    out_path = os.path.join(out_dir, f"{trading_date}_{index_name}_prevday.png")
    title = f"{index_name} - previous day"
    return _render(candles, title, out_path, levels)


def render_opening_chart(
    index_name: str, candles: list[dict], levels: dict, out_dir: str, trading_date: str
) -> str:
    """Live opening 1m chart 09:15->now for one index (Call 2), with the pivot drawn."""
    out_path = os.path.join(out_dir, f"{trading_date}_{index_name}_opening.png")
    title = f"{index_name} - today open"
    return _render(candles, title, out_path, levels)
