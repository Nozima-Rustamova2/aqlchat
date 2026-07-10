from app.nlp.language import classify_cyrillic_language, classify_script


def test_script_classification(mixed_language_examples):
    expected_map = {
        "uz_latin": "latin",
        "uz_cyrillic": "cyrillic",
        "ru": "cyrillic",
        "cyrillic_ambiguous": "cyrillic",
        "mixed_script": "mixed",
    }
    for ex in mixed_language_examples:
        expected = expected_map[ex["script"]]
        actual = classify_script(ex["raw_text"])
        assert actual == expected, f"{ex['id']}: expected script={expected}, got {actual}"


def test_cyrillic_language_classification(mixed_language_examples):
    for ex in mixed_language_examples:
        if ex["script"] not in ("uz_cyrillic", "ru"):
            continue
        expected = "uz" if "uz" in ex["languages_present"] else "ru"
        actual = classify_cyrillic_language(ex["raw_text"])
        assert actual == expected, f"{ex['id']}: expected language={expected}, got {actual} ({ex['raw_text']!r})"


def test_genuinely_ambiguous_cyrillic_word_is_reported_as_ambiguous(mixed_language_examples):
    # "cyrillic_ambiguous" examples have no exclusive-letter or stopword
    # signal either way - classify_cyrillic_language must say so honestly
    # rather than guessing, so callers (transliteration.py) can apply their
    # own run-length-based default instead of this function silently picking one.
    for ex in mixed_language_examples:
        if ex["script"] != "cyrillic_ambiguous":
            continue
        assert classify_cyrillic_language(ex["raw_text"]) == "ambiguous", ex["id"]
