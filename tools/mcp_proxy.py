#!/usr/bin/env python3
"""
stdio proxy for an MCP server binary.

    Hermes  <-- stdio -->  this proxy  <-- stdio -->  MCP server binary

Forwards bytes both ways using single read(2) syscalls (os.read), so a small
message is forwarded the instant it arrives instead of blocking until a full
buffer or EOF. Flushes after every write so a buffered child can't deadlock.

Usage:
    mcp_proxy.py /path/to/codebase-memory-mcp [args...]
"""

import os
import sys
import signal
import threading
import subprocess


def pump(src_fd, dst_fd, *, close_dst_on_eof=False):
    """Copy src_fd -> dst_fd using one read(2) per iteration."""
    try:
        while True:
            chunk = os.read(src_fd, 65536)
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                offset += os.write(dst_fd, chunk[offset:])
    except (BrokenPipeError, OSError):
        pass
    finally:
        if close_dst_on_eof:
            try:
                os.close(dst_fd)
            except OSError:
                pass


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: mcp_proxy.py <binary> [args...]\n")
        return 2

    cmd = sys.argv[1:]

    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            close_fds=True,
        )
    except FileNotFoundError:
        sys.stderr.write(f"mcp_proxy: binary not found: {cmd[0]}\n")
        return 127
    except OSError as e:
        sys.stderr.write(f"mcp_proxy: failed to start {cmd[0]}: {e}\n")
        return 126

    hermes_in_fd = sys.stdin.fileno()
    hermes_out_fd = sys.stdout.fileno()
    hermes_err_fd = sys.stderr.fileno()

    server_stdin_fd = proc.stdin.fileno()
    server_stdout_fd = proc.stdout.fileno()
    server_stderr_fd = proc.stderr.fileno()

    threads = [
        threading.Thread(
            target=pump,
            args=(hermes_in_fd, server_stdin_fd),
            kwargs={"close_dst_on_eof": True},
            daemon=True,
        ),
        threading.Thread(
            target=pump, args=(server_stdout_fd, hermes_out_fd), daemon=True
        ),
        threading.Thread(
            target=pump, args=(server_stderr_fd, hermes_err_fd), daemon=True
        ),
    ]
    for t in threads:
        t.start()

    def terminate(*_):
        try:
            proc.terminate()
        except OSError:
            pass

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, terminate)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, terminate)

    returncode = proc.wait()

    for t in threads[1:]:
        t.join(timeout=2.0)

    return returncode


if __name__ == "__main__":
    sys.exit(main())
