#!/usr/bin/env python3
"""
AI Reviewer — writes diff to temp file, invokes Hermes CLI, parses structured output.
"""

import os
import json
import subprocess
import re
import sys
from pathlib import Path

DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(DIR, "temp")
RULES_DIR = os.path.join(DIR, "rules")
SKILLS_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "skills", "gerrit")
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(RULES_DIR, exist_ok=True)
os.makedirs(SKILLS_DIR, exist_ok=True)

# Dynamically locate the hermes binary at runtime
def find_hermes():
    import shutil

    # 1. Check PATH first (works in most environments)
    h = shutil.which("hermes")
    if h:
        return h

    # 2. Look next to the current Python executable (same venv)
    py = sys.executable  # e.g. .../venv/Scripts/python.exe
    scripts_dir = os.path.dirname(py)
    for name in ("hermes.exe", "hermes"):
        candidate = os.path.join(scripts_dir, name)
        if os.path.isfile(candidate):
            return candidate

    # 3. Ask the OS shell (handles cases where PATH is set in shell but not subprocess)
    try:
        cmd = "where hermes" if os.name == "nt" else "which hermes"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        found = result.stdout.strip().splitlines()[0].strip()
        if found and os.path.isfile(found):
            return found
    except Exception:
        pass

    # 4. Fallback: hope it resolves on PATH at call time
    return "hermes"


HERMES_BIN = find_hermes()
print(f"[AI Reviewer] Using Hermes binary: {HERMES_BIN}")

REVIEW_PROMPT_TEMPLATE = """You are a senior software engineer performing a code review.

{rules_section}Carefully analyze the following code diff and identify:
- Bugs or logic errors
- Security vulnerabilities
- Performance issues
- Code style / maintainability problems
- Missing null/error checks
- Any other important issues

Return your findings as a JSON array ONLY. No prose before or after. No markdown code fences.
Each item must have exactly these fields:
[
  {{
    "file": "path/to/file",
    "line": <integer line number in new file, or 0 if general>,
    "severity": "error" | "warning" | "suggestion",
    "comment": "Clear explanation of the issue and how to fix it"
  }}
]

If there are no issues, return an empty array: []

CODE DIFF TO REVIEW:
---
{diff}
---
"""

RULES_SECTION_TEMPLATE = """PROJECT-SPECIFIC REVIEW RULES (you MUST follow these rules strictly when reviewing):
{rules_content}

"""


def load_project_rules(project_slug: str) -> str:
    """
    Load rules for a project. Prefers _compressed.txt if available and fresh.
    Falls back to raw files, and triggers background compression on first load.
    """
    if not project_slug:
        return ""
    import re as _re
    safe_slug = _re.sub(r"[^a-zA-Z0-9._-]", "_", project_slug)
    project_dir = os.path.join(RULES_DIR, safe_slug)
    if not os.path.isdir(project_dir):
        return ""

    compressed_path = os.path.join(project_dir, "_compressed.txt")

    # Check if compressed exists and is fresh
    raw_files = sorted(
        f for f in os.listdir(project_dir)
        if os.path.isfile(os.path.join(project_dir, f)) and not f.startswith("_")
    )
    if not raw_files:
        return ""

    if os.path.exists(compressed_path):
        latest_raw = max(os.path.getmtime(os.path.join(project_dir, f)) for f in raw_files)
        if os.path.getmtime(compressed_path) >= latest_raw:
            with open(compressed_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            print(f"[Rules] Using compressed rules for '{safe_slug}' ({len(content)} chars) ✅")
            return content

    # No fresh compressed — use raw for this review, but kick off compression in background
    print(f"[Rules] No compressed cache for '{safe_slug}' — using raw rules, queuing compression...")
    threading.Thread(target=compress_and_save_rules, args=(project_slug,), daemon=True).start()

    parts = []
    for fname in raw_files:
        fpath = os.path.join(project_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
            if content:
                parts.append(f"--- {fname} ---\n{content}")
        except Exception as e:
            print(f"[AI Reviewer] Could not read rules file {fname}: {e}")

    if not parts:
        return ""
    return "\n\n".join(parts)


COMPRESS_PROMPT = """You are a technical writer. Below are code review rules for a software project.
Compress them into the shortest possible directive list — keep every actionable rule, remove all
verbose explanations, examples, and redundant wording. Output ONLY the compressed rules as a
numbered list. No intro, no outro.

RULES TO COMPRESS:
{rules_content}
"""

def compress_and_save_rules(project_slug: str) -> str:
    """
    Run a one-time Hermes call to compress the raw rules for a project.
    Saves the result to rules/<slug>/_compressed.txt and returns it.
    Skips compression if _compressed.txt is already newer than all raw rule files.
    """
    import re as _re
    safe_slug = _re.sub(r"[^a-zA-Z0-9._-]", "_", project_slug)
    project_dir = os.path.join(RULES_DIR, safe_slug)
    compressed_path = os.path.join(project_dir, "_compressed.txt")

    # Gather raw rule files (exclude _compressed.txt itself)
    raw_files = sorted(
        f for f in os.listdir(project_dir)
        if os.path.isfile(os.path.join(project_dir, f)) and not f.startswith("_")
    )
    if not raw_files:
        return ""

    # Check freshness — skip if compressed is already up to date
    if os.path.exists(compressed_path):
        compressed_mtime = os.path.getmtime(compressed_path)
        latest_raw = max(os.path.getmtime(os.path.join(project_dir, f)) for f in raw_files)
        if compressed_mtime >= latest_raw:
            print(f"[Rules] Compressed cache is fresh for '{safe_slug}', skipping re-compression")
            with open(compressed_path, "r", encoding="utf-8") as f:
                return f.read()

    # Build combined raw text
    raw_parts = []
    for fname in raw_files:
        with open(os.path.join(project_dir, fname), "r", encoding="utf-8", errors="replace") as f:
            raw_parts.append(f.read().strip())
    raw_combined = "\n\n".join(raw_parts)

    print(f"[Rules] Compressing rules for '{safe_slug}' ({len(raw_combined)} chars) via Hermes...")
    prompt = COMPRESS_PROMPT.format(rules_content=raw_combined)

    try:
        result = subprocess.run(
            [HERMES_BIN, "-z", prompt],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace"
        )
        compressed = result.stdout.strip()
        if not compressed:
            print("[Rules] Compression returned empty output — using raw rules")
            return raw_combined
    except Exception as e:
        print(f"[Rules] Compression failed ({e}) — using raw rules")
        return raw_combined

    # Save compressed
    with open(compressed_path, "w", encoding="utf-8") as f:
        f.write(compressed)
    ratio = round((1 - len(compressed) / max(len(raw_combined), 1)) * 100)
    print(f"[Rules] Compression done: {len(raw_combined)} → {len(compressed)} chars ({ratio}% reduction)")

    # Also create/update a Hermes skill for this project
    _write_hermes_skill(safe_slug, compressed)

    return compressed


def _write_hermes_skill(safe_slug: str, compressed_rules: str):
    """Write a SKILL.md into Hermes skills/gerrit/ so Hermes can load rules natively."""
    skill_name = f"gerrit-rules-{safe_slug}"
    skill_dir  = os.path.join(SKILLS_DIR, skill_name)
    os.makedirs(skill_dir, exist_ok=True)
    skill_path = os.path.join(skill_dir, "SKILL.md")

    skill_md = f"""---
name: {skill_name}
description: "Gerrit code review rules for project '{safe_slug}'. Load this skill before reviewing diffs."
tags: [gerrit, code-review, rules, {safe_slug}]
---

# Code Review Rules — {safe_slug}

These rules MUST be applied when reviewing code diffs for this project.

{compressed_rules}
"""
    with open(skill_path, "w", encoding="utf-8") as f:
        f.write(skill_md)
    print(f"[Rules] Hermes skill written: skills/gerrit/{skill_name}/SKILL.md")


# Token cost estimation (Claude Sonnet approximate pricing)
INPUT_COST_PER_1M       = 3.00    # USD per 1M input tokens
OUTPUT_COST_PER_1M      = 15.00   # USD per 1M output tokens
CACHE_WRITE_COST_PER_1M = 3.75    # USD per 1M tokens written to cache
CACHE_READ_COST_PER_1M  = 0.30    # USD per 1M tokens read from cache

import hashlib
_prompt_cache: dict[str, int] = {}  # prompt_hash -> hit count

def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 chars per token."""
    return max(1, len(text) // 4)

def estimate_cost(input_tokens: int, output_tokens: int,
                  cache_hit: bool = False) -> float:
    if cache_hit:
        return (input_tokens  / 1_000_000) * CACHE_READ_COST_PER_1M + \
               (output_tokens / 1_000_000) * OUTPUT_COST_PER_1M
    return (input_tokens  / 1_000_000) * INPUT_COST_PER_1M + \
           (output_tokens / 1_000_000) * OUTPUT_COST_PER_1M

def check_cache(prompt: str) -> tuple[bool, str]:
    """Return (cache_hit, prompt_hash). Registers the hash on first call."""
    h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    if h in _prompt_cache:
        _prompt_cache[h] += 1
        return True, h
    _prompt_cache[h] = 0
    return False, h


def review(change_id: str, diff_text: str, filenames: list, project_slug: str = "") -> list:
    """
    Write diff to temp file, invoke Hermes, return list of comment dicts.
    If a Hermes skill exists for this project, references it by name (no rules text in prompt).
    Otherwise falls back to loading compressed/raw rules inline.
    """
    # 1. Determine rules strategy
    import re as _re
    safe_slug   = _re.sub(r"[^a-zA-Z0-9._-]", "_", project_slug) if project_slug else ""
    skill_name  = f"gerrit-rules-{safe_slug}" if safe_slug else ""
    skill_path  = os.path.join(SKILLS_DIR, skill_name, "SKILL.md") if skill_name else ""
    skill_exists = skill_name and os.path.exists(skill_path)

    if skill_exists:
        # Skill exists — just reference it; Hermes loads it internally (saves input tokens)
        rules_section = (
            f"IMPORTANT: Before reviewing, load and strictly follow the skill named "
            f"'{skill_name}' which contains the project-specific code review rules.\n\n"
        )
        print(f"[AI Reviewer] Using Hermes skill '{skill_name}' — rules NOT injected into prompt ✅ (token saving)")
    else:
        # No skill yet — fall back to inline rules (compressed if available, else raw)
        rules_text = load_project_rules(project_slug)
        if rules_text:
            rules_section = RULES_SECTION_TEMPLATE.format(rules_content=rules_text)
            rules_source = "raw" if rules_text.startswith("---") else "compressed"
            print(f"[AI Reviewer] Inline rules for '{project_slug}' ({len(rules_text)} chars, {rules_source}) — skill not ready yet")
        else:
            rules_section = ""
            print(f"[AI Reviewer] No rules found for '{project_slug}' — using default prompt")

    # 2. Build prompt
    prompt = REVIEW_PROMPT_TEMPLATE.format(
        rules_section=rules_section,
        diff=diff_text[:12000]  # cap at 12k chars
    )

    # Check prompt cache (same diff+rules combo seen before?)
    cache_hit, prompt_hash = check_cache(prompt)
    cache_label = f"✅ CACHE HIT  (hash={prompt_hash}, seen {_prompt_cache[prompt_hash]}x before)" \
                  if cache_hit else f"❌ CACHE MISS (hash={prompt_hash}, first time)"
    print(f"[AI Reviewer] {cache_label}")

    # 3. Write prompt to temp file
    temp_path = os.path.join(TEMP_DIR, f"diff_{change_id}.txt")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(prompt)

    print(f"[AI Reviewer] Wrote diff to {temp_path}")
    print(f"[AI Reviewer] Invoking Hermes ({HERMES_BIN})...")

    # 2. Invoke Hermes
    try:
        result = subprocess.run(
            [HERMES_BIN, "-z", prompt],
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace"
        )
        raw_output = result.stdout.strip()
        stderr_out = result.stderr.strip()

        if stderr_out:
            print(f"[AI Reviewer] Hermes stderr: {stderr_out[:500]}")

        print(f"[AI Reviewer] Hermes raw output (first 500 chars): {raw_output[:500]}")

    except subprocess.TimeoutExpired:
        raise RuntimeError("Hermes timed out after 180 seconds")
    except FileNotFoundError:
        raise RuntimeError(f"Hermes binary not found at: {HERMES_BIN}. Make sure 'hermes' is on your PATH.")

    # 3. Parse JSON from output
    comments = parse_comments(raw_output)

    # 4. Token & cost summary
    input_tokens  = estimate_tokens(prompt)
    output_tokens = estimate_tokens(raw_output)
    cost_usd      = estimate_cost(input_tokens, output_tokens, cache_hit=cache_hit)
    token_summary = {
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "total_tokens":  input_tokens + output_tokens,
        "estimated_cost_usd": round(cost_usd, 6),
        "cache_hit":   cache_hit,
        "prompt_hash": prompt_hash,
        "cache_hit_count": _prompt_cache[prompt_hash],
        "rules_strategy": "skill-ref" if skill_exists else ("inline-compressed" if (not skill_exists and project_slug) else "none"),
    }
    cache_tag = f"✅ CACHE HIT  (×{_prompt_cache[prompt_hash]})" if cache_hit else "❌ CACHE MISS"
    print(
        f"[AI Reviewer] ── Token Usage ──────────────────────────────\n"
        f"  Cache status  : {cache_tag}\n"
        f"  Input  tokens : {input_tokens:,}\n"
        f"  Output tokens : {output_tokens:,}\n"
        f"  Total  tokens : {input_tokens + output_tokens:,}\n"
        f"  Est. cost     : ${cost_usd:.6f} USD  ({'cache read' if cache_hit else 'full'} rate)\n"
        f"[AI Reviewer] ─────────────────────────────────────────────"
    )

    # 5. Clean up temp file
    try:
        os.remove(temp_path)
    except Exception:
        pass

    return comments, token_summary


def parse_comments(raw: str) -> list:
    """
    Extract JSON array from Hermes output.
    Handles: clean JSON, JSON inside markdown fences, or JSON embedded in prose.
    """
    if not raw:
        return []

    # Try direct parse first
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return normalize(data)
    except Exception:
        pass

    # Try extracting from markdown code fence
    fence_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            if isinstance(data, list):
                return normalize(data)
        except Exception:
            pass

    # Try finding a JSON array anywhere in the output
    array_match = re.search(r"(\[.*\])", raw, re.DOTALL)
    if array_match:
        try:
            data = json.loads(array_match.group(1))
            if isinstance(data, list):
                return normalize(data)
        except Exception:
            pass

    # Fallback: return a single comment with raw output
    return [{
        "file": "general",
        "line": 0,
        "severity": "suggestion",
        "comment": f"AI Review output (could not parse as JSON):\n\n{raw[:2000]}"
    }]


def normalize(items: list) -> list:
    """Ensure each item has required fields."""
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append({
            "file": str(item.get("file", "general")),
            "line": int(item.get("line", 0)) if str(item.get("line", "0")).isdigit() else 0,
            "severity": item.get("severity", "suggestion") if item.get("severity") in ("error", "warning", "suggestion") else "suggestion",
            "comment": str(item.get("comment", ""))
        })
    return result


if __name__ == "__main__":
    # Quick test
    test_diff = """
--- a/src/Main.java
+++ b/src/Main.java
@@ -10,6 +10,10 @@
     public static void main(String[] args) {
+        String input = args[0];
+        int value = Integer.parseInt(input);
+        System.out.println(100 / value);
     }
"""
    comments = review("test123", test_diff, ["src/Main.java"])
    print(json.dumps(comments, indent=2))
