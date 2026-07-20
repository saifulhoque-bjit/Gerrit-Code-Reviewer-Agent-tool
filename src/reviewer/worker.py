"""Strategy A worker: review one file via a `hermes -z` subprocess.

ponytail: keep v3's file-based stdout capture — it's the one thing that
reliably dodged the Windows pipe-deadlock when hermes spawns MCP children
(capture_output=True hangs there). We wrap the blocking subprocess in
asyncio.to_thread so the async orchestrator can run a bounded pool of them.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from .models import ReviewComment
from .parser import parse_comments

PROMPT_TEMPLATE = """You are a senior code reviewer. Review this single file change.

File: {file_path}
Project: {project_slug}

REVIEW RULES: Read these files for the checklist, then apply every rule:
{rules_files}

DIFF:
{diff}

INSTRUCTIONS:
1. Read the rule files first, then analyze the diff for real bugs, security
   issues, or logic errors.
2. Use your MCP tools (gerrit_file_read, gerrit_code_search, get_architecture,
   search_graph) to verify referenced symbols and understand impact.
3. Do NOT flag style, naming, or cosmetic issues.
4. If no real issues found, return [].

OUTPUT (strict JSON array only, no prose):
[{{"file":"{file_path}","line":N,"severity":"error|warning|suggestion","comment":"...","existing_code":"...","suggestion_code":"..."}}]
"""


def find_hermes() -> str:
    """Locate the hermes binary — PATH first, then the running interpreter's dir."""
    import shutil
    import sys

    h = shutil.which("hermes")
    if h:
        return h
    scripts = os.path.dirname(sys.executable)
    for name in ("hermes.exe", "hermes"):
        cand = os.path.join(scripts, name)
        if os.path.isfile(cand):
            return cand
    return "hermes"


HERMES_BIN = find_hermes()


def _run_hermes_blocking(prompt: str, env: dict, timeout: int) -> str:
    """Run hermes, capturing stdout to a temp file (not a pipe). Returns raw text."""
    with tempfile.TemporaryDirectory() as d:
        out_path = Path(d) / "out.txt"
        err_path = Path(d) / "err.txt"
        cmd = [HERMES_BIN, "-z", prompt, "-s", "gerrit-review", "--yolo", "--cli"]
        with open(out_path, "w", encoding="utf-8", errors="replace") as f_out, \
             open(err_path, "w", encoding="utf-8", errors="replace") as f_err:
            try:
                subprocess.run(cmd, stdout=f_out, stderr=f_err, timeout=timeout, env=env)
            except subprocess.TimeoutExpired:
                return ""
        return out_path.read_text(encoding="utf-8", errors="replace").strip()


async def review_file(
    file_path: str,
    diff: str,
    rules_files: list[str],
    project_slug: str,
    mcp_config_path: str,
    diff_cap: int = 8000,
    timeout: int = 1800,
) -> list[ReviewComment]:
    """Review a single file. Returns parsed comments (empty on timeout/failure)."""
    prompt = PROMPT_TEMPLATE.format(
        file_path=file_path,
        project_slug=project_slug or "unknown",
        rules_files=" | ".join(rules_files) or "(no rule files)",
        diff=diff[:diff_cap],
    )
    env = os.environ.copy()
    env["GERRIT_MCP_CONFIG"] = mcp_config_path
    env["HERMES_HOME"] = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "hermes"
    )
    raw = await asyncio.to_thread(_run_hermes_blocking, prompt, env, timeout)
    return parse_comments(raw)
