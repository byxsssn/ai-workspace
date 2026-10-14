"""同源生产入口，使用 Uvicorn 的 --factory 选项启动。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from ai_workspace.core.config import get_settings
from ai_workspace.db.session import engine
from ai_workspace.main import create_api_app


async def check_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


def create_app() -> FastAPI:
    settings = get_settings()
    static_dir = settings.frontend_dist.resolve()
    if (
        not (static_dir / "index.html").is_file()
        or not (static_dir / "assets").is_dir()
    ):
        raise RuntimeError("Frontend build is missing; build frontend before starting")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, debug=False, lifespan=lifespan
    )

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if request.url.path.startswith("/assets/") and response.status_code == 200
            else "no-store"
        )
        return response

    @app.get("/readyz", include_in_schema=False)
    async def ready() -> JSONResponse:
        try:
            async with asyncio.timeout(2):
                await check_database()
        except Exception:  # noqa: BLE001 - 探测异常可能包含数据库凭据。
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/", include_in_schema=False)
    async def homepage() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    app.mount("/api", create_api_app(settings))
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"))
    return app
