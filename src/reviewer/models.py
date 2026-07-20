"""Pydantic models shared across the app."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Severity = Literal["error", "warning", "suggestion"]


class ChangedFile(BaseModel):
    path: str
    lines_inserted: int = 0
    lines_deleted: int = 0
    status: str = "M"  # M=modified A=added D=deleted R=renamed

    @property
    def has_changes(self) -> bool:
        return (self.lines_inserted + self.lines_deleted) > 0


class ReviewComment(BaseModel):
    file: str
    line: int = 0
    severity: Severity = "suggestion"
    comment: str = ""
    existing_code: str = ""
    suggestion_code: str = ""

    @property
    def dedup_key(self) -> tuple[str, int]:
        return (self.file, self.line)


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    files_reviewed: int = 0
    elapsed_seconds: float = 0.0
    cost_usd: float = 0.0
