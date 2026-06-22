# Gerrit AI Code Reviewer Agent

AI-powered code review for Gerrit, built on [Hermes Agent](https://hermes-agent.nousresearch.com) with MCP tools. Reviews code changes in parallel (one worker per file), applies project-specific and language-aware rules, and posts inline suggestions back to Gerrit.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DASHBOARD (localhost:7474)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │  Login   │  │ Projects │  │ Changes  │  │  Review + Diff   │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘   │
├─────────────────────────────────────────────────────────────────────┤
│                        server.py (HTTP)                              │
│  • Serves dashboard UI                                               │
│  • Proxies /api/* → Gerrit REST API                                  │
│  • Manages project clone/branch/index lifecycle                      │
│  • Spawns AI review threads                                          │
├─────────────────────────────────────────────────────────────────────┤
│                   ai_reviewer.py (Orchestrator)                      │
│  1. Fetches file list + per-file diffs via Gerrit REST API           │
│  2. Auto-checks out correct branch if mismatch                       │
│  3. Spawns N parallel hermes workers (max 4)                         │
│  4. Aggregates + deduplicates JSON results                           │
├────────┬────────┬────────┬───────────────────────────────────────────┤
│Worker 1│Worker 2│Worker 3│Worker 4        hermes -z (parallel)       │
│file A  │file B  │file C  │file D                                      │
├────────┴────────┴────────┴───────────────────────────────────────────┤
│              gerrit_mcp_server.py (MCP over stdio)                    │
│  gerrit_get_diff · gerrit_read_diff · gerrit_file_read               │
│  gerrit_code_search · gerrit_list_files · reload_config               │
├──────────────────────────────────────────────────────────────────────┤
│              Gerrit REST API (review2.bjitgroup.com:8443)             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [File Structure](#file-structure)
- [How It Works](#how-it-works)
- [Rules System](#rules-system)
- [Project Management](#project-management)
- [Configuration](#configuration)
- [MCP Tools Reference](#mcp-tools-reference)
- [Dashboard Pages](#dashboard-pages)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

### Prerequisites

| Dependency | Required | Notes |
|------------|----------|-------|
| **Python 3.11+** | ✅ | No pip packages — stdlib only |
| **Hermes Agent** | ✅ | `hermes` CLI on PATH ([install](https://hermes-agent.nousresearch.com)) |
| **codebase-memory-mcp** | Optional | For codebase indexing ([releases](https://github.com/DeusData/codebase-memory-mcp/releases)) |
| **Gerrit account** | ✅ | HTTP password from Gerrit → Settings → HTTP Credentials |

### Launch

```bash
# Windows — double-click:
Start Dashboard.bat

# Or manually:
python server.py
# Opens at http://localhost:7474
```

### First Review

1. **Log in** → Enter Gerrit username + HTTP password
2. **Clone a project** → Projects page → pick a project → Clone
3. **Checkout branch** → Select the branch your changes are on
4. **Index codebase** → Click Analyze (optional but improves quality)
5. **Open a change** → Changes page → pick a change → Review
6. **Click "AI Review"** → Wait 1-2 minutes → see inline comments

---

## Architecture

### Review Flow (v3 — Orchestrator Pattern)

```
User clicks "AI Review"
        │
        ▼
server.py (background thread)
        │
        ▼
ai_reviewer.review()
        │
        ├─ 1. Gerrit REST API: get changed files + branch info
        ├─ 2. Auto-checkout if local clone is on wrong branch
        ├─ 3. Gerrit REST API: get per-file diffs (no hermes)
        ├─ 4. Load rules (_base + _lang + project-specific)
        │
        ├─ 5. ThreadPoolExecutor (max 4 workers)
        │     ├─ Worker 1: hermes -z "review file A" --yolo --cli
        │     ├─ Worker 2: hermes -z "review file B" --yolo --cli
        │     ├─ Worker 3: hermes -z "review file C" --yolo --cli
        │     └─ Worker 4: hermes -z "review file D" --yolo --cli
        │
        ├─ 6. Parse JSON from each worker output
        ├─ 7. Deduplicate by (file, line)
        │
        ▼
Return comments to frontend
```

**Key design decisions:**
- **File list + diffs fetched via Python** (Gerrit REST API) — not via hermes, avoids MCP overhead
- **One worker per file** — focused context (2-5K diff vs 256K), deeper analysis
- **Parallel execution** — 4 files complete in ~1-2 min (vs 3-5 min sequential)
- **No `capture_output=True`** — uses file-based stdout/stderr to avoid Windows pipe deadlock with MCP child processes

---

## File Structure

```
D:/Gerrit Code Reviewer Agent tool/
├── server.py                 # HTTP server, API proxy, project management
├── ai_reviewer.py            # Orchestrator — parallel hermes workers
├── gerrit_mcp_server.py      # MCP server (JSON-RPC over stdio)
├── rules_engine.py           # 3-tier rule resolver
├── run.py                    # Process launcher with restart support
├── Start Dashboard.bat       # Windows launcher (checks deps, opens browser)
│
├── index.html                # Login page
├── projects.html             # Project list + clone/branch/index controls
├── changes.html              # Open changes browser
├── commits.html              # Commit detail + rules toolbar + review trigger
├── review.html               # Review results — diff viewer + inline comments
├── style.css                 # Dark theme (CSS custom properties)
│
├── rules/                    # Review rules (3-tier)
│   ├── _base/                # Always applied
│   │   ├── default.md        # Default review checklist
│   │   ├── clean-code-guard.md   # 23 clean code imperatives
│   │   └── ai-failure-modes.md   # 8 AI-specific failure patterns
│   ├── _lang/                # Language-specific (auto-matched by extension)
│   │   ├── java.md
│   │   ├── python.md
│   │   └── ts_js_tsx_jsx.md
│   └── <project-slug>/       # Project-specific (uploaded via UI)
│
├── projects/                 # Cloned Gerrit repos (gitignored)
│   └── <project-slug>/
│       ├── config.json       # git_url, branch, auto_analyze settings
│       └── repo/             # Shallow git clone (--depth=1)
│
├── temp/                     # Runtime files (gitignored)
│   ├── mcp_runtime.json      # Per-review MCP config (change_id, auth)
│   ├── prompt_*.txt          # Debug copy of prompts
│   └── hermes_*.txt          # Temp stdout/stderr (cleaned after review)
│
├── project_status.json       # Persisted project status (indexed, branch, etc.)
└── .gitignore
```

---

## How It Works

### 1. Authentication

- Login page validates credentials against Gerrit's `/a/accounts/self` REST API
- Credentials stored as Base64 `Basic` auth in browser `sessionStorage`
- All API requests include `Authorization: Basic ...` header
- For git operations (clone, fetch), credentials are embedded in the HTTPS URL

### 2. Project Lifecycle

```
Clone → Branch → Index → Review
  │        │        │        │
  │        │        │        └─ Workers use codebase-memory-mcp for context
  │        │        └─ codebase-memory-mcp indexes source into knowledge graph
  │        └─ git checkout + shallow fetch if branch not local
  └─ git clone --depth=1 (default branch)
```

**Auto-detection** — `_detect_index_target()` identifies project type and narrows indexing:

| Type | Detection | Index Target |
|------|-----------|-------------|
| Android | `app/src/main/java` | `app/src/main` |
| Spring Boot | `src/main/java` | `src/main` |
| Laravel | `artisan` | `app`, `routes`, `resources` |
| Node.js | `package.json` | `.` (root, excludes `node_modules`) |
| Python | `pyproject.toml` / `requirements.txt` | `.` |
| .NET | `*.csproj` | `.` (excludes `bin/obj`) |
| Flutter | `pubspec.yaml` | `lib` |
| Rust | `Cargo.toml` | `src` |
| Go | `go.mod` | `.` |
| Swift | `*.xcodeproj` | `Sources` |

### 3. AI Review (v3 Orchestrator)

Each hermes worker receives:
- **One file's diff** (2-5K chars, capped at 15K)
- **Project rules** (_base + _lang + project-specific)
- **MCP tools** for context (`gerrit_file_read`, `gerrit_code_search`)

Workers return JSON arrays:
```json
[
  {
    "file": "src/Main.java",
    "line": 42,
    "severity": "error",
    "comment": "Division by zero — input is not validated before parseInt",
    "existing_code": "int value = Integer.parseInt(input);\nSystem.out.println(100 / value);",
    "suggestion_code": "int value = Integer.parseInt(input);\nif (value == 0) throw new IllegalArgumentException(\"Cannot divide by zero\");\nSystem.out.println(100 / value);"
  }
]
```

### 4. JSON Parsing

The orchestrator extracts JSON from worker output using:
1. Direct `json.loads()` — if output is pure JSON
2. Markdown code fence extraction — `` ```json [...] ``` ``
3. Bracket-counting — finds all valid JSON arrays, returns the longest
4. Empty fallback — if no JSON found, returns `[]` (no prose pollution)

---

## Rules System

### 3-Tier Priority

```
rules/_base/          ← Always included (lowest priority)
  ├── default.md          Default review checklist
  ├── clean-code-guard.md 23 imperatives
  └── ai-failure-modes.md 8 AI failure patterns

rules/_lang/          ← Matched by file extension (medium priority)
  ├── java.md             *.java
  ├── python.md           *.py
  └── ts_js_tsx_jsx.md    *.ts, *.js, *.tsx, *.jsx

rules/<project-slug>/ ← Project-specific (highest priority)
  └── custom.md           Uploaded via dashboard UI
```

### Base Rules Summary

**`default.md`** — 5 categories: Correctness, Security, Performance, Maintainability, Error Handling

**`clean-code-guard.md`** — 23 imperatives:
- Functions: ≤20 lines, ≤4 args, names reveal intent
- SOLID: one actor per module, extension via new code
- DRY/KISS/YAGNI: correct DRY, complexity ceiling (cyclomatic ≤10)
- AI-Specific: no broad catch-all, verify imports, no hardcoded success

**`ai-failure-modes.md`** — 8 patterns LLMs systematically produce:
1. Broad error swallowing (`catch (Exception) → null`)
2. Hardcoded success returns (`return {"status": "ok"}`)
3. Hallucinated APIs (flagging real APIs as non-existent)
4. Copy-from-similar bugs (off-by-one from copy-paste)
5. Dead code (unreachable branches, unused imports)
6. Defensive guards for impossible cases
7. Premature abstraction (interface with one implementation)
8. Comment pollution (paraphrasing comments)

### Language Rules Summary

| Language | Key Checks |
|----------|-----------|
| **Java** | Typos in declarations, dead code, logic errors, N+1 queries, thread safety |
| **Python** | Mutable defaults, bare except, builtin shadowing, eval/exec, pickle |
| **TS/JS** | No `var`, strict equality, React hooks rules, async error handling, XSS |

### Managing Rules

- **Upload**: Commits page → Rules toolbar → upload `.md`/`.txt` files
- **Compress**: Auto-compressed after upload for token efficiency
- **Clear**: Rules toolbar → Clear button removes all project rules
- **Per-project**: Rules stored in `rules/<project-slug>/`

---

## Project Management

### Clone

```
POST /projects/clone/<slug>
Body: {"git_url": "https://...", "project": "p1737_..."}
```
- Shallow clone (`git clone --depth=1`) of default branch
- Saves `projects/<slug>/config.json`
- Runs in background thread

### Branch

```
POST /projects/checkout/<slug>
Body: {"branch": "dev-20260522"}
```
- Tries local checkout → fetch + create → shallow fetch fallback
- Saves branch to `config.json`
- Auto-triggers indexing after checkout

### Index

```
POST /projects/analyze/<slug>
```
- Runs `codebase-memory-mcp cli index_repository '{"repo_path":"..."}'`
- Indexes source code into knowledge graph
- Status persisted in `project_status.json`
- Workers use the knowledge graph for context during reviews

### Branch-Aware Reviews

When a review starts for change on branch X but the local clone is on branch Y:
1. Orchestrator detects the mismatch via Gerrit API
2. Auto-checks out the correct branch
3. Logs a warning that the codebase index may be stale
4. **User should re-index** for best results (Projects page → Analyze)

---

## Configuration

### Files

| File | Purpose | Persisted |
|------|---------|-----------|
| `project_status.json` | Project indexing status | ✅ Survives restart |
| `projects/<slug>/config.json` | Per-project git URL, branch, settings | ✅ |
| `temp/mcp_runtime.json` | Per-review MCP config (change_id, auth) | ❌ Recreated each review |

### Environment Variables

| Variable | Set By | Purpose |
|----------|--------|---------|
| `GERRIT_MCP_CONFIG` | `ai_reviewer.py` | Path to `mcp_runtime.json` for MCP server |
| `GERRIT_URL` | `server.py` (hardcoded) | Gerrit server URL |
| `GERRIT_MCP_CONFIG` | Hermes config | Points MCP server to runtime config |

### Hardcoded Values

| Setting | Value | Location |
|---------|-------|----------|
| Gerrit URL | `https://review2.bjitgroup.com:8443` | `server.py`, `ai_reviewer.py` |
| Dashboard port | `7474` | `server.py` |
| SSL verification | Disabled | `server.py`, `ai_reviewer.py` |
| Max workers | `4` | `ai_reviewer.py` |
| Worker timeout | `180s` | `ai_reviewer.py` |
| Diff cap per file | `15,000 chars` | `ai_reviewer.py` |
| Clone depth | `1` (shallow) | `server.py` |

---

## MCP Tools Reference

The `gerrit-review` MCP server provides 6 tools to hermes workers:

### `gerrit_get_diff`
Fetch the full unified diff of a Gerrit change.
- **Params**: `change_id` (optional, uses config default)
- **Returns**: Full patch text

### `gerrit_read_diff`
Read the diff of a specific file.
- **Params**: `file_path` (required)
- **Returns**: Unified diff for that file only

### `gerrit_file_read`
Read file content from local clone or Gerrit API.
- **Params**: `file_path` (required), `start_line`, `end_line` (optional)
- **Returns**: File contents with line numbers

### `gerrit_code_search`
Search for text patterns across the entire repository (when cloned) or changed files.
- **Params**: `search_text` (required), `file_patterns` (optional glob array)
- **Returns**: Matching lines with file:line format
- **Note**: Uses `grep -r` on local clone for full-repo search

### `gerrit_list_files`
List all files changed in a Gerrit revision.
- **Params**: `change_id` (optional)
- **Returns**: File paths with insert/delete counts

### `reload_config`
Reload runtime config from `mcp_runtime.json`.
- **Params**: none
- **Returns**: Current config values

---

## Dashboard Pages

### Login (`index.html`)
- Username + password form
- Validates against Gerrit `/a/accounts/self`
- Stores Base64 auth in `sessionStorage`

### Projects (`projects.html`)
- Lists all Gerrit projects
- Clone button → shallow clone in background
- Branch picker → `git ls-remote` for branch list
- Analyze button → codebase-memory-mcp indexing
- Status badges: Cloned, Indexed, Error

### Changes (`changes.html`)
- Lists all `status:open` changes grouped by project
- Search/filter by project name
- Shows change metadata: subject, author, branch, labels
- Click → navigates to review page

### Commits (`commits.html`)
- Shows commit detail: subject, author, date, message
- Changed files list with +/− stats
- Rules toolbar: upload, manage, compress rules
- "AI Review" button → triggers orchestrator
- Live elapsed timer during review

### Review (`review.html`)
- Side-by-side diff viewer (resizable sidebar with drag handle)
- AI comments with severity badges (error/warning/suggestion)
- Inline code suggestions (existing → suggested)
- Per-file review button in sidebar
- Project analysis prompt if not indexed

---

## Troubleshooting

### Review hangs / no output

**Cause**: Windows pipe deadlock with `capture_output=True`
**Fix**: Already fixed in v3 — uses file-based stdout/stderr

### Review returns empty array `[]`

**Possible causes**:
1. Change's branch differs from local clone → re-index on correct branch
2. Diff too large → capped at 15K per file
3. hermes returned prose instead of JSON → bracket-counting parser handles this

### `gerrit_code_search` returns "No matches found"

**Cause**: Searching only changed files (no local clone)
**Fix**: Clone the project first → full-repo search via `grep -r`

### MCP server connection failed

**Cause**: `codebase-memory-mcp.exe` not found or Windows pipe buffering
**Fix**: Ensure binary at `D:\tools\codebase-memory-mcp.exe` or on PATH. See `codebase-memory-mcp-windows` skill for pipe buffering workaround.

### `Start Dashboard.bat` shows "filename syntax incorrect"

**Cause**: Escaped quotes in batch file
**Fix**: Update to latest version — uses `cmd /C` wrapper with proper escaping

### Project not indexing

**Check**:
1. `project_status.json` — look for `error` field
2. `server_output.log` — look for `[Index]` lines
3. Binary exists: `where codebase-memory-mcp` or check `D:\tools\`

### Review takes too long

**Expected times**:
| Files | Workers | Time |
|-------|---------|------|
| 1-2 | 1-2 | 30-60s |
| 3-4 | 4 | 60-120s |
| 5-10 | 4 (batched) | 2-4 min |

If slower: check hermes cold start (~20s), MCP server connection, model response time.

---

## Development

### Adding Language Rules

1. Create `rules/_lang/<language>.md`
2. Rules auto-matched by file extension mapping in `rules_engine.py`
3. Supported extensions: `*.java`, `*.py`, `*.ts`, `*.js`, `*.tsx`, `*.jsx`, `*.kt`, `*.rs`, `*.c`, `*.cpp`, `*.cs`, `*.go`, `*.rb`, `*.php`, `*.swift`, `*.scala`

### Adding Project Rules

1. Create `rules/<project-slug>/` directory
2. Add `.md` or `.txt` files
3. Or use the dashboard: Commits page → Rules toolbar → Upload

### Modifying the MCP Server

The MCP server (`gerrit_mcp_server.py`) communicates via JSON-RPC 2.0 over stdio:
- Reads requests from stdin (one JSON per line)
- Writes responses to stdout
- Logs to stderr (captured in `mcp-stderr.log`)

### Modifying the Orchestrator

Key constants in `ai_reviewer.py`:
```python
MAX_WORKERS = 4          # Parallel hermes workers
WORKER_TIMEOUT = 180     # Seconds per worker
GERRIT_URL = "https://review2.bjitgroup.com:8443"
```

---

## License

Internal tool — BJIT Group.
