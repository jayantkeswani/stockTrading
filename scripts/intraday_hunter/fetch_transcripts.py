"""Fetch transcripts for Intraday Hunter YouTube channel videos.

Downloads video list from the channel, classifies each as "live_trading" or
"analysis", fetches Hindi transcripts WITH per-segment timestamps, and infers
the trading date from the paired analysis video title.

Usage:
    python scripts/intraday_hunter/fetch_transcripts.py
    python scripts/intraday_hunter/fetch_transcripts.py --count 30
    python scripts/intraday_hunter/fetch_transcripts.py --count 30 --type live_trading

Output:
    scripts/intraday_hunter/data/transcripts_{timestamp}.json

Each video entry contains:
  - video_id, title, url, video_type, trading_date (YYYY-MM-DD, inferred)
  - transcript: joined text (for LLM extraction)
  - segments: [{start_secs, text}, ...] with per-segment video timestamps
              (needed by extract_signals.py to find entry moment in video time)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import RequestBlocked

CHANNEL_URL = "https://www.youtube.com/@IntradayHunter/videos"
DATA_DIR = Path(__file__).resolve().parent / "data"
IST = ZoneInfo("Asia/Kolkata")

LIVE_KEYWORDS = ["live", "option trading", "intraday trading"]
ANALYSIS_KEYWORDS = ["analysis", "prediction", "nifty &", "bank nifty", "sensex"]

# Month name → number for parsing "30 APR 2026" from analysis titles
_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _classify(title: str) -> str:
    t = title.lower()
    if any(k in t for k in LIVE_KEYWORDS):
        return "live_trading"
    if any(k in t for k in ANALYSIS_KEYWORDS):
        return "analysis"
    return "other"


def _parse_date_from_analysis_title(title: str) -> str | None:
    """Extract 'YYYY-MM-DD' from titles like 'Prediction For 30 APR 2026'."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", title)
    if not m:
        return None
    day, mon, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    month_num = _MONTH_MAP.get(mon)
    if not month_num:
        return None
    return f"{year:04d}-{month_num:02d}-{day:02d}"


def _infer_trading_dates(videos: list[dict]) -> list[dict]:
    """
    Videos arrive newest-first and alternate: live, analysis, live, analysis...
    Each analysis title says "Prediction For DD MON YYYY" — that's the date of
    the live trade just above it.

    Strategy:
    - For analysis videos: parse date from title directly.
    - For live videos: use the date from the next analysis video in the list.
    """
    # First pass: extract dates for analysis videos
    for v in videos:
        if v["video_type"] == "analysis":
            v["trading_date"] = _parse_date_from_analysis_title(v["title"])
        else:
            v["trading_date"] = None

    # Second pass: propagate to live trading videos
    for i, v in enumerate(videos):
        if v["video_type"] == "live_trading" and v["trading_date"] is None:
            # Look ahead for the next analysis video
            for j in range(i + 1, min(i + 4, len(videos))):
                if videos[j]["video_type"] == "analysis" and videos[j]["trading_date"]:
                    v["trading_date"] = videos[j]["trading_date"]
                    break

    return videos


def fetch_video_list(count: int) -> list[dict]:
    """Use yt-dlp flat-playlist to get the latest N video IDs and titles."""
    print(f"Fetching last {count} videos from channel...")
    result = subprocess.run(
        [
            "yt-dlp",
            "--flat-playlist",
            "--print", "%(id)s\t%(title)s",
            "--playlist-end", str(count),
            CHANNEL_URL,
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        sys.exit(f"yt-dlp error: {result.stderr}")

    videos = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        video_id, title = parts[0].strip(), parts[1].strip()
        videos.append({
            "video_id": video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "video_type": _classify(title),
            "trading_date": None,
            "transcript": "",
            "segments": [],
            "transcript_lang": "none",
            "transcript_words": 0,
        })

    videos = _infer_trading_dates(videos)

    print(f"Found {len(videos)} videos")
    for vtype in ("live_trading", "analysis", "other"):
        n = sum(1 for v in videos if v["video_type"] == vtype)
        dated = sum(1 for v in videos if v["video_type"] == vtype and v["trading_date"])
        print(f"  {vtype}: {n} ({dated} with date)")
    return videos


def _make_session(cookies_path: str | None) -> object | None:
    """Build a requests.Session with cookies loaded from a Netscape cookies file."""
    if not cookies_path:
        return None
    import http.cookiejar
    import requests
    session = requests.Session()
    jar = http.cookiejar.MozillaCookieJar(cookies_path)
    jar.load(ignore_discard=True, ignore_expires=True)
    session.cookies = jar  # type: ignore[assignment]
    return session


def fetch_transcript(video_id: str, cookies_path: str | None = None) -> tuple[list[dict], str, str]:
    """
    Returns (segments, joined_text, language).
    segments: [{start_secs: float, text: str}, ...]
    Raises RequestBlocked (covers IpBlocked too) if YouTube blocks the request.
    Pass cookies_path to authenticate via browser cookies (Netscape format).
    """
    session = _make_session(cookies_path)
    kwargs = {"http_client": session} if session else {}
    api = YouTubeTranscriptApi(**kwargs)
    for lang in ("hi", "en-IN", "en"):
        try:
            raw = api.fetch(video_id, languages=[lang])
            segments = [{"start_secs": s.start, "text": s.text} for s in raw]
            joined = " ".join(s["text"] for s in segments)
            return segments, joined, lang
        except RequestBlocked:
            raise
        except Exception:
            continue
    return [], "", "none"


def main(count: int, filter_type: str | None, cookies: str | None = None) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    videos = fetch_video_list(count)

    if filter_type:
        videos = [v for v in videos if v["video_type"] == filter_type]
        print(f"Filtered to {len(videos)} {filter_type} videos")

    failed = []
    ip_blocked = False

    for i, video in enumerate(videos, 1):
        vid = video["video_id"]
        date_str = video["trading_date"] or "date-unknown"
        print(f"[{i}/{len(videos)}] [{date_str}] {video['title'][:55]}...", end=" ", flush=True)

        if ip_blocked:
            print("SKIPPED (IP blocked)")
            failed.append(vid)
            continue

        try:
            segments, joined, lang = fetch_transcript(vid, cookies_path=cookies)
        except RequestBlocked:
            print("BLOCKED by YouTube — stopping transcript fetches")
            ip_blocked = True
            failed.append(vid)
            continue

        if not joined:
            print("NO TRANSCRIPT")
            failed.append(vid)
        else:
            video["segments"] = segments
            video["transcript"] = joined
            video["transcript_lang"] = lang
            video["transcript_words"] = len(joined.split())
            print(f"OK ({lang}, {len(segments)} segs, {video['transcript_words']} words)")

        time.sleep(0.5)

    ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    out_path = DATA_DIR / f"transcripts_{ts}.json"
    with_transcript = sum(1 for v in videos if v["transcript"])
    out_path.write_text(
        json.dumps({
            "fetched_at_ist": datetime.now(IST).isoformat(),
            "channel": CHANNEL_URL,
            "total_videos": len(videos),
            "with_transcript": with_transcript,
            "with_segments": sum(1 for v in videos if v["segments"]),
            "with_date": sum(1 for v in videos if v["trading_date"]),
            "failed_ids": failed,
            "videos": videos,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved {len(videos)} videos → {out_path}")
    if failed:
        print(f"No transcript ({len(failed)}): {failed}")
    if ip_blocked:
        print("⚠️  IP blocked mid-run — re-run tomorrow to fill gaps")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--count", type=int, default=25, help="Number of videos to fetch (default: 25)")
    p.add_argument("--type", choices=["live_trading", "analysis", "other"],
                   dest="filter_type", help="Filter to a specific video type")
    p.add_argument("--cookies", metavar="PATH",
                   help="Path to cookies.txt (Netscape format) for YouTube auth. "
                        "Export from Chrome using 'Get cookies.txt LOCALLY' extension.")
    args = p.parse_args()
    main(args.count, args.filter_type, args.cookies)
