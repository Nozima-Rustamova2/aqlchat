"""Resend client for the website's signup/login verification email - one
call, one template, nothing generic. See https://resend.com/docs/api-reference/emails/send-email.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def send_verification_code(email: str, code: str) -> None:
    """Fire-and-log: a failed send shouldn't 500 the signup request (the
    merchant can hit resend), so errors are logged, not raised."""
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not configured - verification code for %s not sent (code=%s)", email, code)
        return

    try:
        response = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.resend_from_email,
                "to": [email],
                "subject": f"{code} - Dukan AI tasdiqlash kodi",
                "html": _html(code),
            },
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("failed to send verification email to %s via Resend", email)


def _html(code: str) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:420px;margin:0 auto;padding:24px">
      <p style="font-size:15px;color:#333">Dukan AI-ga xush kelibsiz! Tasdiqlash kodingiz:</p>
      <p style="font-size:32px;font-weight:700;letter-spacing:0.1em;color:#1E4954;margin:16px 0">{code}</p>
      <p style="font-size:13px;color:#777">Kod 10 daqiqa amal qiladi. Agar bu soʻrovni siz yubormagan boʻlsangiz, shunchaki e'tiborsiz qoldiring.</p>
    </div>
    """
