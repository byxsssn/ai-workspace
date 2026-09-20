from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from ai_workspace.core.config import get_settings

ALGORITHM = "HS256"


class InvalidAccessTokenError(Exception):
    """The access token is invalid or has expired."""


def create_access_token(user_id: UUID) -> str:
    settings = get_settings()
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    return jwt.encode(
        {"sub": str(user_id), "iat": issued_at, "exp": expires_at},
        settings.jwt_secret_key.get_secret_value(),
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> UUID:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[ALGORITHM],
            options={"require": ["sub", "iat", "exp"]},
        )
        return UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidAccessTokenError("Invalid access token") from exc
