"""Image embedding service (SigLIP) - swappable, same pattern as
app/nlp/embeddings.py's text embedding wrapper.

Model choice locked 2026-07-11 after an explicit CLIP-vs-SigLIP retrieval
check on real fine-grained product photos (see the aqlchat-phase1-plan
memory for the full spike): SigLIP scored higher on both top-1 (61.1% vs
55.6%) and top-3 (83.3% vs 77.8%) accuracy. Confirmed embedding dimension:
768 (matches IMAGE_EMBEDDING_DIM in app/db/models.py) - a different
embedding space than BGE-M3's 1024-dim text embeddings, not interchangeable
with it.

Neither model supports a confidence threshold the way BGE-M3 does for text
(true/false-match similarities overlapped 30-45 points on real photos, vs
2-12 points for the text pipeline) - see app/image_search/retrieval.py for
why the resulting design is a top-k candidate carousel with customer
confirmation, not a single auto-reply.
"""

import os

from app.config import settings

# Must happen before transformers is imported - see the identical note in
# app/nlp/embeddings.py.
os.environ.setdefault("HF_HOME", settings.hf_home)

import torch  # noqa: E402
from PIL import Image  # noqa: E402
from transformers import AutoModel, AutoProcessor  # noqa: E402

MODEL_NAME = "google/siglip-base-patch16-224"

_model = None
_processor = None


def _get_model_and_processor():
    global _model, _processor
    if _model is None:
        _processor = AutoProcessor.from_pretrained(MODEL_NAME)
        _model = AutoModel.from_pretrained(MODEL_NAME)
        _model.eval()
    return _model, _processor


def embed_image(image: Image.Image) -> list[float]:
    model, processor = _get_model_and_processor()
    inputs = processor(images=[image.convert("RGB")], return_tensors="pt")
    with torch.no_grad():
        feats = model.get_image_features(**inputs)
    # Some transformers versions return a ModelOutput wrapper rather than
    # a bare tensor - normalize either shape (found during the spike).
    if not torch.is_tensor(feats):
        feats = getattr(feats, "image_embeds", None) or getattr(feats, "pooler_output", None)
    feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].tolist()
