"""Refreshes Instagram long-lived access tokens before they expire - run
daily from cron. A silently expired token means silently dead comment
automation for that merchant, so any merchant whose token can't be
refreshed gets a platform-bot notification (same notify path as handoff
escalations) telling them to reconnect.

Usage:
    uv run python -m scripts.refresh_instagram_tokens
"""

import datetime as dt
import logging

from sqlalchemy import select

from app.config import settings
from app.db.models import Merchant, MerchantAdmin
from app.db.session import SessionLocal
from app.instagram.oauth import refresh_long_lived_token
from app.telegram.client import TelegramClient

logger = logging.getLogger(__name__)

# Long-lived tokens last ~60 days and refresh any time after 24h of age -
# a 10-day window means ~50 daily cron chances to succeed before expiry.
REFRESH_WINDOW = dt.timedelta(days=10)

_RECONNECT_NOTICE = (
    "⚠️ Instagram ulanishini yangilab bo'lmadi - komment avtomatikasi "
    "to'xtab qolmasligi uchun Instagram hisobingizni qaytadan ulang."
)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + REFRESH_WINDOW

    db = SessionLocal()
    refreshed = failed = 0
    try:
        merchants = db.scalars(
            select(Merchant).where(
                Merchant.instagram_access_token.is_not(None),
                Merchant.instagram_token_expires_at.is_not(None),
                Merchant.instagram_token_expires_at <= cutoff,
            )
        ).all()

        for merchant in merchants:
            try:
                token, expires_at = refresh_long_lived_token(merchant.instagram_access_token)
            except Exception:
                failed += 1
                logger.exception("token refresh failed for merchant %s", merchant.id)
                _notify_admins(db, merchant)
                continue
            merchant.instagram_access_token = token
            merchant.instagram_token_expires_at = expires_at
            db.commit()
            refreshed += 1
    finally:
        db.close()

    print(f"Refreshed {refreshed} tokens, {failed} failures (of {len(merchants)} due)")


def _notify_admins(db, merchant: Merchant) -> None:
    admins = db.scalars(select(MerchantAdmin).where(MerchantAdmin.merchant_id == merchant.id)).all()
    for admin in admins:
        try:
            TelegramClient(settings.platform_bot_token).send_message(admin.telegram_user_id, _RECONNECT_NOTICE)
        except Exception:
            logger.exception("could not notify admin %s of merchant %s", admin.telegram_user_id, merchant.id)


if __name__ == "__main__":
    main()
