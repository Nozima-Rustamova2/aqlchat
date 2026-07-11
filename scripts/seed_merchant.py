"""Manually insert a merchant row until the dashboard exists.

Usage:
    uv run python -m scripts.seed_merchant --name "Merchant Name" --bot-token "123:ABC"
"""

import argparse
import secrets

from app.db.models import Merchant
from app.db.session import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--bot-token", required=True)
    parser.add_argument(
        "--admin-chat-id",
        type=int,
        default=None,
        help="Merchant's own Telegram chat id with their bot, for the layer-5 human handoff. "
        "Get it by messaging the bot and checking the webhook's inbound raw_update, or via getUpdates.",
    )
    args = parser.parse_args()

    webhook_secret = secrets.token_urlsafe(32)

    db = SessionLocal()
    try:
        merchant = Merchant(
            name=args.name,
            telegram_bot_token=args.bot_token,
            webhook_secret=webhook_secret,
            admin_chat_id=args.admin_chat_id,
        )
        db.add(merchant)
        db.commit()
        db.refresh(merchant)
    finally:
        db.close()

    print(f"merchant_id: {merchant.id}")
    print(f"webhook_secret: {webhook_secret}")
    print(
        "Register this with Telegram:\n"
        f"  https://api.telegram.org/bot{args.bot_token}/setWebhook"
        f"?url=<YOUR_PUBLIC_URL>/telegram/webhook/{merchant.id}"
        f"&secret_token={webhook_secret}"
    )


if __name__ == "__main__":
    main()
