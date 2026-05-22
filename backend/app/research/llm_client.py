"""Provider-agnostic LLM client for research agents.

Default: Gemini 3.5 Flash via google-genai SDK.
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
        response_schema: dict | None = None,
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
        response_schema: dict | None = None,
    ) -> dict:
        """Generate and parse JSON response.

        When response_schema is provided, the model constrains output to match
        the schema exactly (structured output). Falls back to extracting JSON
        from text when no schema is set.

        Retries once on empty/unparseable response (Gemini intermittently
        returns empty text with finish_reason=STOP).
        """
        for attempt in range(2):
            text = await self.generate(
                prompt, system=system, json_mode=True, max_tokens=max_tokens,
                response_schema=response_schema,
            )
            result = _extract_json(text)
            if isinstance(result, dict) and result:
                return result
            if attempt == 0:
                logger.warning(
                    "generate_json empty result (attempt 1), retrying once — "
                    "raw text: %.200s",
                    text or "(empty)",
                )
        return {}


class GeminiClient(LLMClient):
    """Gemini LLM client via google-genai SDK (AI Studio or Vertex AI)."""

    def __init__(
        self,
        model: str = "gemini-3.5-flash",
        api_key: str = "",
        project_id: str = "",
        location: str = "global",
    ):
        self._api_key = api_key
        self._project_id = project_id
        self._location = location
        self._model_name = model
        self._client = None

    def _ensure_client(self):
        """Lazy-init the Gemini client (import is heavy)."""
        if self._client is not None:
            return

        from google import genai

        if self._project_id:
            self._client = genai.Client(
                vertexai=True,
                project=self._project_id,
                location=self._location,
            )
            logger.info(
                "Gemini client initialised in Vertex AI mode (project=%s, location=%s)",
                self._project_id,
                self._location,
            )
        else:
            self._client = genai.Client(api_key=self._api_key)
            logger.info("Gemini client initialised in AI Studio mode")

    async def generate(
        self,
        prompt: str,
        system: str = "",
        json_mode: bool = False,
        max_tokens: int = 4096,
        response_schema: dict | None = None,
    ) -> str:
        """Generate text from the Gemini model. Returns raw text response (empty string on failure)."""
        self._ensure_client()
        from google.genai import types

        # Build config kwargs — all params must go in constructor (immutable)
        config_kwargs: dict = {"max_output_tokens": max_tokens}
        if system:
            config_kwargs["system_instruction"] = system
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
        if response_schema:
            config_kwargs["response_schema"] = response_schema

        config = types.GenerateContentConfig(**config_kwargs)

        response = await asyncio.to_thread(
            self._client.models.generate_content,
            model=self._model_name,
            contents=prompt,
            config=config,
        )

        if not response.text:
            candidate = response.candidates[0] if response.candidates else None
            finish = getattr(candidate, "finish_reason", None) if candidate else None
            safety = getattr(candidate, "safety_ratings", None) if candidate else None
            prompt_feedback = getattr(response, "prompt_feedback", None)
            logger.warning(
                "Gemini empty response: model=%s, finish_reason=%s, candidates=%d, "
                "prompt_feedback=%s, safety=%s, json_mode=%s",
                self._model_name,
                finish,
                len(response.candidates or []),
                prompt_feedback,
                safety,
                json_mode,
            )
            return ""

        return response.text

    async def generate_with_search(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
    ) -> SearchResult:
        """Generate text with Google Search grounding enabled. Returns text and source citations."""
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

        if not response.text:
            candidate = response.candidates[0] if response.candidates else None
            finish = getattr(candidate, "finish_reason", None) if candidate else None
            logger.warning(
                "Gemini empty search response: model=%s, finish_reason=%s, candidates=%d",
                self._model_name,
                finish,
                len(response.candidates or []),
            )

        return SearchResult(
            text=response.text or "",
            search_queries=search_queries,
            sources=sources,
        )


def create_llm_client(pro: bool = False) -> LLMClient:
    """Factory: create the configured LLM client.

    GCP_PROJECT_ID set → Vertex AI (ADC via GCE metadata server).
    GOOGLE_API_KEY set → AI Studio (free tier, local dev).

    pro=True uses the premium model (research_llm_model_pro) for
    high-impact single calls (morning briefing, Stage 3 screener,
    research synthesis).
    """
    from app.config import settings

    provider = getattr(settings, "research_llm_provider", "gemini")
    if provider == "gemini":
        if pro:
            model = getattr(settings, "research_llm_model_pro", "gemini-3.1-pro-preview")
        else:
            model = getattr(settings, "research_llm_model", "gemini-3.5-flash")
        project_id = getattr(settings, "gcp_project_id", "")

        if project_id:
            location = getattr(settings, "vertex_ai_location", "global")
            return GeminiClient(model=model, project_id=project_id, location=location)

        api_key = getattr(settings, "google_api_key", "")
        if api_key:
            return GeminiClient(model=model, api_key=api_key)

        raise ValueError(
            "No Gemini credentials configured. "
            "Set GCP_PROJECT_ID (Vertex AI) or GOOGLE_API_KEY (AI Studio) in .env"
        )
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

    logger.warning(
        "Could not extract JSON from LLM response (len=%d): %.500s",
        len(text), text or "(empty)",
    )
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
