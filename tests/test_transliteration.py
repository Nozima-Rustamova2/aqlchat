import re

from app.nlp.language import clean_unicode
from app.nlp.transliteration import normalize

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")


def test_fixture_category_coverage(mixed_language_examples):
    # Guards every loop-based test below against silently iterating zero
    # times (e.g. a fixture script-tag typo would make a whole test category
    # vacuously "pass" without checking anything). Counts include both the
    # original 32-example set and everything added since (short fragments,
    # clause-switch, adversarial ambiguous-Russian cases).
    from collections import Counter

    counts = Counter(ex["script"] for ex in mixed_language_examples)
    assert counts["uz_latin"] >= 18
    assert counts["uz_cyrillic"] >= 4
    assert counts["ru"] >= 8
    assert counts["mixed_script"] >= 2
    assert counts["cyrillic_ambiguous"] >= 1

    translit_expectation_counts = Counter(
        ex.get("transliteration_expectation") for ex in mixed_language_examples
    )
    assert translit_expectation_counts["full"] >= 1
    assert translit_expectation_counts["partial"] >= 1


def test_latin_text_passes_through_unchanged_apart_from_apostrophe_cleanup(mixed_language_examples):
    for ex in mixed_language_examples:
        if ex["script"] != "uz_latin":
            continue
        result = normalize(ex["raw_text"])
        assert result.transliterated is False
        # Apostrophe variants (', ', straight quote) are canonicalized even
        # on the passthrough path, so the same word doesn't end up with two
        # different byte representations depending on whether it went
        # through transliteration.
        assert result.normalized_text == clean_unicode(ex["raw_text"])


def test_uzbek_cyrillic_is_transliterated_to_latin(mixed_language_examples):
    for ex in mixed_language_examples:
        if ex["script"] != "uz_cyrillic":
            continue
        result = normalize(ex["raw_text"])
        assert result.transliterated is True
        assert not _CYRILLIC_RE.search(result.normalized_text), (
            f"{ex['id']}: expected pure Latin output, got {result.normalized_text!r}"
        )


def test_russian_text_is_left_untouched(mixed_language_examples):
    for ex in mixed_language_examples:
        if ex["script"] != "ru":
            continue
        result = normalize(ex["raw_text"])
        assert result.transliterated is False
        assert result.normalized_text == ex["raw_text"]
        assert result.detected_language == "ru"


def test_mixed_script_single_loanword_is_fully_transliterated(mixed_language_examples):
    # A single Russian/loanword token embedded in an otherwise-Uzbek
    # sentence (or a keyboard-glitch token) gets folded into the same
    # Latin canonical form as the rest of the message.
    for ex in mixed_language_examples:
        if ex.get("transliteration_expectation") != "full":
            continue
        result = normalize(ex["raw_text"])
        assert result.transliterated is True
        assert not _CYRILLIC_RE.search(result.normalized_text), (
            f"{ex['id']}: expected pure Latin output, got {result.normalized_text!r}"
        )


def test_mixed_script_clause_switch_leaves_russian_clause_untouched(mixed_language_examples):
    # A genuine multi-word Russian clause alongside Uzbek text must NOT be
    # blanket-transliterated - that would phonetically mangle real Russian
    # into something matching neither Latin Uzbek nor Cyrillic Russian
    # FAQ/catalog content. This is the per-run granularity fix.
    for ex in mixed_language_examples:
        if ex.get("transliteration_expectation") != "partial":
            continue
        result = normalize(ex["raw_text"])
        assert _CYRILLIC_RE.search(result.normalized_text), (
            f"{ex['id']}: expected the Russian clause to remain in Cyrillic, got {result.normalized_text!r}"
        )
        assert result.detected_language == "mixed"


def test_ambiguous_cyrillic_defaults_to_leaving_untouched(mixed_language_examples):
    # No exclusive-letter or stopword signal either way. A length-based
    # tiebreak ("single ambiguous token = loanword, transliterate it") was
    # tried and rejected: it mangled genuine standalone Russian words with
    # no stopword-list coverage (short_06 "Дорого" -> "Dorogo", short_07
    # "Понятно" -> "Ponyatno") into meaningless pseudo-Latin. Leaving
    # ambiguous Cyrillic untouched is the safer default regardless of run
    # length - imperfect canonicalization beats corrupting real text.
    for ex in mixed_language_examples:
        if ex["script"] != "cyrillic_ambiguous":
            continue
        result = normalize(ex["raw_text"])
        assert result.transliterated is False, ex["id"]
        assert result.normalized_text == ex["raw_text"], ex["id"]


def test_known_good_transliterations():
    # Golden values confirmed during the transliteration spike.
    cases = {
        "Салом": "Salom",
        "Нархи қанча?": "Narxi qancha?",
        "Бу мавжудми?": "Bu mavjudmi?",
        "Раҳмат, яхши кун": "Rahmat, yaxshi kun",
    }
    for raw, expected in cases.items():
        result = normalize(raw)
        assert result.normalized_text == expected


def test_dropped_diacritic_is_a_known_gap():
    # Documents the known limitation rather than hiding it: the customer
    # dropped the 'oʻ' diacritic ("bolsa" instead of "bo'lsa"), and since
    # this is pure Latin text it passes through unchanged - the library
    # does not attempt to recover missing diacritics.
    result = normalize("Kofta narxi qancha bolsa?")
    assert result.normalized_text == "Kofta narxi qancha bolsa?"


def test_romanized_russian_is_a_known_gap(mixed_language_examples):
    # Documents, rather than silently allows, a real limitation: Russian
    # typed in Latin script instead of Cyrillic has zero script-level signal
    # and is indistinguishable from genuine Uzbek. This assertion describes
    # CURRENT (imperfect) behavior - if this ever starts failing because the
    # gap got fixed, update/remove it deliberately; don't just delete it to
    # make the suite green.
    ex = next(e for e in mixed_language_examples if e["id"] == "known_gap_romanized_russian")
    result = normalize(ex["raw_text"])
    assert result.transliterated is False
    assert result.detected_language == "uz"  # wrong! it's actually mixed uz/ru - that's the gap
