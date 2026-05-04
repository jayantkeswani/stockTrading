"""Reverse-engineer Intraday Hunter's trading strategy from YouTube transcripts.

Reads transcript JSON from fetch_transcripts.py, runs an LLM extraction pass
on each video to pull structured trading features, then aggregates across all
videos to produce a strategy hypothesis.

Usage:
    python scripts/intraday_hunter/analyze_strategy.py data/transcripts_*.json
    python scripts/intraday_hunter/analyze_strategy.py data/transcripts_*.json --type live_trading
    python scripts/intraday_hunter/analyze_strategy.py data/transcripts_*.json --out hypothesis.md

Output:
    stdout: aggregated strategy hypothesis
    scripts/intraday_hunter/data/extractions_{timestamp}.json  (per-video LLM output)
    scripts/intraday_hunter/data/hypothesis_{timestamp}.md     (final strategy doc)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
load_dotenv(REPO_ROOT / ".env")

from app.research.llm_client import create_llm_client  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = Path(__file__).resolve().parent / "data"

EXTRACTION_SYSTEM = """You are a trading strategy analyst. You will receive a Hindi transcript of an Indian stock market trading video (auto-generated, so punctuation is minimal).

Extract the following fields as JSON. All fields are optional — use null if not mentioned.

{
  "pre_market_bias": "bullish | bearish | neutral | null",
  "bias_reason": "brief reason for the bias (e.g. 'rejection at previous high', 'gap down open')",
  "entry_direction": "CE | PE | null",
  "entry_trigger": "exact trigger for entry (e.g. 'gap down open with selling', 'pullback to VWAP', 'rejection at PDH')",
  "key_levels": ["list of levels mentioned: VWAP, PDH, PDL, CPR, support, resistance, specific price levels"],
  "indicators_mentioned": ["VWAP", "PDH", "PDL", "CPR", "OI", "volume", "candlestick patterns", etc.],
  "setup_type": "gap_and_go | rejection_short | vwap_pullback | orb_breakout | range_breakout | other | null",
  "entry_timing": "time mentioned for entry (e.g. 'market open', '9:30', 'after first candle')",
  "exit_trigger": "what caused the exit (e.g. 'momentum exhausted', 'target hit', 'time exit')",
  "sl_method": "how SL was defined (e.g. 'previous high', 'VWAP', 'fixed %', 'structure level')",
  "target_method": "how target was defined (e.g. 'previous low', 'support', 'fixed R:R', 'momentum based')",
  "rr_mentioned": "R:R ratio if mentioned (e.g. '1:1.5')",
  "market_context_used": ["prior day candle", "global cues", "gap analysis", "OI data", "volume analysis"],
  "retail_vs_smart_money": "does the trader discuss smart money / institutional vs retail positioning? yes | no",
  "smart_money_logic": "brief description if yes (e.g. 'institutions held short from rejection zone, retail afraid to sell')",
  "risk_rules_mentioned": ["list of explicit risk rules: 'cut loss on big green candle', 'max 2 trades/day', etc."],
  "profitable_trade": "yes | no | unclear",
  "video_type": "live_trading | analysis | other"
}

Respond with ONLY the JSON object, no explanation.
"""


async def extract_features(llm, video: dict) -> dict:
    transcript = video.get("transcript", "")
    if not transcript or len(transcript.split()) < 50:
        return {"video_id": video["video_id"], "title": video["title"], "skipped": True, "reason": "no transcript"}

    prompt = f"Video title: {video['title']}\n\nTranscript:\n{transcript[:6000]}"
    try:
        result = await llm.generate_json(prompt=prompt, system=EXTRACTION_SYSTEM, max_tokens=1024)
        result["video_id"] = video["video_id"]
        result["title"] = video["title"]
        result["video_type_raw"] = video.get("video_type", "unknown")
        result["skipped"] = False
        return result
    except Exception as e:
        return {"video_id": video["video_id"], "title": video["title"], "skipped": True, "reason": str(e)}


def _freq(items: list, top: int = 10) -> list[tuple[str, int]]:
    return Counter(x for x in items if x).most_common(top)


def aggregate(extractions: list[dict]) -> dict:
    live = [e for e in extractions if not e.get("skipped") and e.get("video_type_raw") == "live_trading"]
    analysis = [e for e in extractions if not e.get("skipped") and e.get("video_type_raw") == "analysis"]
    all_valid = live + analysis

    def collect(field: str, vids: list[dict]) -> list:
        out = []
        for v in vids:
            val = v.get(field)
            if isinstance(val, list):
                out.extend(val)
            elif val and val != "null":
                out.append(val)
        return out

    return {
        "summary": {
            "total_videos": len(extractions),
            "live_trading": len(live),
            "analysis": len(analysis),
            "skipped": sum(1 for e in extractions if e.get("skipped")),
        },
        "bias": {
            "distribution": dict(_freq(collect("pre_market_bias", live))),
            "top_reasons": [r for r, _ in _freq(collect("bias_reason", live), 5)],
        },
        "entry": {
            "direction_distribution": dict(_freq(collect("entry_direction", live))),
            "top_triggers": [t for t, _ in _freq(collect("entry_trigger", live), 8)],
            "top_setup_types": dict(_freq(collect("setup_type", all_valid))),
            "top_entry_timings": dict(_freq(collect("entry_timing", live))),
        },
        "levels": {
            "top_key_levels": dict(_freq(collect("key_levels", all_valid))),
            "top_indicators": dict(_freq(collect("indicators_mentioned", all_valid))),
        },
        "exits": {
            "top_exit_triggers": dict(_freq(collect("exit_trigger", live))),
            "sl_methods": dict(_freq(collect("sl_method", all_valid))),
            "target_methods": dict(_freq(collect("target_method", all_valid))),
            "rr_mentioned": dict(_freq(collect("rr_mentioned", all_valid))),
        },
        "context": {
            "market_context_used": dict(_freq(collect("market_context_used", all_valid))),
            "retail_vs_smart_money": dict(_freq(collect("retail_vs_smart_money", all_valid))),
            "top_smart_money_logic": [s for s, _ in _freq(collect("smart_money_logic", all_valid), 5)],
        },
        "risk_rules": dict(_freq(collect("risk_rules_mentioned", all_valid))),
        "profitability": dict(_freq(collect("profitable_trade", live))),
    }


def build_hypothesis(agg: dict) -> str:
    b = agg["bias"]
    e = agg["entry"]
    lv = agg["levels"]
    ex = agg["exits"]
    ctx = agg["context"]
    s = agg["summary"]

    lines = [
        "# Intraday Hunter — Reverse-Engineered Strategy Hypothesis",
        f"\n_Generated from {s['total_videos']} videos ({s['live_trading']} live trades, {s['analysis']} analysis)_",
        "",
        "## Pre-Market Bias",
        f"- Bias distribution: {b['distribution']}",
        "- How bias is formed:",
    ]
    for r in b["top_reasons"]:
        lines.append(f"  - {r}")

    lines += [
        "",
        "## Entry Setup",
        f"- Direction: {e['direction_distribution']}",
        "- Setup types: " + ", ".join(f"{k}({v})" for k, v in e["top_setup_types"].items()),
        "- Timing: " + ", ".join(f"{k}({v})" for k, v in e["top_entry_timings"].items()),
        "- Entry triggers:",
    ]
    for t in e["top_triggers"]:
        lines.append(f"  - {t}")

    lines += [
        "",
        "## Key Levels & Indicators",
        "- Levels used: " + ", ".join(f"{k}({v})" for k, v in lv["top_key_levels"].items()),
        "- Indicators: " + ", ".join(f"{k}({v})" for k, v in lv["top_indicators"].items()),
        "",
        "## Exit & Risk Management",
        "- Exit triggers:",
    ]
    for t, n in ex["top_exit_triggers"].items():
        lines.append(f"  - {t} ({n}x)")
    lines += [
        "- SL methods: " + ", ".join(f"{k}({v})" for k, v in ex["sl_methods"].items()),
        "- Target methods: " + ", ".join(f"{k}({v})" for k, v in ex["target_methods"].items()),
        "- R:R: " + ", ".join(f"{k}({v})" for k, v in ex["rr_mentioned"].items()),
    ]

    lines += [
        "",
        "## Smart Money / Institutional Logic",
        f"- Uses retail vs smart money framing: {ctx['retail_vs_smart_money']}",
        "- Logic patterns:",
    ]
    for logic in ctx["top_smart_money_logic"]:
        lines.append(f"  - {logic}")

    lines += [
        "",
        "## Market Context Used",
    ]
    for k, v in ctx["market_context_used"].items():
        lines.append(f"  - {k}: {v}x")

    lines += [
        "",
        "## Risk Rules",
    ]
    for rule, n in agg["risk_rules"].items():
        lines.append(f"  - {rule} ({n}x)")

    lines += [
        "",
        "## Profitability",
        f"  - {agg['profitability']}",
        "",
        "## Implied Strategy",
        "Based on the above evidence, the implied strategy is:",
        "1. **Pre-market**: Read previous day's candle structure (rejection/acceptance) + gap direction",
        "2. **Bias**: Bearish if prior rejection + gap down; bullish if prior breakout + gap up",
        "3. **Entry**: Wait for open, confirm direction via first 1-2 candles, enter PUT/CALL",
        "4. **Key levels**: PDH/PDL as primary context; VWAP as intraday reference",
        "5. **SL**: Structure-based (previous candle high/low or VWAP)",
        "6. **Target**: Momentum-based; hold until signs of exhaustion",
        "7. **Smart money filter**: Only trade when retail trapped on opposite side",
        "8. **Risk**: Fixed daily loss limit; cut losses quickly; let winners run",
    ]
    return "\n".join(lines)


async def main(transcript_file: Path, filter_type: str | None, out_path: Path | None) -> None:
    data = json.loads(transcript_file.read_text(encoding="utf-8"))
    videos = data["videos"]
    if filter_type:
        videos = [v for v in videos if v.get("video_type") == filter_type]
    videos = [v for v in videos if v.get("transcript")]

    print(f"Extracting features from {len(videos)} videos with transcripts...")
    llm = create_llm_client()

    # Process in batches of 5 to avoid rate limits
    extractions = []
    for i in range(0, len(videos), 5):
        batch = videos[i:i + 5]
        batch_results = await asyncio.gather(*[extract_features(llm, v) for v in batch])
        for r in batch_results:
            status = "SKIP" if r.get("skipped") else "OK"
            print(f"  [{status}] {r['title'][:55]}")
        extractions.extend(batch_results)
        if i + 5 < len(videos):
            await asyncio.sleep(2)  # respect Gemini rate limit

    ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    extractions_path = DATA_DIR / f"extractions_{ts}.json"
    extractions_path.write_text(
        json.dumps({"extractions": extractions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved extractions → {extractions_path}")

    agg = aggregate(extractions)
    hypothesis = build_hypothesis(agg)

    hyp_path = out_path or DATA_DIR / f"hypothesis_{ts}.md"
    hyp_path.write_text(hypothesis, encoding="utf-8")
    print(f"Saved hypothesis → {hyp_path}")
    print("\n" + "=" * 60)
    print(hypothesis)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("transcript_file", type=Path, help="JSON file from fetch_transcripts.py")
    p.add_argument("--type", choices=["live_trading", "analysis"], help="Filter to video type")
    p.add_argument("--out", type=Path, help="Custom output path for hypothesis .md")
    args = p.parse_args()

    if not args.transcript_file.exists():
        sys.exit(f"File not found: {args.transcript_file}")

    asyncio.run(main(args.transcript_file, args.type, args.out))
