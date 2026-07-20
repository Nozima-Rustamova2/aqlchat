"""Website signup/login (app/auth/). Signup collects a password
(app/auth/passwords.py) and sends a 6-digit email code (Resend,
app/auth/email.py) that proves address ownership and completes signup by
logging the merchant in. Login afterward is plain email+password - the
code is a one-time signup step, not an ongoing 2FA. See
EmailVerificationCode/WebSession in app/db/models.py for why sessions are
DB-backed (hashed token, not a JWT).
"""

import datetime as dt
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.email import send_verification_code
from app.auth.passwords import hash_password, verify_password
from app.db.models import EmailVerificationCode, Merchant, WebSession

CODE_LENGTH = 6
CODE_TTL_MINUTES = 10
MAX_VERIFY_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60
SESSION_TTL_DAYS = 30
SESSION_COOKIE_NAME = "dukan_session"


class AlreadyRegistered(Exception):
    """A verified merchant already owns this email (signup path)."""


class InvalidCredentials(Exception):
    """Email/password combo didn't match a verified merchant (login path)."""


class ResendTooSoon(Exception):
    """A code was already sent for this email within the cooldown window."""


class InvalidCode(Exception):
    """Code didn't match, was already used, expired, or ran out of attempts."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"


def _latest_code(db: Session, merchant_id) -> EmailVerificationCode | None:
    return db.scalar(
        select(EmailVerificationCode)
        .where(EmailVerificationCode.merchant_id == merchant_id)
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )


def _issue_code(db: Session, merchant: Merchant) -> None:
    recent = _latest_code(db, merchant.id)
    if recent is not None and recent.consumed_at is None and (_now() - recent.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
        raise ResendTooSoon()

    code = _generate_code()
    db.add(
        EmailVerificationCode(
            merchant_id=merchant.id,
            email=merchant.email,
            code_hash=_hash_token(code),
            expires_at=_now() + dt.timedelta(minutes=CODE_TTL_MINUTES),
        )
    )
    db.commit()
    send_verification_code(merchant.email, code)


def _create_session(db: Session, merchant: Merchant) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        WebSession(
            merchant_id=merchant.id,
            token_hash=_hash_token(token),
            expires_at=_now() + dt.timedelta(days=SESSION_TTL_DAYS),
        )
    )
    db.commit()
    return token


def start_signup(db: Session, owner_name: str, email: str, phone_number: str, password: str) -> Merchant:
    """Get-or-create an unverified Merchant for this email and send a
    fresh code. Re-running signup with the same not-yet-verified email is
    just "resend + update details", not an error - only a previously
    *verified* email blocks it (use login instead)."""
    email = email.lower()
    merchant = db.scalar(select(Merchant).where(Merchant.email == email))
    if merchant is not None and merchant.email_verified_at is not None:
        raise AlreadyRegistered()

    password_hash = hash_password(password)
    if merchant is None:
        merchant = Merchant(email=email, owner_name=owner_name, phone_number=phone_number, password_hash=password_hash)
        db.add(merchant)
    else:
        merchant.owner_name = owner_name
        merchant.phone_number = phone_number
        merchant.password_hash = password_hash
    db.commit()
    db.refresh(merchant)

    _issue_code(db, merchant)
    return merchant


def login(db: Session, email: str, password: str) -> tuple[Merchant, str]:
    """Plain email+password login for an already-verified merchant.
    Returns (merchant, raw session token)."""
    email = email.lower()
    merchant = db.scalar(
        select(Merchant).where(Merchant.email == email, Merchant.email_verified_at.is_not(None))
    )
    if merchant is None or merchant.password_hash is None or not verify_password(password, merchant.password_hash):
        raise InvalidCredentials()

    token = _create_session(db, merchant)
    return merchant, token


def verify_code(db: Session, email: str, code: str) -> tuple[Merchant, str]:
    """Completes signup: proves email ownership and logs the merchant in.
    Returns (merchant, raw session token) - caller sets the token as an
    httponly cookie, it's never stored anywhere but there and the
    session's `token_hash`."""
    email = email.lower()
    merchant = db.scalar(select(Merchant).where(Merchant.email == email))
    if merchant is None:
        raise InvalidCode()

    record = _latest_code(db, merchant.id)
    if record is None or record.consumed_at is not None:
        raise InvalidCode()
    if record.attempts >= MAX_VERIFY_ATTEMPTS or record.expires_at < _now():
        raise InvalidCode()

    if record.code_hash != _hash_token(code):
        record.attempts += 1
        db.commit()
        raise InvalidCode()

    record.consumed_at = _now()
    if merchant.email_verified_at is None:
        merchant.email_verified_at = _now()
    db.commit()

    token = _create_session(db, merchant)
    db.refresh(merchant)
    return merchant, token


def resolve_session(db: Session, token: str) -> Merchant | None:
    if not token:
        return None
    session = db.scalar(select(WebSession).where(WebSession.token_hash == _hash_token(token)))
    if session is None or session.revoked_at is not None or session.expires_at < _now():
        return None
    return db.get(Merchant, session.merchant_id)


def revoke_session(db: Session, token: str) -> None:
    if not token:
        return
    session = db.scalar(select(WebSession).where(WebSession.token_hash == _hash_token(token)))
    if session is not None and session.revoked_at is None:
        session.revoked_at = _now()
        db.commit()
