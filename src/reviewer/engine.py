"""Async review orchestrator.

Flow: fetch changed files + per-file diffs from Gerrit → resolve rule paths →
run a bounded pool of workers (one per file) → dedup → persist.

ponytail: the worker and gerrit client are injected, not imported hard. That
keeps the orchestration logic testable with fakes — no real hermes subprocess
or network needed to prove the fan-out/dedup/persist wiring.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from .diff import dedup_comments
from .models import ReviewComment, TokenUsage
from .rules import resolve_rule_paths
from .settings import Settings
from .store import finish_review, last_reviewed_patchset, start_review

# A worker returns comments, optionally paired with provider-reported token usage.
WorkerResult = list[ReviewComment] | tuple[list[ReviewComment], TokenUsage]
WorkerFn = Callable[..., Awaitable[WorkerResult]]


class GerritLike(Protocol):
    async def changed_files(self, change_id: str, base: int | None = None): ...
    async def file_diff(self, change_id: str, file_path: str) -> str: ...
    async def change_detail(self, change_id: str) -> dict: ...


def _estimate_cost(settings: Settings, in_chars: int, out_chars: int) -> TokenUsage:
    """ponytail: char/N heuristic priced against the CONFIGURED model, not
    hardcoded Claude rates (v3's bug). Real token counts land in Phase 5."""
    cpt = max(1, settings.cost.chars_per_token)
    in_tok = in_chars // cpt
    out_tok = out_chars // cpt
    cost = (
        in_tok / 1_000_000 * settings.cost.input_per_1m_usd
        + out_tok / 1_000_000 * settings.cost.output_per_1m_usd
    )
    return TokenUsage(input_tokens=in_tok, output_tokens=out_tok, cost_usd=round(cost, 6))


def _price_usage(settings: Settings, usage: TokenUsage) -> TokenUsage:
    usage.cost_usd = round(
        usage.input_tokens / 1_000_000 * settings.cost.input_per_1m_usd
        + usage.output_tokens / 1_000_000 * settings.cost.output_per_1m_usd,
        6,
    )
    return usage


def _write_mcp_config(settings: Settings, change_id: str, auth: str, project_dir: str) -> str:
    """Per-review config the MCP server reads (change id, auth, project dir)."""
    temp = Path("temp")
    temp.mkdir(exist_ok=True)
    path = temp / "mcp_runtime.json"
    path.write_text(
        json.dumps({
            "change_id": change_id,
            "auth": auth,
            "gerrit_url": settings.gerrit.url,
            "project_dir": project_dir,
        }),
        encoding="utf-8",
    )
    return str(path)


EmitFn = Callable[[dict], Awaitable[None]]


async def run_review(
    conn: sqlite3.Connection,
    settings: Settings,
    gerrit: GerritLike,
    worker: WorkerFn,
    change_id: str,
    auth: str,
    project_slug: str = "",
    rules_dir: Path | None = None,
    on_event: EmitFn | None = None,
) -> dict:
    """Review a change end to end and persist the result. Returns the stored dict.

    on_event (optional) receives progress dicts as workers finish — the SSE
    stream forwards them. ponytail: a no-op default keeps callers/tests simple.
    """
    async def emit(evt: dict) -> None:
        if on_event:
            await on_event(evt)

    detail = await gerrit.change_detail(change_id)
    branch = detail.get("branch", "")
    patchset = int(detail.get("revisions", {}).get(detail.get("current_revision", ""), {})
                   .get("_number", 1)) if detail.get("current_revision") else 1

    review_id = start_review(conn, change_id, patchset, project_slug, branch)

    # Incremental: if an earlier patchset was already reviewed, only look at
    # files touched since — ponytail: let Gerrit compute the delta via base=.
    base = 0
    if settings.review.incremental:
        base = last_reviewed_patchset(conn, change_id, patchset)
    files = await gerrit.changed_files(change_id, base=base or None)
    to_review = [f for f in files if f.has_changes]
    if not to_review:
        finish_review(conn, review_id, [], TokenUsage(), status="done")
        from .store import get_review
        result = get_review(conn, change_id, patchset)
        await emit({"type": "done", **result})
        return result

    # Per-file diffs (concurrent fetch — independent GETs).
    diffs = await asyncio.gather(*(gerrit.file_diff(change_id, f.path) for f in to_review))
    file_diffs = {f.path: d for f, d in zip(to_review, diffs) if d.strip()}

    rules_dir = rules_dir or settings.review.rules_path()
    rule_paths = [str(p) for p in resolve_rule_paths(rules_dir, list(file_diffs), project_slug)]
    mcp_config = _write_mcp_config(settings, change_id, auth, "")

    await emit({"type": "started", "change_id": change_id, "patchset": patchset,
                "total_files": len(file_diffs)})

    sem = asyncio.Semaphore(settings.review.max_workers)

    async def _one(path: str, diff: str) -> WorkerResult:
        async with sem:
            try:
                found = await worker(
                    path, diff, rule_paths, project_slug, mcp_config,
                    settings.review.diff_cap_chars, settings.review.worker_timeout_seconds,
                )
            except Exception:
                found = []  # ponytail: a worker crash yields no comments, not a failed review
            comments = found[0] if isinstance(found, tuple) else found
            await emit({"type": "file", "file": path,
                        "comments": [c.model_dump() for c in comments]})
            return found

    results = await asyncio.gather(*(_one(p, d) for p, d in file_diffs.items()))
    comments: list[ReviewComment] = []
    provider_usage = TokenUsage()
    has_provider_usage = False
    for result in results:
        if isinstance(result, tuple):
            found, usage = result
            comments.extend(found)
            provider_usage.input_tokens += usage.input_tokens
            provider_usage.output_tokens += usage.output_tokens
            has_provider_usage = True
        else:
            comments.extend(result)

    deduped = dedup_comments(comments)
    in_chars = sum(len(d) for d in file_diffs.values())
    out_chars = sum(len(c.comment) for c in deduped)
    usage = _price_usage(settings, provider_usage) if has_provider_usage else _estimate_cost(settings, in_chars, out_chars)
    usage.files_reviewed = len(file_diffs)

    finish_review(conn, review_id, deduped, usage, status="done")
    from .store import get_review
    result = get_review(conn, change_id, patchset)
    await emit({"type": "done", **result})
    return result


class GerritPoster(Protocol):
    async def list_robot_comments(self, change_id: str) -> dict: ...
    async def post_review(
        self, change_id: str, robot_comments: dict,
        message: str = "", labels: dict | None = None,
    ) -> int: ...


async def post_to_gerrit(
    conn: sqlite3.Connection,
    settings: Settings,
    gerrit: GerritPoster,
    change_id: str,
    patchset: int,
    comment_ids: list[int] | None = None,
) -> dict:
    """Post a stored review's comments to Gerrit as robot comments.

    When `comment_ids` is given, only those comments are posted (selective
    posting from the UI); otherwise the whole review is posted. Dedups against
    comments already posted to the change (re-posting is a no-op on those),
    attaches fix suggestions, and optionally auto-votes.
    """
    from .gerrit import build_robot_comments, posted_comment_keys, vote_label
    from .store import get_review, mark_posted

    stored = get_review(conn, change_id, patchset)
    if not stored:
        return {"posted": 0, "error": "no stored review for that patchset"}

    review_id = stored["id"]
    rows = stored["comments"]
    if comment_ids is not None:
        wanted = set(comment_ids)
        rows = [c for c in rows if c["id"] in wanted]
        if not rows:
            return {"posted": 0, "error": "none of the selected comments exist"}

    # Keep each comment's db id alongside its ReviewComment so we can mark
    # exactly what was posted (selective posting must not flag the rest).
    fields = ("file", "line", "severity", "comment", "existing_code", "suggestion_code")
    pairs = [(c["id"], ReviewComment(**{k: c[k] for k in fields})) for c in rows]

    # Dedup against what Gerrit already has (avoids v3's double-post bug).
    existing = posted_comment_keys(await gerrit.list_robot_comments(change_id))
    fresh = [(cid, c) for cid, c in pairs if (c.file, c.line) not in existing]
    if not fresh:
        return {"posted": 0, "skipped": len(pairs), "reason": "all already posted"}

    fresh_comments = [c for _, c in fresh]
    run_id = f"{change_id}-ps{patchset}"
    robot = build_robot_comments(fresh_comments, run_id)
    labels = vote_label(fresh_comments, settings.post.auto_vote, settings.post.vote_label)
    message = f"Hermes AI review: {len(fresh)} comment(s)"

    status = await gerrit.post_review(change_id, robot, message, labels or None)
    mark_posted(conn, review_id, [cid for cid, _ in fresh])
    return {"posted": len(fresh), "skipped": len(pairs) - len(fresh),
            "http_status": status, "labels": labels}
