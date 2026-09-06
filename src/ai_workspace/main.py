from fastapi import FastAPI

from ai_workspace.core.config import get_settings

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
