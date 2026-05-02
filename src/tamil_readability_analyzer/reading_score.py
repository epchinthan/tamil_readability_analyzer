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

GENTLE_NEAR_THRESHOLD = 0.45
NORMAL_NEAR_THRESHOLD = 0.70
STRICT_NEAR_THRESHOLD = 0.82
GENTLE_NEAR_PENALTY = 0.18
GENTLE_WRONG_PENALTY = 0.65
GENTLE_MISSED_PENALTY = 0.90
GENTLE_EXTRA_PENALTY = 0.15


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


def _near_match(a: str, b: str, stem_fn: Callable[[str], str], threshold: float) -> tuple[bool, float]:
    sim = _similar(a, b)
    stem_a = stem_fn(a)
    stem_b = stem_fn(b)
    sim_stem = _similar(stem_a, stem_b)
    if stem_a == stem_b:
        return True, max(sim, sim_stem)
    if sim >= threshold or sim_stem >= threshold:
        return True, max(sim, sim_stem)
    return False, max(sim, sim_stem)


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
    near_threshold = GENTLE_NEAR_THRESHOLD if strictness == 'gentle' else NORMAL_NEAR_THRESHOLD if strictness == 'normal' else STRICT_NEAR_THRESHOLD
    near_penalty = GENTLE_NEAR_PENALTY if strictness == 'gentle' else NEAR_PENALTY
    wrong_penalty = GENTLE_WRONG_PENALTY if strictness == 'gentle' else WRONG_PENALTY
    missed_penalty = GENTLE_MISSED_PENALTY if strictness == 'gentle' else MISSED_PENALTY
    extra_penalty = GENTLE_EXTRA_PENALTY if strictness == 'gentle' else EXTRA_PENALTY

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        exp_slice = expected[i1:i2]
        sp_slice = spoken[j1:j2]
        if tag == 'equal':
            for e, s in zip(exp_slice, sp_slice):
                words.append({'status': 'correct', 'expected': e, 'spoken': s, 'penalty': 0})
                correct += 1
        elif tag == 'delete':
            for e in exp_slice:
                words.append({'status': 'missed', 'expected': e, 'spoken': '', 'penalty': missed_penalty})
                missed += 1
        elif tag == 'insert':
            for s in sp_slice:
                words.append({'status': 'extra', 'expected': '', 'spoken': s, 'penalty': extra_penalty})
                extra += 1
        else:
            pairs = max(len(exp_slice), len(sp_slice))
            for k in range(pairs):
                e = exp_slice[k] if k < len(exp_slice) else ''
                s = sp_slice[k] if k < len(sp_slice) else ''
                if not e:
                    words.append({'status': 'extra', 'expected': '', 'spoken': s, 'penalty': extra_penalty})
                    extra += 1
                elif not s:
                    words.append({'status': 'missed', 'expected': e, 'spoken': '', 'penalty': missed_penalty})
                    missed += 1
                else:
                    near, sim = _near_match(e, s, stem_fn, near_threshold)
                    if near:
                        words.append({'status': 'near', 'expected': e, 'spoken': s, 'similarity': round(sim, 2), 'penalty': near_penalty})
                        near += 1
                    else:
                        words.append({'status': 'wrong', 'expected': e, 'spoken': s, 'similarity': round(sim, 2), 'penalty': wrong_penalty})
                        wrong += 1

    total_expected = max(1, len(expected))
    penalty = sum(float(w.get('penalty', 0)) for w in words)
    accuracy = max(0.0, 100.0 - (penalty / total_expected * 100.0))
    pronunciation_confidence = round((correct + near * 0.65) / total_expected * 100, 1)
    pronunciation_weight = 0.10 if strictness == 'gentle' else 0.08
    final_mark = round(accuracy * (1 - pronunciation_weight) + pronunciation_confidence * pronunciation_weight, 1)

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
