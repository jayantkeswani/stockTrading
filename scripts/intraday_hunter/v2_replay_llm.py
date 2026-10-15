"""IH v2 prompt replay, step 2 of 3 — run Call 2 minute by minute for two prompt sets.

Runs on the Mac (the `claude` CLI's own login, IH_ALLOW_CLI_LOGIN=1) against the step-1 export:

    cd backend && python ../scripts/intraday_hunter/v2_replay_llm.py \
        --export export.json --base-prompts base_prompts.py --cand-prompts app/services/intraday_hunter_v2/prompts.py \
        --seeds 2 --out results.json

Variant "base" uses the export's base inputs + `--base-prompts` + the prod deadline; variant "cand"
uses the cand inputs (falls back to base when the export has none) + `--cand-prompts` + the
export's `_cand_deadline`. Each (day, variant, seed) is one sequential Call 2 loop that carries
its own earlier checks, normalizes every reply with `decision.normalize_call2`, and stops at
ENTER/SKIP. The model is the prod `call2_model`. Get the base prompts with
`git show <tag>:backend/app/services/intraday_hunter_v2/prompts.py > base_prompts.py`.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.getcwd())
os.environ["IH_ALLOW_CLI_LOGIN"] = "1"

from app.services.intraday_hunter import llm_cli  # noqa: E402
from app.services.intraday_hunter_v2.decision import normalize_call2  # noqa: E402

SLIM = ("_at", "decision", "direction", "pool_broken", "next_pool_target", "confidence",
        "skip_reason_code", "waiting_for", "entry_type")


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def run_seq(x: dict, day: str, variant: str, seed: int, prompts, sem, model: str) -> dict:
    deadline = x["_cand_deadline"] if variant == "cand" else x["_params"]["call2_deadline"]
    mins = x[day].get(variant) or x[day]["base"]
    c1 = x[day]["call1"]
    call1 = c1 if c1 and not c1.get("error") else None
    hist: list[dict] = []
    for m in sorted(mins):
        if m > deadline:
            break
        live = {**mins[m], "deadline": deadline}
        prompt = prompts.SYSTEM_PROMPT_V2 + "\n\n" + prompts.build_call2_prompt(
            call1, live, [{k: h.get(k) for k in SLIM} for h in hist])
        async with sem:
            raw = await llm_cli.call_claude_json(prompt, required_keys=("decision", "confidence"),
                                                 token=None, model=model, timeout_s=120)
        out = normalize_call2(raw, at_deadline=m >= deadline)
        out["_at"] = m
        hist.append(out)
        print(f"{day} {variant} s{seed} {m} {out['decision']} {out.get('direction') or ''}", flush=True)
        if out["decision"] in ("ENTER", "SKIP"):
            break
    return {"day": day, "variant": variant, "seed": seed, "history": hist}


async def main(a) -> None:
    x = json.load(open(a.export))
    sets = {"base": _load("base_prompts", a.base_prompts), "cand": _load("cand_prompts", a.cand_prompts)}
    model = x["_params"].get("call2_model") or "claude-sonnet-5-5"
    sem = asyncio.Semaphore(a.concurrency)
    days = [d for d in x if not d.startswith("_")]
    res = await asyncio.gather(*[run_seq(x, d, v, s, sets[v], sem, model)
                                 for d in days for v in sets for s in range(1, a.seeds + 1)])
    json.dump(res, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", required=True)
    ap.add_argument("--base-prompts", required=True)
    ap.add_argument("--cand-prompts", required=True)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default="results.json")
    asyncio.run(main(ap.parse_args()))
