"""One-off backfill for a live bug fixed in app/instagram/oauth.py:
exchange_code() used to store the OAuth token-exchange response's
"user_id" field as merchants.instagram_user_id, but that field is an
app-scoped id - a different numeric namespace than the id Meta stamps
into entry[].id on webhook deliveries (graph.instagram.com/me's "id"
field is "the app user's app-scoped ID"; its "user_id" field - "the
Instagram professional account ID" - is the one that matches entry.id).
Every merchant connected before the fix has the wrong-namespace id
stored, so their webhooks are silently dropped ("unknown ig user id ...
dropped" in the worker/router log) and comment-to-DM never fires.

This script re-derives the correct id for each already-connected
merchant from their still-valid stored access token (no reconnect
needed, unless the token has actually expired - see
scripts/refresh_instagram_tokens.py for that separate problem) and
corrects the column in place.

Usage:
    uv run python -m scripts.backfill_instagram_webhook_user_id           # apply
    uv run python -m scripts.backfill_instagram_webhook_user_id --dry-run # report only
"""

import argparse
import logging

import httpx
from sqlalchemy import select

from app.config import settings
from app.db.models import Merchant
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


def _fetch_webhook_user_id(access_token: str) -> int:
    response = httpx.get(
        f"https://graph.instagram.com/{settings.instagram_graph_api_version}/me",
        params={"fields": "user_id", "access_token": access_token},
        timeout=15,
    )
    response.raise_for_status()
    return int(response.json()["user_id"])


def main(dry_run: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    db = SessionLocal()
    corrected = unchanged = failed = 0
    try:
        merchants = db.scalars(
            select(Merchant).where(
                Merchant.instagram_access_token.is_not(None),
                Merchant.instagram_user_id.is_not(None),
            )
        ).all()

        for merchant in merchants:
            try:
                correct_id = _fetch_webhook_user_id(merchant.instagram_access_token)
            except Exception:
                failed += 1
                logger.exception(
                    "could not fetch webhook user id for merchant %s (stored id %s) - "
                    "token may be expired; merchant needs to reconnect via /instagram/connect/{webhook_slug}",
                    merchant.id,
                    merchant.instagram_user_id,
                )
                continue

            if correct_id == merchant.instagram_user_id:
                unchanged += 1
                continue

            print(f"merchant {merchant.id}: instagram_user_id {merchant.instagram_user_id} -> {correct_id}")
            if not dry_run:
                merchant.instagram_user_id = correct_id
                db.commit()
            corrected += 1
    finally:
        db.close()

    verb = "would correct" if dry_run else "corrected"
    print(f"{verb} {corrected}, already correct {unchanged}, failed {failed} (of {len(merchants)} connected)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing it")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
