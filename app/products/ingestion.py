"""Creates/updates Product rows from Telegram channel posts - shared by
the forward-match lazy-ingest branch (app/telegram/webhook.py, when a
customer forwards a post that isn't a known product yet but comes from
the merchant's own channel) and the passive channel_post webhook handler
(checkpoint 4). Part of the channel-as-catalog pivot: product intake is
automatic channel ingestion, not manual upload - see the pivot plan.
"""

import io
import logging

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Merchant, Product
from app.image_search.embeddings import embed_image
from app.nlp.embeddings import embed_text
from app.nlp.price_parsing import parse_price
from app.telegram.client import TelegramClient

logger = logging.getLogger(__name__)

_DEFAULT_NAME = "Mahsulot"


def _derive_name(caption: str | None) -> str:
    if not caption:
        return _DEFAULT_NAME
    first_line = caption.strip().splitlines()[0].strip()
    return first_line[:255] if first_line else _DEFAULT_NAME


def _derive_description(caption: str | None) -> str | None:
    """Everything after the caption's first line (which _derive_name
    already used as the product's short title) - sizes, colors,
    attributes, delivery notes merchants often add below the headline.
    Previously discarded entirely (only the first line was ever stored
    anywhere) - a real gap for grounded answering, which needs to answer
    from what the merchant actually wrote, not a 255-char fragment of
    it."""
    if not caption:
        return None
    lines = caption.strip().splitlines()
    rest = "\n".join(lines[1:]).strip()
    return rest or None


def ingest_post(
    db: Session,
    merchant: Merchant,
    source_channel_id: int,
    source_message_id: int,
    photo_file_id: str | None,
    caption_or_text: str | None,
) -> Product:
    """Looked up by (source_channel_id, source_message_id), not
    re-embedded/re-searched against the existing catalog - an
    edited_channel_post reuses the same message_id, which is how this
    tells create from update apart, with no similarity-based dedup
    needed. Downloading/embedding a photo is best-effort (logged, not
    raised) so a transient Telegram API hiccup doesn't lose the rest of
    the post's data (name, price)."""
    product = db.scalar(
        select(Product).where(
            Product.merchant_id == merchant.id,
            Product.source_channel_id == source_channel_id,
            Product.source_message_id == source_message_id,
        )
    )
    name = _derive_name(caption_or_text)
    description = _derive_description(caption_or_text)

    if product is None:
        product = Product(
            merchant_id=merchant.id,
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            name=name,
            description=description,
        )
        db.add(product)
    else:
        product.name = name
        product.description = description

    # BGE-M3 text embedding for grounded-answering retrieval
    # (app/products/retrieval.py) - kept in sync on every create/update,
    # same as image_embedding below, so a newly-ingested product is
    # immediately groundable without a separate backfill step.
    product.embedding = embed_text(f"{name}\n{description}" if description else name)

    if photo_file_id is not None:
        try:
            photo_bytes = TelegramClient(merchant.telegram_bot_token).download_photo(photo_file_id)
            image = Image.open(io.BytesIO(photo_bytes))
            product.image_embedding = embed_image(image)
        except Exception:
            logger.exception("failed to download/embed channel post photo for merchant %s", merchant.id)

    parsed_price = parse_price(caption_or_text) if caption_or_text else None
    if parsed_price is not None:
        product.price, product.currency = parsed_price
        product.price_status = "set"
    elif product.price_status is None:
        # Only set to "missing" on first ingest - an edited post whose
        # new caption simply doesn't repeat the price shouldn't clobber
        # an already-"set"/"pending_merchant" status.
        product.price_status = "missing"

    db.flush()
    return product
