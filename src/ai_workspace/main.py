import logging

from fastapi import FastAPI

from ai_workspace.api.routes.auth import router as auth_router
from ai_workspace.api.routes.chat import router as chat_router
from ai_workspace.api.routes.conversations import router as conversations_router
from ai_workspace.api.routes.providers import router as providers_router
from ai_workspace.api.routes.users import router as users_router
from ai_workspace.core.config import get_settings


def _configure_provider_logging() -> None:
    """Keep upstream headers, bodies and SDK exceptions out of verbose logs."""
    loggers = [logging.getLogger(name) for name in ("openai", "httpx2", "httpcore2")]
    while loggers:
        logger = loggers.pop()
        logger.setLevel(logging.WARNING)
        loggers.extend(logger.getChildren())


# Run after the SDK imports, which may enable logging through OPENAI_LOG.
_configure_provider_logging()

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)
app.include_router(users_router)
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(providers_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
