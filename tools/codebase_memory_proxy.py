#!/usr/bin/env python3
"""
Transparent MCP proxy for codebase-memory-mcp.exe on Windows.

Fixes a pipe buffering issue where the Go binary's stdout isn't flushed
properly when Hermes connects directly via stdio. This wrapper reads
line-by-line from stdin and explicitly flushes after each write.

No external dependencies. ~0ms overhead.
"""
import subprocess
import sys
import threading
import shutil
import os

# Find binary: PATH first, then repo tools/ directory
BINARY = shutil.which("codebase-memory-mcp")
if not BINARY:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    BINARY = os.path.join(SCRIPT_DIR, "codebase-memory-mcp.exe")

if not os.path.isfile(BINARY):
    print("codebase-memory-mcp binary not found", file=sys.stderr)
    sys.exit(1)

p = subprocess.Popen(
    [BINARY],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

def pipe_in():
    try:
        for line in sys.stdin.buffer:
            p.stdin.write(line)
            p.stdin.flush()
    except Exception:
        pass
    finally:
        p.stdin.close()

def pipe_out():
    try:
        for line in p.stdout:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
    except Exception:
        pass

def pipe_err():
    try:
        for line in p.stderr:
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()
    except Exception:
        pass

for fn in (pipe_in, pipe_out, pipe_err):
    threading.Thread(target=fn, daemon=True).start()

p.wait()
sys.exit(p.returncode)
