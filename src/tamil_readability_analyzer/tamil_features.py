"""Offline Tamil-oriented book support features.

This module is deliberately rule-based: no paid API or external service is
needed. The outputs are teacher/author support signals, not final linguistic
judgments.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Callable, Dict, List

from . import analytics as _analytics

TAMIL_WORD_RE = re.compile(r'[\u0B80-\u0BFF]{2,}')
SENT_RE = re.compile(r'[.!?।\u0964\u0965\n]+')

IDIOMS_PROVERBS = {
    'அறிவே செல்வம்': 'Knowledge is true wealth.',
    'ஒற்றுமையே பலம்': 'Unity gives strength.',
    'கற்றது கைமண் அளவு': 'What we know is only a handful.',
    'நன்றி மறப்பது நன்றன்று': 'Do not forget gratitude.',
    'அளவுக்கு மீறினால் அமிர்தமும் நஞ்சு': 'Even good things become harmful in excess.',
    'ஆற்றில் போட்டாலும் அளந்து போடு': 'Be careful even when giving freely.',
    'காக்கைக்கும் தன் குஞ்சு பொன் குஞ்சு': 'Everyone loves their own child.',
    'தீயினால் சுட்ட புண்': 'Words can hurt deeply.',
}

SPOKEN_PATTERNS = {
    'இருக்கார்': 'இருக்கிறார்',
    'இருக்காங்க': 'இருக்கிறார்கள்',
    'சொன்னாங்க': 'சொன்னார்கள்',
    'போறேன்': 'போகிறேன்',
    'வர்றேன்': 'வருகிறேன்',
    'பாக்கலாம்': 'பார்க்கலாம்',
    'பண்ண': 'செய்ய',
    'பண்ணி': 'செய்து',
    'கிட்ட': 'அருகில் / இடம்',
}

ABSTRACT_SUFFIXES = ('மை', 'த்துவம்', 'வியல்', 'நிலை', 'பாடு', 'வாதம்')
PARTICIPLE_ENDINGS = ('த்து', 'ந்து', 'ட்டு', 'க்கொண்டு', 'விட்டு', 'செய்து')
HONORIFIC_ENDINGS = ('ஆர்கள்', 'கிறார்கள்', 'ந்தார்கள்', 'ட்டார்கள்', 'ினார்கள்')

LADDER_SEEDS = [
    ('வீடு', 'இல்லம்', 'குடியிருப்பு'),
    ('மழை', 'சாரல்', 'பருவமழை'),
    ('காற்று', 'தென்றல்', 'வளிமண்டலம்'),
    ('நீர்', 'தண்ணீர்', 'நீர்வளம்'),
    ('கதை', 'சிறுகதை', 'இலக்கியம்'),
    ('நண்பன்', 'தோழன்', 'சகோதரத்துவம்'),
    ('மரம்', 'தாவரம்', 'சுற்றுச்சூழல்'),
    ('வேலை', 'தொழில்', 'பொருளாதாரம்'),
]

EMOTION_WORDS = {'மகிழ்ச்சி','சோகம்','அச்சம்','கோபம்','அன்பு','பயம்','நம்பிக்கை','கருணை'}
ACTION_WORDS = {'சென்றார்','சென்றது','பார்த்தார்','படித்தார்','விளையாடினார்','சொன்னார்','கேட்டார்','ஓடினார்','நடந்தார்','செய்தார்'}
PLACE_HINTS = ('ஊர்','நகர்','பள்ளி','வீடு','காடு','கடல்','மலை','ஆறு','கோவில்','தெரு')


def normalize(text: str) -> str:
    text = unicodedata.normalize('NFC', text or '')
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def words(text: str) -> List[str]:
    return TAMIL_WORD_RE.findall(normalize(text))


def sentences(text: str) -> List[str]:
    return [s.strip() for s in SENT_RE.split(text or '') if len(words(s)) > 0]


def detect_poem_prose(text: str) -> Dict:
    lines = [ln.strip() for ln in (text or '').splitlines() if ln.strip()]
    sents = sentences(text)
    short_lines = sum(1 for ln in lines if 1 <= len(words(ln)) <= 7)
    punct_lines = sum(1 for ln in lines if re.search(r'[.!?।\u0964\u0965]$', ln))
    endings = [words(ln)[-1][-2:] for ln in lines if words(ln)]
    repeated_endings = sum(c for c in Counter(endings).values() if c > 1)
    score = 0
    if len(lines) >= 4 and short_lines / max(1, len(lines)) >= 0.55:
        score += 35
    if lines and punct_lines / max(1, len(lines)) < 0.45:
        score += 20
    if repeated_endings >= 2:
        score += 25
    if len(sents) <= max(2, len(lines) // 3):
        score += 10
    kind = 'Poem / song-like' if score >= 55 else 'Prose'
    return {'type': kind, 'poem_score': min(score, 100), 'line_count': len(lines), 'sentence_count': len(sents)}


def detect_idioms(text: str) -> List[Dict]:
    found = []
    norm = normalize(text)
    for phrase, meaning in IDIOMS_PROVERBS.items():
        if phrase in norm:
            wc = len(words(phrase))
            level = 3 if wc <= 2 else 5 if wc <= 4 else 7
            found.append({'phrase': phrase, 'meaning': meaning, 'suggested_grade': level})
    return found


def grammar_load(text: str) -> Dict:
    toks = words(text)
    sents = sentences(text)
    if not toks:
        return {'score': 0, 'label': 'No Tamil text', 'signals': []}
    long_words = [w for w in toks if len(w) >= 10]
    abstract = [w for w in toks if w.endswith(ABSTRACT_SUFFIXES)]
    participles = [w for w in toks if w.endswith(PARTICIPLE_ENDINGS)]
    honorifics = [w for w in toks if w.endswith(HONORIFIC_ENDINGS)]
    avg_sent = sum(len(words(s)) for s in sents) / max(1, len(sents))
    score = min(100, round(
        len(long_words) / len(toks) * 35 +
        len(abstract) / len(toks) * 30 +
        len(participles) / len(toks) * 20 +
        len(honorifics) / len(toks) * 10 +
        max(0, avg_sent - 8) * 2
    ))
    label = 'Low' if score < 25 else 'Moderate' if score < 55 else 'High'
    return {
        'score': score,
        'label': label,
        'avg_sentence_words': round(avg_sent, 1),
        'signals': [
            {'name': 'Long/compound words', 'count': len(long_words), 'examples': long_words[:12]},
            {'name': 'Abstract nouns', 'count': len(abstract), 'examples': abstract[:12]},
            {'name': 'Participial chains', 'count': len(participles), 'examples': participles[:12]},
            {'name': 'Honorific/complex verb forms', 'count': len(honorifics), 'examples': honorifics[:12]},
        ],
    }


def spoken_tamil(text: str) -> Dict:
    norm = normalize(text)
    hits = []
    for spoken, formal in SPOKEN_PATTERNS.items():
        if spoken in norm:
            hits.append({'spoken': spoken, 'formal': formal})
    return {'count': len(hits), 'items': hits, 'label': 'Spoken/colloquial Tamil present' if hits else 'Mostly formal/literary Tamil'}


def rewrite_levels(text: str) -> Dict:
    sents = sentences(text)[:6]
    levels = {
        'std_1_2': [],
        'std_3_5': [],
        'std_6_8': [],
    }
    for s in sents:
        toks = words(s)
        if len(toks) > 8:
            midpoint = max(4, len(toks) // 2)
            levels['std_1_2'].append(' '.join(toks[:midpoint]) + '. ' + ' '.join(toks[midpoint:]) + '.')
        else:
            levels['std_1_2'].append(s)
        levels['std_3_5'].append(s)
        levels['std_6_8'].append(s)
    return {
        'std_1_2': ' '.join(levels['std_1_2']),
        'std_3_5': ' '.join(levels['std_3_5']),
        'std_6_8': ' '.join(levels['std_6_8']),
        'note': 'Offline rewrite levels use safe structural simplification. Teacher review is recommended.',
    }


def vocabulary_ladder(text: str, stem_fn: Callable[[str], str] | None = None) -> List[Dict]:
    toks = set(words(text))
    ladders = []
    for group in LADDER_SEEDS:
        present = [w for w in group if w in toks]
        if present:
            ladders.append({'ladder': list(group), 'present': present})
    return ladders


def read_aloud_script(text: str) -> Dict:
    out = []
    for s in sentences(text)[:20]:
        toks = words(s)
        hard = [w for w in toks if len(w) >= 9][:5]
        pause = s
        pause = re.sub(r'(ஆனால்|எனவே|மேலும்|அதனால்|ஏனெனில்)', r' / \1', pause)
        out.append({
            'sentence': s,
            'script': pause,
            'teacher_prompt': 'இந்த வாக்கியத்தில் என்ன நடந்தது?' if len(toks) > 6 else 'இந்த வாக்கியத்தை மீண்டும் வாசி.',
            'practice_words': hard,
        })
    return {'items': out}


def illustration_cues(text: str) -> Dict:
    toks = words(text)
    places = sorted({w for w in toks if w.endswith(PLACE_HINTS)})[:12]
    emotions = sorted({w for w in toks if w in EMOTION_WORDS})[:12]
    actions = sorted({w for w in toks if w in ACTION_WORDS})[:12]
    objects = [w for w, _ in Counter(toks).most_common(20) if w not in places and w not in emotions and w not in actions][:12]
    return {'places': places, 'emotions': emotions, 'actions': actions, 'objects': objects}


def analyze(text: str, stem_fn: Callable[[str], str] | None = None) -> Dict:
    text = normalize(text)
    return {
        'poem_prose': detect_poem_prose(text),
        'idioms': detect_idioms(text),
        'grammar': grammar_load(text),
        'child_level_features': _analytics.child_level_features(text, stem_fn=stem_fn),
        'spoken_tamil': spoken_tamil(text),
        'rewrite_levels': rewrite_levels(text),
        'vocabulary_ladder': vocabulary_ladder(text, stem_fn=stem_fn),
        'read_aloud': read_aloud_script(text),
        'illustration_cues': illustration_cues(text),
    }
