from typing import Annotated

from pydantic import BaseModel, EmailStr, Field


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=8, max_length=128)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
