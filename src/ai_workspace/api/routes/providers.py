from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.db.session import get_db_session
from ai_workspace.models import User
from ai_workspace.schemas import OpenRouterCredentialRequest, ProviderCredentialResponse
from ai_workspace.services import (
    ProviderCredentialNotFoundError,
    ProviderCredentialService,
)


class CredentialValidationRoute(APIRoute):
    """Keep credential request values out of validation error responses."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    content={"detail": "Invalid provider credential request"},
                )

        return handler


router = APIRouter(
    prefix="/providers",
    tags=["providers"],
    route_class=CredentialValidationRoute,
)


@router.put("/openrouter", response_model=ProviderCredentialResponse)
async def save_openrouter_credential(
    request: OpenRouterCredentialRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderCredentialResponse:
    credential = await ProviderCredentialService(session).save_openrouter(
        current_user.id, request.api_key.get_secret_value(), request.base_url
    )
    return ProviderCredentialResponse.model_validate(credential)


@router.get("/openrouter", response_model=ProviderCredentialResponse)
async def get_openrouter_credential(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProviderCredentialResponse:
    try:
        credential = await ProviderCredentialService(session).get_openrouter(
            current_user.id
        )
    except ProviderCredentialNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider credential not found",
        ) from exc

    return ProviderCredentialResponse.model_validate(credential)


@router.delete(
    "/openrouter",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_openrouter_credential(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    try:
        await ProviderCredentialService(session).delete_openrouter(current_user.id)
    except ProviderCredentialNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider credential not found",
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
