from fastapi import FastAPI

from ai_workspace.api.routes.auth import router as auth_router
from ai_workspace.api.routes.conversations import router as conversations_router
from ai_workspace.api.routes.users import router as users_router
from ai_workspace.core.config import get_settings

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)
app.include_router(users_router)
app.include_router(auth_router)
app.include_router(conversations_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
