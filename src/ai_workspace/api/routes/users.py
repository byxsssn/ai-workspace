from secrets import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_workspace.api.dependencies.auth import get_current_user
from ai_workspace.core.config import get_settings
from ai_workspace.db.session import get_db_session
from ai_workspace.models import User
from ai_workspace.schemas import UserRegisterRequest, UserResponse
from ai_workspace.services import EmailAlreadyRegisteredError, UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    request: UserRegisterRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    expected = get_settings().registration_invite_code
    if expected is not None:
        supplied = request.invite_code.get_secret_value() if request.invite_code else ""
        if not compare_digest(
            supplied.encode("utf-8"), expected.get_secret_value().encode("utf-8")
        ):
            raise HTTPException(status_code=403, detail="Invalid invitation code")
    try:
        user = await UserService(session).register(
            email=request.email, password=request.password
        )
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        ) from exc

    return UserResponse.model_validate(user)


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserResponse:
    return UserResponse.model_validate(current_user)
