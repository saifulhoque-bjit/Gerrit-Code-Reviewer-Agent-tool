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
│  3. Pre-fetches architecture context from codebase-memory-mcp        │
│  4. Spawns N parallel hermes workers (max 4)                         │
│  5. Streams partial results to UI as workers complete                 │
│  6. Aggregates + deduplicates JSON results                           │
├────────┬────────┬────────┬───────────────────────────────────────────┤
│Worker 1│Worker 2│Worker 3│Worker 4        hermes -z (parallel)       │
│file A  │file B  │file C  │file D                                      │
├────────┴────────┴────────┴───────────────────────────────────────────┤
│              gerrit_mcp_server.py (MCP over stdio)                    │
│  gerrit_get_diff · gerrit_read_diff · gerrit_file_read               │
│  gerrit_code_search · gerrit_list_files · reload_config               │
├──────────────────────────────────────────────────────────────────────┤
│         codebase-memory-mcp (MCP via mcp_proxy.py)                   │
│  get_architecture · search_graph · trace_path · detect_changes        │
│  get_code_snippet · query_graph · search_code · manage_adr            │
└──────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

| Tool | Required | Purpose |
|------|----------|---------|
| **Python 3.11+** | Yes | Runs the dashboard server |
| **Hermes Agent** | Yes | AI model runner ([install](https://hermes-agent.nousresearch.com)) |
| **codebase-memory-mcp** | Optional | For codebase indexing ([releases](https://github.com/DeusData/codebase-memory-mcp/releases)) |

### Launch

```bash
# Windows — double-click or run:
Start Dashboard.bat

# Or manually:
python server.py
# Then open http://localhost:7474
```

The batch file auto-checks dependencies and installs `codebase-memory-mcp` if missing.

### First Review

1. **Login** — enter your Gerrit username + HTTP password
2. **Projects** — clone a project, pick a branch, click "Analyze"
3. **Commits** — browse open changes, click "🤖 Review Commit"
4. **Review** — watch comments stream in, select which to post to Gerrit

---

## Architecture

### File Structure

```
├── server.py                 # HTTP server, API proxy, project management
├── ai_reviewer.py            # Orchestrator: parallel worker management
├── gerrit_mcp_server.py      # MCP server: 6 Gerrit tools (stdio)
├── rules_engine.py           # 3-tier rule resolver (base + lang + project)
├── run.py                    # Process launcher with auto-restart
├── Start Dashboard.bat       # Windows launcher with dependency checks
├── tools/
│   └── mcp_proxy.py          # MCP proxy (fixes pipe buffering on Windows)
├── rules/
│   ├── _base/                # Always-on rules (3 files)
│   ├── _lang/                # Language-specific rules (11 languages)
│   └── <project>/            # Project-specific rules (user-managed)
├── projects/                 # Cloned Gerrit repos
├── temp/                     # Runtime files (auto-cleaned)
├── index.html                # Login page
├── projects.html             # Project management
├── changes.html              # Open changes browser
├── commits.html              # Commit detail + rules toolbar
├── review.html               # Review results + diff viewer
└── style.css                 # Dark theme CSS
```

### Key Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Login page |
| GET | `/projects.html` | Project management |
| GET | `/changes.html` | Open changes browser |
| GET | `/commits.html` | Commit detail + AI review trigger |
| GET | `/review.html` | Review results + diff viewer |
| GET | `/api/*` | Proxy → Gerrit REST API |
| GET | `/ai-review/status/<key>` | Poll review status + partial results |
| POST | `/ai-review/start/<key>` | Start AI review |
| POST | `/ai-review/proceed-anyway/<key>` | Skip branch mismatch warning |
| POST | `/ai-review/reindex-and-retry/<key>` | Re-index + retry review |
| POST | `/projects/clone/<slug>` | Clone a Gerrit project |
| POST | `/projects/checkout/<slug>` | Switch branch + index |
| POST | `/projects/analyze/<slug>` | Run codebase indexing |
| GET/POST | `/rules/*` | Manage review rules |

---

## How It Works

### Review Flow

```
1. User clicks "🤖 Review Commit"
2. Frontend POSTs to /ai-review/start/<poll_key>
3. server.py spawns ai_reviewer.review() in background thread
4. Orchestrator:
   a. Writes MCP config (change_id, auth, project_dir)
   b. Resolves rules file paths (base + lang + project)
   c. Fetches file list + per-file diffs via Gerrit REST API
   d. Ensures correct branch checkout
   e. Pre-fetches architecture context from codebase-memory-mcp
   f. Spawns N parallel hermes workers (max 4)
5. Each worker:
   a. Reads rules files via read_file tool
   b. Calls get_architecture, search_graph, trace_path
   c. Analyzes diff with full codebase context
   d. Returns JSON array of findings
6. Orchestrator aggregates + deduplicates results
7. Frontend polls status → streams partial results → shows final comments
8. User selects comments → clicks "📬 Post Selected" → posts to Gerrit
```

### Branch Mismatch Handling

When a change targets a branch different from the indexed branch:

1. **Detection** — orchestrator checks indexed branch vs target branch
2. **Dialog** — custom modal: "Re-index & Retry" or "Skip & Review Anyway"
3. **Auto-checkout** — fetches + checks out the correct branch (handles shallow clones)
4. **Re-index** — runs codebase-memory-mcp indexing on the new branch
5. **Retry** — restarts the review with `force=True`

### Streaming Results

Comments appear in the UI **as each worker finishes** — no waiting for all files:

```
Worker 1 finishes → 3 comments → UI shows them + "📬 Post Selected" appears
Worker 2 finishes → 1 comment  → UI appends it
Worker 3 finishes → 0 comments → (no change)
Worker 4 finishes → 2 comments → UI shows 6 total
```

---

## Rules System

### 3-Tier Priority

| Tier | Directory | Purpose | Example |
|------|-----------|---------|---------|
| **Base** | `rules/_base/` | Always applied | AI failure modes, clean code guard, default checklist |
| **Language** | `rules/_lang/` | Matched by file extension | Swift, C#, Kotlin, Java, Python, TS/JS, Go, Rust, C/C++, PHP, Ruby |
| **Project** | `rules/<slug>/` | Project-specific overrides | Custom rules for a specific codebase |

### Supported Languages (11)

| Language | File | Key Rules |
|----------|------|-----------|
| Swift | `swift.md` | Force unwrap, retain cycles, MainActor, SwiftUI |
| C# | `csharp.md` | IDisposable, async/await, Blazor lifecycle, EF Core N+1 |
| Kotlin | `kotlin.md` | Null safety, coroutines, Flow, Android lifecycle |
| Java | `java.md` | Thread safety, N+1 queries, boundary errors |
| Python | `python.md` | Mutable defaults, bare except, type hints |
| TS/JS | `ts_js_tsx_jsx.md` | var usage, strict equality, React hooks, async |
| Go | `go.md` | Unchecked errors, goroutine leaks, race conditions |
| Rust | `rust.md` | unwrap() in prod, Arc<Mutex> deadlocks, unsafe |
| C/C++ | `c_cpp.md` | Buffer overflow, use-after-free, RAII, smart pointers |
| PHP | `php.md` | SQL injection, XSS, type juggling |
| Ruby | `ruby.md` | Mass assignment, YAML.load, eval dangers |

### Managing Rules

- **Dashboard** — Commits page → Rules toolbar → Upload `.md`/`.txt` files
- **Manual** — Place files in `rules/<project-slug>/`
- **Compressed** — Auto-generated after upload for faster loading

---

## MCP Tools

### Gerrit Tools (6)

| Tool | Purpose |
|------|---------|
| `gerrit_get_diff` | Full unified diff of a change |
| `gerrit_list_files` | Changed files with insert/delete counts |
| `gerrit_read_diff` | Per-file diff |
| `gerrit_file_read` | Read file content from local clone |
| `gerrit_code_search` | Search across entire repository |
| `reload_config` | Reload MCP runtime config |

### Codebase-Memory Tools (14)

| Tool | Purpose |
|------|---------|
| `get_architecture` | Project structure, packages, layers, hotspots |
| `search_graph` | Find functions/classes/routes by name or pattern |
| `trace_path` | Trace call chains (callers/callees/data flow) |
| `detect_changes` | Impact analysis of code changes |
| `get_code_snippet` | Read source for a specific function/class |
| `query_graph` | Cypher queries against the knowledge graph |
| `search_code` | Graph-augmented code search |
| `list_projects` | List all indexed projects |
| `index_status` | Check indexing status |
| `manage_adr` | Architecture Decision Records |
| + 4 more | Schema, deletion, trace ingestion |

---

## Configuration

### Files

| File | Purpose |
|------|---------|
| `config.json` | Per-project: git URL, branch, auto-analyze |
| `project_status.json` | Persisted indexing status |
| `temp/mcp_runtime.json` | Per-review MCP config (auth, change_id) |
| `rules/_base/*.md` | Base review rules |
| `rules/_lang/*.md` | Language-specific rules |
| `rules/<slug>/*.md` | Project-specific rules |

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `GERRIT_MCP_CONFIG` | Path to MCP runtime config (set by orchestrator) |
| `LOCALAPPDATA` | Used to find codebase-memory-mcp binary |

### Binary Discovery

`codebase-memory-mcp` is found in this order:
1. `PATH` (global install via `install.ps1`)
2. `%LOCALAPPDATA%\Programs\codebase-memory-mcp\` (default install location)
3. `~/.local/bin/` (Unix-style install)

---

## Troubleshooting

### Review returns 0 comments

- **Cause**: Rules not matching, model capability, or branch mismatch
- **Fix**: Check `server_output.log` for worker output. Ensure project is indexed.

### WinError 206: Filename too long

- **Cause**: Prompt exceeds Windows 32K CLI limit
- **Fix**: Rules are referenced by file path (not embedded). If still hitting limits, reduce diff cap in `ai_reviewer.py`.

### codebase-memory-mcp not found

- **Cause**: Binary not on PATH or common locations
- **Fix**: Run `install.ps1` from the [releases page](https://github.com/DeusData/codebase-memory-mcp/releases), or place binary in `%LOCALAPPDATA%\Programs\codebase-memory-mcp\`.

### MCP connection timeout

- **Cause**: Pipe buffering on Windows
- **Fix**: The `tools/mcp_proxy.py` proxy handles this automatically. Ensure Hermes config points to the proxy.

### Branch checkout fails

- **Cause**: Shallow clone or deleted branch
- **Fix**: The system auto-fetches missing branches with `--depth=1`. If the branch was deleted in Gerrit, the review proceeds on the current branch.

---

## Development

### Adding a New Language

1. Create `rules/_lang/<language>.md` with numbered rules
2. Add file extension mapping in `rules_engine.py` → `LANG_MAP`
3. Rules auto-apply when files with matching extensions are reviewed

### Adding Project-Specific Rules

1. Create `rules/<project-slug>/` directory
2. Add `.md` or `.txt` rule files
3. Rules auto-merge with base + language rules (highest priority)

### Modifying MCP Tools

- **Gerrit tools**: Edit `gerrit_mcp_server.py` → add handler + register in `tools/list`
- **Codebase tools**: Use the [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) project

---

## License

Internal tool for BJIT. Built on [Hermes Agent](https://hermes-agent.nousresearch.com) by Nous Research.
