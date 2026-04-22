"""Research orchestrator — coordinates multi-agent stock research.

Lifecycle:
1. Receive research request (symbol)
2. Create ResearchReport record (status=IN_PROGRESS)
3. Pre-fetch shared context via DataGatherer
4. Launch all sub-agents concurrently via asyncio.gather
5. Broadcast progress via WebSocket as each agent completes
6. Invoke synthesis agent to combine all findings
7. Persist final report, broadcast completion
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.task_registry import TaskRegistry, TaskStatus, TaskType
from app.core.utils import now_ist
from app.models.research_report import ResearchAgentRun, ResearchReport
from app.research.agents.base import AgentResult, BaseResearchAgent, ResearchContext
from app.research.agents.fundamental import FundamentalAgent
from app.research.agents.institutional import InstitutionalAgent
from app.research.agents.news_sentiment import NewsSentimentAgent
from app.research.agents.oi_derivatives import OIDerivativesAgent
from app.research.agents.synthesis import synthesize_report
from app.research.agents.technical import TechnicalAgent
from app.research.agents.valuation import ValuationAgent
from app.research.data_gatherer import gather_context
from app.research.llm_client import LLMClient, create_llm_client
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

# All sub-agents (order matters for display)
SUB_AGENTS: list[BaseResearchAgent] = [
    FundamentalAgent(),
    TechnicalAgent(),
    OIDerivativesAgent(),
    InstitutionalAgent(),
    NewsSentimentAgent(),
    ValuationAgent(),
]

# Track active research sessions
_active_sessions: dict[str, asyncio.Task] = {}
_sessions_lock = asyncio.Lock()


async def start_research(symbol: str, report_id: uuid.UUID) -> None:
    """Launch a research session as a background asyncio task."""
    from app.config import settings

    async with _sessions_lock:
        # Check concurrency limit
        active = sum(1 for t in _active_sessions.values() if not t.done())
        if active >= settings.research_max_concurrent:
            raise ValueError(
                f"Maximum {settings.research_max_concurrent} concurrent research sessions. "
                "Please wait for one to complete."
            )

        task = asyncio.create_task(_run_research(symbol, report_id))
        _active_sessions[str(report_id)] = task

        # Register in task registry
        registry = TaskRegistry()
        registry.track_asyncio_task(
            f"research:{symbol}:{str(report_id)[:8]}",
            task,
            metadata={"symbol": symbol, "report_id": str(report_id)},
        )


async def _run_research(symbol: str, report_id: uuid.UUID) -> None:
    """Main research workflow."""
    start_time = time.monotonic()

    # Create LLM client
    try:
        llm = create_llm_client()
    except ValueError as e:
        logger.error("LLM client creation failed: %s", e)
        async with async_session_factory() as db:
            await _update_report_status(db, report_id, "FAILED", error=str(e))
        await ws_manager.broadcast("research:failed", {
            "report_id": str(report_id), "error": str(e),
        })
        return

    async with async_session_factory() as db:
        # Update report status
        report = await db.get(ResearchReport, report_id)
        if not report:
            return
        report.status = "IN_PROGRESS"
        report.started_at = now_ist()
        await db.commit()

    # Broadcast start
    await ws_manager.broadcast("research:started", {
        "report_id": str(report_id),
        "symbol": symbol,
        "agents_total": len(SUB_AGENTS),
    })

    # Phase 1: Gather shared context
    try:
        async with async_session_factory() as db:
            ctx = await gather_context(symbol, db)
    except Exception as e:
        logger.error("Data gathering failed for %s: %s", symbol, e)
        async with async_session_factory() as db:
            await _update_report_status(db, report_id, "FAILED", error=f"Data gathering failed: {e}")
        await ws_manager.broadcast("research:failed", {
            "report_id": str(report_id), "error": str(e),
        })
        return

    # Phase 2: Run all sub-agents concurrently
    from app.config import settings
    timeout = settings.research_agent_timeout_seconds

    agent_results: dict[str, AgentResult] = {}
    tasks = [
        _run_single_agent(agent, ctx, llm, report_id, timeout)
        for agent in SUB_AGENTS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for agent, result in zip(SUB_AGENTS, results):
        if isinstance(result, Exception):
            agent_results[agent.name] = AgentResult(
                agent_name=agent.name,
                status="failed",
                findings={},
                summary="",
                error=str(result),
            )
        else:
            agent_results[agent.name] = result

    # Phase 3: Synthesis
    await ws_manager.broadcast("research:synthesis_started", {
        "report_id": str(report_id),
    })

    try:
        synthesis = await synthesize_report(ctx, agent_results, llm)
    except Exception as e:
        logger.error("Synthesis failed for %s: %s", symbol, e)
        completed_count = sum(1 for r in agent_results.values() if r.status == "completed")
        total = max(len(agent_results), 1)
        synthesis = {
            "executive_summary": f"Research completed but synthesis failed: {e}",
            "recommendation": "HOLD" if completed_count > 0 else "AVOID",
            "confidence_score": round((completed_count / total) * 50),
            "report_json": {"error": str(e)},
            "report_markdown": f"# {symbol}\n\nSynthesis failed. See individual agent findings.",
        }

    # Phase 4: Persist results
    completed_count = sum(1 for r in agent_results.values() if r.status == "completed")
    failed_count = sum(1 for r in agent_results.values() if r.status == "failed")
    total_duration = time.monotonic() - start_time

    # Determine final status
    if completed_count >= len(SUB_AGENTS):
        final_status = "COMPLETED"
    elif completed_count >= 3:
        final_status = "PARTIAL"
    else:
        final_status = "FAILED"

    try:
        async with async_session_factory() as db:
            report = await db.get(ResearchReport, report_id)
            if report:
                report.status = final_status
                report.completed_at = now_ist()
                report.duration_seconds = round(total_duration, 1)
                report.executive_summary = synthesis.get("executive_summary")
                report.recommendation = synthesis.get("recommendation")
                report.confidence_score = synthesis.get("confidence_score")
                report.report_json = _sanitize_for_jsonb(synthesis.get("report_json"))
                report.report_markdown = synthesis.get("report_markdown")
                report.agents_completed = completed_count
                report.price_at_research = ctx.current_price
                report.market_cap_cr = ctx.market_cap_cr

                # Persist agent runs
                for name, result in agent_results.items():
                    run = ResearchAgentRun(
                        report_id=report_id,
                        agent_name=name,
                        status="COMPLETED" if result.status == "completed" else "FAILED",
                        findings_json=_sanitize_for_jsonb(result.findings),
                        summary_text=result.summary or None,
                        error_message=result.error,
                        data_sources_used=result.data_sources or None,
                        duration_seconds=round(result.duration_seconds, 1),
                        started_at=report.started_at,
                        completed_at=now_ist(),
                    )
                    db.add(run)

                await db.commit()
    except Exception as e:
        logger.error("Failed to persist research report for %s: %s", symbol, e, exc_info=True)
        # Try minimal persist so report doesn't stay IN_PROGRESS forever
        try:
            async with async_session_factory() as db:
                report = await db.get(ResearchReport, report_id)
                if report:
                    report.status = "FAILED"
                    report.completed_at = now_ist()
                    report.duration_seconds = round(total_duration, 1)
                    report.executive_summary = f"Persist failed: {e}"
                    report.agents_completed = completed_count
                    await db.commit()
        except Exception:
            logger.error("Even minimal persist failed for %s", symbol, exc_info=True)

    # Broadcast completion (always, even if persist failed)
    await ws_manager.broadcast("research:completed", {
        "report_id": str(report_id),
        "symbol": symbol,
        "recommendation": synthesis.get("recommendation"),
        "confidence_score": synthesis.get("confidence_score"),
        "executive_summary": synthesis.get("executive_summary"),
        "status": final_status,
    })

    # Cleanup
    async with _sessions_lock:
        _active_sessions.pop(str(report_id), None)

    logger.info(
        "Research completed for %s: %s (confidence=%s, %d/%d agents, %.1fs)",
        symbol, synthesis.get("recommendation"), synthesis.get("confidence_score"),
        completed_count, len(SUB_AGENTS), total_duration,
    )


async def _run_single_agent(
    agent: BaseResearchAgent,
    ctx: ResearchContext,
    llm: LLMClient,
    report_id: uuid.UUID,
    timeout: int,
) -> AgentResult:
    """Run one sub-agent with timeout, error handling, and progress broadcasting."""
    # Broadcast start
    await ws_manager.broadcast("research:agent_started", {
        "report_id": str(report_id),
        "agent_name": agent.name,
        "description": agent.description,
    })

    try:
        result = await asyncio.wait_for(
            agent._safe_research(ctx, llm),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        result = AgentResult(
            agent_name=agent.name,
            status="failed",
            findings={},
            summary="",
            error=f"Timed out after {timeout}s",
        )

    # Broadcast result
    if result.status == "completed" or result.status == "partial":
        await ws_manager.broadcast("research:agent_completed", {
            "report_id": str(report_id),
            "agent_name": agent.name,
            "summary": result.summary[:300] if result.summary else "",
            "duration_seconds": round(result.duration_seconds, 1),
        })
    else:
        await ws_manager.broadcast("research:agent_failed", {
            "report_id": str(report_id),
            "agent_name": agent.name,
            "error": result.error or "Unknown error",
        })

    return result


async def _update_report_status(
    db: AsyncSession, report_id: uuid.UUID, status: str, error: str | None = None
) -> None:
    """Update report status in DB."""
    report = await db.get(ResearchReport, report_id)
    if report:
        report.status = status
        if status in ("COMPLETED", "PARTIAL", "FAILED"):
            report.completed_at = now_ist()
        if error:
            report.executive_summary = f"Error: {error}"
        await db.commit()


def get_active_research_count() -> int:
    """Get number of currently running research sessions."""
    return sum(1 for t in _active_sessions.values() if not t.done())


def _sanitize_for_jsonb(data: dict | None) -> dict | None:
    """Ensure data is JSON-serializable for PostgreSQL JSONB storage.

    Converts Decimal, date, datetime to safe types.
    Replaces NaN/Infinity with None (PostgreSQL JSONB rejects NaN).
    """
    if data is None:
        return None
    import json
    import math
    from datetime import date, datetime
    from decimal import Decimal

    def _default(obj):
        if isinstance(obj, Decimal):
            f = float(obj)
            return None if math.isnan(f) or math.isinf(f) else f
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if hasattr(obj, "__dict__"):
            return str(obj)
        return str(obj)

    def _clean_nans(obj):
        """Recursively replace NaN/Infinity floats with None."""
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: _clean_nans(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean_nans(v) for v in obj]
        return obj

    try:
        cleaned = _clean_nans(data)
        return json.loads(json.dumps(cleaned, default=_default))
    except (TypeError, ValueError) as e:
        logger.warning("JSONB sanitization failed: %s", e)
        return {"_sanitization_error": str(e)}
