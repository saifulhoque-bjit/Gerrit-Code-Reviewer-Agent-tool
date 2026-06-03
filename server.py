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

GERRIT_URL = "https://review2.bjitgroup.com:8443"
PORT = 7474
DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(DIR, "temp")
RULES_DIR = os.path.join(DIR, "rules")   # rules/<project_slug>/<file.txt>
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(RULES_DIR, exist_ok=True)

# In-memory store for AI review results (keyed by change_id)
review_results = {}
review_status = {}  # "pending" | "done" | "error"


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
        print(f"  {self.address_string()} {fmt % args}")

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
            self.send_json({"status": status, "result": result})
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
        # Parse: /ai-review/start/<change_id>
        parts = path.strip("/").split("/")
        change_id = parts[-1] if len(parts) >= 3 else None

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

        if not change_id or not diff_text:
            self.send_json({"error": "Missing change_id or diff"}, 400)
            return

        # Mark as pending and kick off background thread
        review_status[change_id] = "pending"
        review_results[change_id] = None
        self.send_json({"status": "started", "change_id": change_id})

        def run_review():
            try:
                comments, token_summary = do_ai_review(change_id, diff_text, filenames, auth, project_slug)
                review_results[change_id] = {"comments": comments, "token_summary": token_summary}
                review_status[change_id] = "done"
            except Exception as ex:
                review_results[change_id] = None
                review_status[change_id] = "error"
                print(f"[AI Review Error] {ex}")

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


def do_ai_review(change_id, diff_text, filenames, auth_header, project_slug=""):
    """Write diff to temp file, call Hermes, parse JSON response."""
    import ai_reviewer
    return ai_reviewer.review(change_id, diff_text, filenames, project_slug)


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"\n  ✅  Gerrit AI Code Reviewer running at  http://localhost:{PORT}")
    print(f"  Proxying /api/* → {GERRIT_URL}")
    print(f"  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
