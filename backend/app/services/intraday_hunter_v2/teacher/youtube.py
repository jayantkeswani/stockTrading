"""yt-dlp / ffmpeg wrappers for teacher ingestion (async subprocess, always with timeouts)."""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import shutil
from datetime import date, datetime

from app.core.constants import IST

logger = logging.getLogger(__name__)

CHANNEL_VIDEOS_URL = "https://www.youtube.com/@IntradayHunter/videos"
LIVE_TITLE_MARKER = "live bank nifty option trading"

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_PLAN_RE = re.compile(r"prediction\s+for\s+(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", re.I)


class TeacherIngestError(Exception):
    """Classified ingestion failure. error_type: VIDEO_NOT_FOUND | DOWNLOAD_BLOCKED |
    SUBS_MISSING | TOOL_MISSING | PARSE_FAILED | TIMEOUT."""

    def __init__(self, error_type: str, detail: str = ""):
        super().__init__(f"{error_type}: {detail}")
        self.error_type = error_type
        self.detail = detail


def require_tool(name: str) -> str:
    """Return the path of an executable on PATH or raise TeacherIngestError(TOOL_MISSING)."""
    path = shutil.which(name)
    if not path:
        raise TeacherIngestError("TOOL_MISSING", f"{name} not found on PATH")
    return path


def classify_ytdlp_error(stderr: str) -> str:
    """Map yt-dlp stderr text to a TeacherIngestError type (PURE)."""
    s = (stderr or "").lower()
    if "sign in to confirm" in s or "429" in s or "403" in s or "too many requests" in s \
            or "forbidden" in s or "bot" in s:
        return "DOWNLOAD_BLOCKED"
    if "video unavailable" in s or "private video" in s or "does not exist" in s \
            or "404" in s or "not found" in s:
        return "VIDEO_NOT_FOUND"
    return "PARSE_FAILED"


async def run_cmd(cmd: list[str], timeout_s: int = 120) -> tuple[int, str, str]:
    """Run a subprocess with a timeout; returns (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as e:
        raise TeacherIngestError("TOOL_MISSING", str(e)) from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise TeacherIngestError("TIMEOUT", f"{os.path.basename(cmd[0])} > {timeout_s}s") from None
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def _ytdlp(args: list[str], timeout_s: int = 120) -> str:
    """Run yt-dlp, raising a classified TeacherIngestError on failure; returns stdout."""
    exe = require_tool("yt-dlp")
    # web_embedded avoids the datacenter/"Sign in to confirm" bot-check on most IPs (cookies:
    # set IH_YTDLP_COOKIES=/path/cookies.txt as an additional fallback).
    extra = ["--extractor-args", "youtube:player_client=web_embedded"]
    if os.environ.get("IH_YTDLP_COOKIES"):
        extra += ["--cookies", os.environ["IH_YTDLP_COOKIES"]]
    rc, out, err = await run_cmd([exe, "--no-update", *extra, *args], timeout_s)
    if rc != 0:
        raise TeacherIngestError(classify_ytdlp_error(err), err.strip()[-300:])
    return out


async def list_channel_videos(limit: int = 40) -> list[dict]:
    """Flat listing of the channel's /videos tab: [{id,title,timestamp,duration}] newest first.

    `timestamp` is usually None in flat mode (see `resolve_timestamp`).
    """
    out = await _ytdlp(["--flat-playlist", "--playlist-end", str(limit), "-j",
                        CHANNEL_VIDEOS_URL], timeout_s=120)
    videos = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("id"):
            videos.append({"id": d["id"], "title": d.get("title") or "",
                           "timestamp": d.get("timestamp"), "duration": d.get("duration")})
    if not videos:
        raise TeacherIngestError("PARSE_FAILED", "channel listing returned no videos")
    return videos


async def resolve_timestamp(video_id: str) -> int | None:
    """Fetch a single video's upload epoch timestamp (like tools/meta.sh). Often None with the
    `web_embedded` player client — use `resolve_upload_day` for matching."""
    day = await resolve_upload_day(video_id)
    return day[0]


def parse_meta_line(line: str) -> tuple[int | None, date | None]:
    """'<timestamp>|<upload_date YYYYMMDD>' → (epoch|None, date|None). yt-dlp prints NA (PURE)."""
    ts_s, _, ud_s = (line or "").strip().partition("|")
    try:
        ts = int(float(ts_s))
    except ValueError:
        ts = None
    try:
        ud = datetime.strptime(ud_s.strip(), "%Y%m%d").date()
    except ValueError:
        ud = None
    return ts, ud


async def resolve_upload_day(video_id: str) -> tuple[int | None, date | None]:
    """(timestamp, upload_date) for one video. The web_embedded client returns timestamp=NA
    but still gives upload_date, which is what live-video matching falls back to."""
    out = await _ytdlp(["--skip-download", "--print", "%(timestamp)s|%(upload_date)s",
                        f"https://www.youtube.com/watch?v={video_id}"], timeout_s=60)
    lines = [ln for ln in out.strip().splitlines() if "|" in ln]
    return parse_meta_line(lines[-1]) if lines else (None, None)


def find_plan_video(videos: list[dict], trading_date: date) -> dict | None:
    """Video whose title says 'Prediction For DD MON YYYY' equal to trading_date (PURE)."""
    for v in videos:
        m = _PLAN_RE.search(v.get("title") or "")
        if not m:
            continue
        mon = _MONTHS.get(m.group(2)[:3].upper())
        if mon and (int(m.group(3)), mon, int(m.group(1))) == (
                trading_date.year, trading_date.month, trading_date.day):
            return v
    return None


def _ist_date(ts) -> date | None:
    """Epoch seconds -> IST calendar date (None if unusable)."""
    try:
        return datetime.fromtimestamp(float(ts), IST).date()
    except (TypeError, ValueError, OSError):
        return None


def find_live_video(videos: list[dict], trading_date: date) -> dict | None:
    """'Live Bank Nifty Option Trading' video uploaded on trading_date (IST) (PURE).

    Matches on the IST date of `timestamp`, else on `upload_date` (a `date`) when the timestamp
    is unavailable. Videos lacking both never match; use `find_live_video_resolved` to fetch them.
    """
    for v in videos:
        if LIVE_TITLE_MARKER not in (v.get("title") or "").lower():
            continue
        day = _ist_date(v.get("timestamp")) or v.get("upload_date")
        if day == trading_date:
            return v
    return None


async def find_live_video_resolved(videos: list[dict], trading_date: date,
                                   max_lookups: int = 4) -> dict | None:
    """Like find_live_video but fetches per-video metadata for live candidates missing timestamps."""
    hit = find_live_video(videos, trading_date)
    if hit:
        return hit
    n = 0
    for v in videos:
        if LIVE_TITLE_MARKER not in (v.get("title") or "").lower() \
                or v.get("timestamp") or v.get("upload_date"):
            continue
        if n >= max_lookups:
            break
        n += 1
        ts, ud = await resolve_upload_day(v["id"])
        v["timestamp"], v["upload_date"] = ts, ud
        if (_ist_date(ts) or ud) == trading_date:
            return v
    return None


def vtt_to_text(vtt: str) -> str:
    """Convert WebVTT (incl. YouTube rolling auto-captions) to plain, de-duplicated text (PURE)."""
    lines: list[str] = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return " ".join(lines)


async def fetch_hindi_auto_subs(video_id: str, workdir: str) -> str | None:
    """Download Hindi auto-captions and return plain text, or None if the video has none."""
    os.makedirs(workdir, exist_ok=True)
    await _ytdlp(["--write-auto-subs", "--sub-langs", "hi", "--sub-format", "vtt",
                  "--skip-download", "-o", os.path.join(workdir, "%(id)s.%(ext)s"),
                  f"https://www.youtube.com/watch?v={video_id}"], timeout_s=120)
    files = glob.glob(os.path.join(workdir, f"{video_id}*.vtt"))
    if not files:
        return None
    with open(files[0], encoding="utf-8", errors="replace") as f:
        text = vtt_to_text(f.read())
    return text or None


async def download_video(video_id: str, workdir: str, height: int = 720) -> str:
    """Download the video (<= height p, mp4) into workdir; returns the file path."""
    os.makedirs(workdir, exist_ok=True)
    require_tool("ffmpeg")
    target = os.path.join(workdir, f"{video_id}.mp4")
    await _ytdlp(["-f", f"b[height<={height}][ext=mp4]/bv*[height<={height}]+ba/b[height<={height}]",
                  "--merge-output-format", "mp4", "-o", target,
                  f"https://www.youtube.com/watch?v={video_id}"], timeout_s=900)
    if not os.path.exists(target):
        found = glob.glob(os.path.join(workdir, f"{video_id}.*"))
        found = [f for f in found if not f.endswith((".vtt", ".part"))]
        if not found:
            raise TeacherIngestError("DOWNLOAD_BLOCKED", "download produced no file")
        target = found[0]
    return target


async def video_duration(path: str) -> float:
    """Duration in seconds via ffprobe."""
    rc, out, err = await run_cmd([require_tool("ffprobe"), "-v", "error", "-show_entries",
                                  "format=duration", "-of", "csv=p=0", path], 30)
    try:
        return float(out.strip())
    except ValueError:
        raise TeacherIngestError("PARSE_FAILED", f"ffprobe: {err.strip()[:200]}") from None


async def extract_frame(path: str, t_seconds: float, out_path: str) -> str:
    """Write a single PNG frame at t_seconds."""
    rc, _, err = await run_cmd([require_tool("ffmpeg"), "-y", "-v", "error", "-ss", f"{t_seconds:.2f}",
                                "-i", path, "-frames:v", "1", out_path], 60)
    if rc != 0 or not os.path.exists(out_path):
        raise TeacherIngestError("PARSE_FAILED", f"ffmpeg frame @{t_seconds}: {err.strip()[:200]}")
    return out_path


async def extract_frames_at(path: str, fractions: list[float]) -> list[str]:
    """PNG frames at the given fractions (0-1) of the video duration, next to the video."""
    dur = await video_duration(path)
    base = os.path.splitext(path)[0]
    return [await extract_frame(path, dur * f, f"{base}_kf{int(f * 100)}.png") for f in fractions]


async def sample_frames(path: str, every_s: int = 2) -> list[tuple[float, str]]:
    """Full-frame JPEGs every `every_s` seconds -> [(t_seconds, path)]."""
    return await sample_crops(path, None, every_s, "full")


async def sample_crops(path: str, crop: tuple[int, int, int, int] | None, every_s: int,
                       tag: str, scale: int = 1) -> list[tuple[float, str]]:
    """Sample frames every `every_s` s, optionally cropped (w,h,x,y) and upscaled, -> [(t, path)]."""
    outdir = os.path.splitext(path)[0] + f"_{tag}"
    os.makedirs(outdir, exist_ok=True)
    # Normalize to 1280x720 first: crop coordinates are defined in that frame, and the
    # bot-check-safe player client often only serves 640x360.
    vf = [f"fps=1/{every_s}", "scale=1280:720"]
    if crop:
        w, h, x, y = crop
        vf.append(f"crop={w}:{h}:{x}:{y}")
    if scale > 1:
        vf.append(f"scale=iw*{scale}:ih*{scale}:flags=lanczos")
    rc, _, err = await run_cmd([require_tool("ffmpeg"), "-y", "-v", "error", "-i", path,
                                "-vf", ",".join(vf), "-q:v", "3",
                                os.path.join(outdir, "%05d.jpg")], 900)
    if rc != 0:
        raise TeacherIngestError("PARSE_FAILED", f"ffmpeg sampling: {err.strip()[:200]}")
    files = sorted(glob.glob(os.path.join(outdir, "*.jpg")))
    return [(i * float(every_s), f) for i, f in enumerate(files)]


