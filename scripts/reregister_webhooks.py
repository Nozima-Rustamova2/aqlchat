"""Re-registers every Telegram webhook (the platform bot + every tenant
merchant) against the current PUBLIC_BASE_URL. Needed any time that URL
changes - most commonly a dev-session ngrok/cloudflared tunnel restart,
which silently kills the previously-registered webhook URL (Telegram
queues updates for a while, then floods on recovery once a valid webhook
reappears). Production will want the same thing after any real domain
change.

Usage:
    uv run python -m scripts.reregister_webhooks
"""

from app.config import settings
from app.db.models import Merchant
from app.db.session import SessionLocal
from app.telegram.client import TelegramClient


def main() -> None:
    if not settings.public_base_url:
        raise SystemExit("PUBLIC_BASE_URL is not configured - nothing to register against.")

    if settings.platform_bot_token:
        url = f"{settings.public_base_url}/telegram/webhook/platform"
        TelegramClient(settings.platform_bot_token).set_webhook(url, settings.platform_webhook_secret)
        print(f"platform bot -> {url}")
    else:
        print("PLATFORM_BOT_TOKEN not configured - skipping platform bot.")

    db = SessionLocal()
    try:
        merchants = db.query(Merchant).all()
        for merchant in merchants:
            url = f"{settings.public_base_url}/telegram/webhook/tenant/{merchant.webhook_slug}"
            try:
                TelegramClient(merchant.telegram_bot_token).set_webhook(url, merchant.webhook_secret)
                print(f"{merchant.name} ({merchant.id}) -> {url}")
            except Exception as exc:
                print(f"FAILED: {merchant.name} ({merchant.id}): {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
