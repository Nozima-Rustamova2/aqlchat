"""Text embedding service - swappable so the underlying model can change as
better/cheaper options appear (see project notes on treating embedding
services as interface-based modules).

Model choice locked 2026-07-10 after an explicit cross-lingual check: BGE-M3
scored 100% top-1 accuracy retrieving true paraphrases across Uzbek
Latin/Cyrillic/Russian (vs 77.78% for Qwen3-Embedding-0.6B, which
systematically failed Russian-to-Uzbek matching specifically). See the
aqlchat-phase1-plan memory for the full comparison. Confirmed embedding
dimension: 1024 (matches EMBEDDING_DIM in app/db/models.py).

All text is lowercased here, at the single choke point every caller (FAQ
questions, intent anchors, product name+description, inbound queries)
already goes through - BGE-M3 is measurably case-sensitive on short Uzbek
fragments, and phone keyboards auto-capitalize the first letter: measured
2026-07-15, "Narxi qancha?" scored only 0.621 against its own lowercase
twin (below the 0.72 FAQ MATCH_THRESHOLD) and 0.583 against "Bu
mahsulotning narxi qancha?", vs 0.858 for the all-lowercase pair. Longer
capitalized sentences were unaffected; it's specifically short fragments.
Embeddings stored before this change live in a different space - re-embed
them with scripts/backfill_product_embeddings.py --force (covers Products
and Faqs).
"""

import os

from app.config import settings

# huggingface_hub reads HF_HOME at import time, so this must be set before
# sentence_transformers (which imports huggingface_hub/transformers) is
# imported. The C: drive on this dev machine has been observed at 0 bytes
# free; without this, model downloads/cache silently target a nearly-full
# system drive.
os.environ.setdefault("HF_HOME", settings.hf_home)

from sentence_transformers import SentenceTransformer  # noqa: E402

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(settings.embedding_model_name)
    return _model


def embed_text(text: str) -> list[float]:
    return _get_model().encode(text.lower(), normalize_embeddings=True).tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    return _get_model().encode([t.lower() for t in texts], normalize_embeddings=True).tolist()
