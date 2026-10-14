import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ai_workspace.api.routes.auth import router as auth_router
from ai_workspace.api.routes.chat import router as chat_router
from ai_workspace.api.routes.conversations import router as conversations_router
from ai_workspace.api.routes.provider_credentials import (
    router as provider_credentials_router,
)
from ai_workspace.api.routes.users import router as users_router
from ai_workspace.core.config import Settings, get_settings


def _configure_provider_logging() -> None:
    """Keep upstream headers, bodies and SDK exceptions out of verbose logs."""
    loggers = [logging.getLogger(name) for name in ("openai", "httpx2", "httpcore2")]
    while loggers:
        logger = loggers.pop()
        logger.setLevel(logging.WARNING)
        loggers.extend(logger.getChildren())


# Run after the SDK imports, which may enable logging through OPENAI_LOG.
_configure_provider_logging()


def health() -> dict[str, str]:
    return {"status": "ok"}


def create_api_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    production = settings.environment == "production"
    api = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )

    @api.exception_handler(RequestValidationError)
    async def safe_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Never return submitted passwords, invitation codes, or malformed bodies.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": error["loc"], "type": error["type"], "msg": "Invalid value"}
                    for error in exc.errors()
                ]
            },
        )

    for router in (
        users_router,
        auth_router,
        conversations_router,
        chat_router,
        provider_credentials_router,
    ):
        api.include_router(router)
    api.add_api_route("/health", health, methods=["GET"])
    return api


app = create_api_app()
