#!/usr/bin/env python3
"""
AI Reviewer v3 — Orchestrator pattern for parallel code review.

Architecture:
  1. Orchestrator gets file list + per-file diffs via Gerrit REST API (Python, no hermes)
  2. Spawns N parallel hermes workers (one per file, max 4)
  3. Each worker reviews a single file with focused context
  4. Orchestrator aggregates + deduplicates results

Benefits:
  - Parallel: 4 files in ~2 min (vs 3-5 min sequential)
  - Focused: each worker sees only one file's diff (2-5K chars vs 256K)
  - Reliable: focused prompts → less prose, more JSON
"""

import os
import json
import subprocess
import re
import sys
import shutil
import ssl
import urllib.request
import urllib.parse
from pathlib import Path

DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(DIR, "temp")
RULES_DIR = os.path.join(DIR, "rules")
PROJECTS_DIR = os.path.join(DIR, "projects")
MCP_CONFIG_PATH = os.path.join(TEMP_DIR, "mcp_runtime.json")
SKILLS_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "skills", "gerrit")

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(RULES_DIR, exist_ok=True)
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs(SKILLS_DIR, exist_ok=True)

# ── Locate Hermes binary ─────────────────────────────────────────
def find_hermes():
    h = shutil.which("hermes")
    if h:
        return h
    py = sys.executable
    scripts_dir = os.path.dirname(py)
    for name in ("hermes.exe", "hermes"):
        candidate = os.path.join(scripts_dir, name)
        if os.path.isfile(candidate):
            return candidate
    try:
        cmd = "where hermes" if os.name == "nt" else "which hermes"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        found = result.stdout.strip().splitlines()[0].strip()
        if found and os.path.isfile(found):
            return found
    except Exception:
        pass
    return "hermes"

HERMES_BIN = find_hermes()
print(f"[AI Reviewer v3] Using Hermes binary: {HERMES_BIN}")

# ── Import rules engine ──────────────────────────────────────────
sys.path.insert(0, DIR)
from rules_engine import resolve_rules

# ── SSL context (skip verification for internal Gerrit) ──────────
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ── Gerrit REST API helpers ──────────────────────────────────────
GERRIT_URL = "https://review2.bjitgroup.com:8443"

def gerrit_api_get(path: str, auth_header: str) -> dict:
    """GET from Gerrit REST API, strip magic prefix, return JSON."""
    url = f"{GERRIT_URL}{path}"
    req = urllib.request.Request(url)
    if auth_header:
        req.add_header("Authorization", auth_header)
    req.add_header("Accept", "application/json")
    resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=30)
    body = resp.read().decode("utf-8")
    # Gerrit prefixes JSON with )]}' to prevent XSSI
    if body.startswith(")]}'"):
        body = body[4:].lstrip()
    return json.loads(body)

def get_changed_files(change_id: str, auth_header: str) -> list:
    """Get list of changed files from Gerrit REST API."""
    data = gerrit_api_get(f"/a/changes/{change_id}/revisions/current/files", auth_header)
    files = []
    for path, info in data.items():
        if path == "/COMMIT_MSG":
            continue
        files.append({
            "path": path,
            "lines_inserted": info.get("lines_inserted", 0),
            "lines_deleted": info.get("lines_deleted", 0),
            "status": info.get("status", "M"),  # M=modified, A=added, D=deleted, R=renamed
        })
    return files

def get_file_diff(change_id: str, file_path: str, auth_header: str) -> str:
    """Get unified diff for a specific file from Gerrit REST API."""
    encoded_path = urllib.parse.quote(file_path, safe="")
    data = gerrit_api_get(
        f"/a/changes/{change_id}/revisions/current/files/{encoded_path}/diff",
        auth_header
    )
    # Convert Gerrit diff JSON to unified diff text
    lines = []
    lines.append(f"--- a/{file_path}")
    lines.append(f"+++ b/{file_path}")
    for chunk in data.get("content", []):
        if "ab" in chunk:  # context (unchanged)
            for line in chunk["ab"]:
                lines.append(f" {line}")
        if "a" in chunk:  # removed
            for line in chunk["a"]:
                lines.append(f"-{line}")
        if "b" in chunk:  # added
            for line in chunk["b"]:
                lines.append(f"+{line}")
    return "\n".join(lines)

# ── MCP runtime config ───────────────────────────────────────────
def write_mcp_config(change_id: str, auth_header: str, project_slug: str = ""):
    """Write per-review config for the MCP server to read."""
    import re as _re
    safe_slug = _re.sub(r"[^a-zA-Z0-9._-]", "_", project_slug) if project_slug else ""

    project_dir = ""
    if safe_slug:
        candidate = os.path.join(PROJECTS_DIR, safe_slug, "repo")
        if os.path.isdir(candidate):
            project_dir = candidate

    config = {
        "change_id": change_id,
        "auth": auth_header,
        "gerrit_url": GERRIT_URL,
        "project_dir": project_dir,
    }

    with open(MCP_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f)

    print(f"[AI Reviewer v3] MCP config: change={change_id}, project_dir={project_dir or '(none)'}")
    return project_dir

# ── Worker prompt template ───────────────────────────────────────
WORKER_PROMPT_TEMPLATE = """You are a senior code reviewer. Review this single file change.

File: {file_path}
Project: {project_slug}

{rules_section}

DIFF:
{diff}

INSTRUCTIONS:
1. Analyze the diff for real bugs, security issues, or logic errors
2. If code references components/methods not in this diff, use gerrit_code_search to verify they exist before flagging
3. Use gerrit_file_read to see surrounding code context if needed
4. Do NOT flag style, naming, or cosmetic issues
5. If no real issues found, return []

OUTPUT (strict JSON array only, no prose):
[{{"file":"{file_path}","line":N,"severity":"error|warning|suggestion","comment":"description","existing_code":"code","suggestion_code":"fixed_code"}}]
"""

# ── Token cost estimation ────────────────────────────────────────
INPUT_COST_PER_1M  = 3.00
OUTPUT_COST_PER_1M = 15.00

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

# ── Orchestrator: parallel review ────────────────────────────────
MAX_WORKERS = 4
WORKER_TIMEOUT = 180  # seconds per worker

def _review_single_file(file_path: str, diff: str, rules_section: str,
                        project_slug: str, env: dict, worker_id: int) -> list:
    """Worker: review one file using a hermes subprocess."""
    import time as _t

    prompt = WORKER_PROMPT_TEMPLATE.format(
        file_path=file_path,
        project_slug=project_slug or "unknown",
        rules_section=rules_section,
        diff=diff[:15000],  # cap per-file diff at 15K chars
    )

    stdout_file = os.path.join(TEMP_DIR, f"worker_{worker_id}_stdout.txt")
    stderr_file = os.path.join(TEMP_DIR, f"worker_{worker_id}_stderr.txt")
    cmd = [HERMES_BIN, "-z", prompt, "-s", "gerrit-review", "--yolo", "--cli"]

    t0 = _t.time()
    try:
        with open(stdout_file, "w", encoding="utf-8", errors="replace") as f_out, \
             open(stderr_file, "w", encoding="utf-8", errors="replace") as f_err:
            subprocess.run(cmd, stdout=f_out, stderr=f_err, timeout=WORKER_TIMEOUT, env=env)

        with open(stdout_file, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read().strip()

        comments = parse_comments(raw)
        elapsed = _t.time() - t0
        print(f"[Worker {worker_id}] {file_path}: {len(comments)} comments in {elapsed:.0f}s")
        sys.stdout.flush()
        return comments

    except subprocess.TimeoutExpired:
        print(f"[Worker {worker_id}] {file_path}: TIMEOUT ({WORKER_TIMEOUT}s)")
        return []
    except Exception as e:
        print(f"[Worker {worker_id}] {file_path}: ERROR: {e}")
        return []
    finally:
        for f in (stdout_file, stderr_file):
            try:
                os.remove(f)
            except Exception:
                pass


def review(change_id: str, diff_text: str, filenames: list, project_slug: str = "",
           auth_header: str = "") -> tuple[list, dict]:
    """
    Review a Gerrit change using parallel workers (one per file).

    Returns: (comments_list, token_summary_dict)
    """
    import time as _t
    import concurrent.futures

    _t0 = _t.time()

    # 1. Write MCP runtime config (for workers to use)
    project_dir = write_mcp_config(change_id, auth_header, project_slug)

    # 2. Resolve rules
    rules = resolve_rules(filenames, project_slug)
    rules_section = f"REVIEW RULES:\n{rules}" if rules else ""
    if rules:
        print(f"[AI Reviewer v3] Rules loaded: {len(rules)} chars")
    else:
        print(f"[AI Reviewer v3] No project rules — using defaults")

    # 3. Get file list via Gerrit REST API (no hermes needed)
    try:
        files = get_changed_files(change_id, auth_header)
    except Exception as e:
        print(f"[AI Reviewer v3] Failed to get file list: {e}")
        return [], {"error": str(e)}

    files_to_review = [f for f in files if f["lines_inserted"] + f["lines_deleted"] > 0]
    print(f"[AI Reviewer v3] {len(files)} files changed, {len(files_to_review)} with modifications")

    # 4. Get per-file diffs via Gerrit REST API (no hermes needed)
    file_diffs = {}
    for f in files_to_review:
        try:
            diff = get_file_diff(change_id, f["path"], auth_header)
            if diff.strip():
                file_diffs[f["path"]] = diff
                print(f"[AI Reviewer v3]   {f['path']}: +{f['lines_inserted']}/-{f['lines_deleted']} ({len(diff)} chars diff)")
        except Exception as e:
            print(f"[AI Reviewer v3]   {f['path']}: FAILED: {e}")

    if not file_diffs:
        print(f"[AI Reviewer v3] No diffs to review")
        return [], {"files_reviewed": 0}

    # 5. Spawn parallel workers
    env = os.environ.copy()
    env["GERRIT_MCP_CONFIG"] = MCP_CONFIG_PATH

    num_workers = min(MAX_WORKERS, len(file_diffs))
    print(f"[AI Reviewer v3] Spawning {num_workers} workers for {len(file_diffs)} files...")
    sys.stdout.flush()

    all_comments = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {}
        for i, (path, diff) in enumerate(file_diffs.items()):
            worker_id = i + 1
            futures[executor.submit(
                _review_single_file, path, diff, rules_section, project_slug, env, worker_id
            )] = path

        for future in concurrent.futures.as_completed(futures):
            path = futures[future]
            try:
                comments = future.result()
                all_comments.extend(comments)
            except Exception as e:
                print(f"[AI Reviewer v3] Worker exception for {path}: {e}")

    # 6. Deduplicate by (file, line)
    seen = set()
    deduped = []
    for c in all_comments:
        key = (c.get("file", ""), c.get("line", 0))
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    elapsed = _t.time() - _t0

    # 7. Token summary
    total_diff_chars = sum(len(d) for d in file_diffs.values())
    token_summary = {
        "input_tokens": estimate_tokens(rules_section) * len(file_diffs),
        "output_tokens": sum(estimate_tokens(json.dumps(c)) for c in deduped),
        "total_tokens": estimate_tokens(rules_section) * len(file_diffs) + sum(estimate_tokens(json.dumps(c)) for c in deduped),
        "estimated_cost_usd": 0.0,
        "files_reviewed": len(file_diffs),
        "workers_used": num_workers,
        "elapsed_seconds": round(elapsed),
        "rules_strategy": "orchestrator-parallel",
        "has_mcp_tools": True,
    }

    print(
        f"[AI Reviewer v3] ── Review Summary ──────────────────────────\n"
        f"  Files reviewed : {len(file_diffs)}\n"
        f"  Workers used   : {num_workers}\n"
        f"  Comments found : {len(deduped)}\n"
        f"  Total time     : {elapsed:.0f}s\n"
        f"[AI Reviewer v3] ─────────────────────────────────────────────"
    )
    sys.stdout.flush()

    return deduped, token_summary


# ── JSON parsing ─────────────────────────────────────────────────
def parse_comments(raw: str) -> list:
    """Extract JSON array from Hermes output."""
    if not raw:
        return []

    # Direct parse
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return normalize(data)
    except Exception:
        pass

    # Markdown code fence
    fence_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            if isinstance(data, list):
                return normalize(data)
        except Exception:
            pass

    # Bracket-counting: find all valid JSON arrays, return the longest
    arrays = []
    i = 0
    while i < len(raw):
        if raw[i] == '[':
            depth = 0
            j = i
            while j < len(raw):
                if raw[j] == '[':
                    depth += 1
                elif raw[j] == ']':
                    depth -= 1
                    if depth == 0:
                        candidate = raw[i:j+1]
                        try:
                            data = json.loads(candidate)
                            if isinstance(data, list) and len(data) > 0:
                                arrays.append(data)
                        except Exception:
                            pass
                        break
                j += 1
        i += 1

    if arrays:
        best = max(arrays, key=len)
        return normalize(best)

    # No JSON found — return empty (don't pollute with prose)
    return []


def normalize(items: list) -> list:
    """Ensure each item has required fields."""
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append({
            "file": str(item.get("file", "general")),
            "line": int(item.get("line", 0)) if str(item.get("line", "0")).isdigit() else 0,
            "severity": item.get("severity", "suggestion") if item.get("severity") in ("error", "warning", "suggestion") else "suggestion",
            "comment": str(item.get("comment", "")),
            "existing_code": str(item.get("existing_code", "")),
            "suggestion_code": str(item.get("suggestion_code", "")),
        })
    return result


if __name__ == "__main__":
    # Quick test: get file list for a known change
    import sys
    change_id = sys.argv[1] if len(sys.argv) > 1 else "198601"
    auth = "Basic c2FpZnVsLmhvcXVlOmtvbGxvbDM2"
    files = get_changed_files(change_id, auth)
    print(f"\nChanged files in {change_id}:")
    for f in files:
        print(f"  {f['path']}: +{f['lines_inserted']}/-{f['lines_deleted']}")
