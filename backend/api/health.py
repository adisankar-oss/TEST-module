import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health/live")
async def liveness():
    return {"status": "alive"}


@router.get("/health")
async def health():
    from backend.services.llm.provider_health_monitor import health_monitor
    from backend.config.settings import settings

    status = health_monitor.status_dict()
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "env": settings.APP_ENV,
        "providers": {
            name: "healthy" if s["healthy"] else "degraded"
            for name, s in status.items()
        },
    }


@router.get("/health/ready")
async def readiness():
    from backend.services.llm.provider_health_monitor import health_monitor

    checks = {}
    degraded = []

    # Groq
    groq_ok = health_monitor.is_healthy("groq")
    checks["groq"] = "ok" if groq_ok else "unavailable"
    if not groq_ok:
        degraded.append("groq")

    # Gemini
    gem_ok = health_monitor.is_healthy("gemini")
    checks["gemini"] = "ok" if gem_ok else "unavailable"
    if not gem_ok:
        degraded.append("gemini")

    # Event bus
    try:
        from backend.services.realtime.event_bus import event_bus

        checks["event_bus"] = "ok"
    except Exception as e:
        checks["event_bus"] = "unavailable"
        degraded.append("event_bus")
        logger.error("ReadinessCheck event_bus failed: %s", e)

    overall = "degraded" if degraded else "ready"
    status_code = 503 if degraded else 200

    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "checks": checks,
            "degraded": degraded,
        },
    )