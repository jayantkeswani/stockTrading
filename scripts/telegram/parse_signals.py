"""Parse messages from a fetched Telegram JSON into structured signal events.

Signal channels typically use a small, stable set of message templates. This
parser classifies each message and extracts numeric fields where possible.

Event kinds:
  ENTRY            - "🚨 New Options Trade" + BUY line + Target(s) + SL
  EXIT_FULL        - "Book Profit in <...> at price ₹X" (profitable full close)
  EXIT_FORCED      - "🚨 Exit <...> at the current price ₹X" or "at cost to cost ₹X"
                     (neutral/SL exit — distinct from Book Profit to avoid win-rate inflation)
  EXIT_PARTIAL     - "🎯 Target N Hit" / "🎯 Book Partial Profit" + partial exit
  WATCHLIST        - "ADD TO WATCHLIST"
  HOLD_OVERNIGHT   - "Hold for next trading day" (positional carry)
  UPDATE           - "📌 Trade Update" + free-text amendment
  REPORT           - pre-market / post-market recap (ignored for stats)
  CANCEL           - "cancel" / "close the trade" (heuristic)
  OTHER            - anything we couldn't classify

Usage:
    python scripts/telegram/parse_signals.py scripts/telegram/data/-1002127259353_*.json
    python scripts/telegram/parse_signals.py <path> --json           # emit structured JSON
    python scripts/telegram/parse_signals.py <path> --unmatched      # show messages we couldn't parse
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass
class Event:
    msg_id: int
    ts_ist: str
    kind: str
    symbol: str | None = None
    expiry_day: int | None = None
    expiry_month: str | None = None
    strike: float | None = None
    option_type: str | None = None  # CE | PE
    entry_price: float | None = None
    entry_cond: str | None = None  # "at" | "above" | "below"
    target1: float | None = None
    target2: float | None = None
    stoploss: float | None = None
    exit_price: float | None = None
    notes: list[str] = field(default_factory=list)
    raw: str = ""


# --- regex patterns -----------------------------------------------------------
# Symbols can contain letters, digits, ampersands, hyphens (e.g. M&M, BAJAJ-AUTO).
_SYM = r"[A-Z][A-Z0-9&\-]+"
_MONTH = r"JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
_PRICE = r"[\d]+(?:\.\d+)?"

# "BUY INDIANB 24 FEB 920 CE at ₹19.5"
# "BUY ITC 24 FEB 330 CE above ₹3.85"
_RE_ENTRY_LINE = re.compile(
    rf"\bBUY\s+({_SYM})\s+(\d{{1,2}})\s+({_MONTH})\s+({_PRICE})\s+(CE|PE)\s+(at|above|below)\s*₹?\s*({_PRICE})",
    re.IGNORECASE,
)
# "BUY NIFTY 24500 CE at ₹180"  (index-style, no day/month before strike)
_RE_ENTRY_LINE_NODATE = re.compile(
    rf"\bBUY\s+({_SYM})\s+({_PRICE})\s+(CE|PE)\s+(at|above|below)\s*₹?\s*({_PRICE})",
    re.IGNORECASE,
)
_RE_TARGET1 = re.compile(rf"(?:🎯\s*)?Target\s*1\s*[:\-]?\s*₹?\s*({_PRICE})", re.IGNORECASE)
_RE_TARGET2 = re.compile(rf"(?:🎯\s*)?Target\s*2\s*[:\-]?\s*₹?\s*({_PRICE})", re.IGNORECASE)
# Fallback for single-target entries: "🎯 Target: ₹60" (no number after "Target")
_RE_TARGET_ONLY = re.compile(rf"(?:🎯\s*)?Target\s*[:\-]\s*₹?\s*({_PRICE})", re.IGNORECASE)
_RE_SL = re.compile(rf"(?:🔴\s*)?Stop\s*loss\s*[:\-]?\s*₹?\s*({_PRICE})", re.IGNORECASE)

# "Book Profit in INDIANB 24 FEB 920 CE at price ₹29"
_RE_BOOK_PROFIT = re.compile(
    rf"Book\s+Profit\s+in\s+({_SYM})\s+(\d{{1,2}})\s+({_MONTH})\s+({_PRICE})\s+(CE|PE)\s+at\s+price\s+₹?\s*({_PRICE})",
    re.IGNORECASE,
)
# Forced/neutral exits — distinct from "Book Profit" so we don't count these as wins:
#   "Exit DELHIVERY 24 FEB 430 CE at cost to cost ₹10.8"
#   "Exit BEL 24 FEB 450 CE at the current price ₹4.9"
#   "Please exit FEDERALBNK 30 MAR 295 CE at the current price ₹10.3"
_RE_EXIT_FORCED = re.compile(
    rf"(?:Please\s+)?Exit\s+(?:from\s+)?({_SYM})\s+(\d{{1,2}})\s+({_MONTH})\s+({_PRICE})\s+(CE|PE)\s+at\s+(?:the\s+current\s+price|cost\s+to\s+cost)\s*:?\s*₹?\s*({_PRICE})",
    re.IGNORECASE,
)
# Partial book — two known layouts:
#   "Book partial profit for INDIANB 24 FEB 920 CE at the current price: ₹23.8"
#   "Book partial profit in OIL 30 MAR 500 CE at ₹20."
_RE_PARTIAL_HIT = re.compile(
    rf"Book\s+partial\s+profit\s+(?:for|in)\s+({_SYM})\s+(\d{{1,2}})\s+({_MONTH})\s+({_PRICE})\s+(CE|PE)\s+(?:at\s+(?:the\s+current\s+price)?)?\s*:?\s*₹\s*({_PRICE})",
    re.IGNORECASE,
)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def classify(text: str) -> str:
    t = text.strip()
    tl = t.lower()
    if not t:
        return "EMPTY"
    # Watchlist check first — also handles the observed typo "ADD T0 WATCHLIST"
    if "add to watchlist" in tl or "add t0 watchlist" in tl:
        return "WATCHLIST"
    # Pre/post-market recaps — regular noise, ignore for signal stats
    if "post-market report" in tl or "pre-market update" in tl:
        return "REPORT"
    # Overnight carry notice: "<SYM> <STRIKE> <TYPE> (<MONTH>) ✅ Hold for next trading day."
    if "hold for next trading day" in tl:
        return "HOLD_OVERNIGHT"
    if "new options trade" in tl:
        return "ENTRY"
    # Book Partial Profit — check before EXIT_FULL because it also contains "book"
    if "book partial profit" in tl or "target 1 hit" in tl or "target 2 hit" in tl:
        return "EXIT_PARTIAL"
    # Book Profit (full profitable close)
    if "book profit in" in tl:
        return "EXIT_FULL"
    # Forced / neutral exits — "at the current price" or "at cost to cost"
    # (We don't want to lump these with Book Profit — separate kind so a verifier
    # can compare channel-claimed outcome to our independent wick-based simulation.)
    if _RE_EXIT_FORCED.search(t):
        return "EXIT_FORCED"
    if "trade update" in tl or t.startswith("📌"):
        return "UPDATE"
    if "stoploss hit" in tl or "sl hit" in tl:
        return "EXIT_FORCED"
    if "exit trade" in tl or "close the trade" in tl:
        return "CANCEL"
    # Heuristic: a free-standing BUY line without the 🚨 marker is still an entry.
    if _RE_ENTRY_LINE.search(t) or _RE_ENTRY_LINE_NODATE.search(t):
        return "ENTRY"
    return "OTHER"


def parse_entry(e: Event, text: str) -> None:
    m = _RE_ENTRY_LINE.search(text)
    if m:
        e.symbol = m.group(1).upper()
        e.expiry_day = int(m.group(2))
        e.expiry_month = m.group(3).upper()
        e.strike = _num(m.group(4))
        e.option_type = m.group(5).upper()
        e.entry_cond = m.group(6).lower()
        e.entry_price = _num(m.group(7))
    else:
        m = _RE_ENTRY_LINE_NODATE.search(text)
        if m:
            e.symbol = m.group(1).upper()
            e.strike = _num(m.group(2))
            e.option_type = m.group(3).upper()
            e.entry_cond = m.group(4).lower()
            e.entry_price = _num(m.group(5))
    if m := _RE_TARGET1.search(text):
        e.target1 = _num(m.group(1))
    if m := _RE_TARGET2.search(text):
        e.target2 = _num(m.group(1))
    # Fallback: single-target entries use bare "Target:" — treat as T1.
    if e.target1 is None:
        if m := _RE_TARGET_ONLY.search(text):
            e.target1 = _num(m.group(1))
    if m := _RE_SL.search(text):
        e.stoploss = _num(m.group(1))


def parse_exit_full(e: Event, text: str) -> None:
    if m := _RE_BOOK_PROFIT.search(text):
        e.symbol = m.group(1).upper()
        e.expiry_day = int(m.group(2))
        e.expiry_month = m.group(3).upper()
        e.strike = _num(m.group(4))
        e.option_type = m.group(5).upper()
        e.exit_price = _num(m.group(6))


def parse_exit_partial(e: Event, text: str) -> None:
    if m := _RE_PARTIAL_HIT.search(text):
        e.symbol = m.group(1).upper()
        e.expiry_day = int(m.group(2))
        e.expiry_month = m.group(3).upper()
        e.strike = _num(m.group(4))
        e.option_type = m.group(5).upper()
        e.exit_price = _num(m.group(6))


def parse_exit_forced(e: Event, text: str) -> None:
    if m := _RE_EXIT_FORCED.search(text):
        e.symbol = m.group(1).upper()
        e.expiry_day = int(m.group(2))
        e.expiry_month = m.group(3).upper()
        e.strike = _num(m.group(4))
        e.option_type = m.group(5).upper()
        e.exit_price = _num(m.group(6))
        if "cost to cost" in text.lower():
            e.notes.append("cost_to_cost")


def parse_message(msg: dict) -> Event:
    text = msg.get("text") or ""
    kind = classify(text)
    e = Event(msg_id=msg["id"], ts_ist=msg["date_ist"], kind=kind, raw=text)
    if kind == "ENTRY":
        parse_entry(e, text)
    elif kind == "EXIT_FULL":
        parse_exit_full(e, text)
    elif kind == "EXIT_FORCED":
        parse_exit_forced(e, text)
    elif kind == "EXIT_PARTIAL":
        parse_exit_partial(e, text)
    return e


def parse_file(path: Path) -> tuple[list[Event], dict]:
    data = json.loads(path.read_text())
    events = [parse_message(m) for m in data["messages"]]
    meta = {
        "chat_id": data.get("chat_id"),
        "chat_title": data.get("chat_title"),
        "message_count": data.get("message_count"),
        "fetched_at_ist": data.get("fetched_at_ist"),
    }
    return events, meta


def _is_complete_entry(e: Event) -> bool:
    return all(
        v is not None
        for v in (e.symbol, e.strike, e.option_type, e.entry_price, e.target1, e.stoploss)
    )


def print_summary(events: list[Event], meta: dict) -> None:
    print(f"# {meta.get('chat_title')} (id={meta.get('chat_id')})")
    print(f"# {meta.get('message_count')} messages fetched at {meta.get('fetched_at_ist')}")
    print()

    kinds = Counter(e.kind for e in events)
    print("## Message classification")
    for k, n in kinds.most_common():
        print(f"  {k:<14} {n:>5}")
    print()

    entries = [e for e in events if e.kind == "ENTRY"]
    complete = [e for e in entries if _is_complete_entry(e)]
    print(f"## Entries: {len(entries)} total, {len(complete)} fully parsed ({len(complete) / max(1, len(entries)) * 100:.1f}%)")
    if entries:
        dates = [datetime.fromisoformat(e.ts_ist).date() for e in entries]
        print(f"  date range: {min(dates)} → {max(dates)}  ({(max(dates) - min(dates)).days + 1} days)")
        unique_days = len({d for d in dates})
        print(f"  active days: {unique_days}  (avg {len(entries) / max(1, unique_days):.1f} entries/day)")
        syms = Counter(e.symbol for e in complete)
        print(f"  distinct symbols: {len(syms)}")
        print(f"  top 10: {', '.join(f'{s}({n})' for s, n in syms.most_common(10))}")
        otypes = Counter(e.option_type for e in complete)
        print(f"  CE/PE split: CE={otypes.get('CE', 0)}  PE={otypes.get('PE', 0)}")
        conds = Counter(e.entry_cond for e in complete)
        print(f"  entry cond: {dict(conds)}")

    exits_full = [e for e in events if e.kind == "EXIT_FULL"]
    exits_partial = [e for e in events if e.kind == "EXIT_PARTIAL"]
    print()
    print(f"## Exits: {len(exits_full)} full-book, {len(exits_partial)} partial-hit")

    # Incomplete entries — worth flagging because they could be index signals with different format
    incomplete = [e for e in entries if not _is_complete_entry(e)]
    if incomplete:
        print()
        print(f"## {len(incomplete)} entries missing one or more fields (symbol/strike/type/entry/T1/SL)")


def print_unmatched(events: list[Event]) -> None:
    others = [e for e in events if e.kind == "OTHER"]
    print(f"# {len(others)} unclassified messages\n")
    for e in others[:40]:
        snippet = e.raw.replace("\n", " ⏎ ")[:200]
        print(f"[{e.ts_ist[:16]}] id={e.msg_id}: {snippet}")


def main(argv: Iterable[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path, help="path to fetched JSON file")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true", help="emit parsed events as JSON to stdout")
    g.add_argument("--unmatched", action="store_true", help="print messages we couldn't classify")
    g.add_argument("--incomplete", action="store_true", help="print entries missing fields")
    args = p.parse_args(list(argv))

    if not args.path.exists():
        print(f"error: file not found: {args.path}", file=sys.stderr)
        return 2

    events, meta = parse_file(args.path)

    if args.json:
        json.dump([asdict(e) for e in events], sys.stdout, ensure_ascii=False, indent=2)
        return 0
    if args.unmatched:
        print_unmatched(events)
        return 0
    if args.incomplete:
        incomplete = [e for e in events if e.kind == "ENTRY" and not _is_complete_entry(e)]
        for e in incomplete:
            print(f"[{e.ts_ist[:16]}] id={e.msg_id}  sym={e.symbol} strike={e.strike} type={e.option_type} entry={e.entry_price} T1={e.target1} SL={e.stoploss}")
            print(f"    raw: {e.raw[:200]}")
        return 0

    print_summary(events, meta)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
