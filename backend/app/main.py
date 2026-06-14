"""FastAPI application entrypoint for the ITR bot backend."""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .logging_setup import configure_logging, get_logger
from .routes import router

configure_logging()
logger = get_logger(__name__)

app = FastAPI(title="ITR Salaried Filler Bot", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
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
            "validation": settings.validation_model,
            "orchestration": settings.orchestration_model,
        },
        "openai_key_set": bool(settings.openai_api_key),
    }


@app.on_event("startup")
def _startup() -> None:
    logger.info("backend started", extra={
        "ay": settings.assessment_year, "models_extraction": settings.extraction_model})
