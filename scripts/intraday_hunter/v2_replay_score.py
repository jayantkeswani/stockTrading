"""IH v2 prompt replay, step 3 of 3 — score every decision with the grade's counterfactual basket.

    cd backend && python ../scripts/intraday_hunter/v2_replay_score.py --export export.json --results results.json

Every arm — live v2, base, cand, and the teacher/plan arms from the stored grade — is priced the
same way: `grading.simulate_basket` (v2's 1-lot leg structure, basket ±T, round hold, 11:30 time
exit) on the captured option closes. Two data-coverage fixes apply to all arms alike:
a minute with no option candle carries the last close forward, and a wanted strike with no price
at the entry minute (the index moved off the morning ATM±2 capture) is replaced by the nearest
captured strike that has one — every substitution is printed.
"""
import argparse
import collections
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.getcwd())

from app.core.constants import LOT_SIZES, STRIKE_GAPS  # noqa: E402
from app.services.intraday_hunter_v2.grading import _add_min, simulate_basket  # noqa: E402


def ffill(series: dict[str, float]) -> dict[str, float]:
    """Carry the last close through minutes with no candle, 09:15–15:29. Pure."""
    out, last = {}, None
    for h in range(9, 16):
        for m in range(60):
            k = f"{h:02d}:{m:02d}"
            if not "09:15" <= k <= "15:29":
                continue
            last = series.get(k, last)
            if last is not None:
                out[k] = last
    return out


def legs_nearest(side, spots, contracts, prem, structure, entry_m, subs, tag):
    """grading.basket_legs_for with nearest-priced-strike substitution (recorded in `subs`). Pure."""
    legs = []
    for idx, depths in structure.items():
        spot, gap = spots.get(idx), STRIKE_GAPS.get(idx)
        if not spot or not gap:
            continue
        atm = round(spot / gap) * gap
        priced = sorted((float(r["strike"]), r["symbol"]) for r in contracts.get(idx, [])
                        if r["type"] == side and prem.get(r["symbol"], {}).get(entry_m))
        for depth in depths:
            want = atm if depth == 0 else (atm - depth * gap if side == "CE" else atm + depth * gap)
            k_used, hit = want, next((s for k, s in priced if k == want), None)
            if hit is None and priced:
                k_used, hit = min(priced, key=lambda ks: (abs(ks[0] - want), ks[0] if side == "PE" else -ks[0]))
                subs.add((tag, side, entry_m, idx, want, k_used))
            if hit:
                legs.append({"index": idx, "qty": LOT_SIZES.get(idx, 1), "symbol": hit,
                             "strike": k_used, "closes": prem[hit]})
    return legs


def main(a) -> None:
    x = json.load(open(a.export))
    results = json.load(open(a.results))
    params = x["_params"]
    structure = params.get("leg_structure") or {}
    subs: set = set()
    for d, v in x.items():
        if not d.startswith("_"):
            v["premium_closes"] = {s: ffill(c) for s, c in v["premium_closes"].items()}

    def price(day, side, at):
        if side not in ("CE", "PE") or not at:
            return None
        v = x[day]
        entry_m = _add_min(at, -1)
        spots = {i: v["index_closes"][i].get(entry_m) for i in v["index_closes"]}
        legs = legs_nearest(side, spots, v["contracts"], v["premium_closes"], structure, entry_m, subs, day)
        return simulate_basket(at, side, legs, v["index_closes"], params, date.fromisoformat(day)) if legs else None

    totals = collections.defaultdict(float)
    for d in sorted(k for k in x if not k.startswith("_")):
        lv = x[d]["live_v2"] or {}
        last = (lv.get("history") or [{}])[-1]
        live = price(d, lv.get("direction"), last.get("_at")) if lv.get("decision") == "ENTER" else None
        ga = x[d].get("grade_arms") or {}
        t = ga.get("teacher") or {}
        print(f"\n{d}  live v2: {lv.get('decision')} {lv.get('direction') or ''} @{last.get('_at')} "
              f"pnl={(live or {}).get('pnl')}  teacher: {t.get('side')} @{t.get('decision_at')} "
              f"pnl={(t.get('cf') or {}).get('pnl')}")
        totals["live_v2"] += (live or {}).get("pnl") or 0
        for r in sorted((r for r in results if r["day"] == d), key=lambda r: (r["variant"], r["seed"])):
            h = r["history"][-1] if r["history"] else {}
            cf = price(d, h.get("direction"), h.get("_at")) if h.get("decision") == "ENTER" else None
            pnl = (cf or {}).get("pnl") or 0.0
            totals[f"{r['variant']} s{r['seed']}"] += pnl
            print(f"   {r['variant']} s{r['seed']}: {h.get('decision')} {h.get('direction') or ''} @{h.get('_at')} "
                  f"pnl={pnl:+,.0f} exit={(cf or {}).get('exit_reason')} {(cf or {}).get('exit_time') or ''}")
    print("\nSubstitutions (day, side, entry candle, index, wanted, used):")
    for s in sorted(subs):
        print("  ", s)
    print("\nTotals:", {k: round(v) for k, v in sorted(totals.items())})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True)
    ap.add_argument("--results", required=True)
    main(ap.parse_args())
