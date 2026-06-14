"""Structured JSON logging with per-session correlation IDs.

Every log record is emitted as a single JSON line so traces can be grepped,
streamed, or fed to a log viewer. A contextvar carries the active session id
so any log line produced while handling a session is automatically tagged.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from .config import settings

session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName",
}

# ANSI colours for the human-readable console formatter.
_COLORS = {
    "DEBUG": "\033[37m", "INFO": "\033[36m", "WARNING": "\033[33m",
    "ERROR": "\033[31m", "CRITICAL": "\033[41m",
}
_RESET = "\033[0m"
_DIM = "\033[2m"


def _extras(record: logging.LogRecord) -> dict[str, object]:
    """Collect non-reserved record attributes (the ``extra=`` payload)."""
    return {k: v for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    """Render log records as compact single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "func": f"{record.funcName}:{record.lineno}",
            "msg": record.getMessage(),
            "session_id": session_id_var.get(),
        }
        payload.update(_extras(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class PrettyFormatter(logging.Formatter):
    """Render a coloured, human-readable single line for live console viewing."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        color = _COLORS.get(record.levelname, "")
        sid = session_id_var.get()
        head = (f"{_DIM}{ts}{_RESET} {color}{record.levelname:<5}{_RESET} "
                f"{_DIM}{record.name}:{record.lineno}{_RESET}")
        if sid:
            head += f" {_DIM}[{sid}]{_RESET}"
        line = f"{head}  {record.getMessage()}"
        extras = _extras(record)
        if extras:
            kv = " ".join(f"{_DIM}{k}={_RESET}{v}" for k, v in extras.items())
            line += f"  {kv}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging() -> None:
    """Attach console + JSON-file handlers to the root logger.

    The console handler is human-readable (or JSON if ``log_pretty`` is off); the
    file handler is always JSON. Verbosity follows ``settings.log_level``.
    """
    root = logging.getLogger()
    if getattr(root, "_itr_configured", False):
        return
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(PrettyFormatter() if settings.log_pretty else JsonFormatter())
    root.addHandler(stream)

    file_handler = logging.FileHandler(settings.logs_dir / "backend.jsonl")
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    # Third-party libraries are noisy at DEBUG; keep them at INFO.
    for noisy in ("LiteLLM", "httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.INFO)
    root._itr_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    """Return a module logger after ensuring logging is configured."""
    configure_logging()
    return logging.getLogger(name)
