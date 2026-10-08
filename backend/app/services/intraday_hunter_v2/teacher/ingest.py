"""Scheduled teacher-ingestion jobs + DB upsert (used by the scheduler and the ingest endpoint)."""
from __future__ import annotations

import logging
import os
import shutil
from datetime import date

from app.agent.notification import send_telegram
from app.config import settings
from app.core.database import async_session_factory
from app.core.utils import now_ist
from app.models.ih_v2 import IhTeacherDay
from app.services.intraday_hunter_v2.teacher import youtube as yt
from app.services.intraday_hunter_v2.teacher.live import extract_live, normalize_live
from app.services.intraday_hunter_v2.teacher.plan import extract_plan, normalize_plan
from app.services.intraday_hunter_v2.teacher.youtube import TeacherIngestError

logger = logging.getLogger(__name__)


def _workdir(trading_date: date, kind: str) -> str:
    """Per-job temp directory under IH_TEACHER_WORKDIR."""
    root = os.environ.get("IH_TEACHER_WORKDIR", "/tmp/ih_teacher")
    return os.path.join(root, f"{trading_date.isoformat()}_{kind}")


def _enabled() -> bool:
    """Ingestion kill-switch (settings.ih_teacher_ingest_enabled)."""
    return bool(settings.ih_teacher_ingest_enabled)


async def get_teacher_day(session, d: date) -> IhTeacherDay | None:
    """Fetch the IhTeacherDay row for a date (or None)."""
    return await session.get(IhTeacherDay, d)


async def _get_or_create(session, d: date) -> IhTeacherDay:
    """Fetch or create (PENDING) the row for a date."""
    row = await session.get(IhTeacherDay, d)
    if row is None:
        row = IhTeacherDay(trading_date=d, status="PENDING", errors=[])
        session.add(row)
        await session.flush()
    return row


def _append_error(row: IhTeacherDay, job: str, err_type: str, detail: str) -> None:
    """Append to the JSONB errors list (reassigned so SQLAlchemy sees the change)."""
    row.errors = [*(row.errors or []),
                  {"job": job, "at": now_ist().isoformat(), "error_type": err_type, "detail": detail[:500]}]


async def _record_failure(trading_date: date, job: str, err_type: str, detail: str,
                          status: str | None = None) -> None:
    """Persist a failure on the day row (+ optional status) and alert via Telegram."""
    try:
        async with async_session_factory() as session:
            row = await _get_or_create(session, trading_date)
            _append_error(row, job, err_type, detail)
            if status:
                row.status = status
            await session.commit()
    except Exception:  # noqa: BLE001 — alerting must still happen
        logger.exception("ih_v2 teacher: could not persist failure")
    await send_telegram(f"⚠️ IH v2 teacher {job} {trading_date}: {err_type} — {detail}")


async def build_plan(trading_date: date, workdir: str, whisper_fallback=None) -> tuple[dict, str]:
    """Find + process the evening plan video -> (normalized plan, video_id). No DB access.

    `whisper_fallback`: optional async callable(video_path)->str used when Hindi subs are missing.
    """
    videos = await yt.list_channel_videos()
    vid = yt.find_plan_video(videos, trading_date)
    if not vid:
        raise TeacherIngestError("VIDEO_NOT_FOUND", f"no 'Prediction For' video for {trading_date}")
    video_path = await yt.download_video(vid["id"], workdir, height=480)
    subs = await yt.fetch_hindi_auto_subs(vid["id"], workdir)
    if not subs:
        if whisper_fallback is None:
            raise TeacherIngestError("SUBS_MISSING", f"no Hindi auto-subs for {vid['id']}")
        subs = await whisper_fallback(video_path)
    frames = await yt.extract_frames_at(video_path, [0.3, 0.6, 0.9])
    return await extract_plan(subs, frames, trading_date), vid["id"]


async def build_live(trading_date: date, workdir: str) -> tuple[dict, str]:
    """Find + process the live-trading video -> (normalized live dict, video_id). No DB access."""
    videos = await yt.list_channel_videos()
    vid = await yt.find_live_video_resolved(videos, trading_date)
    if not vid:
        raise TeacherIngestError("VIDEO_NOT_FOUND", f"no live video for {trading_date}")
    path = await yt.download_video(vid["id"], workdir, height=720)
    return await extract_live(path, trading_date), vid["id"]


async def run_plan_job(trading_date: date, attempt: str) -> IhTeacherDay | None:
    """Fetch + store the teacher's plan for trading_date. Skips if already present/disabled."""
    job = f"plan@{attempt}"
    if not _enabled():
        return None
    async with async_session_factory() as session:
        row = await get_teacher_day(session, trading_date)
        if row is not None and row.plan:
            return row
    workdir = _workdir(trading_date, "plan")
    try:
        plan, vid = await build_plan(trading_date, workdir)
    except TeacherIngestError as e:
        await _record_failure(trading_date, job, e.error_type, e.detail)
        return None
    except Exception as e:  # noqa: BLE001
        logger.exception("ih_v2 teacher plan job crashed")
        await _record_failure(trading_date, job, "PARSE_FAILED", repr(e))
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    async with async_session_factory() as session:
        row = await upsert_teacher_day(session, trading_date, plan=plan, plan_video_id=vid)
        await session.commit()
        return row


async def plan_missing_check(trading_date: date) -> bool:
    """08:30 check: if no plan is stored, alert + mark PLAN_MISSING. True if missing."""
    if not _enabled():
        return False
    async with async_session_factory() as session:
        row = await get_teacher_day(session, trading_date)
        if row is not None and row.plan:
            return False
        row = row or await _get_or_create(session, trading_date)
        row.status = "PLAN_MISSING"
        await session.commit()
    await send_telegram(f"⚠️ IH v2 teacher plan {trading_date}: no plan by 08:30 — "
                        "run v2 without the plan (teacher_plan_missing=true)")
    return True


async def run_live_job(trading_date: date, attempt: str) -> IhTeacherDay | None:
    """Fetch + store the teacher's live trade for trading_date (attempt '15:45'|'17:30'|'manual')."""
    job = f"live@{attempt}"
    if not _enabled():
        return None
    async with async_session_factory() as session:
        row = await get_teacher_day(session, trading_date)
        if row is not None and row.live:
            return row
    workdir = _workdir(trading_date, "live")
    final = attempt in ("17:30", "manual")
    try:
        live, vid = await build_live(trading_date, workdir)
    except TeacherIngestError as e:
        await _record_failure(trading_date, job, e.error_type, e.detail,
                              status="LIVE_MISSING" if final else None)
        return None
    except Exception as e:  # noqa: BLE001
        logger.exception("ih_v2 teacher live job crashed")
        await _record_failure(trading_date, job, "PARSE_FAILED", repr(e),
                              status="LIVE_MISSING" if final else None)
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    async with async_session_factory() as session:
        row = await upsert_teacher_day(session, trading_date, live=live, live_video_id=vid)
        await session.commit()
        return row


async def upsert_teacher_day(session, trading_date: date, *, plan: dict | None = None,
                             live: dict | None = None, plan_video_id: str | None = None,
                             live_video_id: str | None = None,
                             source: str = "server") -> IhTeacherDay:
    """Create/update the day row, normalizing plan/live; caller commits.

    Status: plan -> PLAN_READY (unless already LIVE_READY); live -> LIVE_READY.
    """
    row = await _get_or_create(session, trading_date)
    now = now_ist()
    if plan is not None:
        row.plan = normalize_plan(plan)
        row.plan_video_id = plan_video_id or row.plan_video_id
        row.plan_fetched_at = now
        if row.status != "LIVE_READY":
            row.status = "PLAN_READY"
    if live is not None:
        row.live = normalize_live(live)
        row.live_video_id = live_video_id or row.live_video_id
        row.live_fetched_at = now
        row.status = "LIVE_READY"
    row.source = source
    await session.flush()
    return row
