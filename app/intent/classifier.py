"""Embedding-based intent classification: nearest-anchor-neighbor over a
small hand-built reference set (app/intent/anchors.py), same methodology as
the FAQ cross-lingual check that chose BGE-M3 in the first place. Sits
between the flow executor and FAQ retrieval on one side and the LLM
fallback on the other - see app/intent/router.py for what each label does.
"""

from dataclasses import dataclass

import numpy as np

from app.intent.anchors import ANCHORS
from app.nlp.embeddings import embed_texts

# Calibrated 2026-07-11 against held-out paraphrases NOT in ANCHORS and 5
# adversarial "none" cases (see tests/test_intent_classifier.py):
# true-intent similarities to their nearest correct anchor ranged
# 0.652-0.863 (mean 0.78, 10/10 correctly labeled); none-case similarities
# topped out at 0.669. Small overlap between 0.652 and 0.669 - as with the
# FAQ threshold, biased toward precision: 0.68 sits above the overlap, so
# the one true-intent case that falls inside it (0.652, a "wrong order"
# complaint) is accepted as a false negative (falls through to the next
# pipeline layer) rather than risk misrouting an off-topic message to a
# canned reply.
#
# Two real anchor-coverage gaps were found and fixed during this
# calibration, not just threshold-tuned around - see app/intent/anchors.py
# for both: a Russian human_handoff phrasing and a "wrong order" (as
# opposed to "broken product") complaint phrasing were both missed by the
# original single anchor per intent/language. The complaint gap only
# narrowed (0.629 -> 0.652) rather than fully closing after adding a
# second anchor - left as a documented miss rather than adding more
# anchors chasing this one test sentence, which risks curve-fitting
# instead of real generalization.
CONFIDENCE_THRESHOLD = 0.68

_labels: list[str] = []
_anchor_texts: list[str] = []
for label, utterances in ANCHORS.items():
    for utterance in utterances:
        _labels.append(label)
        _anchor_texts.append(utterance)

_anchor_embeddings: np.ndarray | None = None


def _get_anchor_embeddings() -> np.ndarray:
    global _anchor_embeddings
    if _anchor_embeddings is None:
        _anchor_embeddings = np.array(embed_texts(_anchor_texts))
    return _anchor_embeddings


@dataclass
class IntentMatch:
    label: str
    similarity: float


def classify_intent(text: str) -> IntentMatch | None:
    query_embedding = np.array(embed_texts([text])[0])
    anchor_embeddings = _get_anchor_embeddings()

    similarities = anchor_embeddings @ query_embedding
    best_idx = int(np.argmax(similarities))
    best_similarity = float(similarities[best_idx])

    if best_similarity < CONFIDENCE_THRESHOLD:
        return None

    return IntentMatch(label=_labels[best_idx], similarity=best_similarity)
