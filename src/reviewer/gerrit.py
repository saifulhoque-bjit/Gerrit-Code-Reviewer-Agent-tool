"""Async Gerrit REST client — httpx, CA-pinned TLS (no CERT_NONE).

v3 disabled verification everywhere (check_hostname=False, CERT_NONE).
Here TLS verifies against the system store by default, or a pinned internal
CA bundle when configured. Auth travels as a header, never in a URL.
"""
from __future__ import annotations

import ssl
from typing import Any

import httpx

from .models import ChangedFile
from .diff import gerrit_diff_to_unified

_XSSI = ")]}'"


def _strip_xssi(text: str) -> str:
    """Gerrit prefixes JSON with )]}'\\n to block XSSI."""
    return text[len(_XSSI):].lstrip() if text.startswith(_XSSI) else text


def _verify(ca_bundle: str) -> ssl.SSLContext | str | bool:
    # ponytail: pass the bundle path straight to httpx if set; else system trust.
    return ca_bundle or True


class GerritClient:
    def __init__(self, base_url: str, auth: str, ca_bundle: str = "", timeout: int = 30):
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"Authorization": auth, "Accept": "application/json"},
            verify=_verify(ca_bundle),
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GerritClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def _get_json(self, path: str, params: Any = None) -> Any:
        r = await self._client.get(f"{self._base}{path}", params=params)
        r.raise_for_status()
        import json
        return json.loads(_strip_xssi(r.text))

    async def list_projects(self) -> list[str]:
        """Return all visible Gerrit code-project names."""
        projects: list[str] = []
        start = 0
        while True:
            data = await self._get_json(
                "/a/projects/", {"type": "CODE", "n": 500, "S": start},
            )
            names = [
                name for name, details in data.items()
                if name != "_more_projects" and details.get("state") != "HIDDEN"
            ]
            projects.extend(names)
            if not data.get("_more_projects") or not names:
                return sorted(projects)
            start += len(names)

    async def open_changes(self, project: str = "", limit: int = 100) -> list[dict]:
        """Return the safe change-summary fields the dashboard needs."""
        query = "status:open"
        if project:
            query += f" project:{project}"
        data = await self._get_json(
            "/a/changes/",
            [("q", query), ("n", str(limit)), ("o", "DETAILED_ACCOUNTS")],
        )
        return [{
            "change_id": str(change.get("change_id") or change.get("id") or ""),
            "number": int(change.get("_number") or 0),
            "project": str(change.get("project") or ""),
            "subject": str(change.get("subject") or ""),
            "branch": str(change.get("branch") or ""),
            "owner": str((change.get("owner") or {}).get("display_name")
                         or (change.get("owner") or {}).get("name")
                         or (change.get("owner") or {}).get("username") or "Unknown"),
            "created": str(change.get("created") or ""),
            "updated": str(change.get("updated") or ""),
            "status": str(change.get("status") or "NEW"),
        } for change in data]

    async def changed_files(self, change_id: str, base: int | None = None) -> list[ChangedFile]:
        # base set → only files changed BETWEEN that patchset and current
        # (the engine's incremental-review path uses this to skip untouched files).
        q = f"?base={base}" if base else ""
        data = await self._get_json(
            f"/a/changes/{change_id}/revisions/current/files{q}"
        )
        return [
            ChangedFile(
                path=p,
                lines_inserted=info.get("lines_inserted", 0),
                lines_deleted=info.get("lines_deleted", 0),
                status=info.get("status", "M"),
            )
            for p, info in data.items()
            if p != "/COMMIT_MSG"
        ]

    async def file_diff(self, change_id: str, file_path: str) -> str:
        from urllib.parse import quote
        p = quote(file_path, safe="")
        data = await self._get_json(
            f"/a/changes/{change_id}/revisions/current/files/{p}/diff"
        )
        return gerrit_diff_to_unified(file_path, data.get("content", []))

    async def change_detail(self, change_id: str) -> dict:
        return await self._get_json(f"/a/changes/{change_id}/detail")

    async def account_self(self) -> dict:
        """Validate the supplied Gerrit credentials and return the user profile."""
        return await self._get_json("/a/accounts/self")

    async def list_robot_comments(self, change_id: str) -> dict:
        """Robot comments already on the change, grouped by file (Gerrit shape)."""
        return await self._get_json(f"/a/changes/{change_id}/robotcomments")

    async def post_review(
        self, change_id: str, robot_comments: dict,
        message: str = "", labels: dict | None = None,
    ) -> int:
        """POST a review: robot comments + optional message + optional vote.

        Returns the HTTP status. Auth travels in the header (set on the client),
        never in the URL.
        """
        import json
        payload: dict = {}
        if message:
            payload["message"] = message
        if robot_comments:
            payload["robot_comments"] = robot_comments
        if labels:
            payload["labels"] = labels
        r = await self._client.post(
            f"{self._base}/a/changes/{change_id}/revisions/current/review",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        return r.status_code


# ── Pure payload builders (no I/O — unit-testable) ───────────────────
_ROBOT_ID = "hermes-reviewer"

_SEVERITY_ICON = {"error": "🔴", "warning": "🟡", "suggestion": "💡"}
_SEVERITY_RANK = {"suggestion": 1, "warning": 2, "error": 3}


def posted_comment_keys(robot_comments: dict) -> set[tuple[str, int]]:
    """(file, line) keys already posted, from Gerrit's robotcomments response."""
    keys: set[tuple[str, int]] = set()
    for file_path, entries in robot_comments.items():
        for e in entries:
            keys.add((file_path, int(e.get("line", 0))))
    return keys


def build_robot_comments(comments: list, run_id: str) -> dict:
    """Group ReviewComments into Gerrit's robot_comments payload.

    Each comment carries robotId/robotRunId (unlocks the robot-comment UI) and,
    when a suggestion_code is present, a fix_suggestion (one-click "Apply Fix").
    """
    by_file: dict[str, list[dict]] = {}
    for c in comments:
        icon = _SEVERITY_ICON.get(c.severity, "💡")
        entry: dict = {
            "robot_id": _ROBOT_ID,
            "robot_run_id": run_id,
            "message": f"{icon} [{c.severity.upper()}] {c.comment}",
        }
        if c.line > 0:
            entry["line"] = c.line
        if c.suggestion_code:
            fix: dict = {
                "description": "Apply suggested fix",
                "replacements": [{
                    "path": c.file,
                    "replacement": c.suggestion_code,
                    "range": {
                        "start_line": c.line or 1, "start_character": 0,
                        "end_line": c.line or 1, "end_character": 0,
                    },
                }],
            }
            entry["fix_suggestions"] = [fix]
        by_file.setdefault(c.file, []).append(entry)
    return by_file


def vote_label(comments: list, enabled: bool, label: str = "Code-Review") -> dict:
    """Auto-vote by worst severity: any error → -1, else 0. Empty when disabled."""
    if not enabled or not comments:
        return {}
    worst = max((_SEVERITY_RANK.get(c.severity, 1) for c in comments), default=0)
    return {label: -1 if worst >= _SEVERITY_RANK["error"] else 0}
