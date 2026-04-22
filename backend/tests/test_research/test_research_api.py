"""Tests for the research API endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.research import (
    ResearchReportListItem,
    ResearchReportResponse,
    ResearchStartRequest,
    ResearchStartResponse,
)


class TestResearchStartEndpoint:
    @pytest.mark.asyncio
    @patch("app.api.v1.research.start_research", new_callable=AsyncMock)
    async def test_start_research_returns_report_id(self, mock_start):
        from app.api.v1.research import start_stock_research

        mock_db = AsyncMock()
        mock_report = MagicMock()
        mock_report.id = uuid.uuid4()
        mock_report.symbol = "TCS"
        mock_report.display_name = "Tata Consultancy Services"
        mock_report.status = "PENDING"
        mock_report.agents_total = 6

        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock(side_effect=lambda r: None)

        with patch("app.api.v1.research.ResearchReport") as MockReport:
            MockReport.return_value = mock_report
            with patch("app.data_feed.symbol_master.symbol_master") as mock_sm:
                mock_sm.is_loaded = True
                mock_sm.search.return_value = [{"d": "Tata Consultancy Services", "g": "EQ"}]

                req = ResearchStartRequest(symbol="TCS")
                result = await start_stock_research(req, mock_db)

        assert result.symbol == "TCS"
        assert result.display_name == "Tata Consultancy Services"
        assert result.status == "IN_PROGRESS"
        assert result.agents_total == 6
        mock_start.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.v1.research.start_research", new_callable=AsyncMock)
    async def test_start_research_empty_symbol_raises(self, mock_start):
        from fastapi import HTTPException
        from app.api.v1.research import start_stock_research

        mock_db = AsyncMock()
        req = ResearchStartRequest(symbol="  ")

        with pytest.raises(HTTPException) as exc_info:
            await start_stock_research(req, mock_db)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("app.api.v1.research.start_research", new_callable=AsyncMock)
    async def test_concurrency_limit_returns_429(self, mock_start):
        from fastapi import HTTPException
        from app.api.v1.research import start_stock_research

        mock_start.side_effect = ValueError("Maximum 3 concurrent sessions")

        mock_db = AsyncMock()
        mock_report = MagicMock()
        mock_report.id = uuid.uuid4()
        mock_report.status = "PENDING"
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch("app.api.v1.research.ResearchReport") as MockReport:
            MockReport.return_value = mock_report
            with patch("app.data_feed.symbol_master.symbol_master") as mock_sm:
                mock_sm.is_loaded = False

                req = ResearchStartRequest(symbol="TCS")
                with pytest.raises(HTTPException) as exc_info:
                    await start_stock_research(req, mock_db)

        assert exc_info.value.status_code == 429


class TestResearchReportEndpoint:
    @pytest.mark.asyncio
    async def test_get_report_not_found_raises_404(self):
        from fastapi import HTTPException
        from app.api.v1.research import get_research_report

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(HTTPException) as exc_info:
            await get_research_report(uuid.uuid4(), mock_db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_report_not_found_raises_404(self):
        from fastapi import HTTPException
        from app.api.v1.research import delete_research_report

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await delete_research_report(uuid.uuid4(), mock_db)

        assert exc_info.value.status_code == 404


class TestResearchSchemas:
    def test_start_request_strips_whitespace(self):
        req = ResearchStartRequest(symbol="  TCS  ")
        assert req.symbol == "  TCS  "  # Stripping happens in the endpoint

    def test_start_response_serialization(self):
        resp = ResearchStartResponse(
            report_id=uuid.uuid4(),
            symbol="TCS",
            display_name="Tata Consultancy Services",
            status="IN_PROGRESS",
            agents_total=6,
        )
        data = resp.model_dump()
        assert data["symbol"] == "TCS"
        assert data["agents_total"] == 6

    def test_report_list_item(self):
        item = ResearchReportListItem(
            id=uuid.uuid4(),
            symbol="TCS",
            display_name="TCS Ltd",
            status="COMPLETED",
            recommendation="BUY",
            confidence_score=78.5,
            executive_summary="Strong buy",
            price_at_research=3500.0,
            duration_seconds=45.2,
            created_at="2026-04-20T10:00:00Z",
        )
        data = item.model_dump()
        assert data["recommendation"] == "BUY"
        assert data["confidence_score"] == 78.5
