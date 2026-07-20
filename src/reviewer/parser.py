"""Extract a JSON comment array from an LLM's raw stdout.

Ported from v3 (its most battle-tested piece): models emit the array plainly,
inside a ```json fence, or buried in prose. Try each, hardest last.
"""
from __future__ import annotations

import json
import re

from .models import ReviewComment

_SEVERITIES = {"error", "warning", "suggestion"}


def _normalize(items: list) -> list[ReviewComment]:
    """Coerce loose dicts into validated ReviewComments; drop non-dicts."""
    out: list[ReviewComment] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        line = item.get("line", 0)
        sev = item.get("severity", "suggestion")
        out.append(ReviewComment(
            file=str(item.get("file", "general")),
            line=int(line) if str(line).isdigit() else 0,
            severity=sev if sev in _SEVERITIES else "suggestion",
            comment=str(item.get("comment", "")),
            existing_code=str(item.get("existing_code", "")),
            suggestion_code=str(item.get("suggestion_code", "")),
        ))
    return out


def _iter_arrays(raw: str):
    """Yield every balanced [...] substring that parses as a JSON list."""
    i = 0
    while i < len(raw):
        if raw[i] == "[":
            depth = 0
            for j in range(i, len(raw)):
                if raw[j] == "[":
                    depth += 1
                elif raw[j] == "]":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(raw[i:j + 1])
                            if isinstance(data, list) and data:
                                yield data
                        except Exception:
                            pass
                        break
        i += 1


def parse_comments(raw: str) -> list[ReviewComment]:
    """Best-effort extraction of the review array from model output."""
    if not raw:
        return []

    # 1. Clean JSON.
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return _normalize(data)
    except Exception:
        pass

    # 2. Fenced ```json [...] ``` block.
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                return _normalize(data)
        except Exception:
            pass

    # 3. Bracket-count every array, normalize each, take the one yielding the
    #    most valid comments — ponytail: v3 ranked by raw element count, so a
    #    stray [1,2] in prose (2 ints) beat a real 1-comment array and then
    #    normalized to empty. Rank by comment count so junk arrays lose.
    best: list[ReviewComment] = []
    for data in _iter_arrays(raw):
        cand = _normalize(data)
        if len(cand) > len(best):
            best = cand
    return best  # no JSON → no comments, never prose


if __name__ == "__main__":
    # ponytail: self-check the three extraction paths + junk rejection.
    assert parse_comments("") == []
    assert parse_comments("no json here") == []
    plain = '[{"file":"a.py","line":3,"severity":"error","comment":"x"}]'
    assert parse_comments(plain)[0].severity == "error"
    fenced = "prose\n```json\n" + plain + "\n```\nmore"
    assert parse_comments(fenced)[0].line == 3
    buried = "chat [] then " + plain + " tail"
    assert len(parse_comments(buried)) == 1
    # junk array with more elements must not beat the real payload
    assert parse_comments("noise [1,2,3] " + plain)[0].comment == "x"
    assert parse_comments('[{"severity":"bogus"}]')[0].severity == "suggestion"
    print("parser.py self-check OK")
