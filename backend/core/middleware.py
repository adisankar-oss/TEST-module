import uuid
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        logger.debug(
            "RequestStart request_id=%s path=%s method=%s",
            request_id,
            request.url.path,
            request.method,
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response