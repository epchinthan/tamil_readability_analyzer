"""
simplifier.py — Offline Tamil text simplification engine.

Given a target grade level, suggests:
  1. Simpler word replacements for vocabulary above that grade
  2. Sentence rewrites (split + word-swap)
  3. Word-doc export with tracked-changes-style markup

All logic is offline — uses only the loaded grade database.
No AI APIs needed.

Suggestion sources (in priority order):
  A. Morphological family — stems sharing same Tamil root prefix
  B. Meaning KB concept cluster — same concept, lower grade word
  C. Grade-filtered frequency — most common word at ≤target grade
     that shares ≥ first 2 Tamil chars with the hard stem
"""

from __future__ import annotations
import re, json, os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

TAMIL_RE    = re.compile(r'[\u0B80-\u0BFF]{2,}')
SENT_RE     = re.compile(r'(?<=[.!?।\u0964\u0965])\s+|\n+')

# Tamil connectives where long sentences can be split
CONNECTIVES = {
    'ஆனால்', 'எனவே', 'ஆகவே', 'ஏனெனில்', 'ஏனென்றால்', 'மேலும்',
    'அதனால்', 'இதனால்', 'அதாவது', 'அல்லது', 'இருப்பினும்',
    'எனினும்', 'ஆகையால்', 'தவிர', 'மட்டுமல்ல', 'மட்டுமின்றி',
    'ஆகவும்', 'ஆதலால்', 'எனினும்', 'ஆயினும்',
}

MAX_SENTENCE_WORDS = 12   # sentences longer than this get a split suggestion


def _tamil_prefix(stem: str, length: int = 4) -> str:
    """Return first `length` Tamil Unicode characters of stem."""
    chars = [c for c in stem if '\u0B80' <= c <= '\u0BFF']
    return ''.join(chars[:length])


class SimplifierEngine:
    """
    Build once per session from the grade DB, then call suggest_for_text().
    """

    def __init__(self, grade_vocab: Dict[int, set], word_grade_map: Dict[str, int],
                 stem_fn, grade_freq: Optional[Dict[str, int]] = None):
        """
        grade_vocab    : {grade -> set of stems at that grade}
        word_grade_map : {stem -> first_grade}
        stem_fn        : callable(word) -> stem
        grade_freq     : optional {stem -> frequency in grade books} for ranking
        """
        self.grade_vocab    = grade_vocab
        self.word_grade_map = word_grade_map
        self.stem_fn        = stem_fn
        self.grade_freq     = grade_freq or {}

        # Build cumulative vocab per grade
        self.cumulative: Dict[int, set] = {}
        cum = set()
        for g in sorted(grade_vocab):
            cum = cum | grade_vocab[g]
            self.cumulative[g] = set(cum)

        # Build prefix index: first 4 Tamil chars -> list of (stem, grade)
        self._prefix_idx: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        for stem, grade in word_grade_map.items():
            pfx = _tamil_prefix(stem, 4)
            if pfx:
                self._prefix_idx[pfx].append((stem, grade))
        # Also index by 3-char and 2-char prefix for fallback
        self._prefix3: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        self._prefix2: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        for stem, grade in word_grade_map.items():
            p3 = _tamil_prefix(stem, 3)
            p2 = _tamil_prefix(stem, 2)
            if p3: self._prefix3[p3].append((stem, grade))
            if p2: self._prefix2[p2].append((stem, grade))

    # ── Word-level suggestion ──────────────────────────────────────────────

    def word_grade(self, word: str) -> Optional[int]:
        """Return the grade at which this word first appears, or None."""
        stem = self.stem_fn(word)
        return self.word_grade_map.get(stem)

    def is_hard(self, word: str, target_grade: int) -> bool:
        """True if word is not in cumulative vocab at target_grade."""
        stem = self.stem_fn(word)
        cum  = self.cumulative.get(target_grade, set())
        return bool(stem) and stem not in cum

    def suggest_word(self, word: str, target_grade: int,
                     max_suggestions: int = 3) -> List[Dict]:
        """
        Find up to max_suggestions simpler words for `word` at target_grade.
        Returns list of {stem, grade, source, score}.
        Source: 'morphological' | 'prefix' | 'grade_common'
        """
        stem  = self.stem_fn(word)
        cum   = self.cumulative.get(target_grade, set())
        seen  = set()
        candidates = []

        def _add(s, g, source, score_bonus=0):
            if s not in seen and s != stem and len(s) >= 2:
                seen.add(s)
                freq = self.grade_freq.get(s, 0)
                candidates.append({
                    'stem': s, 'grade': g,
                    'source': source,
                    'score': g * 10 - freq * 0.01 + score_bonus,
                })

        # Strategy A: 4-char prefix family (morphological relatives)
        pfx4 = _tamil_prefix(stem, 4)
        if pfx4:
            for s, g in self._prefix_idx.get(pfx4, []):
                if s in cum:
                    _add(s, g, 'morphological')

        # Strategy B: 3-char prefix
        pfx3 = _tamil_prefix(stem, 3)
        if pfx3 and len(candidates) < max_suggestions:
            for s, g in self._prefix3.get(pfx3, []):
                if s in cum:
                    _add(s, g, 'prefix3', score_bonus=5)

        # Strategy C: 2-char prefix
        pfx2 = _tamil_prefix(stem, 2)
        if pfx2 and len(candidates) < max_suggestions:
            for s, g in self._prefix2.get(pfx2, []):
                if s in cum:
                    _add(s, g, 'prefix2', score_bonus=10)

        # Sort: prefer lower grade, then higher frequency
        candidates.sort(key=lambda x: x['score'])
        return candidates[:max_suggestions]

    # ── Sentence-level analysis ────────────────────────────────────────────

    def analyze_sentence(self, sentence: str, target_grade: int) -> Dict:
        """
        Analyse one sentence.
        Returns {
          original, hard_words, rewritten, split_suggestion,
          word_count, hard_count, grade_after_rewrite, changes
        }
        """
        tamil_words = TAMIL_RE.findall(sentence)
        word_count  = len(tamil_words)

        hard = []
        for w in tamil_words:
            if self.is_hard(w, target_grade):
                g = self.word_grade(w)
                sug = self.suggest_word(w, target_grade)
                hard.append({
                    'word':        w,
                    'stem':        self.stem_fn(w),
                    'grade':       g,
                    'suggestions': sug,
                    'best':        sug[0]['stem'] if sug else None,
                })

        # Build rewritten sentence by substituting best suggestions
        rewritten = sentence
        changes = []
        for h in hard:
            if h['best']:
                # Replace the surface word with the best suggestion stem
                # (simple token swap — not grammatically perfect but informative)
                old = h['word']
                new = h['best']
                if old in rewritten:
                    rewritten = rewritten.replace(old, f'[{new}]', 1)
                    changes.append({'from': old, 'to': new,
                                    'grade_from': h['grade'], 'grade_to':
                                    self.word_grade_map.get(h['best'])})
            else:
                # No suggestion — mark the word as needing manual review
                if h['word'] in rewritten:
                    rewritten = rewritten.replace(h['word'],
                                                  f'[{h["word"]}?]', 1)

        # Split suggestion for long sentences
        split_suggestion = None
        if word_count > MAX_SENTENCE_WORDS:
            split_suggestion = self._try_split(sentence)

        return {
            'original':        sentence,
            'word_count':      word_count,
            'hard_count':      len(hard),
            'hard_words':      hard,
            'rewritten':       rewritten,
            'split_suggestion': split_suggestion,
            'changes':         changes,
            'is_complex':      len(hard) > 0 or word_count > MAX_SENTENCE_WORDS,
        }

    def _try_split(self, sentence: str) -> Optional[List[str]]:
        """Try to split a long sentence at a connective."""
        words = sentence.split()
        if len(words) <= MAX_SENTENCE_WORDS:
            return None
        mid = len(words) // 2
        best_i, best_d = None, len(words)
        for i, w in enumerate(words):
            clean = re.sub(r'[^\u0B80-\u0BFF]', '', w)
            if clean in CONNECTIVES:
                d = abs(i - mid)
                if d < best_d and i > 1 and i < len(words) - 2:
                    best_d, best_i = d, i
        if best_i:
            return [
                ' '.join(words[:best_i]) + '.',
                ' '.join(words[best_i:]),
            ]
        # No connective — split at mid
        return [
            ' '.join(words[:mid]) + '.',
            ' '.join(words[mid:]),
        ]

    # ── Full text analysis ─────────────────────────────────────────────────

    def simplify_text(self, text: str, target_grade: int) -> Dict:
        """
        Analyse the full text and return a structured simplification report.
        """
        sentences = [s.strip() for s in SENT_RE.split(text) if s.strip()]
        if not sentences:
            # Single sentence or no punctuation
            sentences = [text.strip()]

        analysed = [self.analyze_sentence(s, target_grade) for s in sentences]

        total_words    = sum(a['word_count'] for a in analysed)
        total_hard     = sum(a['hard_count'] for a in analysed)
        complex_sents  = [a for a in analysed if a['is_complex']]

        return {
            'target_grade':    target_grade,
            'total_sentences': len(analysed),
            'total_words':     total_words,
            'hard_word_count': total_hard,
            'hard_word_pct':   round(total_hard / max(total_words, 1) * 100, 1),
            'complex_sentence_count': len(complex_sents),
            'sentences':       analysed,
        }
