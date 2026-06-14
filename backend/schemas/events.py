"""Schemas for Server-Sent Events streamed to the UI.

A single ``StreamEvent`` envelope carries every kind of progress update so the
frontend can switch on ``type`` to drive the live extraction view, the
computation waterfall, and the agent activity timeline.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Discriminator for streamed progress events."""

    AGENT_STEP = "agent.step"
    DOC_STARTED = "doc.started"
    DOC_FIELD = "doc.field.extracted"
    DOC_VALIDATED = "doc.validated"
    DOC_COMPLETED = "doc.completed"
    RECON_FLAG = "recon.flag"
    RECON_DONE = "recon.done"
    COMPUTE_STEP = "compute.step"
    COMPUTE_DONE = "compute.done"
    VERIFICATION = "verification"
    INFO = "info"
    ERROR = "error"


class StreamEvent(BaseModel):
    """Envelope for one progress update pushed over SSE."""

    type: EventType
    session_id: str
    ts: float = Field(default_factory=lambda: time.time())
    # Free-form, type-specific payload (field name/value/confidence, step label, etc.).
    data: dict[str, Any] = Field(default_factory=dict)
    # Short human-readable line for the agent activity panel.
    message: str | None = None
