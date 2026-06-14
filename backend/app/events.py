"""In-process pub/sub event bus for streaming progress to the UI over SSE.

Each session has a set of subscriber queues. Agents call ``publish`` to emit a
``StreamEvent``; the SSE endpoint drains a per-connection queue. Every published
event is also logged so the same trace is available offline for debugging.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from ..schemas.events import EventType, StreamEvent
from .logging_setup import get_logger

logger = get_logger(__name__)


class EventBus:
    """Per-session fan-out of progress events to connected SSE clients."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[StreamEvent]]] = defaultdict(set)

    def subscribe(self, session_id: str) -> asyncio.Queue[StreamEvent]:
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        self._subscribers[session_id].add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[StreamEvent]) -> None:
        self._subscribers[session_id].discard(queue)

    async def publish(self, event: StreamEvent) -> None:
        """Broadcast an event to all subscribers of its session."""
        subscribers = len(self._subscribers.get(event.session_id, ()))
        logger.info(
            "stream event",
            extra={"event_type": event.type.value, "event_msg": event.message,
                   "subscribers": subscribers},
        )
        logger.debug("stream event payload",
                     extra={"event_type": event.type.value, "event_data": event.data})
        for queue in list(self._subscribers.get(event.session_id, ())):
            await queue.put(event)

    async def emit(
        self,
        session_id: str,
        type: EventType,
        message: str | None = None,
        **data: Any,
    ) -> None:
        """Convenience wrapper to build and publish a ``StreamEvent``."""
        await self.publish(StreamEvent(
            type=type, session_id=session_id, message=message, data=data))


bus = EventBus()
