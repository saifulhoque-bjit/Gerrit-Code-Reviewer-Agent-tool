"""Strategy B worker: review one file via a direct OpenAI-compatible API call.

Unlike Strategy A (hermes subprocess + MCP tools), this talks straight to a
/chat/completions endpoint. Trade-off: no MCP tool access (can't cross-read the
repo), but it returns REAL token counts from the provider's usage block instead
of v4's char/N estimate — so cost accounting is exact. Returns
(comments, TokenUsage) so the engine prices real usage.
"""
from __future__ import annotations

import httpx

from .models import ReviewComment, TokenUsage
from .parser import parse_comments

PROMPT_TEMPLATE = """You are a senior code reviewer. Review this single file change.

File: {file_path}
Project: {project_slug}
Target branch: {branch}

DIFF:
{diff}

INSTRUCTIONS:
1. Analyze the diff for real bugs, security issues, or logic errors.
2. Do NOT flag style, naming, or cosmetic issues.
3. If no real issues found, return [].

OUTPUT (strict JSON array only, no prose):
[{{"file":"{file_path}","line":N,"severity":"error|warning|suggestion","comment":"...","existing_code":"...","suggestion_code":"..."}}]
"""


async def review_file_direct(
    file_path: str,
    diff: str,
    rules_files: list[str],
    project_slug: str,
    diff_cap: int = 8000,
    timeout: int = 1800,
    *,
    branch: str = "",
    base_url: str,
    model: str,
    api_key: str,
) -> tuple[list[ReviewComment], TokenUsage]:
    """Review one file via direct API. Returns (comments, real-token usage).

    ponytail: no key → no work, no fabricated tokens; the engine treats an
    empty comment list as "clean" and prices the zero usage as $0.
    """
    if not api_key:
        return [], TokenUsage()

    prompt = PROMPT_TEMPLATE.format(
        file_path=file_path,
        project_slug=project_slug or "unknown",
        branch=branch or "unknown",
        diff=diff[:diff_cap],
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(
            timeout=min(timeout, 300),
            headers={"Authorization": f"Bearer {api_key}"},
        ) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return [], TokenUsage()  # network/provider failure → no comments, no cost

    content = ""
    choices = data.get("choices") or []
    if choices:
        content = (choices[0].get("message") or {}).get("content", "") or ""
    comments = parse_comments(content)

    usage_block = data.get("usage") or {}
    usage = TokenUsage(
        input_tokens=int(usage_block.get("prompt_tokens", 0)),
        output_tokens=int(usage_block.get("completion_tokens", 0)),
    )
    return comments, usage
