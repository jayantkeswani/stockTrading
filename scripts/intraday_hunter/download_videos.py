#!/usr/bin/env python3
"""
Download 30 live trade + 30 analysis videos from @IntradayHunter.

Naming: YYYY-MM-DD_live_trade.mp4 / YYYY-MM-DD_analysis.mp4
Output: ~/Downloads/intraday_hunter/

Already-downloaded file is detected by video ID and renamed in-place.
Run: python3 download_videos.py [--dry-run] [--pairs N]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CHANNEL_URL = "https://www.youtube.com/@IntradayHunter/videos"
OUTPUT_DIR = Path.home() / "Downloads" / "intraday_hunter"

# Already-downloaded file (April 30 live trade)
EXISTING_FILE = Path.home() / "Downloads" / "vidssave.com Live Bank Nifty Option Trading 📈 _ Intraday Trading by Intraday Hunter 720P.mp4"
EXISTING_VIDEO_ID = "wT2gdaoUW-0"

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Regex to extract date from analysis titles like "Prediction For 30 APR 2026"
ANALYSIS_DATE_RE = re.compile(
    r"Prediction For\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", re.IGNORECASE
)


def fetch_playlist(limit: int = 200) -> list[dict]:
    """Fetch video metadata from the channel playlist via yt-dlp."""
    print(f"Fetching playlist metadata (up to {limit} videos)...")
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end", str(limit),
        "-J",
        CHANNEL_URL,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ERROR: yt-dlp failed to fetch playlist.")
        print(result.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    return data.get("entries", [])


def parse_analysis_date(title: str) -> datetime | None:
    m = ANALYSIS_DATE_RE.search(title)
    if not m:
        return None
    day, mon, year = int(m.group(1)), m.group(2).upper(), int(m.group(3))
    month = MONTH_MAP.get(mon)
    if not month:
        return None
    return datetime(year, month, day)


def is_live_trade(title: str) -> bool:
    return "Live Bank Nifty Option Trading" in title or "Live Nifty Option Trading" in title


def build_pairs(entries: list[dict], num_pairs: int) -> list[dict]:
    """
    Pair analysis videos (anchored by date in title) with the nearest
    live trade video that appears before them in the playlist (newer upload).
    Returns list of dicts: {date, analysis_id, analysis_title, live_id, live_title}
    """
    pairs = []
    used_live_ids = set()

    for i, entry in enumerate(entries):
        title = entry.get("title", "")
        date = parse_analysis_date(title)
        if date is None:
            continue

        # Search backward (toward index 0 = newer) for the closest live trade
        live_entry = None
        for j in range(i - 1, -1, -1):
            candidate = entries[j]
            cid = candidate.get("id")
            if cid in used_live_ids:
                continue
            if is_live_trade(candidate.get("title", "")):
                live_entry = candidate
                break

        if live_entry is None:
            print(f"  WARNING: no live trade found for {date.date()} — skipping")
            continue

        used_live_ids.add(live_entry["id"])
        pairs.append({
            "date": date,
            "date_str": date.strftime("%Y-%m-%d"),
            "analysis_id": entry["id"],
            "analysis_title": title,
            "live_id": live_entry["id"],
            "live_title": live_entry.get("title", ""),
        })

        if len(pairs) >= num_pairs:
            break

    return pairs


def download_video(video_id: str, output_path: Path, dry_run: bool) -> bool:
    """Download a single video at 720p to output_path."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    tmp_template = str(output_path.parent / f"_tmp_{video_id}.%(ext)s")

    if dry_run:
        print(f"    [dry-run] would download {url} → {output_path.name}")
        return True

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]",
        "--merge-output-format", "mp4",
        "-o", tmp_template,
        "--no-playlist",
        url,
    ]
    print(f"    Downloading {video_id}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    ERROR downloading {video_id}: {result.stderr[-300:]}")
        return False

    # yt-dlp may produce _tmp_<id>.mp4 or similar — find it
    tmp_files = list(output_path.parent.glob(f"_tmp_{video_id}.*"))
    if not tmp_files:
        print(f"    ERROR: downloaded file not found for {video_id}")
        return False

    tmp_files[0].rename(output_path)
    print(f"    Saved: {output_path.name}")
    return True


def handle_existing_file(pairs: list[dict], output_dir: Path, dry_run: bool):
    """Move/rename the already-downloaded April 30 live trade file."""
    for pair in pairs:
        if pair["live_id"] == EXISTING_VIDEO_ID:
            dest = output_dir / f"{pair['date_str']}_live_trade.mp4"
            if dest.exists():
                print(f"  Already exists: {dest.name} — skipping")
                return
            if EXISTING_FILE.exists():
                if dry_run:
                    print(f"  [dry-run] would move {EXISTING_FILE.name} → {dest.name}")
                else:
                    EXISTING_FILE.rename(dest)
                    print(f"  Moved existing file → {dest.name}")
            return


def main():
    parser = argparse.ArgumentParser(description="Download @IntradayHunter videos")
    parser.add_argument("--dry-run", action="store_true", help="List what would be downloaded without downloading")
    parser.add_argument("--pairs", type=int, default=30, help="Number of trading day pairs to download (default: 30)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Fetch enough playlist entries to find 30 pairs (channel has non-trading videos mixed in)
    entries = fetch_playlist(limit=120)
    print(f"Fetched {len(entries)} playlist entries\n")

    pairs = build_pairs(entries, args.pairs)
    print(f"Found {len(pairs)} pairs:\n")
    for p in pairs:
        print(f"  {p['date_str']}  live={p['live_id']}  analysis={p['analysis_id']}")
    print()

    if not pairs:
        print("No pairs found — exiting.")
        sys.exit(1)

    # Handle already-downloaded file first
    handle_existing_file(pairs, OUTPUT_DIR, args.dry_run)

    # Download remaining videos
    errors = []
    for pair in pairs:
        date_str = pair["date_str"]

        live_path = OUTPUT_DIR / f"{date_str}_live_trade.mp4"
        analysis_path = OUTPUT_DIR / f"{date_str}_analysis.mp4"

        # Live trade
        if live_path.exists():
            print(f"  {date_str} live_trade — already exists, skipping")
        else:
            ok = download_video(pair["live_id"], live_path, args.dry_run)
            if not ok:
                errors.append(f"{date_str} live_trade ({pair['live_id']})")
            if not args.dry_run:
                time.sleep(2)

        # Analysis
        if analysis_path.exists():
            print(f"  {date_str} analysis   — already exists, skipping")
        else:
            ok = download_video(pair["analysis_id"], analysis_path, args.dry_run)
            if not ok:
                errors.append(f"{date_str} analysis ({pair['analysis_id']})")
            if not args.dry_run:
                time.sleep(2)

    print(f"\nDone. Output folder: {OUTPUT_DIR}")
    if errors:
        print(f"\nFailed downloads ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
    else:
        print("All downloads completed successfully.")


if __name__ == "__main__":
    main()
