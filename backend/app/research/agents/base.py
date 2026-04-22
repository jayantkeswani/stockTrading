"""Base classes for research sub-agents.

Each sub-agent:
1. Receives a ResearchContext with shared pre-fetched data
2. Fetches agent-specific data from existing data sources
3. Optionally uses LLM to interpret/summarize findings
4. Returns an AgentResult with structured findings + summary
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.data_sources.schemas import PriceHistory, StockInfo
    from app.models.fundamental_data import StockFundamental
    from app.research.llm_client import LLMClient


@dataclass
class ResearchContext:
    """Shared context pre-fetched by DataGatherer, passed to all agents.

    Prevents N agents from redundantly fetching the same stock info / price history.
    """

    symbol: str
    display_name: str
    stock_info: StockInfo | None = None
    price_history_1y: list[PriceHistory] = field(default_factory=list)
    current_price: float | None = None
    market_cap_cr: float | None = None
    is_fo_eligible: bool = False
    existing_fundamental: StockFundamental | None = None
    fyers_symbol: str | None = None  # e.g. "NSE:TCS-EQ"


@dataclass
class AgentResult:
    """Output from a single research sub-agent."""

    agent_name: str
    status: str  # "completed" | "failed" | "partial"
    findings: dict  # Structured findings (JSON-serializable)
    summary: str  # Human-readable summary paragraph
    data_sources: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None


class BaseResearchAgent(ABC):
    """Abstract base for research sub-agents."""

    name: str  # Agent identifier, e.g. "fundamental"
    description: str  # Human-readable, e.g. "Analyzing earnings and growth"

    @abstractmethod
    async def research(self, ctx: ResearchContext, llm: LLMClient) -> AgentResult:
        """Execute research for the given symbol.

        Must be idempotent and handle its own errors — never raise.
        Return AgentResult with status="failed" on error.
        """
        ...

    async def _safe_research(self, ctx: ResearchContext, llm: LLMClient) -> AgentResult:
        """Wrapper that catches unexpected errors and returns a failed result."""
        start = time.monotonic()
        try:
            result = await self.research(ctx, llm)
            result.duration_seconds = time.monotonic() - start
            return result
        except Exception as e:
            return AgentResult(
                agent_name=self.name,
                status="failed",
                findings={},
                summary="",
                error=str(e),
                duration_seconds=time.monotonic() - start,
            )
