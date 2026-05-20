"""Tamil morphology support signals for readability analysis.

This is a rule-based helper, not a full linguistic parser. Its job is to keep
inflected Tamil nouns and likely proper nouns from being treated as plain
unknown vocabulary when a base form or strong name/place signal is available.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Callable, Dict, Iterable, List, Set

TAMIL_WORD_RE = re.compile(r'[\u0B80-\u0BFF]{2,}')

CASE_SUFFIXES = (
    ('ablative', 'யிலிருந்து'), ('ablative', 'இலிருந்து'), ('ablative', 'லிருந்து'),
    ('dative', 'யிற்கு'), ('dative', 'இற்கு'), ('dative', 'க்கு'), ('dative', 'ற்கே'),
    ('locative', 'யில்'), ('locative', 'இல்'), ('locative', 'ல்'),
    ('instrumental', 'யினால்'), ('instrumental', 'இனால்'), ('instrumental', 'னால்'), ('instrumental', 'ஆல்'),
    ('genitive', 'உடைய'), ('genitive', 'யின்'), ('genitive', 'இன்'),
    ('sociative', 'உடன்'), ('sociative', 'யோடு'), ('sociative', 'ஓடு'),
    ('accusative', 'யைப்'), ('accusative', 'ஐப்'), ('accusative', 'யை'), ('accusative', 'ஐ'),
    ('limit', 'வரை'), ('emphatic', 'வே'), ('emphatic', 'யே'), ('emphatic', 'ஏ'),
)
PLURAL_SUFFIXES = ('ங்கள்', 'கள்')
ABSTRACT_SUFFIXES = ('மை', 'த்துவம்', 'வியல்', 'வாதம்', 'நிலை', 'பாடு', 'முறை')
VERB_SUFFIXES = (
    'கிறார்கள்', 'கிறார்', 'கிறாள்', 'கிறான்', 'கிறது', 'கின்றனர்',
    'ந்தார்கள்', 'ந்தார்', 'ந்தாள்', 'ந்தான்', 'ந்தது',
    'ட்டார்கள்', 'ட்டார்', 'ட்டாள்', 'ட்டான்', 'ட்டது',
    'ன்றார்கள்', 'ன்றார்', 'ன்றாள்', 'ன்றான்', 'ன்றது',
    'றார்கள்', 'றார்', 'றாள்', 'றான்', 'றது',
    'ப்பார்கள்', 'ப்பார்', 'ப்பாள்', 'ப்பான்', 'ப்பது',
)
PARTICIPLE_SUFFIXES = ('க்கொண்டு', 'கொண்டு', 'விட்டு', 'த்து', 'ந்து', 'ட்டு', 'செய்து')
SANDHI_REPAIRS = (
    (r'ட்டி$', 'டு'), (r'ட்டு$', 'டு'), (r'ற்றி$', 'று'), (r'ற்ற$', 'று'),
    (r'ண்ண$', 'ண்'), (r'ல்ல$', 'ல்'), (r'ன்ன$', 'ன்'),
)

PLACE_SUFFIXES = (
    'நகர்', 'நகரம்', 'பட்டணம்', 'பட்டினம்', 'பூர்', 'புரம்', 'பூரம்',
    'மலை', 'குன்று', 'கோட்டை', 'நல்லூர்', 'ஊர்', 'கிராமம்', 'தீவு',
    'காடு', 'நாடு', 'மாவட்டம்', 'மாநிலம்', 'தலைநகர்', 'துறை', 'கரை',
)
PERSON_SUFFIXES = (
    'ராஜ்', 'ராஜா', 'ராணி', 'குமார்', 'குமாரன்', 'குமாரி', 'சேகர்',
    'மோகன்', 'தேவி', 'லட்சுமி', 'முருகன்', 'கணேஷ்', 'ராமன்',
    'கிருஷ்ணன்', 'அம்மன்', 'அம்மாள்', 'பிள்ளை', 'ஐயர்', 'தேவர்',
)
NAME_STARTERS = ('ஸ', 'ஜ', 'ஹ', 'ஷ', 'க்ஷ', 'ஃ')
FOREIGN_CLUSTERS = ('க்ஸ்', 'ட்ர', 'ப்ர', 'ஸ்ட்', 'ல்ட்', 'ன்ஸ்', 'ஜ்', 'ஹ்')


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFC', text or '')
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def tamil_words(text: str) -> List[str]:
    return TAMIL_WORD_RE.findall(normalize(text))


def _strip_suffix_once(word: str, suffixes: Iterable[str]) -> tuple[str, str | None]:
    for suffix in sorted(suffixes, key=len, reverse=True):
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[:-len(suffix)], suffix
    return word, None


def _candidate_bases(word: str, stem_fn: Callable[[str], str] | None = None) -> List[str]:
    candidates = [word]
    stem_fn = stem_fn or (lambda w: w)
    current = word
    for _ in range(3):
        before = current
        current, plural = _strip_suffix_once(current, PLURAL_SUFFIXES)
        if plural and current not in candidates:
            candidates.append(current)
        for _, suffix in sorted(CASE_SUFFIXES, key=lambda x: len(x[1]), reverse=True):
            if current.endswith(suffix) and len(current) - len(suffix) >= 2:
                current = current[:-len(suffix)]
                if current not in candidates:
                    candidates.append(current)
                break
        if current == before:
            break
    for c in list(candidates):
        repaired = c
        for pattern, repl in SANDHI_REPAIRS:
            repaired = re.sub(pattern, repl, repaired)
        if repaired and repaired not in candidates:
            candidates.append(repaired)
    for c in list(candidates):
        try:
            stem = stem_fn(c)
        except Exception:
            stem = c
        if stem and stem not in candidates:
            candidates.append(stem)
    return candidates


def analyze_word(word: str, known_stems: Set[str] | None = None, stem_fn: Callable[[str], str] | None = None) -> Dict:
    word = normalize(word)
    known_stems = known_stems or set()
    stem_fn = stem_fn or (lambda w: w)
    base_candidates = _candidate_bases(word, stem_fn)
    stem = base_candidates[-1] if base_candidates else word

    suffixes = []
    temp = word
    plural_base, plural = _strip_suffix_once(temp, PLURAL_SUFFIXES)
    if plural:
        suffixes.append({'type': 'plural', 'suffix': plural})
        temp = plural_base
    for kind, suffix in sorted(CASE_SUFFIXES, key=lambda x: len(x[1]), reverse=True):
        if temp.endswith(suffix) and len(temp) - len(suffix) >= 2:
            suffixes.append({'type': kind, 'suffix': suffix})
            break

    proper_reasons = []
    if any(word.endswith(s) for s in PLACE_SUFFIXES):
        proper_reasons.append('place-name suffix')
    if any(word.endswith(s) for s in PERSON_SUFFIXES):
        proper_reasons.append('person-name suffix')
    if any(word.startswith(s) for s in NAME_STARTERS) or any(s in word for s in FOREIGN_CLUSTERS):
        proper_reasons.append('name/loanword pattern')

    known_base = next((c for c in base_candidates if c in known_stems), None)
    is_abstract = word.endswith(ABSTRACT_SUFFIXES)
    is_verb_like = not suffixes and (word.endswith(VERB_SUFFIXES) or word.endswith(PARTICIPLE_SUFFIXES))
    possible_noun = bool(suffixes or is_abstract or (len(word) >= 4 and not is_verb_like))

    return {
        'word': word,
        'stem': stem,
        'base_candidates': base_candidates[:6],
        'known_base': known_base,
        'known_by_parts': bool(known_base and known_base != word),
        'suffixes': suffixes,
        'possible_noun': possible_noun,
        'abstract_noun': is_abstract,
        'verb_like': is_verb_like,
        'possible_proper_noun': bool(proper_reasons),
        'proper_noun_reasons': proper_reasons,
    }


def analyze_text(text: str, known_stems: Set[str] | None = None, stem_fn: Callable[[str], str] | None = None, limit: int = 40) -> Dict:
    words = tamil_words(text)
    if not words:
        return {'enabled': False, 'message': 'No Tamil words found.'}
    known_stems = known_stems or set()
    rows = [analyze_word(w, known_stems=known_stems, stem_fn=stem_fn) for w in words]
    unique_by_word = {}
    for row in rows:
        unique_by_word.setdefault(row['word'], row)

    inflected = [r for r in rows if r['suffixes']]
    known_by_parts = [r for r in rows if r['known_by_parts']]
    proper = [r for r in unique_by_word.values() if r['possible_proper_noun']]
    possible_nouns = [r for r in rows if r['possible_noun']]
    unknown_roots = [
        r for r in unique_by_word.values()
        if not r['known_base'] and r['stem'] not in known_stems and not r['possible_proper_noun']
    ]
    suffix_counter = Counter(s['type'] for r in rows for s in r['suffixes'])

    return {
        'enabled': True,
        'word_count': len(words),
        'unique_words': len(unique_by_word),
        'possible_noun_count': len(possible_nouns),
        'inflected_word_count': len(inflected),
        'inflected_word_pct': round(len(inflected) / len(words) * 100, 1),
        'known_by_parts_count': len(known_by_parts),
        'known_by_parts_pct': round(len(known_by_parts) / len(words) * 100, 1),
        'possible_proper_noun_count': len(proper),
        'unknown_root_count': len(unknown_roots),
        'suffix_types': dict(suffix_counter),
        'known_by_parts_examples': known_by_parts[:limit],
        'proper_noun_examples': proper[:limit],
        'unknown_root_examples': unknown_roots[:limit],
        'note': 'Rule-based Tamil morphology: checks noun case/plural endings, common abstract noun endings, and possible person/place names.',
    }
