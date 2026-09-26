from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.fernet import Fernet

from ai_workspace.core import tokens
from ai_workspace.core.config import Settings

TEST_SECRET = "test-only-jwt-secret-not-for-production-" + "x" * 32


@pytest.fixture
def token_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        _env_file=None,
        jwt_secret_key=TEST_SECRET,
        jwt_access_token_expire_minutes=30,
        credential_encryption_key=Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    return settings


@pytest.mark.parametrize("expire_minutes", [5, 30])
def test_access_token_round_trip_and_minimal_claims(
    token_settings: Settings, expire_minutes: int
) -> None:
    token_settings.jwt_access_token_expire_minutes = expire_minutes
    user_id = uuid4()

    token = tokens.create_access_token(user_id)

    assert isinstance(token, str)
    decoded_user_id = tokens.decode_access_token(token)
    assert isinstance(decoded_user_id, UUID)
    assert decoded_user_id == user_id
    claims = jwt.decode(token, TEST_SECRET, algorithms=["HS256"])
    assert set(claims) == {"sub", "iat", "exp"}
    assert claims["sub"] == str(user_id)
    assert claims["exp"] > claims["iat"]
    assert claims["exp"] - claims["iat"] == expire_minutes * 60


def test_different_users_receive_different_tokens(token_settings: Settings) -> None:
    assert tokens.create_access_token(uuid4()) != tokens.create_access_token(uuid4())


def test_tampered_signature_is_rejected(token_settings: Settings) -> None:
    token = tokens.create_access_token(uuid4())
    header, payload, signature = token.split(".")
    signature_bytes = urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    tampered_bytes = bytes([signature_bytes[0] ^ 1]) + signature_bytes[1:]
    tampered_signature = urlsafe_b64encode(tampered_bytes).rstrip(b"=").decode()
    tampered_token = f"{header}.{payload}.{tampered_signature}"

    with pytest.raises(tokens.InvalidAccessTokenError, match="^Invalid access token$"):
        tokens.decode_access_token(tampered_token)


@pytest.mark.parametrize("token", ["", "not-a-jwt", "invalid.jwt.token"])
def test_invalid_token_strings_are_rejected(
    token_settings: Settings, token: str
) -> None:
    with pytest.raises(tokens.InvalidAccessTokenError, match="^Invalid access token$"):
        tokens.decode_access_token(token)


@pytest.mark.parametrize(
    "invalid_case",
    [
        "expired",
        "missing-sub",
        "missing-iat",
        "missing-exp",
        "non-uuid-sub",
        "non-string-sub",
        "wrong-key",
        "hs384",
        "unsigned",
    ],
)
def test_invalid_claims_signatures_and_algorithms_are_rejected(
    token_settings: Settings, invalid_case: str
) -> None:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=30),
    }
    key: str | None = TEST_SECRET
    algorithm = "HS256"
    if invalid_case == "expired":
        claims["iat"] = now - timedelta(days=2)
        claims["exp"] = now - timedelta(days=1)
    elif invalid_case.startswith("missing-"):
        del claims[invalid_case.removeprefix("missing-")]
    elif invalid_case == "non-uuid-sub":
        claims["sub"] = "not-a-uuid"
    elif invalid_case == "non-string-sub":
        claims["sub"] = 123
    elif invalid_case == "wrong-key":
        key = "a-different-test-only-secret-" + "y" * 40
    elif invalid_case == "hs384":
        algorithm = "HS384"
    elif invalid_case == "unsigned":
        key = None
        algorithm = "none"
    token = jwt.encode(claims, key, algorithm=algorithm)

    with pytest.raises(tokens.InvalidAccessTokenError, match="^Invalid access token$"):
        tokens.decode_access_token(token)
