"""Regression tests for the embedding choke point (app/nlp/embeddings.py).

BGE-M3 is measurably case-sensitive on short Uzbek fragments - measured
2026-07-15, pre-fix: "Narxi qancha?" scored 0.621 against its own
lowercase twin and 0.583 against "Bu mahsulotning narxi qancha?", both
below the 0.72 FAQ MATCH_THRESHOLD, while the all-lowercase pair scored
0.858. Phone keyboards auto-capitalize the first letter, so real traffic
is mostly the capitalized form - lowercasing at the choke point is what
makes short-fragment matching work at all.
"""

import numpy as np

from app.nlp.embeddings import embed_text, embed_texts


def test_embed_text_is_case_invariant():
    assert embed_text("Narxi qancha?") == embed_text("narxi qancha?")


def test_embed_texts_is_case_invariant():
    # Batch encoding of identical (post-lowercasing) strings is not
    # bit-identical - padding/batching introduces ~1e-7 float noise - so
    # assert with tolerance rather than exact equality.
    upper, lower = embed_texts(["NARXI QANCHA", "narxi qancha"])
    assert np.allclose(upper, lower, atol=1e-6)


def test_short_capitalized_fragment_stays_close_to_longer_phrasing():
    # The pre-fix failure pair: 0.583, far below the 0.72 FAQ threshold.
    # Post-fix this must sit comfortably above it (measured 0.893).
    fragment = np.array(embed_text("Narxi qancha?"))
    faq_question = np.array(embed_text("Bu mahsulotning narxi qancha?"))
    assert float(fragment @ faq_question) > 0.80
