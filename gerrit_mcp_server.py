#!/usr/bin/env python3
"""
Gerrit MCP Server — bridges Hermes Agent to Gerrit REST API.

Exposes Gerrit operations as MCP tools so the review agent can:
- Fetch diffs directly from Gerrit (no temp files)
- Read file content from the repository
- Search for patterns across changed files
- List files in a change
- Read the project's knowledge graph context

Protocol: MCP over stdio (JSON-RPC 2.0, newline-delimited).

Configuration via environment variables:
  GERRIT_URL       — Gerrit server URL (default: https://review2.bjitgroup.com:8443)
  GERRIT_AUTH      — Authorization header value (Basic base64 or Bearer token)
  GERRIT_CHANGE_ID — Current change ID being reviewed (set per-invocation)
  PROJECT_DIR      — Path to local project clone (optional, for local file access)
  KNOWLEDGE_GRAPH  — Path to knowledge-graph.json (optional, for context queries)
"""

import sys
import os
import json
import base64
import ssl
import urllib.request
import urllib.parse
import urllib.error
import fnmatch
import re

# ── Configuration ────────────────────────────────────────────────
GERRIT_URL = os.environ.get("GERRIT_URL", "https://review2.bjitgroup.com:8443")
GERRIT_AUTH = os.environ.get("GERRIT_AUTH", "")
CHANGE_ID = os.environ.get("GERRIT_CHANGE_ID", "")
PROJECT_DIR = os.environ.get("PROJECT_DIR", "")
KNOWLEDGE_GRAPH = os.environ.get("KNOWLEDGE_GRAPH", "")

# Per-invocation config file (written by ai_reviewer.py before each review)
CONFIG_FILE = os.environ.get("GERRIT_MCP_CONFIG", "")

def _load_runtime_config():
    """Load per-invocation config from JSON file if available."""
    global CHANGE_ID, GERRIT_AUTH, PROJECT_DIR
    if CONFIG_FILE and os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("change_id"):
                CHANGE_ID = cfg["change_id"]
            if cfg.get("auth"):
                GERRIT_AUTH = cfg["auth"]
            if cfg.get("project_dir"):
                PROJECT_DIR = cfg["project_dir"]
            if cfg.get("gerrit_url"):
                global GERRIT_URL
                GERRIT_URL = cfg["gerrit_url"]
            log(f"Loaded runtime config from {CONFIG_FILE}")
        except Exception as e:
            log(f"Warning: Could not load config file: {e}")

# ── SSL Context ──────────────────────────────────────────────────
def make_ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

# ── Gerrit API helpers ───────────────────────────────────────────
def gerrit_get(path, accept="application/json"):
    """Authenticated GET to Gerrit REST API. Returns parsed JSON or raw text."""
    url = GERRIT_URL.rstrip("/") + path
    headers = {
        "Authorization": GERRIT_AUTH,
        "Accept": accept,
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, context=make_ssl_ctx(), timeout=30) as r:
            raw = r.read().decode("utf-8", errors="replace")
            if accept == "application/json":
                # Strip Gerrit's XSSI prefix
                if raw.startswith(")]}'"):
                    raw = raw[5:]
                return json.loads(raw)
            return raw
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code}: {body}")
    except Exception as e:
        raise RuntimeError(f"Gerrit API error: {e}")

def strip_xssi(text):
    if text.startswith(")]}'"):
        return text[5:]
    return text

# ── File cache (within a single review session) ─────────────────
_file_cache: dict[str, str] = {}

def fetch_file_content(file_path: str) -> str:
    """Fetch file content from Gerrit. Caches within session."""
    if file_path in _file_cache:
        return _file_cache[file_path]
    
    encoded = urllib.parse.quote(file_path, safe="")
    url = f"{GERRIT_URL.rstrip('/')}/a/changes/{CHANGE_ID}/revisions/current/files/{encoded}/content"
    headers = {"Authorization": GERRIT_AUTH}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, context=make_ssl_ctx(), timeout=30) as r:
            raw = r.read()
            content = base64.b64decode(raw).decode("utf-8", errors="replace")
            _file_cache[file_path] = content
            return content
    except Exception as e:
        raise RuntimeError(f"Failed to read {file_path}: {e}")

# ── Tool implementations ────────────────────────────────────────

def _ensure_config():
    """Reload runtime config before each tool call."""
    _load_runtime_config()

def tool_gerrit_get_diff(args: dict) -> str:
    """Fetch the full diff of a Gerrit change."""
    _ensure_config()
    change_id = args.get("change_id", CHANGE_ID)
    if not change_id:
        return "Error: No change_id provided and GERRIT_CHANGE_ID not set."
    
    try:
        # Use the patch endpoint for unified diff
        url = f"/a/changes/{change_id}/revisions/current/patch"
        raw = gerrit_get(url, accept="text/plain")
        
        # Gerrit returns base64-encoded patch
        try:
            patch = base64.b64decode(raw.strip()).decode("utf-8", errors="replace")
        except Exception:
            patch = raw if isinstance(raw, str) else str(raw)
        
        # Truncate if too large (keep under 50k chars for LLM context)
        if len(patch) > 50000:
            patch = patch[:50000] + f"\n\n... [TRUNCATED — diff is {len(patch)} chars, showing first 50000]"
        
        return patch
    except Exception as e:
        return f"Error fetching diff: {e}"


def tool_gerrit_file_read(args: dict) -> str:
    """Read file content from the repository."""
    _ensure_config()
    file_path = args.get("file_path", "")
    start_line = args.get("start_line")
    end_line = args.get("end_line")
    
    if not file_path:
        return "Error: file_path is required."
    
    try:
        # Try local project dir first (faster)
        if PROJECT_DIR:
            local_path = os.path.join(PROJECT_DIR, file_path)
            if os.path.isfile(local_path):
                with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            else:
                content = fetch_file_content(file_path)
        else:
            content = fetch_file_content(file_path)
        
        lines = content.splitlines()
        total = len(lines)
        
        # Apply line range
        start = max((start_line or 1) - 1, 0)
        end = min(end_line or total, total)
        selected = lines[start:end]
        
        # Format with line numbers (like OpenCodeReview)
        result = f"File: {file_path} (Total lines: {total})\n"
        result += f"LINE_RANGE: {start+1}-{start+len(selected)}\n"
        for i, line in enumerate(selected):
            result += f"{start+1+i}|{line}\n"
        
        return result
    except Exception as e:
        return f"Error reading {file_path}: {e}"


def tool_gerrit_code_search(args: dict) -> str:
    """Search for text patterns across the repository (or changed files if no local clone)."""
    _ensure_config()
    search_text = args.get("search_text", "")
    file_patterns = args.get("file_patterns", [])
    
    if not search_text:
        return "Error: search_text is required."
    
    try:
        matches = []
        
        # If we have a local clone, search the entire repository
        if PROJECT_DIR and os.path.isdir(PROJECT_DIR):
            import subprocess
            # Use grep for fast recursive search
            cmd = ["grep", "-r", "-i", "-n", "--include=*", search_text, PROJECT_DIR]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                        encoding="utf-8", errors="replace")
                for line in result.stdout.splitlines()[:200]:
                    # Strip the project dir prefix for readability
                    if PROJECT_DIR in line:
                        line = line.replace(PROJECT_DIR, "").lstrip("/\\")
                    matches.append(line)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                # Fallback: manual search
                for root, dirs, files in os.walk(PROJECT_DIR):
                    # Skip common non-source dirs
                    dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "bin", "obj", ".vs")]
                    for fname in files:
                        if file_patterns and not any(fnmatch.fnmatch(fname, p) for p in file_patterns):
                            continue
                        fpath = os.path.join(root, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                                for i, line in enumerate(fh):
                                    if search_text.lower() in line.lower():
                                        rel = os.path.relpath(fpath, PROJECT_DIR)
                                        matches.append(f"{rel}:{i+1}: {line.strip()}")
                        except Exception:
                            continue
                        if len(matches) >= 200:
                            break
                    if len(matches) >= 200:
                        break
        else:
            # No local clone — search only changed files via Gerrit API
            files_data = gerrit_get(f"/a/changes/{CHANGE_ID}/revisions/current/files")
            file_list = [f for f in files_data.keys() if f != "/COMMIT_MSG"]
            
            for f in file_list:
                if file_patterns and not any(fnmatch.fnmatch(f, p) for p in file_patterns):
                    continue
                try:
                    content = fetch_file_content(f)
                    for i, line in enumerate(content.splitlines()):
                        if search_text.lower() in line.lower():
                            matches.append(f"{f}:{i+1}: {line.strip()}")
                except Exception:
                    continue
        
        result = "\n".join(matches[:100])
        if not result:
            result = "No matches found."
        elif len(matches) > 100:
            result += f"\n\n... [{len(matches)} total matches, showing first 100]"
        
        return result
    except Exception as e:
        return f"Error searching: {e}"


def tool_gerrit_list_files(args: dict) -> str:
    """List all files changed in a Gerrit revision."""
    _ensure_config()
    change_id = args.get("change_id", CHANGE_ID)
    if not change_id:
        return "Error: No change_id provided."
    
    try:
        files = gerrit_get(f"/a/changes/{change_id}/revisions/current/files")
        result = []
        for f, meta in files.items():
            if f == "/COMMIT_MSG":
                continue
            ins = meta.get("lines_inserted", 0)
            dele = meta.get("lines_deleted", 0)
            status = meta.get("status", "M")  # M=modified, A=added, D=deleted, R=renamed
            result.append(f"{status} {f} (+{ins} -{dele})")
        
        return "\n".join(result) if result else "No files changed."
    except Exception as e:
        return f"Error listing files: {e}"


def tool_gerrit_read_diff(args: dict) -> str:
    """Read the diff of a specific file in the change."""
    _ensure_config()
    file_path = args.get("file_path", "")
    if not file_path:
        return "Error: file_path is required."
    
    try:
        encoded = urllib.parse.quote(file_path, safe="")
        diff = gerrit_get(f"/a/changes/{CHANGE_ID}/revisions/current/files/{encoded}/diff")
        
        # Convert Gerrit's structured diff to unified text
        out = f"--- a/{file_path}\n+++ b/{file_path}\n"
        for chunk in diff.get("content", []):
            if "ab" in chunk:
                for line in chunk["ab"]:
                    out += f" {line}\n"
            if "a" in chunk:
                for line in chunk["a"]:
                    out += f"-{line}\n"
            if "b" in chunk:
                for line in chunk["b"]:
                    out += f"+{line}\n"
        
        return out
    except Exception as e:
        return f"Error reading diff for {file_path}: {e}"


def tool_reload_config(args: dict) -> str:
    """Reload runtime configuration from config file."""
    _load_runtime_config()
    return json.dumps({
        "change_id": CHANGE_ID,
        "gerrit_url": GERRIT_URL,
        "project_dir": PROJECT_DIR,
        "has_auth": bool(GERRIT_AUTH)
    })


# ── Tool definitions (MCP schema) ───────────────────────────────

TOOLS = [
    {
        "name": "gerrit_get_diff",
        "description": "Fetch the full unified diff of a Gerrit change. Returns the complete patch. Use this first to see all changes in the review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_id": {
                    "type": "string",
                    "description": "Gerrit change ID. If omitted, uses the current change from GERRIT_CHANGE_ID env var."
                }
            }
        }
    },
    {
        "name": "gerrit_file_read",
        "description": "Read file content from the repository. Use this to see surrounding code context before making review judgments. Reads from local clone if available, otherwise fetches from Gerrit API.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file relative to the repository root."
                },
                "start_line": {
                    "type": "integer",
                    "description": "Start line number (1-indexed). Defaults to 1."
                },
                "end_line": {
                    "type": "integer",
                    "description": "End line number (1-indexed). Defaults to end of file."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "gerrit_code_search",
        "description": "Search for text patterns across the entire repository (when cloned locally) or changed files (if no local clone). Use to verify if patterns exist elsewhere, check naming consistency, or find similar code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search_text": {
                    "type": "string",
                    "description": "Text to search for (case-insensitive)."
                },
                "file_patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional glob patterns to filter files (e.g., ['*.java', '*.py'])."
                }
            },
            "required": ["search_text"]
        }
    },
    {
        "name": "gerrit_list_files",
        "description": "List all files changed in a Gerrit revision with insert/delete counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_id": {
                    "type": "string",
                    "description": "Gerrit change ID. If omitted, uses the current change."
                }
            }
        }
    },
    {
        "name": "gerrit_read_diff",
        "description": "Read the diff of a specific file in the current change. Returns unified diff format for just that file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file relative to the repository root."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "reload_config",
        "description": "Reload runtime configuration (change_id, auth, project paths). Call this before starting a new review if the config file has been updated.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

# ── MCP JSON-RPC Server ─────────────────────────────────────────

def tool_reload_config(args: dict) -> str:
    """Reload runtime configuration from config file."""
    _load_runtime_config()
    return json.dumps({
        "change_id": CHANGE_ID,
        "gerrit_url": GERRIT_URL,
        "project_dir": PROJECT_DIR,
        "knowledge_graph": KNOWLEDGE_GRAPH,
        "has_auth": bool(GERRIT_AUTH)
    })

TOOL_HANDLERS = {
    "gerrit_get_diff": tool_gerrit_get_diff,
    "gerrit_file_read": tool_gerrit_file_read,
    "gerrit_code_search": tool_gerrit_code_search,
    "gerrit_list_files": tool_gerrit_list_files,
    "gerrit_read_diff": tool_gerrit_read_diff,
    "reload_config": tool_reload_config,
}

def send_response(msg_id, result):
    """Send a JSON-RPC response to stdout."""
    response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()

def send_error(msg_id, code, message):
    """Send a JSON-RPC error response."""
    response = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()

def log(msg):
    """Log to stderr (stdout is reserved for JSON-RPC)."""
    print(f"[gerrit-mcp] {msg}", file=sys.stderr, flush=True)

def handle_message(msg: dict):
    """Handle a single JSON-RPC message."""
    msg_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {})
    
    if method == "initialize":
        send_response(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "gerrit-review",
                "version": "1.0.0"
            }
        })
        log("Initialized MCP server")
    
    elif method == "notifications/initialized":
        # Client acknowledgment — no response needed
        pass
    
    elif method == "tools/list":
        send_response(msg_id, {"tools": TOOLS})
    
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            send_response(msg_id, {
                "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                "isError": True
            })
            return
        
        try:
            import time as _t
            _t0 = _t.time()
            log(f"TOOL CALL ▶ {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:200]})")
            result = handler(tool_args)
            elapsed = _t.time() - _t0
            log(f"TOOL CALL ✓ {tool_name} → {len(result)} chars in {elapsed:.1f}s")
            send_response(msg_id, {
                "content": [{"type": "text", "text": result}]
            })
        except Exception as e:
            log(f"TOOL CALL ✗ {tool_name} → ERROR: {e}")
            send_response(msg_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True
            })
    
    elif method == "ping":
        send_response(msg_id, {})
    
    else:
        # Unknown method — send error
        if msg_id is not None:
            send_error(msg_id, -32601, f"Method not found: {method}")

def main():
    """Main loop: read JSON-RPC from stdin, write responses to stdout."""
    global CHANGE_ID, GERRIT_AUTH, GERRIT_URL, PROJECT_DIR, KNOWLEDGE_GRAPH
    
    # Override config from env vars
    CHANGE_ID = os.environ.get("GERRIT_CHANGE_ID", CHANGE_ID)
    GERRIT_AUTH = os.environ.get("GERRIT_AUTH", GERRIT_AUTH)
    GERRIT_URL = os.environ.get("GERRIT_URL", GERRIT_URL)
    PROJECT_DIR = os.environ.get("PROJECT_DIR", PROJECT_DIR)
    KNOWLEDGE_GRAPH = os.environ.get("KNOWLEDGE_GRAPH", KNOWLEDGE_GRAPH)
    
    log(f"Starting Gerrit MCP server")
    log(f"  Gerrit URL: {GERRIT_URL}")
    log(f"  Change ID: {CHANGE_ID or '(dynamic)'}")
    log(f"  Project Dir: {PROJECT_DIR or '(none)'}")
    log(f"  Knowledge Graph: {KNOWLEDGE_GRAPH or '(none)'}")
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        
        try:
            msg = json.loads(line)
            handle_message(msg)
        except json.JSONDecodeError as e:
            log(f"Invalid JSON: {e}")
        except Exception as e:
            log(f"Error handling message: {e}")

if __name__ == "__main__":
    main()
