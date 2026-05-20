"""Optional bridge to Indic NLP Library.

The analyzer must stay usable with only its core dependencies installed. This
module wraps the small parts of indic-nlp-library that are useful for Tamil
readability and falls back silently when the package or its resources are not
available.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import List

TAMIL_WORD_RE = re.compile(r'[\u0B80-\u0BFF]{2,}')
SENTENCE_RE = re.compile(r'[.!?।\u0964\u0965\n]+')


def fallback_normalize(text: str) -> str:
    text = unicodedata.normalize('NFC', text or '')
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def fallback_words(text: str) -> List[str]:
    return TAMIL_WORD_RE.findall(fallback_normalize(text))


def fallback_sentences(text: str) -> List[str]:
    return [s.strip() for s in SENTENCE_RE.split(text or '') if fallback_words(s)]


def fallback_syllable_count(word: str) -> int:
    word = fallback_normalize(word)
    consonants = len(re.findall(r'[\u0B95-\u0BB9]', word))
    return max(1, consonants)


@lru_cache(maxsize=1)
def _normalizer():
    try:
        from indicnlp.normalize.indic_normalize import IndicNormalizerFactory

        return IndicNormalizerFactory().get_normalizer('ta')
    except Exception:
        return None


def available() -> bool:
    try:
        import indicnlp  # noqa: F401

        return True
    except Exception:
        return False


def normalize(text: str) -> str:
    text = fallback_normalize(text)
    normalizer = _normalizer()
    if normalizer is None:
        return text
    try:
        return fallback_normalize(normalizer.normalize(text))
    except Exception:
        return text


def words(text: str) -> List[str]:
    text = normalize(text)
    try:
        from indicnlp.tokenize import indic_tokenize

        tokens = indic_tokenize.trivial_tokenize(text, lang='ta')
        tamil_tokens = [tok for tok in tokens if TAMIL_WORD_RE.fullmatch(tok)]
        if tamil_tokens:
            return tamil_tokens
    except Exception:
        pass
    return fallback_words(text)


def sentences(text: str) -> List[str]:
    text = normalize(text)
    try:
        from indicnlp.tokenize import sentence_tokenize

        parts = sentence_tokenize.sentence_split(text, lang='ta')
        tamil_parts = [part.strip() for part in parts if words(part)]
        if tamil_parts:
            return tamil_parts
    except Exception:
        pass
    return fallback_sentences(text)


def syllable_count(word: str) -> int:
    word = normalize(word)
    try:
        from indicnlp.syllable import syllabifier

        syllables = syllabifier.orthographic_syllabify(word, 'ta')
        if syllables:
            return max(1, len(syllables))
    except Exception:
        pass
    return fallback_syllable_count(word)


def status() -> dict:
    return {
        'available': available(),
        'normalizer': _normalizer() is not None,
        'mode': 'indic-nlp-library' if available() else 'regex-fallback',
    }
