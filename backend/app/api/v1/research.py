"""Research API endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.research_report import ResearchAgentRun, ResearchReport
from app.research.orchestrator import get_active_research_count, start_research
from app.schemas.research import (
    ResearchReportListItem,
    ResearchReportResponse,
    ResearchStartRequest,
    ResearchStartResponse,
)

router = APIRouter()


@router.post("/start", response_model=ResearchStartResponse)
async def start_stock_research(
    req: ResearchStartRequest,
    db: AsyncSession = Depends(get_db),
):
    """Start a new research session for a stock.

    Validates the symbol, creates a report record, and launches the
    orchestrator as a background asyncio task. Returns immediately.
    """
    symbol = req.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")

    # Validate symbol exists
    from app.data_feed.symbol_master import symbol_master

    display_name = symbol
    if symbol_master.is_loaded:
        results = symbol_master.search(symbol, limit=1)
        if results and results[0].get("g") == "EQ":
            display_name = results[0].get("d", symbol)
        elif not results:
            # Try yfinance as fallback validation
            pass  # Will be validated during data gathering

    # Create report record
    report = ResearchReport(
        symbol=symbol,
        display_name=display_name,
        status="PENDING",
        agents_total=6,
        agents_completed=0,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    # Launch orchestrator
    try:
        await start_research(symbol, report.id)
    except ValueError as e:
        # Concurrency limit reached
        report.status = "FAILED"
        report.executive_summary = str(e)
        await db.commit()
        raise HTTPException(status_code=429, detail=str(e))

    return ResearchStartResponse(
        report_id=report.id,
        symbol=symbol,
        display_name=display_name,
        status="IN_PROGRESS",
        agents_total=6,
    )


@router.get("/reports", response_model=list[ResearchReportListItem])
async def list_research_reports(
    symbol: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List past research reports, most recent first."""
    query = select(ResearchReport).order_by(ResearchReport.created_at.desc()).limit(limit)

    if symbol:
        query = query.where(ResearchReport.symbol == symbol.upper())

    result = await db.execute(query)
    reports = result.scalars().all()
    return reports


@router.get("/reports/{report_id}", response_model=ResearchReportResponse)
async def get_research_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a full research report with all agent runs."""
    result = await db.execute(
        select(ResearchReport)
        .options(selectinload(ResearchReport.agent_runs))
        .where(ResearchReport.id == report_id)
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    return report


@router.delete("/reports/{report_id}")
async def delete_research_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a research report."""
    report = await db.get(ResearchReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    await db.delete(report)
    await db.commit()
    return {"status": "deleted"}
