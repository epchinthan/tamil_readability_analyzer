"""Tamil child-friendly reading assessment scoring.

The score compares the expected Tamil passage text with the ASR transcript.
It does not compare voices or require a child voice database.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Callable, Dict, List

TAMIL_WORD_RE = re.compile(r'[\u0B80-\u0BFF]{2,}')

NEAR_PENALTY = 0.25
WRONG_PENALTY = 0.75
MISSED_PENALTY = 1.0
EXTRA_PENALTY = 0.20


def normalize_tamil(text: str) -> str:
    text = unicodedata.normalize('NFC', text or '')
    text = re.sub(r'[\u200B-\u200D\uFEFF]', '', text)
    text = re.sub(r'[^\u0B80-\u0BFF\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def tamil_words(text: str) -> List[str]:
    return TAMIL_WORD_RE.findall(normalize_tamil(text))


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def score_reading(
    expected_text: str,
    transcript: str,
    *,
    stem_fn: Callable[[str], str] | None = None,
    strictness: str = 'gentle',
) -> Dict:
    """Return marks and word-level alignment.

    `gentle` is intentionally forgiving for children: stem matches and close
    Tamil spelling variants count as nearly correct, and pronunciation clarity
    is reported separately instead of heavily reducing marks.
    """
    stem_fn = stem_fn or (lambda w: w)
    expected = tamil_words(expected_text)
    spoken = tamil_words(transcript)

    sm = difflib.SequenceMatcher(None, [stem_fn(w) for w in expected], [stem_fn(w) for w in spoken])
    words = []
    correct = near = wrong = missed = extra = 0

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        exp_slice = expected[i1:i2]
        sp_slice = spoken[j1:j2]
        if tag == 'equal':
            for e, s in zip(exp_slice, sp_slice):
                words.append({'status': 'correct', 'expected': e, 'spoken': s, 'penalty': 0})
                correct += 1
        elif tag == 'delete':
            for e in exp_slice:
                words.append({'status': 'missed', 'expected': e, 'spoken': '', 'penalty': MISSED_PENALTY})
                missed += 1
        elif tag == 'insert':
            for s in sp_slice:
                words.append({'status': 'extra', 'expected': '', 'spoken': s, 'penalty': EXTRA_PENALTY})
                extra += 1
        else:
            pairs = max(len(exp_slice), len(sp_slice))
            for k in range(pairs):
                e = exp_slice[k] if k < len(exp_slice) else ''
                s = sp_slice[k] if k < len(sp_slice) else ''
                if not e:
                    words.append({'status': 'extra', 'expected': '', 'spoken': s, 'penalty': EXTRA_PENALTY})
                    extra += 1
                elif not s:
                    words.append({'status': 'missed', 'expected': e, 'spoken': '', 'penalty': MISSED_PENALTY})
                    missed += 1
                else:
                    same_stem = stem_fn(e) == stem_fn(s)
                    sim = _similar(e, s)
                    near_threshold = 0.58 if strictness == 'gentle' else 0.70 if strictness == 'normal' else 0.82
                    if same_stem or sim >= near_threshold:
                        words.append({'status': 'near', 'expected': e, 'spoken': s, 'similarity': round(sim, 2), 'penalty': NEAR_PENALTY})
                        near += 1
                    else:
                        words.append({'status': 'wrong', 'expected': e, 'spoken': s, 'similarity': round(sim, 2), 'penalty': WRONG_PENALTY})
                        wrong += 1

    total_expected = max(1, len(expected))
    penalty = sum(float(w.get('penalty', 0)) for w in words)
    accuracy = max(0.0, 100.0 - (penalty / total_expected * 100.0))
    pronunciation_confidence = round((correct + near * 0.65) / total_expected * 100, 1)
    final_mark = round(accuracy * 0.92 + pronunciation_confidence * 0.08, 1)

    return {
        'expected_word_count': len(expected),
        'spoken_word_count': len(spoken),
        'reading_accuracy': round(accuracy, 1),
        'pronunciation_confidence': pronunciation_confidence,
        'final_mark': final_mark,
        'counts': {
            'correct': correct,
            'near': near,
            'wrong': wrong,
            'missed': missed,
            'extra': extra,
        },
        'words': words,
        'practice_words': [w['expected'] for w in words if w['status'] in {'near', 'wrong', 'missed'} and w.get('expected')][:40],
    }
