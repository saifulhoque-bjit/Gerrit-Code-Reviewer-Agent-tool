"""In-process pub/sub for live review events (SSE backing store).

ponytail: one asyncio.Queue per subscriber, keyed by change_id. No Redis, no
broker — a localhost single-process tool streams to a handful of browser tabs.
Bounded queues drop nothing important because the review also persists to
SQLite; the stream is a live view, the DB is the source of truth.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, change_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[change_id].append(q)
        return q

    def unsubscribe(self, change_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(change_id)
        if subs and q in subs:
            subs.remove(q)
        if subs is not None and not subs:
            self._subs.pop(change_id, None)

    async def publish(self, change_id: str, event: dict) -> None:
        for q in list(self._subs.get(change_id, [])):
            await q.put(event)


# Sentinel telling an SSE handler the stream is complete and it may close.
DONE = {"type": "done"}
