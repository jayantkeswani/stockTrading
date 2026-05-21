"""Tests for LLM client retry logic, response_schema pass-through, and parse failure logging."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.research.llm_client import GeminiClient, _extract_json


class TestGenerateJsonRetry:
    """Tests for generate_json retry-once on empty response."""

    @pytest.fixture
    def client(self):
        return GeminiClient(model="gemini-3.5-flash", api_key="test-key")

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, client):
        """First call returns empty, second returns valid JSON."""
        client.generate = AsyncMock(
            side_effect=["", '{"approach": "normal", "summary": "test"}']
        )
        result = await client.generate_json(prompt="test")
        assert result == {"approach": "normal", "summary": "test"}
        assert client.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_empty_after_both_attempts_fail(self, client):
        """Both attempts return empty — returns {}."""
        client.generate = AsyncMock(return_value="")
        result = await client.generate_json(prompt="test")
        assert result == {}
        assert client.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_first_success(self, client):
        """Valid JSON on first attempt — no retry needed."""
        client.generate = AsyncMock(return_value='{"key": "value"}')
        result = await client.generate_json(prompt="test")
        assert result == {"key": "value"}
        assert client.generate.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_non_json_text(self, client):
        """First call returns non-JSON text, second returns valid JSON."""
        client.generate = AsyncMock(
            side_effect=["I cannot help with that.", '{"ok": true}']
        )
        result = await client.generate_json(prompt="test")
        assert result == {"ok": True}
        assert client.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_logs_warning(self, client):
        """Verify a retry warning is logged on empty first attempt."""
        client.generate = AsyncMock(
            side_effect=["", '{"ok": true}']
        )
        with patch("app.research.llm_client.logger") as mock_logger:
            await client.generate_json(prompt="test")
            retry_calls = [
                c for c in mock_logger.warning.call_args_list
                if "retry" in c[0][0].lower()
            ]
            assert len(retry_calls) == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_json_array(self, client):
        """Top-level JSON array → _extract_json returns list, not dict.
        generate_json should retry since it's not a dict result."""
        client.generate = AsyncMock(
            side_effect=['[1, 2, 3]', '{"fixed": true}']
        )
        result = await client.generate_json(prompt="test")
        assert result == {"fixed": True}
        assert client.generate.call_count == 2


class TestResponseSchemaPassthrough:
    """Tests that response_schema is passed through to generate()."""

    @pytest.fixture
    def client(self):
        return GeminiClient(model="gemini-3.5-flash", api_key="test-key")

    @pytest.mark.asyncio
    async def test_schema_passed_to_generate(self, client):
        """response_schema kwarg is forwarded to generate()."""
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        client.generate = AsyncMock(return_value='{"a": "hello"}')

        await client.generate_json(
            prompt="test", response_schema=schema, max_tokens=8192,
        )

        client.generate.assert_called_once_with(
            "test",
            system="",
            json_mode=True,
            max_tokens=8192,
            response_schema=schema,
        )

    @pytest.mark.asyncio
    async def test_schema_none_by_default(self, client):
        """Without response_schema, None is passed to generate()."""
        client.generate = AsyncMock(return_value='{"a": 1}')
        await client.generate_json(prompt="test")

        client.generate.assert_called_once_with(
            "test",
            system="",
            json_mode=True,
            max_tokens=4096,
            response_schema=None,
        )


class TestGeminiGenerateSchemaConfig:
    """Tests that GeminiClient.generate passes response_schema to Gemini config."""

    @staticmethod
    async def _call_sync(fn, *args, **kwargs):
        """Stand-in for asyncio.to_thread that calls synchronously."""
        return fn(*args, **kwargs)

    @pytest.mark.asyncio
    async def test_response_schema_in_config(self):
        """When response_schema is set, it appears in GenerateContentConfig."""
        client = GeminiClient(model="gemini-3.5-flash", api_key="test-key")
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}

        mock_response = MagicMock()
        mock_response.text = '{"x": 42}'
        mock_response.candidates = []

        captured_configs = []

        def capture_call(*args, **kwargs):
            captured_configs.append(kwargs.get("config"))
            return mock_response

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.side_effect = capture_call
        client._client = mock_genai_client

        with patch("app.research.llm_client.asyncio.to_thread", side_effect=self._call_sync):
            await client.generate(prompt="test", json_mode=True, response_schema=schema)

        assert len(captured_configs) == 1
        config = captured_configs[0]
        assert config.response_schema == schema
        assert config.response_mime_type == "application/json"

    @pytest.mark.asyncio
    async def test_no_schema_when_none(self):
        """When response_schema is None, it's not set in config."""
        client = GeminiClient(model="gemini-3.5-flash", api_key="test-key")

        mock_response = MagicMock()
        mock_response.text = '{"x": 1}'
        mock_response.candidates = []

        captured_configs = []

        def capture_call(*args, **kwargs):
            captured_configs.append(kwargs.get("config"))
            return mock_response

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.side_effect = capture_call
        client._client = mock_genai_client

        with patch("app.research.llm_client.asyncio.to_thread", side_effect=self._call_sync):
            await client.generate(prompt="test", json_mode=True)

        config = captured_configs[0]
        assert not hasattr(config, "response_schema") or config.response_schema is None


class TestExtractJsonLogging:
    """Tests for improved parse failure logging."""

    def test_logs_raw_text_on_failure(self):
        """Non-JSON text should log the raw content for diagnostics."""
        with patch("app.research.llm_client.logger") as mock_logger:
            result = _extract_json("This is not JSON at all, just text from Gemini")
            assert result == {}
            mock_logger.warning.assert_called_once()
            log_msg = mock_logger.warning.call_args[0][0]
            assert "len=" in log_msg
            # The raw text should be in the log args
            log_args = mock_logger.warning.call_args[0]
            assert "This is not JSON" in str(log_args)

    def test_logs_empty_text(self):
        """Empty text should log '(empty)' for clarity."""
        with patch("app.research.llm_client.logger") as mock_logger:
            result = _extract_json("")
            assert result == {}
            mock_logger.warning.assert_called_once()
            log_args = mock_logger.warning.call_args[0]
            assert "(empty)" in str(log_args)
