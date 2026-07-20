"""PBKDF2-HMAC-SHA256 password hashing - stdlib `hashlib` only, no
bcrypt/argon2 dependency needed at this scale. Iteration count follows
OWASP's 2023 PBKDF2-SHA256 minimum (600,000)."""

import hashlib
import hmac
import secrets

_ITERATIONS = 600_000
_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS).hex()
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations_str, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations_str)).hex()
    return hmac.compare_digest(candidate, digest)
