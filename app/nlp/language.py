"""Script and Uzbek/Russian language detection.

Deliberately NOT using a general-purpose language-ID model: fastText's
lid.176 was tested against tests/fixtures/mixed_language_examples.yaml and
found unreliable on this traffic (0/4 correct on Uzbek Cyrillic, 3/18 on
Uzbek Latin - see the aqlchat-phase1-plan memory for the full writeup).
Script detection via Unicode ranges is deterministic and needs no model.
Cyrillic Uzbek-vs-Russian disambiguation only needs a binary answer, which
a small exclusive-letter check + stopword list handles far better than a
176-way classifier ever could on 2-4 word messages.

Classification happens at the token/run level, not the whole message: real
mixed messages are either (a) a single Russian/loanword token embedded in an
otherwise-Uzbek sentence, or (b) a genuine multi-word Russian clause
alongside Uzbek text, and these need different handling (see
transliteration.py). Message-level classify_script() below is kept only as
a coarse label for logging/analytics, not for the transliteration decision.
"""

import re
import unicodedata
from typing import Literal

Script = Literal["latin", "cyrillic", "mixed", "other"]
Language = Literal["uz", "ru", "ambiguous"]
TokenKind = Literal["latin", "cyrillic", "glitch", "neutral"]

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_TOKEN_RE = re.compile(r"\S+|\s+")

# These four letters exist in the Uzbek Cyrillic alphabet but not in Russian
# at all, so their presence is a near-certain signal of Uzbek text.
_UZBEK_EXCLUSIVE_LETTERS = set("ЎўҚқҒғҲҳ")

# Small, hand-built stopword lists for the remaining ambiguous cases (no
# exclusive letters present). Deliberately picks words with low overlap
# with the other language. Expected to grow from real pilot conversations.
_UZ_STOPWORDS = {
    "салом", "нима", "яхши", "ёмон", "керак", "билан", "учун", "менга",
    "сенга", "сизга", "борми", "мавжуд", "буюртма", "нарх", "нархи",
    "тугади", "сиз", "мен", "бизга", "жуда", "илтимос", "кечирасиз", "бу",
}
_RU_STOPWORDS = {
    "здравствуйте", "привет", "спасибо", "пожалуйста", "сколько", "стоит",
    "доставка", "заказ", "оплатил", "оплата", "наличии", "хочу", "могу",
    "человеком", "поговорить", "деньги", "вернуть", "где", "мой", "это",
    "как", "бракованный", "куртка", "вас", "есть", "размер",
}

# Apostrophe-like characters customers actually type for oʻ/gʻ, mapped to
# whatever UzTransliterator itself uses internally (confirmed via testing:
# it produces U+2018 on cyr->lat output, so passthrough Latin text needs to
# match that or the same word ends up with two different representations
# depending on which code path it took).
_APOSTROPHE_VARIANTS = "'’ʻʼ`´"
_CANONICAL_APOSTROPHE = "‘"


def clean_unicode(text: str) -> str:
    """NFKC-normalize and canonicalize oʻ/gʻ apostrophe variants."""
    text = unicodedata.normalize("NFKC", text)
    for variant in _APOSTROPHE_VARIANTS:
        text = text.replace(variant, _CANONICAL_APOSTROPHE)
    return text


def classify_script(text: str) -> Script:
    has_cyrillic = bool(_CYRILLIC_RE.search(text))
    has_latin = bool(_LATIN_RE.search(text))
    if has_cyrillic and has_latin:
        return "mixed"
    if has_cyrillic:
        return "cyrillic"
    if has_latin:
        return "latin"
    return "other"


def classify_cyrillic_language(text: str) -> Language:
    """Classify Cyrillic (or mixed) text as Uzbek or Russian.

    Only meaningful for text that contains Cyrillic characters. Returns
    "ambiguous" when neither signal fires - callers decide the default
    based on run length (see transliteration.py).
    """
    if _UZBEK_EXCLUSIVE_LETTERS & set(text):
        return "uz"

    words = set(re.findall(r"[Ѐ-ӿ]+", text.lower()))
    uz_hits = len(words & _UZ_STOPWORDS)
    ru_hits = len(words & _RU_STOPWORDS)
    if uz_hits > ru_hits:
        return "uz"
    if ru_hits > uz_hits:
        return "ru"
    return "ambiguous"


def tokenize(text: str) -> list[str]:
    """Split into words and whitespace runs, preserving both for reassembly."""
    return _TOKEN_RE.findall(text)


def classify_token(token: str) -> TokenKind:
    has_cyrillic = bool(_CYRILLIC_RE.search(token))
    has_latin = bool(_LATIN_RE.search(token))
    if has_cyrillic and has_latin:
        # A single token mixing both scripts (e.g. "нarxi") is essentially
        # always an accidental keyboard-layout switch, not a deliberate
        # word - no real word mixes scripts internally.
        return "glitch"
    if has_cyrillic:
        return "cyrillic"
    if has_latin:
        return "latin"
    return "neutral"
