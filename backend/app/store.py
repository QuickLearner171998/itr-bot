"""Local session store (SQLite) and uploaded-file storage.

A session holds the full working state for one filer: profile, form decision,
document checklist, extractions, consolidated input, computation, and guidance.
State is persisted as a JSON blob keyed by session id so a local restart keeps
work intact.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import settings
from .logging_setup import get_logger

logger = get_logger(__name__)

# Source files whose contents change the extracted output (logic + prompts +
# field schema + model params). Their hash is folded into the cache key so any
# edit here auto-invalidates every cached extraction.
_EXTRACTION_SOURCE_FILES = (
    "agents/doc_intel/extract.py",
    "schemas/documents.py",
    "agents/llm.py",
)


@functools.lru_cache(maxsize=1)
def _extraction_fingerprint() -> str:
    """Stable fingerprint of everything that affects extraction output.

    Combines the manual version override, the extraction model + sampling
    params, and a content hash of the extraction source files. Computed once per
    process. Bumping any of these invalidates the content cache so stale,
    pre-fix extractions are never served after a deploy.

    Returns:
        A 16-char hex fingerprint.
    """
    h = hashlib.sha256()
    h.update(settings.extraction_version.encode())
    h.update(settings.extraction_model.encode())
    h.update(settings.extraction_reasoning_effort.encode())
    h.update(str(settings.llm_seed).encode())
    pkg_root = Path(__file__).resolve().parents[1]  # the backend/ package root
    for rel in _EXTRACTION_SOURCE_FILES:
        try:
            h.update((pkg_root / rel).read_bytes())
        except OSError:
            h.update(b"missing")
    return h.hexdigest()[:16]


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
            conn.execute(
                "CREATE TABLE IF NOT EXISTS extraction_cache ("
                "content_hash TEXT PRIMARY KEY, "
                "extraction TEXT NOT NULL, "
                "created REAL NOT NULL)"
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

    @staticmethod
    def _content_hash(data: bytes) -> str:
        # Salt the key with an auto-computed extraction fingerprint so any
        # change to extraction logic / prompts / schema / model auto-invalidates
        # old entries — a deployed fix can never return a stale pre-fix result.
        return hashlib.sha256(
            data + _extraction_fingerprint().encode()).hexdigest()

    def cache_get(self, data: bytes) -> dict[str, Any] | None:
        """Return a cached extraction for ``data`` if one exists and is fresh.

        Args:
            data: Raw file bytes whose SHA-256 is the cache key.

        Returns:
            Deserialised extraction dict, or ``None`` on a miss or expired entry.
        """
        if not settings.extraction_cache_ttl:
            return None
        key = self._content_hash(data)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT extraction, created FROM extraction_cache WHERE content_hash = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        age = time.time() - row["created"]
        if age > settings.extraction_cache_ttl:
            # Expired — evict lazily.
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM extraction_cache WHERE content_hash = ?", (key,))
            return None
        logger.debug("extraction cache hit", extra={"hash": key[:12], "age_s": int(age)})
        return json.loads(row["extraction"])

    def cache_set(self, data: bytes, extraction: dict[str, Any]) -> None:
        """Store an extraction result keyed by the SHA-256 of ``data``.

        Args:
            data: Raw file bytes.
            extraction: Serialised ``DocumentExtraction`` to cache.
        """
        if not settings.extraction_cache_ttl:
            return
        key = self._content_hash(data)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO extraction_cache (content_hash, extraction, created) "
                "VALUES (?, ?, ?)",
                (key, json.dumps(extraction, default=str), time.time()),
            )
        logger.debug("extraction cached", extra={"hash": key[:12]})

    def upload_dir(self, session_id: str) -> Path:
        """Return (creating if needed) the upload directory for a session."""
        path = settings.uploads_dir / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path


store = SessionStore(settings.db_path)
