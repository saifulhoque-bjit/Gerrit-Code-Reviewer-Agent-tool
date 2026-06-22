#!/usr/bin/env python3
"""
Rules Engine — language-aware rule matching for code reviews.

Resolves rules in priority order:
  1. _base/ rules (always included)
  2. _lang/ rules (matched by file extension in the diff)
  3. <project-slug>/ rules (project-specific overrides, highest priority)
"""

import os
import fnmatch

DIR = os.path.dirname(os.path.abspath(__file__))
RULES_DIR = os.path.join(DIR, "rules")

# File extension → language rule file
LANG_MAP = {
    "*.java": "java.md",
    "*.py": "python.md",
    "*.ts": "ts_js_tsx_jsx.md",
    "*.js": "ts_js_tsx_jsx.md",
    "*.tsx": "ts_js_tsx_jsx.md",
    "*.jsx": "ts_js_tsx_jsx.md",
    "*.kt": "kotlin.md",
    "*.rs": "rust.md",
    "*.cpp": "cpp.md",
    "*.cc": "cpp.md",
    "*.hpp": "cpp.md",
    "*.c": "c.md",
    "*.cs": "csharp.md",
    "*.go": "go.md",
    "*.rb": "ruby.md",
    "*.php": "php.md",
    "*.swift": "swift.md",
    "*.scala": "scala.md",
}


def detect_languages(filenames: list[str]) -> set[str]:
    """Detect which language rule files match the files in the diff."""
    matched = set()
    for fname in filenames:
        for pattern, rule_file in LANG_MAP.items():
            if fnmatch.fnmatch(fname, pattern):
                matched.add(rule_file)
                break
    return matched


def read_rule_file(path: str) -> str:
    """Read a rule file, return empty string if missing."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def load_base_rules() -> str:
    """Load all _base/ rules."""
    base_dir = os.path.join(RULES_DIR, "_base")
    if not os.path.isdir(base_dir):
        return ""
    
    parts = []
    for fname in sorted(os.listdir(base_dir)):
        if fname.endswith(".md"):
            content = read_rule_file(os.path.join(base_dir, fname))
            if content:
                parts.append(content)
    
    return "\n\n---\n\n".join(parts)


def load_lang_rules(filenames: list[str]) -> str:
    """Load language-specific rules matched by file extensions in the diff."""
    lang_dir = os.path.join(RULES_DIR, "_lang")
    if not os.path.isdir(lang_dir):
        return ""
    
    matched_langs = detect_languages(filenames)
    parts = []
    for lang_file in sorted(matched_langs):
        path = os.path.join(lang_dir, lang_file)
        content = read_rule_file(path)
        if content:
            parts.append(content)
    
    return "\n\n---\n\n".join(parts)


def load_project_rules(project_slug: str) -> str:
    """Load project-specific rules from rules/<slug>/."""
    if not project_slug:
        return ""
    
    import re
    safe_slug = re.sub(r"[^a-zA-Z0-9._-]", "_", project_slug)
    project_dir = os.path.join(RULES_DIR, safe_slug)
    if not os.path.isdir(project_dir):
        return ""
    
    # Prefer compressed rules if fresh
    compressed_path = os.path.join(project_dir, "_compressed.txt")
    raw_files = sorted(
        f for f in os.listdir(project_dir)
        if os.path.isfile(os.path.join(project_dir, f))
        and not f.startswith("_")
        and f.endswith((".md", ".txt"))
    )
    
    if not raw_files:
        return ""
    
    # Check compressed freshness
    if os.path.exists(compressed_path):
        latest_raw = max(
            os.path.getmtime(os.path.join(project_dir, f))
            for f in raw_files
        )
        if os.path.getmtime(compressed_path) >= latest_raw:
            return read_rule_file(compressed_path)
    
    # Fallback to raw files
    parts = []
    for fname in raw_files:
        content = read_rule_file(os.path.join(project_dir, fname))
        if content:
            parts.append(f"--- {fname} ---\n{content}")
    
    return "\n\n".join(parts)


def resolve_rules(filenames: list[str], project_slug: str = "") -> str:
    """Build the complete rules text for a review.
    
    Priority: _base/ (always) + _lang/ (matched) + <project>/ (overrides)
    """
    parts = []
    
    # 1. Base rules (always included)
    base = load_base_rules()
    if base:
        parts.append(base)
    
    # 2. Language rules (only for languages in this diff)
    lang = load_lang_rules(filenames)
    if lang:
        parts.append(lang)
    
    # 3. Project-specific rules (highest priority)
    project = load_project_rules(project_slug)
    if project:
        parts.append(project)
    
    return "\n\n===\n\n".join(parts)
