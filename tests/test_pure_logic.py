"""Tests for the pure logic ported from v3 — diff + rules resolution.

ponytail: only the branchy bits need tests. No fixtures, no mocks —
these functions do no I/O beyond a tmp rules tree.
"""
from pathlib import Path

from reviewer.diff import dedup_comments, extract_change_id, gerrit_diff_to_unified
from reviewer.models import ReviewComment
from reviewer.rules import detect_lang_files, resolve_rule_paths, safe_slug


def test_extract_change_id_handles_underscored_slugs():
    assert extract_change_id("all_198601") == "198601"
    assert extract_change_id("file_198601_foo") == "198601"
    # v3's split('_')[1] returned 'p1386' here; ours takes the first digit run.
    assert extract_change_id("p1386_x_42") == "42"
    assert extract_change_id("nodigits") == "nodigits"


def test_gerrit_diff_to_unified():
    content = [
        {"ab": ["kept"]},
        {"a": ["gone"], "b": ["added"]},
    ]
    out = gerrit_diff_to_unified("f.py", content)
    assert out.splitlines() == [
        "--- a/f.py",
        "+++ b/f.py",
        " kept",
        "-gone",
        "+added",
    ]


def test_dedup_comments_preserves_order():
    cs = [
        ReviewComment(file="a", line=1, comment="first"),
        ReviewComment(file="a", line=1, comment="dup dropped"),
        ReviewComment(file="a", line=2, comment="kept"),
        ReviewComment(file="b", line=1, comment="kept"),
    ]
    out = dedup_comments(cs)
    assert [(c.file, c.line) for c in out] == [("a", 1), ("a", 2), ("b", 1)]
    assert out[0].comment == "first"  # first wins


def test_detect_lang_files():
    assert detect_lang_files(["a.py", "b.java", "c.unknown"]) == {"python.md", "java.md"}
    assert detect_lang_files(["x.tsx", "y.jsx"]) == {"ts_js_tsx_jsx.md"}
    assert detect_lang_files([]) == set()


def test_safe_slug():
    assert safe_slug("p1/386 x") == "p1_386_x"
    assert safe_slug("") == ""
    assert safe_slug("clean-slug_1.2") == "clean-slug_1.2"


def test_resolve_rule_paths_tier_order(tmp_path: Path):
    (tmp_path / "_base").mkdir()
    (tmp_path / "_base" / "default.md").write_text("base")
    (tmp_path / "_base" / "aaa.md").write_text("base2")  # sorted before default
    (tmp_path / "_lang").mkdir()
    (tmp_path / "_lang" / "python.md").write_text("py")
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "conv.md").write_text("proj")
    (tmp_path / "proj" / "_hidden.md").write_text("skip")  # underscore = skipped

    out = [p.name for p in resolve_rule_paths(tmp_path, ["x.py"], "proj")]
    assert out == ["aaa.md", "default.md", "python.md", "conv.md"]


def test_resolve_rule_paths_no_lang_match(tmp_path: Path):
    (tmp_path / "_base").mkdir()
    (tmp_path / "_base" / "default.md").write_text("base")
    out = [p.name for p in resolve_rule_paths(tmp_path, ["README.txt"], "")]
    assert out == ["default.md"]
