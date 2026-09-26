from cryptography.fernet import Fernet, InvalidToken

from ai_workspace.core.config import get_settings


class CredentialDecryptionError(Exception):
    """The stored API key cannot be decrypted."""


def _get_fernet() -> Fernet:
    return Fernet(get_settings().credential_encryption_key.get_secret_value())


def encrypt_api_key(api_key: str) -> str:
    """Encrypt the original API key for storage without normalizing it."""
    return _get_fernet().encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_api_key(encrypted_api_key: str) -> str:
    """Decrypt stored ciphertext without exposing cryptography exceptions."""
    fernet = _get_fernet()
    try:
        return fernet.decrypt(encrypted_api_key.encode("ascii")).decode("utf-8")
    except InvalidToken, UnicodeError:
        raise CredentialDecryptionError("Unable to decrypt credential") from None
