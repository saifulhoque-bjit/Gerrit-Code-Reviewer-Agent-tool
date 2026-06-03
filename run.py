#!/usr/bin/env python3
"""
run.py — Launcher for Gerrit AI Code Reviewer server.
Press  R  then  Enter  to restart the server at any time.
Press  Ctrl+C  to stop.
"""

import subprocess
import sys
import os
import threading
import time

DIR   = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(DIR, "server.py")

proc = None
restart_flag = threading.Event()


def start_server():
    global proc
    print("\n" + "─" * 54)
    print("  ✅  Starting Gerrit AI Code Reviewer…")
    print("  💡  Press  R  then  Enter  to restart")
    print("  💡  Press  Ctrl+C          to stop")
    print("─" * 54 + "\n")
    proc = subprocess.Popen(
        [sys.executable, ENTRY],
        cwd=DIR,
    )


def stop_server():
    global proc
    if proc and proc.poll() is None:
        print("\n[Launcher] Stopping server…")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        proc = None


def input_listener():
    """Runs in a background thread; sets restart_flag when user types R + Enter."""
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().lower() == "r":
            restart_flag.set()


if __name__ == "__main__":
    start_server()

    listener = threading.Thread(target=input_listener, daemon=True)
    listener.start()

    try:
        while True:
            if restart_flag.is_set():
                restart_flag.clear()
                stop_server()
                time.sleep(0.5)
                print("\n[Launcher] 🔄  Restarting server…\n")
                start_server()

            # Also restart automatically if server crashes unexpectedly
            if proc and proc.poll() is not None:
                code = proc.returncode
                print(f"\n[Launcher] ⚠️  Server exited (code {code}) — restarting in 2s…\n")
                time.sleep(2)
                start_server()

            time.sleep(0.3)

    except KeyboardInterrupt:
        stop_server()
        print("\n[Launcher] Stopped. Bye!\n")
