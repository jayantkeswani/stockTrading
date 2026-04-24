"""Feature-importance analysis: which indicator features separate Arjun's
winning signals from his losers?

Joins the output of `analyze_setups.py` (features at entry minute) with the
output of `verify_signals.py` (simulated trade outcome) on msg_id, then for
each feature computes the win rate inside vs outside each bucket. Features
whose lift is both large (>= 5 percentage points) and well-populated
(N >= 30 per bucket) become candidate filter rules.

Analysis is done per-direction (CE and PE) because features like `above_vwap`
are bullish for CE but bearish for PE — pooling them would cancel out.

Also runs a combined-filter pass: what happens if we require the top 3
positive-lift filters all fire? That gives us an upper bound on how much
edge we could extract by being pickier than Arjun himself.

Usage:
    python scripts/telegram/analyze_edge.py \\
        --features scripts/telegram/data/setups_*.json \\
        --trades   scripts/telegram/data/verification_*.json
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Callable, Iterable


# -----------------------------------------------------------------------------
# Data loading + join.
# -----------------------------------------------------------------------------

def _latest(pattern: str) -> Path:
    matches = glob.glob(pattern)
    if not matches:
        sys.exit(f"no files matched: {pattern}")
    return Path(max(matches))  # lexicographic max == newest timestamp in our naming


def load_joined(features_path: Path, trades_path: Path) -> list[dict]:
    fjson = json.loads(features_path.read_text())
    tjson = json.loads(trades_path.read_text())
    features = fjson.get("features") or fjson  # handle both wrapper + raw
    trades = tjson.get("trades") or tjson

    by_id = {t["msg_id"]: t for t in trades if t.get("mode") in ("accurate", "delta")}
    joined = []
    for f in features:
        t = by_id.get(f["msg_id"])
        if t is None:
            continue
        row = dict(f)
        row["_outcome"] = t["outcome"]
        row["_pnl_per_lot"] = t["pnl_per_lot"]
        row["_pnl_pct"] = t["pnl_pct"]
        row["_mode"] = t["mode"]
        # Three win definitions — any feature's lift should be stable across them;
        # if not, we flag that in the report.
        row["_win_any"]    = t["pnl_per_lot"] > 0
        row["_win_clean"]  = t["outcome"] == "WIN_T2"
        row["_win_or_t1"]  = t["outcome"] in ("WIN_T2", "PARTIAL_T1_BE", "TIMEOUT_POST_T1")
        joined.append(row)
    return joined


# -----------------------------------------------------------------------------
# Bucketing.
# -----------------------------------------------------------------------------

@dataclass
class Bucket:
    name: str
    n: int
    wins: int
    wr: float         # win rate %
    lift: float       # WR - baseline WR
    avg_pnl: float    # avg pnl_per_lot in this bucket

    def as_row(self, baseline: float) -> str:
        marker = "⬆" if self.lift > 5 else ("⬇" if self.lift < -5 else " ")
        return (f"    {self.name:<28} n={self.n:>4}  WR={self.wr:>5.1f}%  "
                f"lift={self.lift:+5.1f}pp {marker}  avg_pnl={self.avg_pnl:>+7.1f}")


def _bucket_stats(rows: list[dict], label: str, win_key: str, baseline: float) -> Bucket:
    if not rows:
        return Bucket(label, 0, 0, 0.0, 0.0, 0.0)
    n = len(rows)
    wins = sum(1 for r in rows if r[win_key])
    wr = wins / n * 100
    avg_pnl = mean(r["_pnl_per_lot"] for r in rows)
    return Bucket(label, n, wins, wr, wr - baseline, avg_pnl)


def analyze_bool(rows: list[dict], key: str, win_key: str, baseline: float) -> list[Bucket]:
    t = [r for r in rows if r[key]]
    f = [r for r in rows if not r[key]]
    return [
        _bucket_stats(t, f"{key}=True", win_key, baseline),
        _bucket_stats(f, f"{key}=False", win_key, baseline),
    ]


def analyze_categorical(rows: list[dict], key: str, win_key: str, baseline: float) -> list[Bucket]:
    vals = Counter(r[key] for r in rows)
    out = []
    for v, _ in vals.most_common():
        subset = [r for r in rows if r[key] == v]
        out.append(_bucket_stats(subset, f"{key}={v}", win_key, baseline))
    return out


def analyze_numeric(rows: list[dict], key: str, win_key: str, baseline: float, bins: int = 4) -> list[Bucket]:
    vals = sorted(r[key] for r in rows if r[key] is not None)
    if len(vals) < bins * 10:
        return []
    # Quantile edges
    edges = [vals[int(len(vals) * i / bins)] for i in range(1, bins)]
    labels = []
    buckets_rows: list[list[dict]] = [[] for _ in range(bins)]
    for r in rows:
        v = r[key]
        if v is None:
            continue
        idx = bins - 1
        for i, e in enumerate(edges):
            if v < e:
                idx = i
                break
        buckets_rows[idx].append(r)
    # Human-readable ranges
    ranges = []
    for i in range(bins):
        lo = f"{edges[i - 1]:.2f}" if i > 0 else "min"
        hi = f"{edges[i]:.2f}" if i < bins - 1 else "max"
        ranges.append(f"{lo}..{hi}")
    return [_bucket_stats(sub, f"{key} [{rng}]", win_key, baseline)
            for sub, rng in zip(buckets_rows, ranges)]


# -----------------------------------------------------------------------------
# Scoring + reporting.
# -----------------------------------------------------------------------------

FEATURE_SPECS: list[tuple[str, str]] = [
    # (key, type) — type in {"bool", "cat", "num"}
    ("above_vwap",     "bool"),
    ("near_vwap",      "bool"),
    ("above_pdh",      "bool"),
    ("below_pdl",      "bool"),
    ("bull_reversal",  "bool"),
    ("bear_reversal",  "bool"),
    ("bull_engulf",    "bool"),
    ("bear_engulf",    "bool"),
    ("bull_pin",       "bool"),
    ("bear_pin",       "bool"),
    ("doji",           "bool"),
    ("tod_bucket",     "cat"),
    ("cpr_position",   "cat"),
    ("cpr_type",       "cat"),
    ("prev_day_bias",  "cat"),
    ("vwap_dist_pct",  "num"),
    ("vwap_slope_15m_pct", "num"),
    ("pdh_dist_pct",   "num"),
    ("pdc_dist_pct",   "num"),
    ("gap_pct",        "num"),
    ("vol_ratio",      "num"),
    ("range_pos",      "num"),
    ("momentum_15m_pct", "num"),
    ("intraday_move_pct", "num"),
    ("moneyness_pct",  "num"),
    ("entry_price",    "num"),
]


def _analyze_feature(rows: list[dict], key: str, kind: str, win_key: str, baseline: float) -> list[Bucket]:
    if kind == "bool":
        return analyze_bool(rows, key, win_key, baseline)
    if kind == "cat":
        return analyze_categorical(rows, key, win_key, baseline)
    if kind == "num":
        return analyze_numeric(rows, key, win_key, baseline)
    return []


def max_abs_lift(buckets: list[Bucket], min_n: int = 30) -> float:
    qualifying = [b for b in buckets if b.n >= min_n]
    if not qualifying:
        return 0.0
    return max(abs(b.lift) for b in qualifying)


def best_positive_bucket(buckets: list[Bucket], min_n: int = 30) -> Bucket | None:
    q = [b for b in buckets if b.n >= min_n and b.lift > 0]
    return max(q, key=lambda b: b.lift) if q else None


def print_direction_report(label: str, rows: list[dict], win_key: str) -> list[tuple[str, list[Bucket]]]:
    n = len(rows)
    if n == 0:
        return []
    baseline = sum(1 for r in rows if r[win_key]) / n * 100
    total_pnl = sum(r["_pnl_per_lot"] for r in rows)
    print()
    print("=" * 78)
    print(f"{label}  —  N={n}   baseline WR (`{win_key}`)={baseline:.1f}%   total pnl/lot={total_pnl:,.0f}")
    print("=" * 78)

    per_feature: list[tuple[str, list[Bucket]]] = []
    for key, kind in FEATURE_SPECS:
        try:
            buckets = _analyze_feature(rows, key, kind, win_key, baseline)
        except Exception as exc:
            print(f"  {key}: ERROR {exc}")
            continue
        per_feature.append((key, buckets))

    # Rank features by max absolute lift among well-populated buckets.
    per_feature.sort(key=lambda kv: max_abs_lift(kv[1]), reverse=True)

    print(f"\n  (bold = |lift| >= 5pp AND bucket n >= 30 — the only combos worth trusting)\n")
    for key, buckets in per_feature:
        if not buckets or max_abs_lift(buckets) < 3:
            continue
        print(f"  [{key}]")
        for b in buckets:
            print(b.as_row(baseline))
    return per_feature


def combined_filter_run(rows: list[dict], filters: list[Callable[[dict], bool]], label: str, win_key: str) -> None:
    kept = [r for r in rows if all(f(r) for f in filters)] if filters else list(rows)
    if not kept:
        print(f"\n  {label}: 0 signals match")
        return
    base_wr = sum(1 for r in rows if r[win_key]) / len(rows) * 100
    kept_wr = sum(1 for r in kept if r[win_key]) / len(kept) * 100
    total_pnl = sum(r["_pnl_per_lot"] for r in kept)
    print(f"\n  {label}")
    print(f"    kept  {len(kept):>4} / {len(rows)} signals  ({len(kept) / len(rows) * 100:.0f}%)")
    print(f"    WR    {kept_wr:.1f}%  (baseline {base_wr:.1f}%, lift {kept_wr - base_wr:+.1f}pp)")
    print(f"    total pnl/lot: {total_pnl:+,.0f}  (avg {mean(r['_pnl_per_lot'] for r in kept):+.1f})")


def propose_filters(ce_rows: list[dict], pe_rows: list[dict],
                    ce_features: list[tuple[str, list[Bucket]]],
                    pe_features: list[tuple[str, list[Bucket]]],
                    win_key: str) -> None:
    print()
    print("=" * 78)
    print("COMBINED FILTER STRESS TEST")
    print("=" * 78)
    print("  Picks the top 3 features per side whose best bucket has positive lift,")
    print("  then checks what happens when we require ALL of them.\n")

    def build_filters(feats: list[tuple[str, list[Bucket]]]) -> list[tuple[str, Callable[[dict], bool]]]:
        out: list[tuple[str, Callable[[dict], bool]]] = []
        for key, buckets in feats:
            b = best_positive_bucket(buckets)
            if b is None or b.lift < 3:
                continue
            # Translate bucket name back into a predicate.
            name = b.name
            if name.endswith("=True"):
                out.append((name, lambda r, k=key: bool(r[k])))
            elif name.endswith("=False"):
                out.append((name, lambda r, k=key: not r[k]))
            elif "=" in name and "[" not in name:
                _, v = name.split("=", 1)
                out.append((name, lambda r, k=key, v=v: str(r[k]) == v))
            elif "[" in name:
                # numeric bucket: "key [lo..hi]"
                rng = name[name.index("[") + 1: name.index("]")]
                lo_s, hi_s = rng.split("..")
                lo = None if lo_s == "min" else float(lo_s)
                hi = None if hi_s == "max" else float(hi_s)
                def _pred(r, k=key, lo=lo, hi=hi):
                    v = r[k]
                    if v is None:
                        return False
                    if lo is not None and v < lo:
                        return False
                    if hi is not None and v >= hi:
                        return False
                    return True
                out.append((name, _pred))
            if len(out) >= 3:
                break
        return out

    for label, rows, feats in (("CE side", ce_rows, ce_features), ("PE side", pe_rows, pe_features)):
        print(f"-- {label} --")
        filters = build_filters(feats)
        if not filters:
            print("  no qualifying filters")
            continue
        print("  filters chosen:")
        for name, _ in filters:
            print(f"    • {name}")
        combined_filter_run(rows, [f for _, f in filters], "COMBINED (all filters)", win_key)
        # Also show each filter alone.
        for name, f in filters:
            combined_filter_run(rows, [f], f"ONLY: {name}", win_key)


# -----------------------------------------------------------------------------
# CLI.
# -----------------------------------------------------------------------------

def main(argv: Iterable[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--features", help="features JSON (glob ok; newest match used)")
    p.add_argument("--trades",   help="verification JSON (glob ok; newest match used)")
    p.add_argument("--win-def", choices=("win_any", "win_clean", "win_or_t1"), default="win_any",
                   help="win definition: win_any (pnl>0, default), win_clean (WIN_T2 only), "
                        "win_or_t1 (any T1 touch incl. timeout)")
    args = p.parse_args(list(argv))

    features_path = _latest(args.features or str(Path(__file__).resolve().parent / "data" / "setups_*.json"))
    trades_path = _latest(args.trades or str(Path(__file__).resolve().parent / "data" / "verification_*.json"))
    print(f"# features: {features_path}")
    print(f"# trades:   {trades_path}")
    print(f"# win def:  _{args.win_def}")

    rows = load_joined(features_path, trades_path)
    if not rows:
        sys.exit("join produced 0 rows — msg_ids don't match")
    print(f"# joined:   {len(rows)} rows\n")

    win_key = f"_{args.win_def}"
    ce = [r for r in rows if r["opt_type"] == "CE"]
    pe = [r for r in rows if r["opt_type"] == "PE"]

    ce_feats = print_direction_report("CE ENTRIES", ce, win_key)
    pe_feats = print_direction_report("PE ENTRIES", pe, win_key)
    propose_filters(ce, pe, ce_feats, pe_feats, win_key)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
