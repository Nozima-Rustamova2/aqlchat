"""Held-out paraphrases NOT in app/intent/anchors.py, and adversarial
"none" cases - same rigor as the FAQ threshold calibration. Testing
against the anchors themselves would be tautological and wouldn't prove
the classifier generalizes.
"""

from app.intent.classifier import classify_intent


def test_greeting_uzbek():
    match = classify_intent("Assalomu alaykum, ishlaringiz yaxshimi")
    assert match is not None
    assert match.label == "greeting"


def test_greeting_russian():
    match = classify_intent("Здравствуйте, как у вас дела")
    assert match is not None
    assert match.label == "greeting"


def test_thanks_uzbek():
    match = classify_intent("Katta rahmat, juda yordam berdingiz")
    assert match is not None
    assert match.label == "thanks"


def test_thanks_russian():
    match = classify_intent("Спасибо огромное за помощь")
    assert match is not None
    assert match.label == "thanks"


def test_human_handoff_uzbek():
    match = classify_intent("Operator bilan gaplashsam bo'ladimi")
    assert match is not None
    assert match.label == "human_handoff"


def test_human_handoff_russian_live_person_phrasing():
    # This specific phrasing ("switch me to a live human") was the case
    # that exposed a real anchor-coverage gap during calibration - the
    # original single Russian anchor ("contact your manager") missed it
    # entirely (scored 0.555, misclassified as greeting).
    match = classify_intent("Переключите меня на живого человека")
    assert match is not None
    assert match.label == "human_handoff"


def test_complaint_uzbek_wrong_order_phrasing():
    # Was a documented miss under the 2026-07-11 calibration (0.652, just
    # below the 0.68 threshold, even after adding a dedicated anchor). The
    # 2026-07-15 lowercasing fix in app/nlp/embeddings.py closed it: the
    # sentence starts with a capital "Buyurtmam" and now scores 0.831 -
    # see the recalibration note in app/intent/classifier.py.
    match = classify_intent("Buyurtmam noto'g'ri kelib qoldi, chalkashib ketdi")
    assert match is not None
    assert match.label == "complaint"


def test_complaint_russian():
    match = classify_intent("Мне прислали не тот заказ, это возмутительно")
    assert match is not None
    assert match.label == "complaint"


def test_product_inquiry_uzbek():
    match = classify_intent("Ushbu mahsulot haqida ko'proq ma'lumot bera olasizmi")
    assert match is not None
    assert match.label == "product_inquiry"


def test_product_inquiry_russian():
    match = classify_intent("Есть ли у этого товара другие цвета")
    assert match is not None
    assert match.label == "product_inquiry"


def test_unrelated_smalltalk_does_not_match():
    assert classify_intent("Bugun havo juda issiq") is None


def test_unrelated_question_does_not_match():
    assert classify_intent("Sizning ish vaqtingiz qachongacha") is None


def test_standalone_ambiguous_word_does_not_match():
    assert classify_intent("Дорого") is None


def test_near_miss_mixed_clause_does_not_match():
    # Mentions "rahmat" but isn't actually expressing thanks yet - a
    # naive keyword-style match would false-positive on this.
    assert classify_intent("Понятно, rahmat aytaman keyinroq") is None


def test_catalog_browsing_is_not_single_product_inquiry():
    assert classify_intent("Katalogingizni ko'rsam bo'ladimi") is None
