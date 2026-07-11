"""Application-level encryption for secrets stored at rest (currently just
`Merchant.telegram_bot_token`). `EncryptedString` is a SQLAlchemy
TypeDecorator, not a plain helper function, specifically so every existing
call site that reads/writes `merchant.telegram_bot_token` as a plain
Python str keeps working unchanged - the column stores ciphertext, the
ORM attribute is still plaintext. See app/db/models.py's Merchant model
and the platform-bot-refactor plan for why encryption was added.

Fernet is randomized (a fresh IV per encryption), so encrypting the same
plaintext twice produces different ciphertext - this is why
`telegram_bot_token` can no longer carry a DB-level `unique=True`
constraint; `Merchant.telegram_bot_id` (the bot's own numeric Telegram
ID, not sensitive, not encrypted) is the real uniqueness key now.
"""

from cryptography.fernet import Fernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not settings.token_encryption_key:
            raise RuntimeError(
                "TOKEN_ENCRYPTION_KEY is not configured - required to read/write "
                "encrypted merchant bot tokens. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        _fernet = Fernet(settings.token_encryption_key.encode())
    return _fernet


class EncryptedString(TypeDecorator):
    """Transparently encrypts on write, decrypts on read. Backed by
    Text (not String) since Fernet ciphertext runs meaningfully longer
    than the plaintext it wraps."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return _get_fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return _get_fernet().decrypt(value.encode()).decode()
