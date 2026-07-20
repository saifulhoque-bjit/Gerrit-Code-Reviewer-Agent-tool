"""Single source of truth for rule resolution (v3 duplicated this 3 ways).

Tiers, lowest to highest priority:
  _base/   always included
  _lang/   matched by file extension
  <slug>/  project-specific overrides

Returns the ordered list of rule-file paths; the worker reads them itself
(v3's file-referenced approach — keeps prompts small).
"""
from __future__ import annotations

import re
from pathlib import Path

# extension -> language rule filename
LANG_MAP = {
    ".java": "java.md", ".py": "python.md",
    ".ts": "ts_js_tsx_jsx.md", ".js": "ts_js_tsx_jsx.md",
    ".tsx": "ts_js_tsx_jsx.md", ".jsx": "ts_js_tsx_jsx.md",
    ".kt": "kotlin.md", ".rs": "rust.md",
    ".cpp": "c_cpp.md", ".cc": "c_cpp.md", ".hpp": "c_cpp.md",
    ".c": "c_cpp.md", ".h": "c_cpp.md",
    ".cs": "csharp.md", ".go": "go.md", ".rb": "ruby.md",
    ".php": "php.md", ".swift": "swift.md", ".scala": "scala.md",
}


def safe_slug(slug: str) -> str:
    """Sanitize a project slug for use as a directory name."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", slug) if slug else ""


def detect_lang_files(filenames: list[str]) -> set[str]:
    """Language rule files matching the extensions in a changeset."""
    return {
        LANG_MAP[ext]
        for f in filenames
        if (ext := Path(f).suffix.lower()) in LANG_MAP
    }


# ── Rule management (Phase 5: browse + edit rule files from the UI) ──
# Editable rule files are markdown only; anything else is treated as read-only
# content we never let the API write.
_EDITABLE_SUFFIXES = {".md"}


def _safe_rule_file(rules_dir: Path, rel_path: str) -> Path:
    """Resolve a caller-supplied relative path *inside* rules_dir or raise.

    Guards against path traversal ('../', absolute paths, symlink escapes):
    the resolved file must sit under the resolved rules_dir and be a .md file.
    """
    if not rel_path or rel_path.strip() != rel_path:
        raise ValueError("rule path is required")
    candidate = (rules_dir / rel_path).resolve()
    root = rules_dir.resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("rule path escapes the rules directory")
    if candidate.suffix.lower() not in _EDITABLE_SUFFIXES:
        raise ValueError("only .md rule files can be read or edited")
    return candidate


def list_rule_files(rules_dir: Path) -> list[dict]:
    """Every .md rule file under rules_dir, grouped-friendly and sorted.

    Each entry carries its tier (_base / _lang / <project>), POSIX-style
    relative path, size and mtime — enough for a browsable rules panel.
    """
    root = rules_dir.resolve()
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        tier = rel.parts[0] if len(rel.parts) > 1 else "_root"
        stat = p.stat()
        out.append({
            "path": rel.as_posix(),
            "name": p.name,
            "tier": tier,
            "bytes": stat.st_size,
            "modified": int(stat.st_mtime),
        })
    return out


def read_rule_file(rules_dir: Path, rel_path: str) -> str:
    """Return a rule file's text. Raises ValueError on a bad path, FileNotFoundError if absent."""
    path = _safe_rule_file(rules_dir, rel_path)
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    return path.read_text(encoding="utf-8")


def write_rule_file(rules_dir: Path, rel_path: str, content: str) -> dict:
    """Overwrite (or create) a rule file. Creates parent tier dirs as needed.

    Returns the file's fresh metadata. Raises ValueError on a bad path.
    """
    path = _safe_rule_file(rules_dir, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    stat = path.stat()
    return {"path": path.relative_to(rules_dir.resolve()).as_posix(),
            "bytes": stat.st_size, "modified": int(stat.st_mtime)}


def resolve_rule_paths(
    rules_dir: Path, filenames: list[str], slug: str = ""
) -> list[Path]:
    """Ordered rule-file paths: base + matched-lang + project."""
    paths: list[Path] = []

    base = rules_dir / "_base"
    if base.is_dir():
        paths += sorted(base.glob("*.md"))

    lang = rules_dir / "_lang"
    for name in sorted(detect_lang_files(filenames)):
        p = lang / name
        if p.is_file():
            paths.append(p)

    if (s := safe_slug(slug)):
        proj = rules_dir / s
        if proj.is_dir():
            paths += sorted(
                p for p in proj.glob("*")
                if p.suffix in (".md", ".txt") and not p.name.startswith("_")
            )

    return paths


if __name__ == "__main__":
    # ponytail: self-check the two branchy bits (ext match + tier order).
    assert detect_lang_files(["a.py", "b.java", "c.unknown"]) == {"python.md", "java.md"}
    assert safe_slug("p1/386 x") == "p1_386_x"
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        rd = Path(d)
        (rd / "_base").mkdir()
        (rd / "_base" / "default.md").write_text("base")
        (rd / "_lang").mkdir()
        (rd / "_lang" / "python.md").write_text("py")
        (rd / "proj").mkdir()
        (rd / "proj" / "conv.md").write_text("proj")
        out = resolve_rule_paths(rd, ["x.py"], "proj")
        names = [p.name for p in out]
        assert names == ["default.md", "python.md", "conv.md"], names

        # Management helpers: list, read, write, and traversal guard.
        listed = {e["path"] for e in list_rule_files(rd)}
        assert listed == {"_base/default.md", "_lang/python.md", "proj/conv.md"}, listed
        assert read_rule_file(rd, "_base/default.md") == "base"
        meta = write_rule_file(rd, "_base/default.md", "updated base")
        assert meta["path"] == "_base/default.md"
        assert read_rule_file(rd, "_base/default.md") == "updated base"
        for bad in ("../escape.md", "/etc/passwd", "_base/x.txt", ""):
            try:
                _safe_rule_file(rd, bad)
                raise SystemExit(f"FAIL: traversal not blocked for {bad!r}")
            except ValueError:
                pass
    print("rules.py self-check OK")
