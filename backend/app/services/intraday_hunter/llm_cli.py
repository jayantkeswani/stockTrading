"""Claude CLI wrapper for the Intraday Hunter agent — inline-image / single-turn path.

Calls Claude through the `claude` CLI on a subscription OAuth token (draws from the
Pro/Max plan, not API credits). Charts are passed as **inline base64 images** in a
stream-json user message, so the whole call is a SINGLE turn (no Read-tool round trips)
— this is what keeps latency to seconds even on Opus with several charts attached.

The working invocation (confirmed):
    cat msg.jsonl | env -u ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN=$TOK \
      claude -p --input-format stream-json --output-format stream-json --verbose \
             --model <model> --allowed-tools ""
Constraints: --input-format stream-json forces --output-format stream-json; the message
MUST be piped to stdin (file-redirect hits immediate EOF); image blocks use the standard
API shape (base64 / media_type / data). The model's answer is in the `result` event.

Any failure returns None — callers treat None as a SKIP (a malformed reply never
produces a bogus trade plan).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"
DEFAULT_TIMEOUT_S = 300


def _resolve_token() -> str | None:
    """OAuth token from the environment (prod: container env var / GH Actions secret)."""
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or None


def _cli_login_allowed() -> bool:
    """Dev-only: IH_ALLOW_CLI_LOGIN=1 lets a call with no OAuth token use the `claude` CLI's own
    local login (a developer Mac). Never set in prod — there the token is required as before."""
    return os.environ.get("IH_ALLOW_CLI_LOGIN") == "1"


def _child_env(token: str | None) -> dict:
    """Force the OAuth token; drop the API key so it can't override subscription auth.

    `token=None` (only reachable with IH_ALLOW_CLI_LOGIN=1) keeps the CLI's own login.
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def _build_message(prompt: str, image_paths: list[str]) -> str:
    """One stream-json user message: the text prompt + inline base64 chart images."""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for p in image_paths:
        try:
            with open(p, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("ascii")
        except OSError:
            logger.warning("intraday_hunter: chart image missing, skipping: %s", p)
            continue
        content.append(
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png", "data": b64}}
        )
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"


def _extract_json_object(text: str) -> dict | None:
    """Parse the first JSON object out of model text, tolerating fences/prose."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        parts = s.split("```")
        s = parts[1] if len(parts) >= 2 else s
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    s = s.strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _parse_stream_result(stdout: str) -> tuple[str | None, bool, dict]:
    """From stream-json stdout, return (result_text, is_error, meta{cost,turns,duration})."""
    result_text: str | None = None
    is_error = False
    meta: dict = {}
    assistant_text: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        if etype == "assistant":
            for block in evt.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    assistant_text.append(block["text"])
        elif etype == "result":
            is_error = bool(evt.get("is_error"))
            result_text = evt.get("result")
            meta = {
                "cost_usd": evt.get("total_cost_usd", 0),
                "num_turns": evt.get("num_turns"),
                "duration_ms": evt.get("duration_ms"),
            }
    if result_text is None and assistant_text:
        result_text = "".join(assistant_text)
    return result_text, is_error, meta


async def call_claude_json(
    prompt: str,
    *,
    image_paths: list[str] | None = None,
    required_keys: tuple[str, ...] = (),
    token: str | None = None,
    model: str = MODEL,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict | None:
    """Run one single-turn `claude` call with inline charts; return parsed JSON or None.

    `required_keys` asserts the reply's shape; a reply missing any is treated as a
    failure (-> None -> SKIP).
    """
    tok = token or _resolve_token()
    if not tok and not _cli_login_allowed():
        logger.error("intraday_hunter: no CLAUDE_CODE_OAUTH_TOKEN; cannot call Claude")
        return None
    claude_bin = shutil.which("claude")
    if not claude_bin:
        logger.error("intraday_hunter: `claude` CLI not found on PATH")
        return None

    message = _build_message(prompt, image_paths or [])
    cmd = [
        claude_bin, "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--allowed-tools", "",
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_child_env(tok),
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=message.encode()), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        logger.error("intraday_hunter: claude CLI timed out after %ss", timeout_s)
        if proc is not None and proc.returncode is None:  # never leave an orphan claude process
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        return None
    except Exception:  # noqa: BLE001 — never let an LLM call crash the loop
        logger.exception("intraday_hunter: claude CLI invocation failed")
        return None

    if proc.returncode != 0:
        logger.error("intraday_hunter: claude CLI exit %s: %s",
                     proc.returncode, stderr_b.decode(errors="replace").strip()[:500])
        return None

    result_text, is_error, meta = _parse_stream_result(stdout_b.decode(errors="replace"))
    if is_error:
        logger.error("intraday_hunter: claude reported error: %s", str(result_text)[:300])
        return None

    parsed = _extract_json_object(str(result_text or ""))
    if parsed is None:
        logger.error("intraday_hunter: could not parse model JSON from result")
        return None

    missing = [k for k in required_keys if k not in parsed]
    if missing:
        logger.error("intraday_hunter: model JSON missing keys %s", missing)
        return None

    logger.debug("intraday_hunter: claude ok model=%s turns=%s cost=$%.4f dur=%sms",
                 model, meta.get("num_turns"), meta.get("cost_usd", 0), meta.get("duration_ms"))
    return parsed
