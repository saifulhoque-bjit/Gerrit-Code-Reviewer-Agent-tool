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
    print("rules.py self-check OK")
