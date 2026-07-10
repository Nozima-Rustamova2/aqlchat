"""Normalize inbound customer text to a canonical script for downstream matching.

Classification and transliteration happen per contiguous same-script run of
tokens, not once for the whole message (see aqlchat-phase1-plan memory for
the reasoning and spike evidence). Within a Cyrillic run:
  - Classified as Uzbek (exclusive letter or Uzbek-stopword signal) - always
    transliterated to Latin, whether it's one word or a whole clause.
  - Classified as Russian, OR genuinely ambiguous (no signal either way) -
    always left untouched, regardless of run length. A length-based tiebreak
    ("a single ambiguous token is probably an embedded loanword, so
    transliterate it") was tried and rejected: it phonetically mangled
    standalone Russian words with no stopword-list coverage (e.g. "Дорого"
    -> "Dorogo", "Понятно" -> "Ponyatno") into meaningless pseudo-Latin.
    Russian has far more standalone short words than Uzbek has embedded
    loanword tokens, so ambiguous Cyrillic - even a single word like
    "скидка"/"чек" that reads fine in either language - defaults to staying
    untouched. This costs some canonicalization coverage for genuinely
    shared loanwords; that's judged a better trade than risking corruption.
  - A single token that itself mixes both scripts (e.g. "нarxi") is treated
    as an accidental keyboard-layout glitch and always repaired, independent
    of the run-level Uzbek/Russian classification above.

Run boundaries are script-transition points between tokens, not punctuation
- confirmed this still segments correctly with commas removed ("salom у вас
есть размер M?" behaves the same without the comma).

KNOWN GAP, not handled: Russian romanized into Latin script (e.g. "u vas est
razmer" instead of "у вас есть размер") is invisible to this module - script
detection has no signal at all when the input is already Latin, so it's
indistinguishable from genuine Uzbek. This rests on an unverified assumption
that customers overwhelmingly type Russian in Cyrillic rather than romanizing
it; that assumption has not been validated against real traffic. If pilot
data shows otherwise, this whole approach needs revisiting - not something a
targeted heuristic can patch, since it would require per-word language
classification even within Latin script.

Also applies a Unicode/apostrophe cleanup pass first: real customer input
uses several different characters for the oʻ/gʻ apostrophe (straight quote,
curly quote, the "correct" modifier letter), and UzTransliterator itself
only ever outputs one of them - without this pass, the same word ends up
with different byte representations depending on whether it went through
transliteration or passed through untouched, which would fragment matching
in the embedding layer downstream.

Does NOT recover dropped diacritics (customer typing "bolsa" instead of
"bo'lsa") - that's a genuinely different, harder problem (needs a
dictionary or language model to guess intent) and is explicitly out of
scope for Phase 1. Logged as a known gap, not silently papered over.
"""

from dataclasses import dataclass
from typing import Literal

from UzTransliterator.UzTransliterator import UzTransliterator

from app.nlp.language import (
    Script,
    classify_cyrillic_language,
    classify_script,
    classify_token,
    clean_unicode,
    tokenize,
)

_transliterator = UzTransliterator()

Language = Literal["uz", "ru", "mixed", "unknown"]


@dataclass
class NormalizationResult:
    normalized_text: str
    script: Script
    detected_language: Language
    transliterated: bool


def _transliterate(text: str) -> str:
    return _transliterator.transliterate(text, from_="cyr", to="lat")


def normalize(text: str) -> NormalizationResult:
    text = clean_unicode(text)
    script = classify_script(text)

    if script in ("latin", "other"):
        return NormalizationResult(text, script, "uz" if script == "latin" else "unknown", False)

    tokens = tokenize(text)
    kinds = [classify_token(t) if t.strip() else "neutral" for t in tokens]

    output: list[str] = []
    any_transliterated = False
    any_untouched_cyrillic = False

    # Group consecutive tokens into runs of the same kind ("neutral" tokens
    # - whitespace/punctuation/digits - attach to whichever run they're
    # inside rather than splitting it), then resolve one run at a time.
    run_kind: str | None = None
    run_tokens: list[str] = []

    def flush_run():
        nonlocal any_transliterated, any_untouched_cyrillic
        if not run_tokens:
            return
        run_text = "".join(run_tokens)
        if run_kind == "cyrillic":
            language = classify_cyrillic_language(run_text)
            if language == "uz":
                output.append(_transliterate(run_text))
                any_transliterated = True
            else:
                # "ru" and "ambiguous" are both left untouched. A length-based
                # tiebreak for ambiguous runs was tried and rejected: short
                # genuine Russian words with no stopword-list coverage (e.g.
                # "Дорого", "Понятно") got phonetically mangled into
                # meaningless pseudo-Latin under a "single ambiguous token =
                # loanword" assumption. Russian has far more standalone short
                # words than Uzbek has embedded loanword tokens, so leaving
                # ambiguous Cyrillic untouched (even a single shared/ambiguous
                # word like "скидка"/"чек") is the safer default - imperfect
                # canonicalization beats corrupting genuine Russian text.
                output.append(run_text)
                any_untouched_cyrillic = True
        else:
            output.append(run_text)

    for token, kind in zip(tokens, kinds):
        if kind == "glitch":
            flush_run()
            run_kind, run_tokens = None, []
            output.append(_transliterate(token))
            any_transliterated = True
            continue
        if kind == "neutral":
            run_tokens.append(token)
            continue
        if kind != run_kind:
            flush_run()
            run_kind, run_tokens = kind, []
        run_tokens.append(token)
    flush_run()

    normalized_text = "".join(output)

    if any_untouched_cyrillic and (any_transliterated or script == "mixed"):
        language: Language = "mixed"
    elif any_untouched_cyrillic:
        language = "ru"
    else:
        language = "uz"

    return NormalizationResult(normalized_text, script, language, any_transliterated)
