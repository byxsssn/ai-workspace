from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.core.tokens import create_access_token
from ai_workspace.db.session import get_db_session
from ai_workspace.schemas import TokenResponse, UserLoginRequest
from ai_workspace.services import (
    InactiveUserError,
    InvalidCredentialsError,
    UserService,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(
    request: UserLoginRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TokenResponse:
    try:
        user = await UserService(session).authenticate(
            email=request.email, password=request.password
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InactiveUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        ) from exc

    return TokenResponse(access_token=create_access_token(user.id))
