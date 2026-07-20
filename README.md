# Gerrit Reviewer v4

Local, async AI review for Gerrit: a FastAPI dashboard, durable SQLite history,
and a bounded per-file worker pool. It replaces v3's in-memory orchestration,
unsafe TLS, unauthenticated API, and hardcoded credentials.

## Start

1. Double-click **`run.cmd`**. On first run it installs the locked dependencies,
   copies `.env.example` to `.env`, starts `http://127.0.0.1:7474`, and opens the login page.
2. Sign in with your Gerrit username and **HTTP password**. The reviewer validates
   it against Gerrit and creates an HttpOnly local session automatically.

`API_TOKEN` plus `GERRIT_AUTH` or `GERRIT_USER`/`GERRIT_HTTP_PASSWORD` remain
supported for scripts and webhook-only deployments, but are not required for
interactive dashboard login.

`config.toml` is non-secret configuration. `.env` is ignored by Git.

## Review modes

`[review].strategy = "hermes"` is the default. It starts a focused Hermes/MCP
worker per changed file (up to `max_workers`).

Set `[review].strategy = "direct"` to call the OpenAI-compatible endpoint in
`[model]` directly. Put `MODEL_API_KEY` in `.env`. Direct mode persists the
provider's actual prompt/completion token counts and calculates cost from the
configured `[cost]` prices.

## Review flow

1. The signed-in user starts a review for a Gerrit change ID.
2. The engine fetches the change detail, current patchset, changed-file list,
   and per-file diffs directly from Gerrit.
3. With `review.incremental = true`, it finds the last completed patchset in
   SQLite and asks Gerrit for the delta since that patchset. A first review uses
   the complete current patchset.
4. It resolves base, language-specific, and optional project-specific rules.
5. A bounded async pool reviews files in parallel: the default `hermes` mode
   invokes a Hermes/MCP worker per file; `direct` calls the configured model API.
6. Findings stream to the dashboard over SSE, are deduplicated by file and line,
   then persist to SQLite. Users can optionally post completed findings to Gerrit.

### No repository indexing in v4

v4 does **not** clone or index repositories and has no indexed-branch mismatch
check. It uses live Gerrit diffs plus MCP context lookups. Incremental review is
patchset-based; it is not codebase indexing.

## API

Dashboard routes accept the HttpOnly login session. Script clients can use
`X-Auth-Token: <API_TOKEN>`; SSE script clients pass `?token=<API_TOKEN>`.

| Route | Purpose |
|---|---|
| `POST /review/{change_id}` | Start a review (`project_slug` optional query parameter) |
| `GET /review/{change_id}` | Latest persisted review for a change |
| `GET /review/{change_id}/stream?token=...` | Live SSE progress |
| `POST /review/{change_id}/post` | Post completed comments as Gerrit robot comments |
| `GET /reviews` | Latest review history (optional `limit`, max 200) |
| `POST /comments/{id}/feedback` | JSON `{ "feedback": "useful" | "dismissed" | "" }` |
| `GET /rules/effectiveness` | Useful/dismissed feedback totals by severity |
| `POST /hooks/gerrit` | Queue a Gerrit `patchset-created` webhook |
| `POST /auth/login` | Validate Gerrit HTTP credentials and set a local session |
| `POST /auth/logout` | Clear the local session |

The dashboard has a **History** button and **Useful/Dismiss** controls for
stored comments. Feedback is local reviewer data; it never modifies Gerrit.

## Gerrit webhook

Set a high-entropy `WEBHOOK_SECRET` in `.env`. Configure Gerrit to POST its
`patchset-created` JSON event to:

```text
http://<reviewer-host>:7474/hooks/gerrit
X-Webhook-Secret: <WEBHOOK_SECRET>
```

The endpoint only accepts the `patchset-created` event type and returns `202`
after it queues the review. Keep the reviewer on a trusted network; this
endpoint is deliberately separate from the dashboard's API token because
Gerrit sends it.

## Verify

```bash
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q
```

## Architecture

```text
Gerrit REST → engine (incremental patchset delta) → per-file workers
       live diffs + MCP context lookups                 └→ SQLite history
Dashboard ← SSE progress / review history / local feedback
Completed review → Gerrit robot comments + optional one-click fix suggestions
```

## Safety and behavior

- Interactive Gerrit credentials are validated server-side and retained only in
  the process-memory session. The browser receives an opaque HttpOnly cookie,
  never a Basic-auth token or password. `.env` secrets are not stored in
  `config.toml` or source.
- TLS verifies with the system trust store by default; set `gerrit.ca_bundle`
  for an internal CA. Verification is never disabled.
- Re-reviewing a patchset replaces only that patchset's stored comments.
- Incremental review uses the last completed patchset as Gerrit's `base`, so it
  reviews only files changed since that patchset.
- Posting deduplicates against existing Gerrit robot comments by file and line.
  Auto-voting is opt-in in `[post]`.
