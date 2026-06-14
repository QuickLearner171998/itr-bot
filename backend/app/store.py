"""Local session store (SQLite) and uploaded-file storage.

A session holds the full working state for one filer: profile, form decision,
document checklist, extractions, consolidated input, computation, and guidance.
State is persisted as a JSON blob keyed by session id so a local restart keeps
work intact.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import settings
from .logging_setup import get_logger

logger = get_logger(__name__)


class SessionStore:
    """Thin SQLite-backed key/value store for session state."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._doc_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "id TEXT PRIMARY KEY, state TEXT NOT NULL, "
                "created REAL DEFAULT (strftime('%s','now')), "
                "updated REAL DEFAULT (strftime('%s','now')))"
            )

    def create(self) -> str:
        """Create a new empty session and return its id."""
        session_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, state) VALUES (?, ?)",
                (session_id, json.dumps({})),
            )
        logger.info("session created", extra={"new_session_id": session_id})
        return session_id

    def get(self, session_id: str) -> dict[str, Any]:
        """Return the stored state dict for a session (empty if unknown)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return json.loads(row["state"]) if row else {}

    def exists(self, session_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return row is not None

    def update(self, session_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Shallow-merge ``patch`` into the session state and persist it."""
        state = self.get(session_id)
        state.update(patch)
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET state = ?, updated = strftime('%s','now') WHERE id = ?",
                (json.dumps(state, default=str), session_id),
            )
        return state

    async def add_document(
        self, session_id: str, doc_type: str, extraction: dict[str, Any], multi: bool
    ) -> None:
        """Persist a finished extraction, serialising concurrent writers.

        Multiple documents extract in parallel background tasks, so the
        read-modify-write of the ``documents`` blob is guarded by a per-session
        lock to prevent lost updates. ``multi`` types accumulate in a list;
        single types overwrite.

        Args:
            session_id: Owning session.
            doc_type: Document type value (the storage key).
            extraction: Serialised ``DocumentExtraction``.
            multi: Whether this doc type supports multiple uploads.
        """
        async with self._doc_locks[session_id]:
            state = self.get(session_id)
            docs = state.get("documents", {})
            if multi:
                slot = docs.get(doc_type)
                docs[doc_type] = (slot + [extraction]) if isinstance(slot, list) else [extraction]
            else:
                docs[doc_type] = extraction
            self.update(session_id, {"documents": docs})

    def upload_dir(self, session_id: str) -> Path:
        """Return (creating if needed) the upload directory for a session."""
        path = settings.uploads_dir / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path


store = SessionStore(settings.db_path)
