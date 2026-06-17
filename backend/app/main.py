"""FastAPI application entrypoint for the ITR bot backend."""

from __future__ import annotations

import asyncio
import os
import shutil
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .logging_setup import configure_logging, get_logger
from .routes import router

configure_logging()
logger = get_logger(__name__)

# Allow additional origins from the CORS_ORIGINS environment variable so the
# Vercel frontend URL can be added at deploy time without a code change.
_base_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
_extra = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
_all_origins = list(dict.fromkeys(_base_origins + _extra))

app = FastAPI(title="ITR Salaried Filler Bot", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_all_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every HTTP request with method, path, status, and duration."""
    start = time.perf_counter()
    logger.info("request start", extra={
        "method": request.method, "path": request.url.path})
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    logger.info("request done", extra={
        "method": request.method, "path": request.url.path,
        "status": response.status_code, "elapsed_ms": elapsed_ms})
    return response


app.include_router(router)


@app.get("/health")
def health() -> dict:
    """Liveness probe and model/config summary."""
    return {
        "status": "ok",
        "assessment_year": settings.assessment_year,
        "financial_year": settings.financial_year,
        "models": {
            "extraction": settings.extraction_model,
            "orchestration": settings.orchestration_model,
        },
        "openai_key_set": bool(settings.openai_api_key),
    }


_UPLOAD_MAX_AGE_SECS: int = 24 * 3600  # 24 hours
_CLEANUP_INTERVAL_SECS: int = 3600     # run every hour


async def _cleanup_uploads_loop() -> None:
    """Periodically delete session upload directories older than 24 hours."""
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECS)
        cutoff = time.time() - _UPLOAD_MAX_AGE_SECS
        uploads_root = settings.uploads_dir
        if not uploads_root.exists():
            continue
        removed = 0
        for entry in uploads_root.iterdir():
            if not entry.is_dir():
                continue
            mtime = entry.stat().st_mtime
            if mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        if removed:
            logger.info("upload cleanup done", extra={"removed_dirs": removed})


@app.on_event("startup")
def _startup() -> None:
    logger.info("backend started", extra={
        "ay": settings.assessment_year, "models_extraction": settings.extraction_model})
    asyncio.create_task(_cleanup_uploads_loop())
