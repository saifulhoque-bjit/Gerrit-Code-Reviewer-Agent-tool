# Gerrit Reviewer v4

A local, async AI code-review dashboard for Gerrit. It fetches Gerrit changes directly, reviews changed files in a bounded parallel pool, streams findings live, and stores every completed review in SQLite.

The dashboard is designed for a deliberate workflow:

1. inspect a Gerrit change and its diff;
2. run an AI review;
3. read, edit, select, or dismiss findings;
4. optionally post selected findings to Gerrit as robot comments;
5. reopen the exact saved patchset later from Review History.

v4 replaces the v3 tool's in-memory review state, unauthenticated dashboard, unsafe TLS handling, and hardcoded credentials.

## Contents

- [Quick start](#quick-start)
- [Daily dashboard workflow](#daily-dashboard-workflow)
- [Review lifecycle](#review-lifecycle)
- [Saved reviews, reloads, and history](#saved-reviews-reloads-and-history)
- [Review comments and posting to Gerrit](#review-comments-and-posting-to-gerrit)
- [Rules and feedback analytics](#rules-and-feedback-analytics)
- [Review strategies and cost accounting](#review-strategies-and-cost-accounting)
- [Configuration](#configuration)
- [Authentication and security](#authentication-and-security)
- [Webhook automation](#webhook-automation)
- [API reference](#api-reference)
- [Architecture and persistence](#architecture-and-persistence)
- [Testing and troubleshooting](#testing-and-troubleshooting)

## Quick start

### Prerequisites

- Windows with `uv` installed
- Python 3.11 or later (managed by `uv`)
- Network access to the configured Gerrit instance
- A Gerrit account with an **HTTP password**
- For the default `hermes` strategy: Hermes Agent and its Gerrit/MCP integration available on `PATH`

### First launch

1. Double-click **`run.cmd`**.
2. On first launch it runs `uv sync --extra dev`, creates `.venv`, and copies `.env.example` to `.env`.
3. The browser opens `http://127.0.0.1:7474`.
4. Sign in with your Gerrit username and Gerrit HTTP password.

The login validates the credentials against Gerrit. The browser receives an opaque, HttpOnly local session cookie; it never receives the password or Gerrit authorization header.

### Configure secrets when needed

Interactive dashboard login does not require Gerrit credentials in `.env`. Fill in `.env` only for script clients, webhook-only runs, or the direct model strategy:

```dotenv
# Optional for scripts and webhooks. Prefer one complete option.
GERRIT_AUTH=
GERRIT_USER=
GERRIT_HTTP_PASSWORD=

# Required for X-Auth-Token API access outside an interactive browser session.
API_TOKEN=replace-with-a-long-random-value

# Required only when [review].strategy = "direct".
MODEL_API_KEY=

# Required only for Gerrit webhook automation.
WEBHOOK_SECRET=
```

Never commit `.env`. It is intentionally ignored by Git.

## Daily dashboard workflow

### 1. Open a change

On the landing page, either:

- enter a Gerrit change number or ID and click **Review**; or
- choose a project, click **Open changes**, then click **Inspect** beside a change.

**Inspect** opens a dedicated change-detail URL. The workspace shows the changed-file list and the selected file's diff. Switch between **Unified** and **Side by side** views as needed.

### 2. Inspect the diff before review

Use the file list to load each diff directly from Gerrit. This is a read-only inspection step: no review is started and no data is posted to Gerrit.

### 3. Start a review

Click **Review** on the landing page or **Review change** inside the workspace.

The UI immediately:

- subscribes to the review's Server-Sent Events (SSE) stream;
- shows a loader and live file-progress indicator;
- adds the change identity to the browser URL;
- renders findings as each reviewed file finishes.

The Review button is re-enabled on completion, start failure, worker error, or an SSE disconnection.

### 4. Read findings

Findings are grouped by file and labelled by severity:

| Severity | Meaning |
|---|---|
| `error` | Bug, security issue, crash risk, or data-loss risk |
| `warning` | Potential bug, performance issue, or important maintainability concern |
| `suggestion` | Small actionable improvement |

Click a finding card to open the matching file and pulse the relevant diff line. Code context and suggested replacement blocks are shown when the worker provides them.

### 5. Decide what to do with each finding

For each persisted, unposted finding you can:

- **Edit** the text before posting it;
- uncheck it to exclude it from the current Gerrit post operation;
- mark it **Useful** or **Dismiss** to provide local rule-effectiveness feedback;
- leave it unposted as a stored internal review note.

Feedback is local to Gerrit Reviewer. It never changes a Gerrit comment or vote.

### 6. Post selected findings only when ready

The **Post to Gerrit** bar appears when the current review has unposted persisted comments. Use **Select all** or **Deselect all**, then post the selected subset.

Posting is an explicit user action. Starting a review never writes comments or votes to Gerrit.

## Review lifecycle

The following is the full processing path for one review request.

```text
Dashboard
  │ POST /review/{change_id}
  ▼
FastAPI background task
  │ validate local session or X-Auth-Token
  ▼
Gerrit REST
  │ change detail → target branch + current patchset
  │ changed files → current patchset or delta since prior review
  │ per-file unified diffs
  ▼
Review engine
  │ resolve base/language/project rule files
  │ write per-review MCP runtime context
  │ run a bounded async file-worker pool
  ▼
Workers
  │ Hermes/MCP subprocess OR direct model API
  │ strict JSON findings
  ▼
Engine
  │ deduplicate by file and line
  │ calculate usage/cost
  │ save review and comments to SQLite
  │ emit file and completion events through SSE
  ▼
Dashboard
  │ render comments, diff highlights, edit/select/feedback controls
```

### Detailed execution steps

1. **Resolve change metadata.** The engine fetches Gerrit's change detail, including the current numeric patchset and target branch.
2. **Create/reset the local review row.** A review is unique by `(change_id, patchset)`. Re-running the same patchset replaces only that patchset's old local findings.
3. **Determine the review scope.** If `[review].incremental = true` and an earlier patchset was completed locally, Gerrit receives that patchset as `base`; only files touched since then are reviewed. A first review processes the full current patchset.
4. **Fetch file diffs concurrently.** The engine fetches individual Gerrit diffs for files with real additions or deletions.
5. **Resolve rules.** The engine combines always-on base rules, language rules inferred from changed extensions, and optional project-specific overrides.
6. **Create branch-aware MCP context.** The runtime context includes the Gerrit URL, change ID, authorization header, and target branch so MCP lookups can resolve against the correct branch rather than the repository default.
7. **Review files in a bounded pool.** `max_workers` limits simultaneous workers. A worker failure produces no finding for that file rather than aborting the entire review.
8. **Stream progress.** The engine emits `started`, per-file, `done`, or `error` events to the SSE stream.
9. **Deduplicate and persist.** Findings with the same file and line are deduplicated, then review metadata, comments, token usage, and estimated cost are saved atomically to SQLite.
10. **Hydrate the final UI.** The completion event contains the authoritative persisted comment set, including database IDs used by edit, feedback, and selective-post operations.

## Saved reviews, reloads, and history

Completed reviews are durable. SQLite stores a review row plus its individual comment rows, including:

- change ID and patchset;
- project slug and target branch;
- completion status and any error;
- files reviewed;
- input/output token counts and cost;
- comment file, line, severity, body, code context, and suggested replacement;
- posted and feedback state.

### Reload behavior

When a review starts, the browser URL becomes:

```text
/?change=<change-id>
```

When it completes, the URL records the exact reviewed patchset:

```text
/?change=<change-id>&patchset=<patchset-number>
```

Reloading that URL opens the dedicated detail view and hydrates the saved comments from SQLite. It also requests the matching Gerrit patchset's file list and diffs, so the review comments remain aligned with the historical code rather than silently showing today's latest patchset.

### Review History page

Click **History** to open:

```text
/?page=history
```

The page lists saved review records with their change ID, patchset, project, branch, creation time, comment count, and file count. Selecting a row opens the saved patchset detail URL described above.

A history entry remains available after browser reloads and server restarts as long as `reviewer.db` is retained. Deleting that database permanently removes the local review history.

## Review comments and posting to Gerrit

### Local comment state

A generated comment is saved locally before it is ever sent to Gerrit. This lets reviewers safely edit wording, choose only relevant findings, and come back later.

| Action | Local effect | Gerrit effect |
|---|---|---|
| Review | Stores generated comments | None |
| Edit | Updates an unposted local comment | None |
| Useful / Dismiss | Records feedback value | None |
| Uncheck | Excludes it from this post action | None |
| Post selected | Marks successfully posted comments | Creates Gerrit robot comments |

Posted comments are locked locally to prevent accidental edits and are labelled **Posted** in the dashboard.

### Gerrit post process

When you click **Post to Gerrit**, the server:

1. loads the selected saved comments for the current reviewed patchset;
2. reads existing Gerrit robot comments;
3. skips comments whose `(file, line)` is already present on Gerrit;
4. builds Gerrit robot-comment payloads, including optional fix suggestions when `suggestion_code` exists;
5. optionally adds a configured vote;
6. posts the fresh subset to the Gerrit current revision;
7. marks only successfully sent local comment IDs as posted.

The Gerrit robot run ID is based on the change and patchset, for example `12345-ps7`.

### Optional auto-vote

Auto-voting is disabled by default:

```toml
[post]
auto_vote = false
vote_label = "Code-Review"
```

When enabled, a posted set containing an `error` sends `Code-Review: -1`; a set without an error sends `Code-Review: 0`. Enable this only when that behaviour matches the team's Gerrit policy.

## Rules and feedback analytics

### Rule resolution

Rules are Markdown files under `rules/` and are loaded by tier:

| Tier | Location | Applied when |
|---|---|---|
| Base | `rules/_base/` | Every review |
| Language | `rules/_lang/` | A changed file matches that language's extension |
| Project | `rules/<project-slug>/` | The request has a matching project slug |

`rules_dir` is resolved relative to the repository root, not the current working directory. This prevents rule loading from changing depending on how the service was launched.

The **Rules** page in the dashboard lists the resolved rule tree and lets authenticated users edit Markdown rule files. Rule paths are restricted to `.md` files inside `rules_dir`; traversal and absolute-path requests are rejected.

### Feedback analytics

Use **Useful** or **Dismiss** on any saved comment. The **Analytics** page aggregates those local feedback signals by severity. Use it to identify review noise and refine rules; it does not train an external model automatically.

## Review strategies and cost accounting

Set `[review].strategy` in `config.toml`.

### `hermes` — default

```toml
[review]
strategy = "hermes"
```

Each changed file runs through a focused `hermes -z` subprocess with the `gerrit-review` skill and file-referenced rules. It can use MCP tools to inspect surrounding code and repository context.

On Windows, stdout and stderr are redirected to temporary files instead of `capture_output=True`. This avoids a known pipe deadlock when Hermes starts MCP child processes.

Hermes mode estimates token use from character counts using the configured `[cost]` values. Treat this as a heuristic, not provider-reported usage.

### `direct` — OpenAI-compatible model API

```toml
[review]
strategy = "direct"

[model]
base_url = "https://api.xiaomimimo.com/v1"
name = "mimo-v2.5-pro"
```

Set `MODEL_API_KEY` in `.env` before using direct mode. It calls `<base_url>/chat/completions` directly and persists the provider's `prompt_tokens` and `completion_tokens` values. Costs use `[cost].input_per_1m_usd` and `[cost].output_per_1m_usd`.

Trade-off: direct mode has no MCP repository-tool access, while Hermes mode can use MCP context lookups.

## Configuration

`config.toml` contains non-secret configuration:

```toml
[gerrit]
url = "https://review2.bjitgroup.com:8443"
# Empty uses the operating-system trust store. Set an internal CA bundle if required.
ca_bundle = ""
timeout_seconds = 30

[server]
host = "127.0.0.1"
port = 7474

[review]
max_workers = 4
worker_timeout_seconds = 1800
diff_cap_chars = 8000
strategy = "hermes"
incremental = true
rules_dir = "rules"

[post]
auto_vote = false
vote_label = "Code-Review"

[cost]
input_per_1m_usd = 0.30
output_per_1m_usd = 0.30
chars_per_token = 4

[model]
base_url = "https://api.xiaomimimo.com/v1"
name = "mimo-v2.5-pro"
```

### Configuration guidance

- Keep `max_workers` conservative. More workers increase Gerrit, Hermes, and model load simultaneously.
- `worker_timeout_seconds = 1800` supports large changes and slow MCP initialization.
- Keep `incremental = true` for normal patchset updates. Set it to `false` when a full re-review of every file is required.
- Set `gerrit.ca_bundle` for private/internal certificate authorities; do not disable TLS verification.
- Set cost values to the selected model's real prices if cost reporting matters.

## Authentication and security

### Browser dashboard

- `POST /auth/login` checks Gerrit HTTP credentials server-side.
- A successful login creates an in-memory session and sends an HttpOnly, `SameSite=Lax` cookie.
- Session lifetime is eight hours.
- Restarting the service clears active login sessions but does **not** delete saved reviews in SQLite.

### Script/API access

Non-browser clients use:

```http
X-Auth-Token: <API_TOKEN>
```

SSE clients that cannot set request headers use:

```text
/review/<change-id>/stream?token=<API_TOKEN>
```

Set a long random `API_TOKEN`; the default `change-me` deliberately refuses authenticated API access.

### Data boundaries

- Gerrit credentials from interactive login stay only in server memory.
- `.env` is not committed.
- The local SQLite database stores review data, not Gerrit passwords.
- TLS verification uses system trust by default or the configured CA bundle.
- Posting to Gerrit is opt-in from the dashboard.

## Webhook automation

Use Gerrit's `patchset-created` event to queue local reviews automatically.

1. Set `WEBHOOK_SECRET` in `.env`.
2. Configure Gerrit to `POST` JSON to:

```text
http://<reviewer-host>:7474/hooks/gerrit
X-Webhook-Secret: <WEBHOOK_SECRET>
```

3. Provide Gerrit authorization through `GERRIT_AUTH` or `GERRIT_USER` plus `GERRIT_HTTP_PASSWORD` in `.env`.

The endpoint accepts only the `patchset-created` event and returns `202 Accepted` after queuing the background review. It does not post review comments automatically.

Keep webhook traffic on a trusted network. The local server binds to `127.0.0.1` by default; expose it deliberately only if Gerrit must reach it over the network.

## API reference

Dashboard API routes accept the authenticated local session cookie. Scripts may use `X-Auth-Token` unless stated otherwise.

| Route | Purpose |
|---|---|
| `GET /health` | Service status and non-secret active strategy metadata |
| `POST /auth/login` | Validate Gerrit HTTP credentials and issue a local session |
| `GET /auth/status` | Return current browser-session state |
| `POST /auth/logout` | Clear the local browser session |
| `GET /gerrit/projects` | List visible Gerrit projects |
| `GET /gerrit/changes/open?project=&limit=` | List open Gerrit changes |
| `GET /gerrit/changes/{id}/files?patchset=` | List files for current or specified historical patchset |
| `GET /gerrit/changes/{id}/files/{path}/diff?patchset=` | Fetch a normalized unified diff for current or specified patchset |
| `POST /review/{id}?project_slug=` | Queue a review |
| `GET /review/{id}` | Load the most recent stored review |
| `GET /review/{id}?patchset=N` | Load a specific stored patchset review |
| `GET /review/{id}/stream?token=` | Receive live SSE review events |
| `POST /review/{id}/post` | Post all or selected stored comments to Gerrit |
| `PATCH /comments/{id}` | Edit one unposted local comment with `{ "comment": "..." }` |
| `POST /comments/{id}/feedback` | Set `{ "feedback": "useful" | "dismissed" | "" }` |
| `GET /reviews?limit=` | List saved reviews, newest first; limit is 1–200 |
| `GET /rules` | List rule files and metadata |
| `GET /rules/file?path=` | Read one validated rule file |
| `PUT /rules/file?path=` | Overwrite one validated rule file with `{ "content": "..." }` |
| `GET /rules/effectiveness` | Useful/dismissed totals by severity |
| `POST /hooks/gerrit` | Accept a secret-gated Gerrit webhook |

## Architecture and persistence

```text
                    ┌────────────────────────┐
                    │ Browser dashboard       │
                    │ queue / diff / history  │
                    └───────────┬────────────┘
                                │ authenticated HTTP + SSE
                    ┌───────────▼────────────┐
                    │ FastAPI application     │
                    │ sessions + event bus    │
                    └───────┬────────┬───────┘
                            │        │
                 Gerrit REST│        │SQLite
                            │        │
                 ┌──────────▼───┐ ┌──▼────────────────────────┐
                 │ change/files │ │ reviews, comments, posted, │
                 │ diffs/comments│ │ feedback, usage, history  │
                 └──────────────┘ └───────────────────────────┘
                            │
                    ┌────────────▼───────────┐
                    │ async engine            │
                    │ bounded worker pool     │
                    └─────┬───────────┬───────┘
                        │         │
               ┌────────▼─┐   ┌──▼──────────────────┐
               │ Hermes + │   │ Direct model API     │
               │ MCP      │   │ OpenAI-compatible    │
               └──────────┘   └─────────────────────┘
```

`reviewer.db` is created in the repository root by default. You may set `REVIEWER_DB` to an alternate database path. Preserve this file if review history must survive deployments or machine moves.

### No repository indexing in v4

v4 does **not** clone or index repositories and does not perform the v3 `indexed_branch` mismatch workflow. It uses live Gerrit diffs plus MCP context lookups. Incremental review is patchset-delta based, not repository indexing.

## Testing and troubleshooting

### Run the test suite

```bash
uv run pytest -q
```

The suite covers the engine, SQLite persistence, review history, historical patchset routes, posting and selective posting, auth, rules-path safety, SSE wiring, and dashboard structure markers.

### Common issues

| Symptom | Cause / action |
|---|---|
| Dashboard redirects to login | Login session expired or the server restarted. Sign in again; saved reviews remain in SQLite. |
| Reload shows no comments | Open the completed review's `?change=...&patchset=...` URL. If the database was deleted or changed via `REVIEWER_DB`, that history is unavailable. |
| History page is empty | No completed review exists in the active SQLite database. Run a review to completion first. |
| Historical diff does not load | Gerrit must retain and permit access to that patchset revision. Comments are still stored locally even if Gerrit no longer serves the diff. |
| Direct strategy will not start | Set `MODEL_API_KEY` and verify `[model]` is OpenAI-compatible. |
| Hermes workers return no findings | Confirm `hermes` is on `PATH`, the `gerrit-review` skill/MCP tools are available, and the Gerrit/MCP runtime context can access the change. |
| Internal Gerrit TLS fails | Set `[gerrit].ca_bundle` to the internal CA bundle. Do not disable certificate validation. |
| A browser shows stale UI after an update | Restart `run.cmd` and reload. Dashboard HTML sends no-cache headers. |
| Webhook returns 401 | Ensure the header is exactly `X-Webhook-Secret` and matches `WEBHOOK_SECRET`. |
| Post to Gerrit is unavailable | The review must be completed, its comments must be unposted, and the current session must have Gerrit authorization. |

## Development notes

- Source lives in `src/reviewer/`.
- Tests live in `tests/`.
- The dashboard is a framework-free SPA in `src/reviewer/static/index.html`.
- Runtime artifacts such as `.env`, `reviewer.db`, and temporary MCP runtime files must remain out of source control.
- Do not commit Gerrit HTTP passwords, authorization headers, API tokens, model keys, or webhook secrets.
