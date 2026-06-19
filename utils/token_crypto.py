import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import TOKEN_ENCRYPTION_KEY


def _fernet() -> Fernet:
    if not TOKEN_ENCRYPTION_KEY:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY not set in .env")
    return Fernet(TOKEN_ENCRYPTION_KEY.encode() if isinstance(TOKEN_ENCRYPTION_KEY, str) else TOKEN_ENCRYPTION_KEY)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.strip().encode()).decode()


def decrypt_token(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Invalid encrypted token") from e
