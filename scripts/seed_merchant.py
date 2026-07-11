"""Manually insert a merchant row - still useful for scripted/manual
seeding (e.g. test fixtures with fake tokens that would fail a real
getMe), even though self-serve onboarding through the platform bot
(app/onboarding/) is the normal path now.

Usage:
    uv run python -m scripts.seed_merchant --name "Merchant Name" --bot-token "123:ABC"
"""

import argparse
import secrets

import httpx

from app.config import settings
from app.db.models import Merchant, MerchantAdmin
from app.db.session import SessionLocal


def _try_get_me(bot_token: str) -> dict | None:
    """Best-effort - fake tokens used for tests are a real, intentional
    use case here, so a failure just means telegram_bot_id stays None."""
    try:
        response = httpx.get(f"{settings.telegram_api_base}/bot{bot_token}/getMe", timeout=10)
        response.raise_for_status()
        return response.json()["result"]
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--bot-token", required=True)
    parser.add_argument(
        "--admin-telegram-user-id",
        type=int,
        default=None,
        help="Telegram user id to register as this merchant's admin (layer-5 human handoff, "
        "/reply and /release). Sent via the shared platform bot, not this merchant's own bot - "
        "the admin must /start the platform bot first, or notifications will silently fail.",
    )
    args = parser.parse_args()

    bot_info = _try_get_me(args.bot_token)

    db = SessionLocal()
    try:
        merchant = Merchant(
            name=args.name,
            telegram_bot_token=args.bot_token,
            telegram_bot_id=bot_info["id"] if bot_info else None,
            webhook_secret=secrets.token_urlsafe(32),
        )
        db.add(merchant)
        db.flush()

        if args.admin_telegram_user_id is not None:
            db.add(MerchantAdmin(merchant_id=merchant.id, telegram_user_id=args.admin_telegram_user_id))
            print(
                f"NOTE: {args.admin_telegram_user_id} must /start the platform bot before "
                "escalation notifications will reach them."
            )

        db.commit()
        db.refresh(merchant)
    finally:
        db.close()

    print(f"merchant_id: {merchant.id}")
    print(f"webhook_slug: {merchant.webhook_slug}")
    print(f"webhook_secret: {merchant.webhook_secret}")
    if bot_info is None:
        print("WARNING: getMe failed for this token - telegram_bot_id left unset (fine for test fixtures).")
    print(
        "Register this with Telegram:\n"
        f"  https://api.telegram.org/bot{args.bot_token}/setWebhook"
        f"?url=<YOUR_PUBLIC_URL>/telegram/webhook/tenant/{merchant.webhook_slug}"
        f"&secret_token={merchant.webhook_secret}"
    )


if __name__ == "__main__":
    main()
