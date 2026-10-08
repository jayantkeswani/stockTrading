"""Tests for IH v2 teacher-ingestion pure logic (clock, title matching, plan/live normalization)."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.core.constants import IST
from app.services.intraday_hunter_v2.teacher.clock import decode_clock, is_session_time
from app.services.intraday_hunter_v2.teacher.live import (
    detect_entry_exit, merge_live, normalize_live, pick_frames, positions_segments, sum_check)
from app.services.intraday_hunter_v2.teacher.plan import normalize_plan
from app.services.intraday_hunter_v2.teacher.youtube import (
    classify_ytdlp_error, find_live_video, find_plan_video, vtt_to_text)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ih_teacher"


def _load(name):
    return json.loads((FIX / name).read_text())


@pytest.mark.parametrize("raw,expected", [
    ("$ 81:60", "09:18"), ("81:60", "09:18"), ("09:18", "09:18"), (" 9:18 ", "09:18"),
    ("O9:l8", "09:18"), ("09.18", "09:18"), ("15:30", "15:30"),
    ("10:01", "10:01"), ("23:59", None), ("08:59", None), ("16:00", None),
    ("", None), ("abc", None), ("99:99", None),
])
def test_decode_clock(raw, expected):
    assert decode_clock(raw) == expected


def test_decode_clock_ambiguous_prefers_normal():
    # "15:31" is plausible as-is; its rotated reading (13:51) is only a fallback
    assert decode_clock("15:31") is None or decode_clock("15:31") == "13:51"


def test_decode_clock_rotated_other():
    # 14:25 rotated -> reverse "52:41" (no 6/9 swap needed)
    assert decode_clock("52:41") == "14:25"


def test_is_session_time():
    assert is_session_time("09:00") and is_session_time("15:30")
    assert not is_session_time("08:59") and not is_session_time(None) and not is_session_time("9:15")


def test_find_plan_video():
    vids = [{"id": "a", "title": "Nifty & Bank nifty | SENSEX Analysis | Prediction For 09 OCT 2026"},
            {"id": "b", "title": "Nifty & Bank nifty | SENSEX Analysis | Prediction For 08 Oct 2026"},
            {"id": "c", "title": "Live Bank Nifty Option Trading"}]
    assert find_plan_video(vids, date(2026, 10, 9))["id"] == "a"
    assert find_plan_video(vids, date(2026, 10, 8))["id"] == "b"
    assert find_plan_video(vids, date(2026, 10, 7)) is None
    assert find_plan_video(vids, date(2025, 10, 9)) is None


def test_find_live_video():
    ts = int(datetime(2026, 10, 9, 16, 0, tzinfo=IST).timestamp())
    prev = int(datetime(2026, 10, 8, 16, 0, tzinfo=IST).timestamp())
    title = "Live Bank Nifty Option Trading 📈 | Intraday Trading by Intraday Hunter"
    vids = [{"id": "x", "title": title, "timestamp": ts},
            {"id": "y", "title": title, "timestamp": prev},
            {"id": "z", "title": "Other", "timestamp": ts},
            {"id": "n", "title": title, "timestamp": None}]
    assert find_live_video(vids, date(2026, 10, 9))["id"] == "x"
    assert find_live_video(vids, date(2026, 10, 8))["id"] == "y"
    assert find_live_video(vids, date(2026, 10, 7)) is None
    # 20:30 UTC on the 8th is already the 9th in IST
    utc_late = int(datetime(2026, 10, 8, 20, 30, tzinfo=timezone.utc).timestamp())
    assert find_live_video([{"id": "q", "title": title, "timestamp": utc_late}], date(2026, 10, 9))


def test_classify_ytdlp_error_and_vtt():
    assert classify_ytdlp_error("HTTP Error 429: Too Many Requests") == "DOWNLOAD_BLOCKED"
    assert classify_ytdlp_error("Sign in to confirm you're not a bot") == "DOWNLOAD_BLOCKED"
    assert classify_ytdlp_error("Video unavailable") == "VIDEO_NOT_FOUND"
    text = vtt_to_text((FIX / "subs_synthetic.vtt").read_text())
    assert text.count("नमस्कार दोस्तों") == 1 and "निफ्टी आज" in text


def test_normalize_plan():
    p = normalize_plan(_load("plan_raw_synthetic.json"))
    assert (p["gap_up_side"], p["flat_side"], p["gap_down_side"]) == ("CE", "none", "PE")
    assert p["levels_onscreen"]["NIFTY"] == [25150.0, 25230.0]
    assert p["levels_onscreen"]["BANKNIFTY"] == [56800.5] and p["levels_onscreen"]["SENSEX"] == []
    assert p["levels_audio"]["BANKNIFTY"] == [56700.0]
    assert normalize_plan({})["gap_up_side"] == "none"
    assert normalize_plan({"gap_up_side": "call"})["gap_up_side"] == "CE"


def test_sum_check():
    legs = [{"pnl": 100.0}, {"pnl": 50.5}]
    assert sum_check(legs, 150.5) and sum_check(legs, 151.0)
    assert not sum_check(legs, 160) and not sum_check([], 0) and not sum_check(legs, None)
    assert not sum_check([{"pnl": None}], 0)


def test_normalize_live():
    lv = normalize_live(_load("live_raw_synthetic.json"))
    assert lv["side"] == "CE" and lv["entry_clock"] == "09:18" and lv["exit_clock"] == "10:42"
    assert lv["legs"][0]["strike"] == 56800.0 and lv["legs"][0]["avg"] == 310.5
    assert lv["total_pnl"] == 9750.5 and lv["legs_sum_ok"] is True
    assert lv["index_prices_entry"]["BANKNIFTY"] == 56810.2
    assert lv["frame_times"] == {"entry_t": 31.0, "exit_t": 600.0, "closed_t": 602.0}
    bad = normalize_live({"legs": [{"option_type": "PE", "pnl": 10}], "total_pnl": 99})
    assert bad["side"] == "PE" and bad["legs_sum_ok"] is False


def test_detect_entry_exit_with_teaser():
    reads = [
        # teaser from late in the session: open, then closed at 13:40
        {"t": 0, "clock": "13:35", "positions_open": True, "all_closed": False},
        {"t": 4, "clock": "13:40", "positions_open": False, "all_closed": True},
        # real start: no positions yet, then entry at 09:18
        {"t": 20, "clock": "09:15", "positions_open": None, "all_closed": None},
        {"t": 30, "clock": "09:18", "positions_open": True, "all_closed": False},
        {"t": 40, "clock": "09:18", "positions_open": True, "all_closed": False},
        {"t": 50, "clock": None, "positions_open": True, "all_closed": False},
        {"t": 300, "clock": "10:42", "positions_open": False, "all_closed": True},
        {"t": 310, "clock": "10:43", "positions_open": False, "all_closed": True},
        {"t": 100, "clock": "09:10", "positions_open": False, "all_closed": True},  # before entry
    ]
    r = detect_entry_exit(reads)
    assert r == {"entry_clock": "09:18", "exit_clock": "10:42", "entry_t": 30,
                 "exit_t": 300, "closed_frame_t": 300}


def test_detect_entry_exit_empty_and_no_exit():
    assert detect_entry_exit([])["entry_clock"] is None
    r = detect_entry_exit([{"t": 1, "clock": "09:20", "positions_open": True, "all_closed": False}])
    assert r["entry_clock"] == "09:20" and r["exit_clock"] is None


def test_parse_meta_line_and_upload_date_fallback():
    """web_embedded yt-dlp prints timestamp=NA; live matching falls back to upload_date."""
    from datetime import date as _d

    from app.services.intraday_hunter_v2.teacher.youtube import find_live_video, parse_meta_line

    assert parse_meta_line("NA|20261008") == (None, _d(2026, 10, 8))
    assert parse_meta_line("1790006126|20260921")[0] == 1790006126
    assert parse_meta_line("garbage") == (None, None)
    vids = [{"id": "p", "title": "Prediction For 09 OCT 2026", "timestamp": None},
            {"id": "x", "title": "Live Bank Nifty Option Trading 📈 | Intraday Trading by Intraday Hunter",
             "timestamp": None, "upload_date": _d(2026, 10, 8)}]
    assert find_live_video(vids, _d(2026, 10, 8))["id"] == "x"
    assert find_live_video(vids, _d(2026, 10, 7)) is None


def test_positions_segments_and_pick_frames():
    """Blue-card scores (0.68 positions / 0 charts) → segments → representative frames."""
    scored = [(0, 0), (5, 0), (10, .68), (15, .68), (20, .53), (25, 0), (30, .68), (35, 0), (40, 0),
              (45, .68), (50, .68)] + [(55 + 5 * i, .68) for i in range(10)]
    segs = positions_segments(scored)
    assert segs[0] == [10, 15, 20, 30]  # one low frame inside a segment does not split it
    assert segs[1][0] == 45 and segs[1][-1] == 100
    picks = pick_frames(segs, stride_s=20)
    assert picks[0] == 10 and 30 in picks and 45 in picks and 100 in picks
    assert pick_frames([[1.0]]) == [1.0]


def test_merge_live_real_oct8_shape():
    """Shape of the real 2026-10-08 video: entry frame has qty/avg, closed frame has qty 0 + P&L."""
    entry = [{"clock": "09:19", "index_prices": {"NIFTY": 22533.6},
              "legs": [{"index": "BANKNIFTY", "strike": 54800, "option_type": "PE", "qty": 1170, "avg": 666.35},
                       {"index": "NIFTY", "strike": 22550, "option_type": "PE", "qty": 0, "avg": 0}]},
             {"clock": "09:24", "index_prices": {"NIFTY": 22540.0},
              "legs": [{"index": "BANKNIFTY", "strike": 54800, "option_type": "PE", "qty": 1170, "avg": 666.35},
                       {"index": "NIFTY", "strike": 22550, "option_type": "PE", "qty": 1430, "avg": 148.35}]}]
    closed = {"clock": "09:48", "total_pnl": 178881.25 - 0, "index_prices": {"NIFTY": 22449.0},
              "legs": [{"index": "BANKNIFTY", "strike": 54800, "option_type": "PE", "qty": 0, "avg": 0, "pnl": 116032.5},
                       {"index": "NIFTY", "strike": 22550, "option_type": "PE", "qty": 0, "avg": 0, "pnl": 62848.75}]}
    live = normalize_live(merge_live(entry, closed))
    assert live["side"] == "PE" and live["entry_clock"] == "09:19" and live["exit_clock"] == "09:48"
    assert [l["qty"] for l in live["legs"]] == [1170, 1430] and live["legs"][0]["avg"] == 666.35
    assert [l["entry_clock"] for l in live["legs"]] == ["09:19", "09:24"]  # legs added later
    assert live["legs_sum_ok"] and live["index_prices_entry"]["NIFTY"] == 22533.6


@pytest.mark.parametrize("raw,expected", [
    ("PE, avoid calls", "PE"), ("CE", "CE"), ("buy puts", "PE"), ("no CE", "none"),
    ("call", "CE"), ("CE or PE", "none"), ("don't buy PE; calls only", "CE"), ("none", "none"),
    ("", "none"), ("PUT", "PE"),
])
def test_plan_side_negations(raw, expected):
    from app.services.intraday_hunter_v2.teacher.plan import _side

    assert _side(raw) == expected
