"""RunManager: tracks background verification runs and fans events out to
SSE subscribers. New subscribers get the full history replayed first."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from planlint.models import RunEvent

_SENTINEL = None


@dataclass
class Run:
    run_id: str
    history: list[RunEvent] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    done: bool = False
    task: asyncio.Task | None = None


class RunManager:
    def __init__(self):
        self._runs: dict[str, Run] = {}

    def create(self, run_id: str) -> Run:
        run = Run(run_id=run_id)
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    async def emit(self, run_id: str, event: RunEvent) -> None:
        run = self._runs[run_id]
        run.history.append(event)
        for queue in run.subscribers:
            queue.put_nowait(event)
        if event.stage in ("done", "error"):
            run.done = True
            for queue in run.subscribers:
                queue.put_nowait(_SENTINEL)

    async def subscribe(self, run_id: str):
        """Async iterator over a run's events: history, then live, until done."""
        run = self._runs[run_id]
        queue: asyncio.Queue = asyncio.Queue()
        run.subscribers.append(queue)
        try:
            for event in list(run.history):
                yield event
            if run.done:
                return
            while True:
                event = await queue.get()
                if event is _SENTINEL:
                    return
                yield event
        finally:
            run.subscribers.remove(queue)
