#!/usr/bin/env python3
"""
Gerrit AI Code Reviewer — Local Proxy Server
Serves the dashboard UI and proxies all /api/* requests to Gerrit.
Also handles /ai-review/* for Hermes AI review invocations.

Usage:
    python server.py
    Then open http://localhost:7474
"""

import os
import json
import re
import ssl
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import base64
import tempfile
import threading
import time
import sys

# ── Unbuffered stdout + tee to log file ────────────────────────
class _Tee:
    """Write to both stdout and a log file."""
    def __init__(self, log_path):
        self._stdout = sys.stdout
        self._file = open(log_path, "a", encoding="utf-8")
    def write(self, data):
        self._stdout.write(data)
        self._file.write(data)
        self._file.flush()
    def flush(self):
        self._stdout.flush()
        self._file.flush()

_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_output.log")
sys.stdout = _Tee(_LOG)
sys.stderr = _Tee(_LOG)
print(f"\n[Server] === Started at {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)

GERRIT_URL = "https://review2.bjitgroup.com:8443"
PORT = 7474
DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(DIR, "temp")
RULES_DIR = os.path.join(DIR, "rules")   # rules/<project_slug>/<file.txt>
PROJECTS_DIR = os.path.join(DIR, "projects")
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(RULES_DIR, exist_ok=True)
os.makedirs(PROJECTS_DIR, exist_ok=True)

# In-memory store for AI review results (keyed by change_id)
review_results = {}
review_status = {}  # "pending" | "done" | "error"

# Project analysis status (persisted to disk)
PROJECT_STATUS_FILE = os.path.join(DIR, "project_status.json")

def _load_project_status():
    try:
        if os.path.isfile(PROJECT_STATUS_FILE):
            with open(PROJECT_STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_project_status():
    try:
        with open(PROJECT_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(project_status, f, indent=2)
    except Exception:
        pass

project_status = _load_project_status()
# Reset transient flags on startup
for slug in project_status:
    project_status[slug]["indexing"] = False


def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def strip_xssi(text):
    if text.startswith(")]}'"):
        return text[5:]
    return text


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        msg = fmt % args
        # Suppress polling logs (status checks every 2s)
        if "ai-review/status/" in msg:
            return
        print(f"  {self.address_string()} {msg}")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, filepath):
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(data))
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def send_file(self, filepath, content_type):
        try:
            with open(filepath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(data))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_html(os.path.join(DIR, "index.html"))
        elif path == "/projects.html":
            self.send_html(os.path.join(DIR, "projects.html"))
        elif path == "/commits.html":
            self.send_html(os.path.join(DIR, "commits.html"))
        elif path == "/changes.html":
            self.send_html(os.path.join(DIR, "changes.html"))
        elif path == "/review.html":
            self.send_html(os.path.join(DIR, "review.html"))
        elif path == "/style.css":
            self.send_file(os.path.join(DIR, "style.css"), "text/css")
        elif path.startswith("/api/"):
            self._proxy(parsed, "GET")
        elif path.startswith("/ai-review/status/"):
            change_id = path.split("/")[-1]
            status = review_status.get(change_id, "not_started")
            result = review_results.get(change_id)
            error_msg = (result or {}).get("error", "") if status == "error" else ""
            self.send_json({"status": status, "result": result, "error": error_msg})
        elif path.startswith("/rules/list/"):
            slug = urllib.parse.unquote(path.split("/rules/list/")[-1])
            self._rules_list(slug)
        elif path.startswith("/rules/compress/"):
            slug = urllib.parse.unquote(path.split("/rules/compress/")[-1])
            self._rules_compress(slug)
        elif path.startswith("/rules/compress-status/"):
            slug = urllib.parse.unquote(path.split("/rules/compress-status/")[-1])
            slug = self._safe_slug(slug)
            compressed_path = os.path.join(RULES_DIR, slug, "_compressed.txt")
            self.send_json({"skill_ready": os.path.exists(compressed_path)})
        elif path.startswith("/projects/status/"):
            slug = urllib.parse.unquote(path.split("/projects/status/")[-1])
            self._project_status(slug)
        elif path == "/projects/list":
            self._project_list()
        elif path.startswith("/projects/branches/"):
            slug = urllib.parse.unquote(path.split("/projects/branches/")[-1])
            self._project_branches(slug)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            self._proxy(parsed, "POST")
        elif path.startswith("/ai-review/start/"):
            self._start_ai_review(path)
        elif path.startswith("/rules/upload"):
            self._rules_upload()
        elif path.startswith("/rules/clear/"):
            slug = urllib.parse.unquote(path.split("/rules/clear/")[-1])
            self._rules_clear(slug)
        elif path.startswith("/projects/clone/"):
            slug = urllib.parse.unquote(path.split("/projects/clone/")[-1])
            self._project_clone(slug)
        elif path.startswith("/projects/checkout/"):
            slug = urllib.parse.unquote(path.split("/projects/checkout/")[-1])
            self._project_checkout(slug)
        elif path.startswith("/projects/analyze/"):
            slug = urllib.parse.unquote(path.split("/projects/analyze/")[-1])
            self._project_analyze(slug)
        elif path.startswith("/projects/config/"):
            slug = urllib.parse.unquote(path.split("/projects/config/")[-1])
            self._project_config(slug)
        else:
            self.send_response(404)
            self.end_headers()

    def _proxy(self, parsed, method):
        upstream_path = parsed.path[4:]  # strip /api
        query = ("?" + parsed.query) if parsed.query else ""
        url = GERRIT_URL + upstream_path + query

        fwd_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        auth = self.headers.get("Authorization", "")
        if auth:
            fwd_headers["Authorization"] = auth

        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None

        req = urllib.request.Request(url, data=body, headers=fwd_headers, method=method)
        try:
            with urllib.request.urlopen(req, context=ssl_ctx(), timeout=30) as r:
                raw = r.read().decode("utf-8", errors="replace")
                raw = strip_xssi(raw)
                body_out = raw.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(body_out))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body_out)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            err_body = strip_xssi(err_body)
            self.send_json({"error": f"HTTP {e.code}: {e.reason}", "detail": err_body}, e.code)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def _start_ai_review(self, path):
        # Parse: /ai-review/start/<poll_key>
        # poll_key formats: "all_<change_id>" or "file_<change_id>_<filename>"
        parts = path.strip("/").split("/")
        poll_key = parts[-1] if len(parts) >= 3 else None

        # Extract actual Gerrit change ID from poll key
        # "all_198601" → "198601", "file_198601_foo" → "198601"
        gerrit_change_id = ""
        if poll_key:
            kp = poll_key.split("_")
            if len(kp) >= 2:
                gerrit_change_id = kp[1]  # second segment is the numeric change ID

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        auth = self.headers.get("Authorization", "")
        diff_text = payload.get("diff", "")
        filenames = payload.get("files", [])
        project_slug = payload.get("project_slug", "")

        if not poll_key or not diff_text:
            self.send_json({"error": "Missing change_id or diff"}, 400)
            return

        # Mark as pending and kick off background thread
        review_status[poll_key] = "pending"
        review_results[poll_key] = None
        self.send_json({"status": "started", "change_id": poll_key})

        def run_review():
            import time as _rt
            _rt0 = _rt.time()
            print(f"[AI Review] ▶ run_review START for {poll_key} (gerrit={gerrit_change_id}) at {time.strftime('%H:%M:%S')}", flush=True)
            try:
                comments, token_summary = do_ai_review(gerrit_change_id, diff_text, filenames, auth, project_slug)
                review_results[poll_key] = {"comments": comments, "token_summary": token_summary}
                review_status[poll_key] = "done"
                files_n = token_summary.get("files_reviewed", "?")
                workers_n = token_summary.get("workers_used", "?")
                elapsed = token_summary.get("elapsed_seconds", round(_rt.time()-_rt0))
                print(f"[AI Review] ✓ DONE for {poll_key} — {len(comments)} comments, {files_n} files, {workers_n} workers, {elapsed}s", flush=True)
            except Exception as ex:
                import traceback
                traceback.print_exc()
                review_results[poll_key] = {"error": str(ex)}
                review_status[poll_key] = "error"
                print(f"[AI Review] ✗ ERROR for {poll_key} after {_rt.time()-_rt0:.0f}s: {ex}", flush=True)

        t = threading.Thread(target=run_review, daemon=True)
        t.start()

    # ── Rules endpoints ───────────────────────────────────────────
    def _safe_slug(self, slug):
        """Strip path traversal attempts — only allow safe chars."""
        return re.sub(r"[^a-zA-Z0-9._-]", "_", slug) or "unknown"

    def _rules_list(self, slug):
        slug = self._safe_slug(slug)
        project_dir = os.path.join(RULES_DIR, slug)
        if not os.path.isdir(project_dir):
            self.send_json({"files": [], "skill_ready": False})
            return
        files = sorted(
            f for f in os.listdir(project_dir)
            if os.path.isfile(os.path.join(project_dir, f)) and not f.startswith("_")
        )
        compressed_path = os.path.join(project_dir, "_compressed.txt")
        skill_ready = os.path.exists(compressed_path)
        self.send_json({"files": files, "skill_ready": skill_ready})

    def _rules_upload(self):
        """Parse multipart/form-data using email package (cgi removed in Python 3.13)."""
        from email import message_from_bytes
        from email.policy import HTTP as HTTP_POLICY

        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length)

        # Build a fake email message so email.message can parse the multipart body
        fake_msg = f"Content-Type: {content_type}\r\n\r\n".encode() + raw_body
        msg = message_from_bytes(fake_msg)

        slug = "unknown"
        saved = 0
        project_dir = None

        for part in msg.walk():
            disp = part.get("Content-Disposition", "")
            if not disp:
                continue

            # Extract name= from Content-Disposition
            name = None
            filename = None
            for segment in disp.split(";"):
                segment = segment.strip()
                if segment.startswith("name="):
                    name = segment[5:].strip('"')
                elif segment.startswith("filename="):
                    filename = segment[9:].strip('"')

            if name == "project_slug" and not filename:
                slug = part.get_payload(decode=True).decode("utf-8", errors="replace").strip()
                slug = re.sub(r"[^a-zA-Z0-9._-]", "_", slug) or "unknown"
                project_dir = os.path.join(RULES_DIR, slug)
                os.makedirs(project_dir, exist_ok=True)

        # Second pass: save files (slug may have been set in first pass)
        if not project_dir:
            slug = self._safe_slug(slug)
            project_dir = os.path.join(RULES_DIR, slug)
            os.makedirs(project_dir, exist_ok=True)

        for part in msg.walk():
            disp = part.get("Content-Disposition", "")
            if not disp:
                continue
            name = None
            filename = None
            for segment in disp.split(";"):
                segment = segment.strip()
                if segment.startswith("name="):
                    name = segment[5:].strip('"')
                elif segment.startswith("filename="):
                    filename = segment[9:].strip('"')

            if name == "files" and filename:
                safe_name = os.path.basename(filename).replace(" ", "_")
                dest = os.path.join(project_dir, safe_name)
                with open(dest, "wb") as f:
                    f.write(part.get_payload(decode=True))
                saved += 1
                print(f"[Rules] Saved {safe_name} for project slug '{slug}'")

        self.send_json({"saved": saved, "project_slug": slug})

        # Trigger one-time compression + skill creation in background
        if saved > 0:
            def _compress():
                import ai_reviewer
                ai_reviewer.compress_and_save_rules(slug)
            threading.Thread(target=_compress, daemon=True).start()
            print(f"[Rules] Queued compression job for '{slug}'")

    def _rules_compress(self, slug):
        """Trigger manual re-compression for a project's rules."""
        slug = self._safe_slug(slug)
        def _run():
            import ai_reviewer
            ai_reviewer.compress_and_save_rules(slug)
        threading.Thread(target=_run, daemon=True).start()
        self.send_json({"status": "compression_started", "project_slug": slug})

    def _rules_clear(self, slug):
        slug = self._safe_slug(slug)
        project_dir = os.path.join(RULES_DIR, slug)
        cleared = 0
        if os.path.isdir(project_dir):
            for f in os.listdir(project_dir):
                fp = os.path.join(project_dir, f)
                if os.path.isfile(fp):
                    os.remove(fp)
                    cleared += 1
        self.send_json({"cleared": cleared})

    # ── Project management endpoints ─────────────────────────────
    def _project_status(self, slug):
        """Get project indexing status."""
        slug = self._safe_slug(slug)
        project_dir = os.path.join(PROJECTS_DIR, slug)
        repo_dir = os.path.join(project_dir, "repo")
        config_path = os.path.join(project_dir, "config.json")
        
        config = {}
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
            except Exception:
                pass
        
        self.send_json({
            "slug": slug,
            "cloned": os.path.isdir(repo_dir),
            "clone_done": project_status.get(slug, {}).get("clone_done", False),
            "indexed": project_status.get(slug, {}).get("indexed", False),
            "indexing": project_status.get(slug, {}).get("indexing", False),
            "error": project_status.get(slug, {}).get("error", ""),
            "status_msg": project_status.get(slug, {}).get("status_msg", ""),
            "config": config,
        })

    def _project_list(self):
        """List all projects."""
        projects = []
        if os.path.isdir(PROJECTS_DIR):
            for slug in sorted(os.listdir(PROJECTS_DIR)):
                project_dir = os.path.join(PROJECTS_DIR, slug)
                if not os.path.isdir(project_dir):
                    continue
                repo_dir = os.path.join(project_dir, "repo")
                config_path = os.path.join(project_dir, "config.json")
                config = {}
                if os.path.isfile(config_path):
                    try:
                        with open(config_path, "r") as f:
                            config = json.load(f)
                    except Exception:
                        pass
                projects.append({
                    "slug": slug,
                    "cloned": os.path.isdir(repo_dir),
                    "indexed": project_status.get(slug, {}).get("indexed", False),
                    "indexing": project_status.get(slug, {}).get("indexing", False),
                    "git_url": config.get("git_url", ""),
                })
        self.send_json({"projects": projects})

    def _project_clone(self, slug):
        """Clone a project repository."""
        import subprocess
        
        # Read request body for git_url
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}
        
        git_url = payload.get("git_url", "")
        project_name = payload.get("project", "")   # full Gerrit project name
        if not git_url:
            self.send_json({"error": "git_url is required"}, 400)
            return
        
        slug = self._safe_slug(slug)
        project_dir = os.path.join(PROJECTS_DIR, slug)
        repo_dir = os.path.join(project_dir, "repo")
        os.makedirs(project_dir, exist_ok=True)
        
        # Save config — include project name for later ls-remote calls
        config_path = os.path.join(project_dir, "config.json")
        config = {"git_url": git_url, "project": project_name, "auto_analyze": True}
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        
        if os.path.isdir(repo_dir):
            is_git = os.path.isdir(os.path.join(repo_dir, ".git"))
            if is_git:
                # Already a valid clone — go straight to branch picker
                print(f"[Clone] '{slug}' already cloned. Skipping re-clone.")
                project_status.setdefault(slug, {})["clone_done"] = True
                project_status[slug]["cloned"] = True
                self.send_json({"status": "already_cloned", "slug": slug, "repo_dir": repo_dir})
                return
            else:
                # Dir exists but not a git repo (partial/interrupted clone) — wipe it
                print(f"[Clone] '{slug}' dir exists but is not a git repo — wiping...")
                import shutil
                shutil.rmtree(repo_dir, ignore_errors=True)

        # Clone in background — single branch (master), depth=1.
        # Branch list comes from git ls-remote (no local branches needed).
        # Checkout will shallow-fetch the user's chosen branch on demand.
        def _clone():
            try:
                env = os.environ.copy()
                env["GIT_TERMINAL_PROMPT"] = "0"
                env["GIT_ASKPASS"] = ""
                env["SSH_ASKPASS"] = ""
                env["GIT_SSL_NO_VERIFY"] = "1"

                print(f"[Clone] Shallow-cloning default branch of '{slug}' (--depth=1)...")
                project_status.setdefault(slug, {})["status_msg"] = "Cloning default branch…"
                result = subprocess.run(
                    ["git", "clone", "--depth=1", git_url, repo_dir],
                    capture_output=True, text=True, timeout=1200,
                    env=env
                )
                if result.returncode == 0:
                    print(f"[Clone] ✅ Cloned '{slug}' to {repo_dir}")
                    project_status.setdefault(slug, {})["cloned"] = True
                    project_status[slug]["clone_done"] = True
                    project_status[slug]["status_msg"] = "Clone complete"
                else:
                    err = result.stderr.strip()[:500]
                    print(f"[Clone] ❌ Clone failed for '{slug}': {err}")
                    project_status.setdefault(slug, {})["error"] = err
                    project_status[slug]["status_msg"] = ""
            except Exception as e:
                print(f"[Clone] ❌ Clone error for '{slug}': {e}")
                project_status.setdefault(slug, {})["error"] = str(e)
                project_status[slug]["status_msg"] = ""
            finally:
                _save_project_status()
        
        threading.Thread(target=_clone, daemon=True).start()
        self.send_json({"status": "cloning", "slug": slug})

    def _project_branches(self, slug):
        """List ALL remote branches directly from Gerrit (no local fetch needed)."""
        slug = self._safe_slug(slug)

        # Load project config to get git_url
        config_path = os.path.join(PROJECTS_DIR, slug, "config.json")
        config = {}
        if os.path.isfile(config_path):
            try:
                with open(config_path) as f:
                    config = json.load(f)
            except Exception:
                pass

        git_url = config.get("git_url", "")
        if not git_url:
            # Fallback: reconstruct from slug → project name mapping
            # slug is like "p1641_cwasa_mr_app", project is stored in config
            project_name = config.get("project", slug)
            git_url = f"{GERRIT_URL}/a/{project_name}"

        # Embed credentials into URL so git ls-remote can auth against Gerrit
        auth_header = self.headers.get("Authorization", "")
        authed_url = git_url
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
                # decoded = "user:password"
                from urllib.parse import urlparse, urlunparse
                parsed = urlparse(git_url)
                authed_url = urlunparse(parsed._replace(netloc=f"{decoded}@{parsed.netloc}"))
            except Exception:
                pass

        try:
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            env["GIT_ASKPASS"] = ""
            env["SSH_ASKPASS"] = ""
            env["GIT_SSL_NO_VERIFY"] = "1"   # self-signed cert on Gerrit

            result = subprocess.run(
                ["git", "ls-remote", "--heads", authed_url],
                capture_output=True, text=True, timeout=30, env=env
            )
            branches = []
            for line in result.stdout.strip().splitlines():
                # format: "<sha>\trefs/heads/<branch>"
                if "\trefs/heads/" in line:
                    b = line.split("\trefs/heads/")[-1].strip()
                    if b and b not in branches:
                        branches.append(b)
            branches.sort()

            if not branches and result.returncode != 0:
                # ls-remote failed — fall back to local branch -r
                repo_dir = os.path.join(PROJECTS_DIR, slug, "repo")
                if os.path.isdir(repo_dir):
                    r2 = subprocess.run(
                        ["git", "-C", repo_dir, "branch", "-r"],
                        capture_output=True, text=True, timeout=10
                    )
                    for line in r2.stdout.strip().splitlines():
                        line = line.strip()
                        if not line or "HEAD ->" in line:
                            continue
                        b = line.split("/", 1)[-1] if "/" in line else line
                        if b and b not in branches:
                            branches.append(b)
                    branches.sort()

            self.send_json({"branches": branches})
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def _project_checkout(self, slug):
        """Checkout a specific branch, then start indexing."""
        slug = self._safe_slug(slug)
        repo_dir = os.path.join(PROJECTS_DIR, slug, "repo")
        if not os.path.isdir(repo_dir):
            self.send_json({"error": "Project not cloned yet"}, 400)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        branch = payload.get("branch", "").strip()
        if not branch:
            self.send_json({"error": "branch is required"}, 400)
            return

        # Capture auth + build authenticated URL before thread starts
        # (self.headers not safe to access from background thread)
        auth_header = self.headers.get("Authorization", "")
        config_path = os.path.join(PROJECTS_DIR, slug, "config.json")
        cfg = {}
        if os.path.isfile(config_path):
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
            except Exception:
                pass
        git_url = cfg.get("git_url", "")
        authed_url = git_url
        if auth_header.startswith("Basic ") and git_url:
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
                from urllib.parse import urlparse, urlunparse
                parsed = urlparse(git_url)
                authed_url = urlunparse(parsed._replace(netloc=f"{decoded}@{parsed.netloc}"))
            except Exception:
                pass

        self.send_json({"status": "checkout_started", "branch": branch})

        def _checkout_and_index():
            try:
                env = os.environ.copy()
                env["GIT_TERMINAL_PROMPT"] = "0"
                env["GIT_ASKPASS"] = ""
                env["SSH_ASKPASS"] = ""
                env["GIT_SSL_NO_VERIFY"] = "1"

                project_status.setdefault(slug, {})["error"] = ""
                project_status[slug]["indexing"] = False

                # ── Step 1: check if branch is already checked out / local ───
                # Clone was --depth=1 of default branch (master/main).
                # If the user picks the same branch → already there, no network.
                # If different → shallow-fetch just that branch, then checkout.
                cur = subprocess.run(
                    ["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, timeout=10, env=env
                )
                current_branch = cur.stdout.strip()

                if current_branch == branch:
                    print(f"[Checkout] '{branch}' is already the current branch — skipping checkout.")
                    project_status[slug]["status_msg"] = f"Already on {branch}"
                else:
                    # Try local checkout first (works if branch was fetched before)
                    print(f"[Checkout] Checking out '{branch}' for '{slug}'...")
                    project_status[slug]["status_msg"] = f"Checking out {branch}…"

                    r = subprocess.run(
                        ["git", "-C", repo_dir, "checkout", branch],
                        capture_output=True, text=True, timeout=30, env=env
                    )

                    if r.returncode != 0:
                        r2 = subprocess.run(
                            ["git", "-C", repo_dir, "checkout", "-b", branch,
                             f"origin/{branch}"],
                            capture_output=True, text=True, timeout=30, env=env
                        )
                        if r2.returncode != 0:
                            # ── Step 2: shallow-fetch only this branch ────────
                            # Hits network only when branch not yet local.
                            print(f"[Checkout] Fetching '{branch}' from remote (shallow)...")
                            project_status[slug]["status_msg"] = f"Fetching {branch} from remote…"
                            r_fetch = subprocess.run(
                                ["git", "-C", repo_dir, "fetch", "--depth=1",
                                 authed_url,
                                 f"refs/heads/{branch}:refs/remotes/origin/{branch}"],
                                capture_output=True, text=True, timeout=1200, env=env
                            )
                            if r_fetch.returncode != 0:
                                err = r_fetch.stderr.strip()[:300] or "fetch failed"
                                print(f"[Checkout] ❌ Fetch failed: {err}")
                                project_status[slug]["error"] = f"Checkout failed: {err}"
                                project_status[slug]["indexing"] = False
                                return

                            r3 = subprocess.run(
                                ["git", "-C", repo_dir, "checkout", "-b", branch,
                                 f"origin/{branch}"],
                                capture_output=True, text=True, timeout=30, env=env
                            )
                            if r3.returncode != 0:
                                err = r3.stderr.strip()[:300]
                                print(f"[Checkout] ❌ Checkout still failed: {err}")
                                project_status[slug]["error"] = f"Checkout failed: {err}"
                                project_status[slug]["indexing"] = False
                                return

                    print(f"[Checkout] ✅ Checked out '{branch}' for '{slug}'")

                # Save chosen branch to config
                config_path = os.path.join(PROJECTS_DIR, slug, "config.json")
                try:
                    cfg = {}
                    if os.path.isfile(config_path):
                        with open(config_path) as f:
                            cfg = json.load(f)
                    cfg["branch"] = branch
                    with open(config_path, "w") as f:
                        json.dump(cfg, f, indent=2)
                except Exception:
                    pass

                # ── Step 3: index ─────────────────────────────────────────────
                _run_index(slug, repo_dir)

            except Exception as e:
                import traceback
                traceback.print_exc()
                project_status.setdefault(slug, {})["error"] = str(e)
                project_status[slug]["indexing"] = False
            finally:
                _save_project_status()

        threading.Thread(target=_checkout_and_index, daemon=True).start()

    def _project_analyze(self, slug):
        """Index a project with codebase-memory-mcp."""
        slug = self._safe_slug(slug)
        project_dir = os.path.join(PROJECTS_DIR, slug)
        repo_dir = os.path.join(project_dir, "repo")
        
        if not os.path.isdir(repo_dir):
            self.send_json({"error": "Project not cloned yet"}, 400)
            return
        
        # Run indexing in background
        threading.Thread(target=_run_index, args=(slug, repo_dir), daemon=True).start()
        self.send_json({"status": "indexing", "slug": slug})

    def _project_config(self, slug):
        """Get or update project configuration."""
        slug = self._safe_slug(slug)
        project_dir = os.path.join(PROJECTS_DIR, slug)
        config_path = os.path.join(project_dir, "config.json")
        
        if not os.path.isdir(project_dir):
            os.makedirs(project_dir, exist_ok=True)
        
        # Read existing config
        config = {}
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
            except Exception:
                pass
        
        # Update with new values from request body
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            updates = json.loads(body)
            config.update(updates)
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass
        
        self.send_json({"config": config})


HERMES_BIN = None  # Initialized on first use
def _ensure_hermes():
    global HERMES_BIN
    if HERMES_BIN is None:
        import shutil
        HERMES_BIN = shutil.which("hermes") or "hermes"


def _detect_index_target(repo_dir):
    """
    Detect project type and return the best directory to index.
    Avoids build artifacts, vendor dirs, generated code, and caches
    that would massively inflate indexing time.

    Detection is marker-file based — checks for well-known config files
    that uniquely identify a project type, then returns the source root.
    Falls back to repo_dir if nothing matches.
    """
    def exists(*parts):
        return os.path.exists(os.path.join(repo_dir, *parts))

    def isdir(*parts):
        return os.path.isdir(os.path.join(repo_dir, *parts))

    def p(*parts):
        return os.path.join(repo_dir, *parts)

    # Some repos have one subdirectory wrapping the actual project
    # e.g. repo/MyApp/build.gradle — unwrap one level if needed
    root = repo_dir
    try:
        entries = [e for e in os.listdir(repo_dir)
                   if os.path.isdir(os.path.join(repo_dir, e))
                   and not e.startswith('.')]
        if len(entries) == 1:
            candidate = os.path.join(repo_dir, entries[0])
            # If the single subdir looks like the real project root, use it
            project_markers = [
                "build.gradle", "build.gradle.kts", "pom.xml", "package.json",
                "composer.json", "pubspec.yaml", "requirements.txt", "Cargo.toml",
                "go.mod", "setup.py", "pyproject.toml",
            ]
            if any(os.path.exists(os.path.join(candidate, m)) for m in project_markers):
                root = candidate
                print(f"[Index]   unwrapped root → {root}")
    except Exception:
        pass

    def rexists(*parts):
        return os.path.exists(os.path.join(root, *parts))

    def risdir(*parts):
        return os.path.isdir(os.path.join(root, *parts))

    def rp(*parts):
        return os.path.join(root, *parts)

    # ── Android (Kotlin/Java) ────────────────────────────────────
    # Marker: build.gradle / build.gradle.kts + app/src
    if (rexists("build.gradle") or rexists("build.gradle.kts")) and risdir("app", "src"):
        print(f"[Index]   detected: Android")
        return rp("app", "src")

    # ── Laravel (PHP) ────────────────────────────────────────────
    # Marker: artisan + app/
    if rexists("artisan") and risdir("app"):
        print(f"[Index]   detected: Laravel (PHP)")
        return rp("app")

    # ── Generic PHP (CodeIgniter, Symfony, etc.) ─────────────────
    if rexists("composer.json") and risdir("app"):
        print(f"[Index]   detected: PHP (composer)")
        return rp("app")
    if rexists("composer.json") and risdir("src"):
        print(f"[Index]   detected: PHP (composer)")
        return rp("src")

    # ── Spring Boot / Maven (Java/Kotlin) ────────────────────────
    # Marker: pom.xml + src/main
    if rexists("pom.xml") and risdir("src", "main"):
        print(f"[Index]   detected: Maven (Java/Kotlin)")
        return rp("src", "main")

    # ── Gradle (Java/Kotlin non-Android) ─────────────────────────
    if (rexists("build.gradle") or rexists("build.gradle.kts")) and risdir("src", "main"):
        print(f"[Index]   detected: Gradle (Java/Kotlin)")
        return rp("src", "main")

    # ── Flutter / Dart ───────────────────────────────────────────
    if rexists("pubspec.yaml") and risdir("lib"):
        print(f"[Index]   detected: Flutter/Dart")
        return rp("lib")

    # ── Node.js / TypeScript / React / Vue / Angular ─────────────
    if rexists("package.json"):
        # Prefer src/ if it exists — avoids node_modules, dist, .next
        if risdir("src"):
            print(f"[Index]   detected: Node.js/JS (src/)")
            return rp("src")
        # Angular uses src/app
        if risdir("src", "app"):
            print(f"[Index]   detected: Angular")
            return rp("src", "app")
        print(f"[Index]   detected: Node.js/JS (no src/ — using root, node_modules excluded by codebase-memory-mcp)")
        return root

    # ── Python ───────────────────────────────────────────────────
    if rexists("pyproject.toml") or rexists("setup.py") or rexists("requirements.txt"):
        if risdir("src"):
            print(f"[Index]   detected: Python (src/)")
            return rp("src")
        # Find the main package dir (non-standard names)
        for entry in os.listdir(root):
            ep = os.path.join(root, entry)
            if os.path.isdir(ep) and os.path.exists(os.path.join(ep, "__init__.py")):
                print(f"[Index]   detected: Python package ({entry}/)")
                return ep
        print(f"[Index]   detected: Python (using root)")
        return root

    # ── Rust ─────────────────────────────────────────────────────
    if rexists("Cargo.toml") and risdir("src"):
        print(f"[Index]   detected: Rust")
        return rp("src")

    # ── Go ───────────────────────────────────────────────────────
    if rexists("go.mod"):
        print(f"[Index]   detected: Go (using root)")
        return root  # Go uses flat layout, no separate src dir

    # ── .NET (C#, F#, VB) ────────────────────────────────────────
    # Marker: .sln or .csproj / .fsproj / .vbproj
    # Index src/ if exists, else root — skip bin/, obj/, .vs/
    _dotnet_markers = (
        any(f.endswith((".sln", ".csproj", ".fsproj", ".vbproj"))
            for f in os.listdir(root) if os.path.isfile(os.path.join(root, f)))
        if os.path.isdir(root) else False
    )
    if _dotnet_markers:
        if risdir("src"):
            print(f"[Index]   detected: .NET (src/)")
            return rp("src")
        print(f"[Index]   detected: .NET (using root — bin/obj excluded by codebase-memory-mcp)")
        return root

    # ── Ruby on Rails ─────────────────────────────────────────────
    # Marker: Gemfile + app/
    if rexists("Gemfile") and risdir("app"):
        print(f"[Index]   detected: Ruby on Rails")
        return rp("app")

    # ── Ruby (generic) ───────────────────────────────────────────
    if rexists("Gemfile") and risdir("lib"):
        print(f"[Index]   detected: Ruby (lib/)")
        return rp("lib")

    # ── Swift / iOS / macOS ──────────────────────────────────────
    # Marker: .xcodeproj or .xcworkspace dir, or Package.swift (SPM)
    _xcode = any(
        f.endswith((".xcodeproj", ".xcworkspace"))
        for f in os.listdir(root) if os.path.isdir(os.path.join(root, f))
    ) if os.path.isdir(root) else False
    if _xcode or rexists("Package.swift"):
        if risdir("Sources"):
            print(f"[Index]   detected: Swift/SPM (Sources/)")
            return rp("Sources")
        print(f"[Index]   detected: Swift/Xcode (using root)")
        return root

    # ── Kotlin Multiplatform ─────────────────────────────────────
    if (rexists("build.gradle") or rexists("build.gradle.kts")) and risdir("shared", "src"):
        print(f"[Index]   detected: Kotlin Multiplatform (shared/src/)")
        return rp("shared", "src")

    # ── Elixir / Phoenix ─────────────────────────────────────────
    if rexists("mix.exs"):
        if risdir("lib"):
            print(f"[Index]   detected: Elixir/Phoenix (lib/)")
            return rp("lib")
        print(f"[Index]   detected: Elixir (using root)")
        return root

    # ── C / C++ (CMake) ──────────────────────────────────────────
    if rexists("CMakeLists.txt"):
        if risdir("src"):
            print(f"[Index]   detected: C/C++ CMake (src/)")
            return rp("src")
        if risdir("include"):
            print(f"[Index]   detected: C/C++ CMake (using root)")
        return root

    # ── C / C++ (Makefile) ───────────────────────────────────────
    if rexists("Makefile") and risdir("src"):
        print(f"[Index]   detected: C/C++ Makefile (src/)")
        return rp("src")

    # ── Scala / sbt ──────────────────────────────────────────────
    if rexists("build.sbt") and risdir("src", "main"):
        print(f"[Index]   detected: Scala/sbt (src/main/)")
        return rp("src", "main")

    # ── Haskell (Cabal / Stack) ───────────────────────────────────
    if rexists("stack.yaml") or any(
        f.endswith(".cabal") for f in os.listdir(root)
        if os.path.isfile(os.path.join(root, f))
    ) if os.path.isdir(root) else False:
        if risdir("src"):
            print(f"[Index]   detected: Haskell (src/)")
            return rp("src")
        if risdir("app"):
            print(f"[Index]   detected: Haskell (app/)")
            return rp("app")

    # ── Generic src/ fallback ────────────────────────────────────
    if risdir("src"):
        print(f"[Index]   detected: generic src/ layout")
        return rp("src")

    # ── No match — index full repo ───────────────────────────────
    print(f"[Index]   detected: unknown — indexing full repo")
    return root



def _run_index(slug, repo_dir):
    """Index a project repository with codebase-memory-mcp."""
    import subprocess
    import shutil
    import time
    _ensure_hermes()

    project_status.setdefault(slug, {})["indexing"] = True
    project_status[slug]["error"] = ""
    project_status[slug]["indexed"] = False
    project_status[slug]["status_msg"] = "Indexing codebase…"
    start_time = time.time()
    proc = None

    try:
        print(f"[Index] ▶ Starting index for '{slug}'")
        print(f"[Index]   repo_dir: {repo_dir}")

        # Find codebase-memory-mcp binary
        cbmcp = shutil.which("codebase-memory-mcp")
        if not cbmcp:
            for candidate in [
                r"D:\tools\codebase-memory-mcp.exe",
                os.path.expanduser("~/.local/bin/codebase-memory-mcp"),
            ]:
                if os.path.isfile(candidate):
                    cbmcp = candidate
                    break

        if not cbmcp:
            raise RuntimeError("codebase-memory-mcp binary not found at D:\\tools\\codebase-memory-mcp.exe")

        print(f"[Index]   binary: {cbmcp}")

        # Detect project type and choose the best index target
        # Avoids indexing build artifacts, vendor dirs, node_modules etc.
        index_target = _detect_index_target(repo_dir)
        print(f"[Index]   index_target: {index_target}")
        if index_target != repo_dir:
            print(f"[Index]   (narrowed from repo root to avoid build artifacts)")
        print(f"[Index]   launching subprocess...")

        # Correct CLI: codebase-memory-mcp cli index_repository '{"repo_path":"..."}'
        # NOT: codebase-memory-mcp index <dir>  ← that subcommand does not exist
        import json as _json
        index_args = _json.dumps({"repo_path": index_target.replace("\\", "/")})
        proc = subprocess.Popen(
            [cbmcp, "cli", "index_repository", index_args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        last_line = ""
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                print(f"[Index]   {line}")
                last_line = line

        proc.wait(timeout=7200)  # 2 hour hard cap

        elapsed = int(time.time() - start_time)
        if proc.returncode == 0:
            print(f"[Index] ✅ Complete for '{slug}' in {elapsed}s")
            project_status[slug]["indexed"] = True
        else:
            err = last_line or f"Indexing failed (exit code {proc.returncode})"
            print(f"[Index] ❌ Failed for '{slug}' after {elapsed}s — {err}")
            project_status[slug]["error"] = err[:500]

    except subprocess.TimeoutExpired:
        elapsed = int(time.time() - start_time)
        print(f"[Index] ⏰ Timed out for '{slug}' after {elapsed}s")
        project_status[slug]["error"] = f"Indexing timed out after {elapsed}s (2h limit)"
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
    except Exception as e:
        import traceback
        elapsed = int(time.time() - start_time)
        print(f"[Index] ❌ Exception for '{slug}' after {elapsed}s:")
        traceback.print_exc()
        project_status[slug]["error"] = str(e)
    finally:
        project_status[slug]["indexing"] = False
        elapsed = int(time.time() - start_time)
        print(f"[Index] ■ Done for '{slug}' (total: {elapsed}s) — "
              f"indexed={project_status[slug].get('indexed')} "
              f"error='{project_status[slug].get('error','')[:80]}'")
        _save_project_status()



def do_ai_review(change_id, diff_text, filenames, auth_header, project_slug=""):
    """Write diff to temp file, call Hermes, parse JSON response."""
    import ai_reviewer
    return ai_reviewer.review(change_id, diff_text, filenames, project_slug, auth_header)


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"\n  ✅  Gerrit AI Code Reviewer running at  http://localhost:{PORT}")
    print(f"  Proxying /api/* → {GERRIT_URL}")
    print(f"  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
