from cryptography.fernet import Fernet, InvalidToken

from ai_workspace.core.config import get_settings


class CredentialDecryptionError(Exception):
    """已保存的 API 密钥无法解密。"""


def _get_fernet() -> Fernet:
    return Fernet(get_settings().credential_encryption_key.get_secret_value())


def encrypt_api_key(api_key: str) -> str:
    """加密保存原始 API 密钥，不修改其内容。"""
    return _get_fernet().encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_api_key(encrypted_api_key: str) -> str:
    """解密已保存的密文，并屏蔽底层加密库异常。"""
    fernet = _get_fernet()
    try:
        return fernet.decrypt(encrypted_api_key.encode("ascii")).decode("utf-8")
    except InvalidToken, UnicodeError:
        raise CredentialDecryptionError("Unable to decrypt credential") from None
