import pytest
from cryptography.fernet import Fernet

from ai_workspace.core import encryption
from ai_workspace.core.config import Settings


@pytest.fixture
def encryption_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        _env_file=None,
        jwt_secret_key="test-only-jwt-secret-not-used-for-encryption",
        credential_encryption_key=Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setattr(encryption, "get_settings", lambda: settings)
    return settings


def test_api_key_encryption_round_trip(encryption_settings: Settings) -> None:
    api_key = "test-only-api-key"

    encrypted = encryption.encrypt_api_key(api_key)

    assert isinstance(encrypted, str)
    assert encrypted != api_key
    assert encryption.decrypt_api_key(encrypted) == api_key


def test_api_key_original_content_is_preserved(encryption_settings: Settings) -> None:
    api_key = " \t test-only-key-原文\n  "

    assert encryption.decrypt_api_key(encryption.encrypt_api_key(api_key)) == api_key


def test_invalid_ciphertext_raises_project_error(encryption_settings: Settings) -> None:
    wrong_key_token = (
        Fernet(Fernet.generate_key()).encrypt(b"test-only-key").decode("ascii")
    )
    for ciphertext in ("", "not-fernet-ciphertext", "无效密文", wrong_key_token):
        with pytest.raises(
            encryption.CredentialDecryptionError, match="^Unable to decrypt credential$"
        ):
            encryption.decrypt_api_key(ciphertext)
