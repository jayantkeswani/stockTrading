#!/usr/bin/env python
"""Run teacher ingestion on the Mac and print JSON or push it to the server.

    python scripts/intraday_hunter/teacher_ingest_local.py --date 2026-10-09 --plan --live \
        [--push http://HOST] [--whisper]

Needs: yt-dlp, ffmpeg, tesseract (live), CLAUDE_CODE_OAUTH_TOKEN (LLM). --whisper falls back to
mlx_whisper audio transcription when Hindi auto-subs are missing (only if importable).
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.intraday_hunter_v2.teacher.ingest import build_live, build_plan  # noqa: E402
from app.services.intraday_hunter_v2.teacher.youtube import TeacherIngestError  # noqa: E402


def _whisper_fallback():
    """Return an async transcribe(video_path)->str using mlx_whisper, or None if unavailable."""
    try:
        import mlx_whisper  # type: ignore
    except ImportError:
        print("warning: --whisper requested but mlx_whisper is not importable", file=sys.stderr)
        return None

    async def _run(video_path: str) -> str:
        res = await asyncio.to_thread(mlx_whisper.transcribe, video_path, language="hi")
        return res.get("text", "")
    return _run


async def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="trading date YYYY-MM-DD")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--push", metavar="URL")
    ap.add_argument("--whisper", action="store_true")
    a = ap.parse_args()
    d = date.fromisoformat(a.date)
    if not (a.plan or a.live):
        a.plan = a.live = True
    root = os.environ.get("IH_TEACHER_WORKDIR", "/tmp/ih_teacher_local")
    out: dict = {"trading_date": d.isoformat(), "plan": None, "plan_video_id": None,
                 "live": None, "live_video_id": None}
    rc = 0
    try:
        if a.plan:
            wd = os.path.join(root, f"{d}_plan")
            try:
                out["plan"], out["plan_video_id"] = await build_plan(
                    d, wd, _whisper_fallback() if a.whisper else None)
            finally:
                shutil.rmtree(wd, ignore_errors=True)
        if a.live:
            wd = os.path.join(root, f"{d}_live")
            try:
                out["live"], out["live_video_id"] = await build_live(d, wd)
            finally:
                shutil.rmtree(wd, ignore_errors=True)
    except TeacherIngestError as e:
        print(f"FAILED {e.error_type}: {e.detail}", file=sys.stderr)
        rc = 1
    if a.push and (out["plan"] or out["live"]):
        import httpx
        r = httpx.post(a.push.rstrip("/") + "/api/v1/intraday-hunter/teacher/ingest",
                       json=out, timeout=60)
        print(f"push -> HTTP {r.status_code}: {r.text[:300]}")
        rc = rc or (0 if r.is_success else 1)
    else:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
