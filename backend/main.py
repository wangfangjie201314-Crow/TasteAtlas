"""TasteAtlas web service.

Serves the single-page frontend and one JSON endpoint:

    POST /api/plan  {"city": "...", "likes": "...", "occasion": "foodie_day"}
      -> 200 {"city", "occasion", "taste_profile", "stops": [...],
              "dropped_by_critic": [...], "qloo_calls": [...]}
      -> 400/500 with {"detail": "..."} on misconfiguration or pipeline failure.

    GET  /api/health -> {"ok": true, "qloo_configured": bool, "llm_configured": bool}
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from typing import Literal

from .agents import TasteAtlasPipeline
from .config import settings
from .llm_client import LLMClient, LLMError
from .qloo_client import QlooClient, QlooError
from .credentials import local_available
from .guided import GuidedError
from .runtime import guided_service
from .tool_models import ResolveRequest, ProfileRequest, GuidedPlanRequest

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tasteatlas")

app = FastAPI(title="TasteAtlas", version="1.0.0")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class PlanRequest(BaseModel):
    city: str = Field(..., min_length=2, max_length=80)
    likes: str = Field(..., min_length=3, max_length=1000)
    occasion: Literal["foodie_day", "date_night", "solo_adventure", "family"] = "foodie_day"

    @field_validator("city", "likes", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


def _build_pipeline() -> TasteAtlasPipeline:
    if not settings.QLOO_API_KEY:
        raise QlooError("QLOO_API_KEY is not configured on the server.")
    if not settings.LLM_API_KEY:
        raise LLMError("LLM_API_KEY is not configured on the server.")
    if not settings.LLM_ENABLED:
        raise LLMError("Generative mode is disabled. Use the guided Qloo agent tool.")
    qloo = QlooClient(
        api_key=settings.QLOO_API_KEY,
        base_url=settings.QLOO_BASE_URL,
        timeout_s=settings.REQUEST_TIMEOUT_S,
    )
    try:
        llm = LLMClient(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL,
            timeout_s=90.0,
        )
    except Exception:
        qloo.close()
        raise
    return TasteAtlasPipeline(qloo, llm)


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "qloo_configured": bool(settings.QLOO_API_KEY) or (settings.LOCAL_QLOO and local_available()),
        "llm_configured": bool(settings.LLM_API_KEY),
        "llm_model": settings.LLM_MODEL,
        "integration_verified": False,
        "guided_enabled": bool(settings.STATE_DIR or settings.DATABASE_URL),
        "llm_enabled": settings.LLM_ENABLED,
    }


def _tool(action,*args):
    try:
        return JSONResponse(getattr(guided_service(),action)(*args))
    except GuidedError as error:
        return JSONResponse({'detail':str(error)},status_code=error.status)
    except QlooError as error:
        return JSONResponse({'detail':str(error)},status_code=502)
    except Exception as error:
        log.error('guided tool failed: %s',type(error).__name__)
        return JSONResponse({'detail':'The tool could not complete this request. No automatic retry was sent.'},status_code=503)


@app.post('/api/tools/resolve')
def resolve_taste(req:ResolveRequest):
    return _tool('resolve',req.city,req.favorite)


@app.post('/api/tools/plan')
def guided_plan(req:GuidedPlanRequest):
    return _tool('plan',req.profile_ref,req.entity_id)


@app.post('/api/tools/state')
def read_guided_state(req:ProfileRequest):
    return _tool('state',req.profile_ref)


@app.post("/api/plan")
def plan(req: PlanRequest) -> JSONResponse:
    pipeline = None
    try:
        pipeline = _build_pipeline()
        result = pipeline.plan(
            city=req.city.strip(),
            likes=req.likes.strip(),
            occasion=req.occasion.strip() or "foodie_day",
        )
        return JSONResponse(result.to_dict())
    except (QlooError, LLMError, RuntimeError) as exc:
        log.warning("plan failed: %s", type(exc).__name__)
        return JSONResponse({"detail": str(exc)}, status_code=400)
    except Exception as exc:  # pragma: no cover - defensive
        log.error("unexpected plan failure: %s", type(exc).__name__)
        return JSONResponse({"detail": "Unexpected server error. Please try again later."}, status_code=500)
    finally:
        if pipeline is not None:
            pipeline.profiler.qloo.close()
            pipeline.profiler.llm.close()


# --- static frontend (no build step) --------------------------------------- #
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "guided.html"))


if __name__ == "__main__":  # local dev convenience: python -m backend.main
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=settings.PORT, reload=True)
