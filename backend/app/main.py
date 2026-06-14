"""FastAPI application entrypoint for the ITR bot backend."""

from __future__ import annotations

from fastapi import FastAPI
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
