"""The live-run event bus.

The pipeline is synchronous, blocking code (PDF parsing, an HTTP call to
Gemini, pure-Python rules) and runs in a worker thread so it cannot block the
event loop. The UI listens over SSE on the event loop. This is the bridge
between the two, and the only place that threading concern lives.

Events are also persisted as RunStep rows by the runner, so a browser that
connects late - or opens a run from the dashboard tomorrow - replays exactly
what happened rather than seeing nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


class RunEventBus:
    """Fan-out of run events to any number of SSE listeners."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the event loop, so worker threads can post into it."""
        self._loop = loop

    # -- listening (event loop side) ---------------------------------------
    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        listeners = self._subscribers.get(run_id)
        if not listeners:
            return
        if queue in listeners:
            listeners.remove(queue)
        if not listeners:
            self._subscribers.pop(run_id, None)

    # -- publishing (worker thread side) -----------------------------------
    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """Post an event from any thread. Never raises into the pipeline.

        A failure to deliver a progress update must not fail the run itself -
        the run's result is persisted regardless, and the UI falls back to
        replaying stored steps.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._deliver, run_id, event)
        except RuntimeError:  # pragma: no cover - loop shutting down
            pass

    def _deliver(self, run_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(run_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A listener that cannot keep up is dropped rather than
                # allowed to stall the run.
                log.warning("dropping slow SSE listener for run %s", run_id)
                self.unsubscribe(run_id, queue)


bus = RunEventBus()


def sse_format(event: dict[str, Any]) -> str:
    """Render one Server-Sent Event frame."""
    name = event.get("type", "message")
    payload = json.dumps(event, default=str)
    return f"event: {name}\ndata: {payload}\n\n"
