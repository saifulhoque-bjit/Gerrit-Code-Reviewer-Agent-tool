"""Pure diff/comment helpers. No I/O — trivially testable."""
from __future__ import annotations

from .models import ReviewComment


def extract_change_id(poll_key: str) -> str:
    """'all_198601' -> '198601', 'file_198601_foo' -> '198601'.

    ponytail: v3 used split('_')[1] which breaks if a segment before the
    numeric id contains no digits. Take the first all-digit segment instead.
    """
    for part in poll_key.split("_"):
        if part.isdigit():
            return part
    return poll_key


def gerrit_diff_to_unified(file_path: str, content: list[dict]) -> str:
    """Convert Gerrit's diff-JSON 'content' array to unified diff text."""
    lines = [f"--- a/{file_path}", f"+++ b/{file_path}"]
    for chunk in content:
        for line in chunk.get("ab", []):
            lines.append(f" {line}")
        for line in chunk.get("a", []):
            lines.append(f"-{line}")
        for line in chunk.get("b", []):
            lines.append(f"+{line}")
    return "\n".join(lines)


def dedup_comments(comments: list[ReviewComment]) -> list[ReviewComment]:
    """Drop repeats sharing a (file, line) key, preserving order."""
    seen: set[tuple[str, int]] = set()
    out: list[ReviewComment] = []
    for c in comments:
        if c.dedup_key not in seen:
            seen.add(c.dedup_key)
            out.append(c)
    return out


if __name__ == "__main__":
    # ponytail: self-check for the two branchy bits.
    assert extract_change_id("all_198601") == "198601"
    assert extract_change_id("file_198601_foo") == "198601"
    assert extract_change_id("p1386_x_42") == "42"  # slug with underscores
    dup = [ReviewComment(file="a", line=1), ReviewComment(file="a", line=1)]
    assert len(dedup_comments(dup)) == 1
    print("diff.py self-check OK")
