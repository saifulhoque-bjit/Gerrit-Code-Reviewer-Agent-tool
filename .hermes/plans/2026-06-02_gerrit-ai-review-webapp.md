# Gerrit AI Code Review Web App — Implementation Plan

## Goal
Build a local web app that:
1. Logs into BJIT's Gerrit (`https://review2.bjitgroup.com:8443`)
2. Shows pending changes (open reviews) organized by project
3. Lets you browse changed files and see code diffs
4. Has an "AI Review" button that invokes Hermes to analyze the diff and returns structured review comments
5. Lets you post those comments back to Gerrit

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | **Python + FastAPI** | Lightweight, easy async, good for proxying Gerrit REST API |
| Frontend | **Single-page HTML + Vanilla JS** | No build step, easy to ship, runs locally |
| Gerrit API | **Gerrit REST API** (HTTP Basic Auth) | Native, no OAuth needed for internal use |
| AI Review | **Hermes CLI** (`hermes run`) | Invokes Hermes to review diff, returns JSON comments |
| Temp storage | **Local temp `.txt` files** | Stores diff before passing to Hermes |

---

## Architecture

```
Browser (HTML/JS)
    │
    ▼
FastAPI Backend (localhost:8080)
    │   ├── /api/login          → Validates credentials against Gerrit
    │   ├── /api/changes        → Lists open changes per project
    │   ├── /api/change/{id}    → Gets file list for a change
    │   ├── /api/diff/{id}/{file}→ Gets diff for a specific file
    │   ├── /api/review/{id}    → Triggers AI review (writes temp file, calls Hermes)
    │   └── /api/post/{id}      → Posts comments to Gerrit
    │
    ▼
Gerrit REST API (https://review2.bjitgroup.com:8443)
```

---

## Screen Flow

```
[Login Screen]
  → Enter username + password
  → POST to /api/login (validates via Gerrit REST)
  → On success: redirect to Projects screen

[Projects + Changes Screen]
  → Lists all projects with OPEN changes
  → Expandable project accordion
  → Each row: commit subject, owner, date, # of files
  → Click row → go to Change Detail Screen

[Change Detail Screen]
  → Shows commit message, metadata
  → Lists changed files with +/- stats
  → Click file → shows diff inline (side-by-side or unified)
  → [AI Review] button at top → triggers review flow

[AI Review Flow]
  → Button press → backend writes diff to temp file
  → Backend calls: hermes run "Review this code diff..."
  → Loading spinner shown while Hermes processes
  → Results returned as structured JSON:
    { file: "...", line: N, severity: "error|warning|info", comment: "..." }
  → Comments displayed in organized panel grouped by file

[Post Comments]
  → Review the AI comments in the UI
  → Uncheck any you don't want
  → [Post to Gerrit] button → calls /api/post/{id}
  → Posts as inline comments via Gerrit REST API
```

---

## File Structure

```
D:\Gerrit Code Reviewer Agent tool\
├── backend\
│   ├── main.py              # FastAPI app, all routes
│   ├── gerrit_client.py     # Gerrit REST API wrapper
│   ├── ai_reviewer.py       # Writes temp diff, calls Hermes, parses output
│   └── requirements.txt     # fastapi, uvicorn, httpx, python-dotenv
├── frontend\
│   ├── index.html           # Login page
│   ├── changes.html         # Projects + changes list
│   ├── review.html          # Change detail + diff + AI review panel
│   └── style.css            # Clean dark theme styles
├── temp\
│   └── (auto-created diff files go here)
├── .env                     # GERRIT_URL, saved session (optional)
└── start.bat                # One-click launcher: starts FastAPI server + opens browser
```

---

## Step-by-Step Build Plan

### Phase 1 — Backend scaffold
1. Create `requirements.txt` and install deps (`fastapi`, `uvicorn`, `httpx`, `python-dotenv`)
2. Build `gerrit_client.py`:
   - `login(user, password)` → test auth via `/a/accounts/self`
   - `get_open_changes()` → `/a/changes/?q=status:open&o=DETAILED_LABELS&o=CURRENT_REVISION`
   - `get_change_files(change_id)` → `/a/changes/{id}/revisions/current/files`
   - `get_file_diff(change_id, file_path)` → `/a/changes/{id}/revisions/current/files/{file}/diff`
   - `post_review(change_id, comments_json)` → `POST /a/changes/{id}/revisions/current/review`
3. Build `ai_reviewer.py`:
   - `write_diff_to_temp(diff_text, change_id)` → writes to `temp/{change_id}.txt`
   - `invoke_hermes(temp_file_path)` → runs `hermes run --no-interactive < prompt.txt`
   - `parse_hermes_output(raw_output)` → extracts structured comments
4. Build `main.py` with all FastAPI routes

### Phase 2 — Frontend
5. `index.html` — Login form, sends credentials to `/api/login`, stores session token
6. `changes.html` — Fetches `/api/changes`, renders project accordion with change rows
7. `review.html` — Shows file list, renders diffs, AI review button + comment panel
8. `style.css` — Dark theme, clean layout, diff syntax highlighting

### Phase 3 — Integration & Polish
9. Wire AI Review button → loading state → display results
10. Post Comments flow with checkboxes
11. `start.bat` launcher
12. Test end-to-end

---

## Hermes Invocation Design

The `ai_reviewer.py` will call Hermes like this:

```python
import subprocess

prompt = f"""
You are a senior software engineer doing a code review.
Review the following code diff and return your findings as a JSON array.

Each item must follow this exact format:
[
  {{
    "file": "path/to/file.java",
    "line": 42,
    "severity": "error" | "warning" | "suggestion",
    "comment": "Short clear comment explaining the issue"
  }}
]

Only return valid JSON. No prose before or after.

DIFF:
{diff_content}
"""

result = subprocess.run(
    ["hermes", "run", "--no-interactive", prompt],
    capture_output=True, text=True, timeout=120
)
```

---

## Suggested Skills to Install

You may want these skills available during build:

| Skill | Why |
|---|---|
| `karpathy-guidelines` | Already available — reduces common LLM coding mistakes during build |
| `systematic-debugging` | Already available — helps if backend/frontend wiring breaks |
| `browser-dashboard-internal-api` | **Recommended install** — directly covers building browser dashboards for internal APIs like Gerrit |

> 💡 **Suggestion:** Install `browser-dashboard-internal-api` skill before we start — it has proven patterns for building exactly this kind of internal tool dashboard.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Gerrit uses digest/cookie auth not basic | Test `/a/accounts/self` with HTTP Basic first; fallback to cookie session |
| CORS issues hitting Gerrit from browser | All Gerrit calls go through FastAPI backend (not browser-direct) |
| Hermes CLI not on PATH | Detect path at startup, fallback to full path |
| Large diffs timeout Hermes | Chunk diff by file, review file by file |
| SSL cert issues on internal Gerrit | Use `verify=False` in httpx for internal server |

---

## Estimated Effort

| Phase | Estimated Time |
|---|---|
| Phase 1 (Backend) | ~45 min |
| Phase 2 (Frontend) | ~45 min |
| Phase 3 (Integration) | ~20 min |
| **Total** | **~2 hours** |

---
*Plan saved: 2026-06-02 | Project: Gerrit AI Code Review Web App*
