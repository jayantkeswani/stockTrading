"""Integration tests for LLM client against the real Gemini API.

Requires GOOGLE_API_KEY in .env (AI Studio mode).
Skipped automatically when no API key is configured.

Run explicitly:
    python -m pytest tests/test_research/test_llm_integration.py -v -s
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

_has_api_key = bool(os.environ.get("GOOGLE_API_KEY", "").strip())
pytestmark = pytest.mark.skipif(not _has_api_key, reason="No GOOGLE_API_KEY configured")


@pytest.fixture(scope="module")
def flash_client():
    from app.research.llm_client import GeminiClient

    api_key = os.environ["GOOGLE_API_KEY"]
    return GeminiClient(model="gemini-2.0-flash", api_key=api_key)


@pytest.fixture(scope="module")
def pro_client():
    from app.research.llm_client import GeminiClient

    api_key = os.environ["GOOGLE_API_KEY"]
    return GeminiClient(model="gemini-3.1-pro-preview", api_key=api_key)


class TestGenerateJsonBasic:
    """Verify generate_json returns valid parsed JSON from a real LLM call."""

    @pytest.mark.asyncio
    async def test_simple_json_response(self, flash_client):
        result = await flash_client.generate_json(
            prompt='Return a JSON object with keys "name" (string) and "score" (integer).',
            system="You are a helpful assistant that returns valid JSON.",
            max_tokens=256,
        )
        assert isinstance(result, dict), f"Expected dict, got {type(result)}: {result}"
        assert result, "Expected non-empty dict"
        assert "name" in result or "score" in result, f"Unexpected keys: {result.keys()}"
        print(f"  [PASS] Simple JSON: {result}")

    @pytest.mark.asyncio
    async def test_complex_nested_json(self, flash_client):
        result = await flash_client.generate_json(
            prompt=(
                'Return a JSON object with: "stocks" (array of 2 objects, each with '
                '"symbol" string and "price" number), and "summary" (string).'
            ),
            max_tokens=512,
        )
        assert isinstance(result, dict)
        assert "stocks" in result, f"Missing 'stocks' key: {result.keys()}"
        assert isinstance(result["stocks"], list)
        assert len(result["stocks"]) >= 1
        print(f"  [PASS] Nested JSON: {len(result['stocks'])} stocks, summary={result.get('summary', '')[:50]}")


class TestResponseSchema:
    """Verify response_schema constrains output to match the schema."""

    SCHEMA = {
        "type": "object",
        "properties": {
            "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
            "confidence": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["sentiment", "confidence", "reason"],
    }

    @pytest.mark.asyncio
    async def test_schema_constrains_output(self, flash_client):
        result = await flash_client.generate_json(
            prompt="Analyze the sentiment of: 'Reliance Industries reported record quarterly profits, beating all analyst estimates.'",
            system="Analyze stock news sentiment. Return a JSON object.",
            max_tokens=512,
            response_schema=self.SCHEMA,
        )
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "sentiment" in result, f"Missing 'sentiment': {result.keys()}"
        assert result["sentiment"] in ("positive", "negative", "neutral"), (
            f"Invalid enum: {result['sentiment']}"
        )
        assert "confidence" in result
        assert isinstance(result["confidence"], int)
        assert "reason" in result
        print(f"  [PASS] Schema-constrained: sentiment={result['sentiment']}, confidence={result['confidence']}")

    @pytest.mark.asyncio
    async def test_schema_with_briefing_format(self, flash_client):
        """Test with the actual morning briefing schema shape."""
        schema = {
            "type": "object",
            "properties": {
                "approach": {"type": "string", "enum": ["aggressive", "normal", "conservative"]},
                "summary": {"type": "string"},
                "max_lots_recommendation": {"type": "integer"},
            },
            "required": ["approach", "summary", "max_lots_recommendation"],
        }
        result = await flash_client.generate_json(
            prompt=(
                "Given the market context: Nifty up 0.8%, VIX at 14.5, crude stable. "
                "Yesterday's P&L was +₹12,000 with 2/3 wins. "
                "Recommend a trading approach for today."
            ),
            system="You are a trading AI. Return your recommendation as structured JSON.",
            max_tokens=1024,
            response_schema=schema,
        )
        assert isinstance(result, dict)
        assert result.get("approach") in ("aggressive", "normal", "conservative"), (
            f"Invalid approach: {result.get('approach')}"
        )
        assert isinstance(result.get("summary"), str) and len(result["summary"]) > 5
        assert isinstance(result.get("max_lots_recommendation"), int)
        print(f"  [PASS] Briefing schema: approach={result['approach']}, lots={result['max_lots_recommendation']}")


class TestProModel:
    """Verify Pro model works (gemini-3.1-pro-preview via AI Studio)."""

    @pytest.mark.asyncio
    async def test_pro_model_generates_json(self, pro_client):
        result = await pro_client.generate_json(
            prompt='Return a JSON object with "model" set to "pro" and "status" set to "ok".',
            max_tokens=256,
        )
        assert isinstance(result, dict)
        assert result, "Pro model returned empty dict"
        print(f"  [PASS] Pro model response: {result}")


class TestRetryBehavior:
    """Verify retry-once works by checking that generate_json handles edge cases."""

    @pytest.mark.asyncio
    async def test_large_schema_doesnt_break(self, flash_client):
        """Larger schema with array output — verify it doesn't cause empty responses.

        Note: additionalProperties is Vertex AI-only (not supported in AI Studio).
        This test uses an array-of-objects schema that works in both modes.
        """
        schema = {
            "type": "object",
            "properties": {
                "ratings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
                            "reason": {"type": "string"},
                        },
                        "required": ["symbol", "confidence", "reason"],
                    },
                },
            },
            "required": ["ratings"],
        }
        result = await flash_client.generate_json(
            prompt=(
                "Rate these stocks for intraday trading today:\n"
                "- VEDL: RS 85, composite score 72, long buildup in futures OI\n"
                "- TATAPOWER: RS 60, composite score 55, short covering\n"
                "- SAIL: RS 45, composite score 40, flat OI\n"
                "For each, provide a confidence rating (HIGH/MEDIUM/LOW) and brief reason."
            ),
            system="You are a stock screener AI. Rate candidates for intraday futures trading.",
            max_tokens=2048,
            response_schema=schema,
        )
        assert isinstance(result, dict)
        assert "ratings" in result, f"Missing 'ratings': {result.keys()}"
        ratings = result["ratings"]
        assert len(ratings) >= 2, f"Expected ≥2 ratings, got {len(ratings)}"
        for rating in ratings:
            assert rating["confidence"] in ("HIGH", "MEDIUM", "LOW"), f"bad confidence: {rating}"
            assert len(rating["reason"]) > 5, f"reason too short: {rating}"
        summary = ", ".join(f"{r['symbol']}={r['confidence']}" for r in ratings)
        print(f"  [PASS] Stage 3 schema: {len(ratings)} ratings — {summary}")
