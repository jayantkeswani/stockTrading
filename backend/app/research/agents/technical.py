"""Technical analysis agent.

Analyzes price trends, chart patterns, relative strength, support/resistance levels.
Uses existing indicator functions and Fyers historical data.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.research.agents.base import AgentResult, BaseResearchAgent, ResearchContext
from app.research.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a technical analyst specializing in Indian equities (NSE/BSE).
Analyze the provided technical data and produce a concise assessment.
Focus on trend direction, key support/resistance levels, chart patterns, and momentum.
Be specific with price levels. Identify actionable entry/exit zones."""


class TechnicalAgent(BaseResearchAgent):
    name = "technical"
    description = "Analyzing price trends, patterns, and key levels"

    async def research(self, ctx: ResearchContext, llm: LLMClient) -> AgentResult:
        data_sources = []
        findings: dict = {}

        prices = ctx.price_history_1y
        if not prices:
            return AgentResult(
                agent_name=self.name,
                status="failed",
                findings={},
                summary="No price history available.",
                error="No price data from yfinance",
            )

        data_sources.append("yfinance_price_history_1y")
        closes = [p.close for p in prices]
        current_price = ctx.current_price or closes[-1]

        # 1. Moving averages
        ma_50 = _sma(closes, 50)
        ma_200 = _sma(closes, 200)
        ma_20 = _sma(closes, 20)

        findings["moving_averages"] = {
            "sma_20": round(ma_20, 2) if ma_20 else None,
            "sma_50": round(ma_50, 2) if ma_50 else None,
            "sma_200": round(ma_200, 2) if ma_200 else None,
            "above_50_dma": current_price > ma_50 if ma_50 else None,
            "above_200_dma": current_price > ma_200 if ma_200 else None,
            "golden_cross": ma_50 > ma_200 if ma_50 and ma_200 else None,
        }

        # 2. Trend direction
        if ma_50 and ma_200:
            if current_price > ma_50 > ma_200:
                trend = "STRONG_UPTREND"
            elif current_price > ma_200:
                trend = "UPTREND"
            elif current_price < ma_50 < ma_200:
                trend = "STRONG_DOWNTREND"
            elif current_price < ma_200:
                trend = "DOWNTREND"
            else:
                trend = "SIDEWAYS"
        else:
            trend = "INSUFFICIENT_DATA"
        findings["trend_direction"] = trend

        # 3. Relative Strength
        try:
            from app.indicators.relative_strength import compute_rs_raw_score

            rs_raw = compute_rs_raw_score(closes)
            findings["rs_raw_score"] = round(rs_raw, 2)
            data_sources.append("indicators/relative_strength")
        except Exception:
            logger.debug("RS calculation failed", exc_info=True)

        # RS from existing fundamental data if available
        if ctx.existing_fundamental and ctx.existing_fundamental.relative_strength_rating:
            findings["rs_percentile"] = float(ctx.existing_fundamental.relative_strength_rating)

        # 4. Volume analysis
        volumes = [p.volume for p in prices]
        try:
            from app.indicators.volume_analysis import compute_avg_volume, volume_ratio

            avg_vol = compute_avg_volume(volumes, period=20)
            if avg_vol > 0 and volumes:
                latest_vol = volumes[-1]
                vol_rat = volume_ratio(latest_vol, avg_vol)
                findings["volume"] = {
                    "latest_volume": latest_vol,
                    "avg_volume_20d": avg_vol,
                    "volume_ratio": round(vol_rat, 2),
                    "volume_surge": vol_rat > 1.5,
                }
                data_sources.append("indicators/volume_analysis")
        except Exception:
            logger.debug("Volume analysis failed", exc_info=True)

        # 5. Support / Resistance from recent price action
        highs = [p.high for p in prices[-60:]]
        lows = [p.low for p in prices[-60:]]
        findings["support_resistance"] = {
            "recent_high_60d": round(max(highs), 2) if highs else None,
            "recent_low_60d": round(min(lows), 2) if lows else None,
            "52_week_high": round(max(p.high for p in prices), 2),
            "52_week_low": round(min(p.low for p in prices), 2),
        }

        # Key support levels (recent swing lows)
        support_levels = _find_support_levels(prices[-90:], current_price)
        resistance_levels = _find_resistance_levels(prices[-90:], current_price)
        findings["support_levels"] = [round(s, 2) for s in support_levels[:3]]
        findings["resistance_levels"] = [round(r, 2) for r in resistance_levels[:3]]

        # 6. Chart patterns (from daily bars)
        try:
            from app.strategies.canslim.base_patterns import detect_any_base_pattern

            # Convert PriceHistory to Candle-like dicts
            candle_dicts = [
                type("Candle", (), {
                    "open": p.open, "high": p.high, "low": p.low,
                    "close": p.close, "volume": p.volume,
                })()
                for p in prices[-120:]
            ]
            pattern = detect_any_base_pattern(candle_dicts)
            if pattern:
                findings["chart_patterns"] = [{
                    "type": pattern.pattern_type,
                    "breakout_price": round(pattern.breakout_price, 2),
                    "base_low": round(pattern.base_low, 2),
                    "depth_pct": round(pattern.depth_pct, 1),
                }]
                data_sources.append("canslim/base_patterns")
        except Exception:
            logger.debug("Pattern detection failed", exc_info=True)

        # 7. RSI (14-period)
        rsi = _rsi(closes, 14)
        if rsi is not None:
            findings["rsi_14"] = round(rsi, 1)
            findings["rsi_zone"] = (
                "OVERSOLD" if rsi < 30
                else "OVERBOUGHT" if rsi > 70
                else "NEUTRAL"
            )

        # 8. Price performance
        if len(closes) >= 5:
            findings["performance"] = {
                "1_week_pct": round((closes[-1] / closes[-5] - 1) * 100, 1) if len(closes) >= 5 else None,
                "1_month_pct": round((closes[-1] / closes[-22] - 1) * 100, 1) if len(closes) >= 22 else None,
                "3_month_pct": round((closes[-1] / closes[-66] - 1) * 100, 1) if len(closes) >= 66 else None,
                "6_month_pct": round((closes[-1] / closes[-132] - 1) * 100, 1) if len(closes) >= 132 else None,
                "1_year_pct": round((closes[-1] / closes[0] - 1) * 100, 1),
            }

        # 9. LLM interpretation
        prompt = f"""Analyze the technical data for {ctx.symbol} at Rs {current_price:.2f}:

{_format_findings(findings)}

Provide a concise technical assessment covering:
1. Trend direction and strength
2. Key support and resistance levels
3. Chart patterns detected (if any)
4. Momentum assessment (RSI, volume)
5. Suggested entry zone and stop-loss level

Keep it under 200 words. Be specific with price levels."""

        summary = await llm.generate(prompt, system=SYSTEM_PROMPT, max_tokens=2048)

        return AgentResult(
            agent_name=self.name,
            status="completed",
            findings=findings,
            summary=summary,
            data_sources=data_sources,
        )


def _sma(values: list[float], period: int) -> float | None:
    """Simple moving average."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Relative Strength Index."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _find_support_levels(prices, current_price) -> list[float]:
    """Find recent swing lows below current price."""
    lows = []
    for i in range(2, len(prices) - 2):
        if prices[i].low < prices[i - 1].low and prices[i].low < prices[i + 1].low:
            if prices[i].low < current_price:
                lows.append(prices[i].low)
    lows.sort(reverse=True)  # Nearest support first
    return lows


def _find_resistance_levels(prices, current_price) -> list[float]:
    """Find recent swing highs above current price."""
    highs = []
    for i in range(2, len(prices) - 2):
        if prices[i].high > prices[i - 1].high and prices[i].high > prices[i + 1].high:
            if prices[i].high > current_price:
                highs.append(prices[i].high)
    highs.sort()  # Nearest resistance first
    return highs


def _format_findings(findings: dict) -> str:
    parts = []

    if "trend_direction" in findings:
        parts.append(f"TREND: {findings['trend_direction']}")

    if "moving_averages" in findings:
        ma = findings["moving_averages"]
        parts.append(
            f"MOVING AVERAGES: 20DMA={ma['sma_20']}, 50DMA={ma['sma_50']}, "
            f"200DMA={ma['sma_200']}, Golden Cross={ma['golden_cross']}"
        )

    if "rsi_14" in findings:
        parts.append(f"RSI(14): {findings['rsi_14']} ({findings.get('rsi_zone', '')})")

    if "volume" in findings:
        v = findings["volume"]
        parts.append(f"VOLUME: Latest={v['latest_volume']}, Avg20D={v['avg_volume_20d']}, Ratio={v['volume_ratio']}")

    if "support_levels" in findings:
        parts.append(f"SUPPORT LEVELS: {findings['support_levels']}")
    if "resistance_levels" in findings:
        parts.append(f"RESISTANCE LEVELS: {findings['resistance_levels']}")

    if "chart_patterns" in findings:
        for p in findings["chart_patterns"]:
            parts.append(f"PATTERN: {p['type']}, breakout={p['breakout_price']}, base_low={p['base_low']}")

    if "performance" in findings:
        perf = findings["performance"]
        parts.append(f"PERFORMANCE: 1W={perf.get('1_week_pct')}%, 1M={perf.get('1_month_pct')}%, "
                     f"3M={perf.get('3_month_pct')}%, 1Y={perf.get('1_year_pct')}%")

    if "rs_percentile" in findings:
        parts.append(f"RELATIVE STRENGTH: Percentile={findings['rs_percentile']}")

    return "\n".join(parts)
