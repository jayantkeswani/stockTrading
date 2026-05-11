"""Provider-agnostic LLM client for research agents.

Default: Gemini 2.5 Flash via google-generativeai SDK.
Supports search grounding for real-time news.
Swappable to other providers via config.
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Result from an LLM call with web search grounding."""

    text: str
    search_queries: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)  # [{url, title, snippet}]


class LLMClient(ABC):
    """Abstract LLM interface — swap providers without changing agent code."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: str = "",
        json_mode: bool = False,
        max_tokens: int = 4096,
    ) -> str:
        """Generate text from prompt. Returns raw text response."""
        ...

    @abstractmethod
    async def generate_with_search(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
    ) -> SearchResult:
        """Generate with web search grounding. Returns text + source citations."""
        ...

    async def generate_json(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
    ) -> dict:
        """Generate and parse JSON response. Falls back to extracting JSON from text."""
        text = await self.generate(prompt, system=system, json_mode=True, max_tokens=max_tokens)
        result = _extract_json(text)
        return result if isinstance(result, dict) else {}


class GeminiClient(LLMClient):
    """Gemini LLM client using google-generativeai SDK."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self._api_key = api_key
        self._model_name = model
        self._client = None
        self._model = None

    def _ensure_client(self):
        """Lazy-init the Gemini client (import is heavy)."""
        if self._client is not None:
            return

        from google import genai

        self._client = genai.Client(api_key=self._api_key)

    async def generate(
        self,
        prompt: str,
        system: str = "",
        json_mode: bool = False,
        max_tokens: int = 4096,
    ) -> str:
        self._ensure_client()
        from google.genai import types

        # Build config kwargs — all params must go in constructor (immutable)
        config_kwargs: dict = {"max_output_tokens": max_tokens}
        if system:
            config_kwargs["system_instruction"] = system
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_kwargs)

        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=self._model_name,
            contents=prompt,
            config=config,
        )
        return response.text or ""

    async def generate_with_search(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
    ) -> SearchResult:
        self._ensure_client()
        from google.genai import types

        # Build config kwargs — all params must go in constructor (immutable)
        config_kwargs: dict = {
            "max_output_tokens": max_tokens,
            "tools": [types.Tool(google_search=types.GoogleSearch())],
        }
        if system:
            config_kwargs["system_instruction"] = system

        config = types.GenerateContentConfig(**config_kwargs)

        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=self._model_name,
            contents=prompt,
            config=config,
        )

        # Extract grounding metadata
        search_queries = []
        sources = []
        if hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            gm = getattr(candidate, "grounding_metadata", None)
            if gm:
                search_queries = list(getattr(gm, "web_search_queries", []) or [])
                for chunk in getattr(gm, "grounding_chunks", []) or []:
                    web = getattr(chunk, "web", None)
                    if web:
                        sources.append({
                            "url": getattr(web, "uri", ""),
                            "title": getattr(web, "title", ""),
                        })

        return SearchResult(
            text=response.text or "",
            search_queries=search_queries,
            sources=sources,
        )


def create_llm_client() -> LLMClient:
    """Factory: create the configured LLM client."""
    from app.config import settings

    provider = getattr(settings, "research_llm_provider", "gemini")
    if provider == "gemini":
        api_key = getattr(settings, "google_api_key", "")
        model = getattr(settings, "research_llm_model", "gemini-3-flash-preview")
        if not api_key:
            raise ValueError(
                "GOOGLE_API_KEY is required for research. Set it in .env"
            )
        return GeminiClient(api_key=api_key, model=model)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response text.

    Handles: clean JSON, markdown code blocks, truncated JSON.
    """
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            clean = part.strip()
            if clean.startswith("json"):
                clean = clean[4:].strip()
            try:
                return json.loads(clean)
            except json.JSONDecodeError:
                continue

    # Extract first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    # Handle truncated JSON — try to repair by closing open brackets
    if start != -1:
        fragment = text[start:]
        repaired = _repair_truncated_json(fragment)
        if repaired:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

    logger.warning("Could not extract JSON from LLM response: %s...", text[:200])
    return {}


def _repair_truncated_json(text: str) -> str | None:
    """Attempt to repair truncated JSON by finding the last valid position.

    Strategy: progressively trim from the end, trying to close brackets
    at each position after a complete value (after }, ], ", digit, true/false/null).
    """
    # Try trimming at each comma/brace from the end
    for i in range(len(text) - 1, 0, -1):
        ch = text[i]
        # Only try positions after a complete value
        if ch not in (",", "}", "]", '"'):
            continue

        candidate = text[: i + 1] if ch != "," else text[:i]

        # Close any unmatched quotes
        in_string = False
        for j, c in enumerate(candidate):
            if c == '"' and (j == 0 or candidate[j - 1] != "\\"):
                in_string = not in_string
        if in_string:
            candidate += '"'

        # Close brackets/braces in reverse order of opening
        open_braces = candidate.count("{") - candidate.count("}")
        open_brackets = candidate.count("[") - candidate.count("]")

        if open_braces < 0 or open_brackets < 0:
            continue

        # Build closing sequence by scanning what's still open
        closers = []
        stack = []
        for c in candidate:
            if c == "{":
                stack.append("}")
            elif c == "[":
                stack.append("]")
            elif c in ("}", "]") and stack and stack[-1] == c:
                stack.pop()
        closers = list(reversed(stack))
        candidate += "".join(closers)

        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue

    return None
