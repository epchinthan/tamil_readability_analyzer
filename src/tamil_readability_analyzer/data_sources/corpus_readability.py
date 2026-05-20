"""Tamil corpus-backed readability signals.

This layer is intentionally additive. It does not replace the existing
grade-vocabulary or analytics reports; it adds TAVI, a Tamil-context lens
inspired by AVI-style vocabulary indexing and adapted for Tamil morphology.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List

from .. import indic_nlp_adapter as _indic_nlp
from .. import tamil_morphology as _tamil_morphology

TAMIL_WORD_RE = re.compile(r'[\u0B80-\u0BFF]{2,}')
SENTENCE_RE = re.compile(r'[.!?।\u0964\u0965\n]+')
TAMIL_CORPUS_DIR = Path('data/tamil_corpus')
CORPUS_STATS = TAMIL_CORPUS_DIR / 'corpus_stats.json'
WORD_FREQUENCY = TAMIL_CORPUS_DIR / 'word_frequency.json'
GRADE_VOCABULARY = TAMIL_CORPUS_DIR / 'grade_vocabulary.json'
WIKI_DB = Path('data/wiki_corpus.db')
VOCAB_INDEX_DB = TAMIL_CORPUS_DIR / 'vocabulary_index.db'

FORMAL_SUFFIXES = ('த்துவம்', 'வியல்', 'வாதம்', 'நிலை', 'முறை', 'சார்', 'பாடு')
COMPOUND_HINTS = ('கொண்டு', 'விட்டு', 'ஆகிய', 'மற்றும்', 'உடைய')


def normalize(text: str) -> str:
    return _indic_nlp.normalize(text)


def tamil_words(text: str) -> List[str]:
    return _indic_nlp.words(text)


def tamil_sentences(text: str) -> List[str]:
    return _indic_nlp.sentences(text)


def band_for_grade(grade: int | None) -> str:
    if grade is None:
        return 'ungraded'
    if grade <= 3:
        return 'school_grade_1_3'
    if grade <= 5:
        return 'school_grade_4_5'
    if grade <= 8:
        return 'school_grade_6_8'
    if grade <= 10:
        return 'school_grade_9_10'
    return 'school_grade_11_12'


def _infer_grade(path: Path) -> int | None:
    for part in path.parts:
        m = re.search(r'(?:class|grade|std|standard)[_\-\s]?0?(\d{1,2})', part, re.I)
        if m:
            g = int(m.group(1))
            if 1 <= g <= 12:
                return g
    m = re.search(r'(?:class|grade|std|standard)[_\-\s]?0?(\d{1,2})', path.name, re.I)
    if m:
        g = int(m.group(1))
        if 1 <= g <= 12:
            return g
    return None


def _source_kind(path: Path) -> str:
    p = str(path).lower()
    if 'textbooks_imported_text' in p or 'samacheer' in p or 'kalvi' in p:
        return 'school_textbook'
    if 'mozhi_ai_tamil_corpus' in p:
        return 'mozhi_ai_tamil_corpus'
    if 'tamil_asr_corpus_transcripts' in p:
        return 'tamil_asr_corpus_transcripts'
    if 'local_large_corpus' in p:
        if 'dinamalar' in p:
            return 'dinamalar'
        if 'tamilmurasu' in p:
            return 'tamilmurasu'
        if 'tamil_articles_corpus' in p:
            return 'tamil_articles_corpus'
        if 'ta_text_corpus' in p:
            return 'ta_text_corpus'
        return 'local_large_corpus'
    if 'wiki' in p:
        return 'wikipedia'
    if 'government' in p or 'gov' in p:
        return 'government'
    if 'news' in p:
        return 'news'
    if 'literary' in p or 'classic' in p:
        return 'literary'
    return 'local_text'


def _iter_text_files(root: Path) -> Iterable[Path]:
    candidates = [
        root / 'textbooks_imported_text',
        root / 'data' / 'corpus_sources',
        root / 'data' / 'wiki_text',
    ]
    for base in candidates:
        if not base.exists():
            continue
        yield from sorted(base.rglob('*.txt'))


def build_corpus(root: str | Path = '.', stem_fn: Callable[[str], str] | None = None) -> Dict:
    """Build local corpus JSON files from imported textbook/corpus text files."""
    root = Path(root)
    stem_fn = stem_fn or (lambda w: w)
    files = list(_iter_text_files(root))
    TAMIL_CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    global_freq: Counter[str] = Counter()
    global_surface: Counter[str] = Counter()
    band_freq: dict[str, Counter[str]] = defaultdict(Counter)
    band_surface: dict[str, Counter[str]] = defaultdict(Counter)
    source_counts: Counter[str] = Counter()
    sentence_lengths: dict[str, list[int]] = defaultdict(list)
    file_rows = []

    for path in files:
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        words = tamil_words(text)
        if not words:
            continue
        stems = [stem_fn(w) for w in words if stem_fn(w)]
        grade = _infer_grade(path)
        band = band_for_grade(grade)
        source = _source_kind(path)
        sents = tamil_sentences(text)
        lengths = [len(tamil_words(s)) for s in sents if tamil_words(s)]

        global_freq.update(stems)
        global_surface.update(words)
        band_freq[band].update(stems)
        band_surface[band].update(words)
        source_counts[source] += 1
        sentence_lengths[band].extend(lengths)
        file_rows.append({
            'path': str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
            'source': source,
            'grade': grade,
            'band': band,
            'words': len(words),
            'unique_stems': len(set(stems)),
            'sentences': len(lengths),
        })

    grade_vocabulary = {}
    for band, freq in band_freq.items():
        # Store the most common local stems for compact, fast analysis.
        grade_vocabulary[band] = [
            {'stem': stem, 'count': count}
            for stem, count in freq.most_common(25000)
        ]

    word_frequency = {
        'global': [{'stem': stem, 'count': count} for stem, count in global_freq.most_common(50000)],
        'surface': [{'word': word, 'count': count} for word, count in global_surface.most_common(50000)],
    }

    band_stats = {}
    for band, lengths in sentence_lengths.items():
        total = sum(band_freq[band].values())
        band_stats[band] = {
            'documents': sum(1 for f in file_rows if f['band'] == band),
            'tokens': total,
            'unique_stems': len(band_freq[band]),
            'avg_sentence_words': round(sum(lengths) / len(lengths), 1) if lengths else 0,
            'max_sentence_words': max(lengths) if lengths else 0,
            'top_words': [
                {'word': word, 'count': count}
                for word, count in band_surface[band].most_common(40)
            ],
        }

    stats = {
        'built': True,
        'documents': len(file_rows),
        'tokens': sum(global_freq.values()),
        'unique_stems': len(global_freq),
        'sources': dict(source_counts),
        'bands': band_stats,
        'files': file_rows[:1000],
    }

    CORPUS_STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    WORD_FREQUENCY.write_text(json.dumps(word_frequency, ensure_ascii=False), encoding='utf-8')
    GRADE_VOCABULARY.write_text(json.dumps(grade_vocabulary, ensure_ascii=False), encoding='utf-8')
    return stats


def _load_wiki_frequency(limit: int = 100000) -> Dict:
    if not WIKI_DB.exists():
        return {'frequency': {}, 'stems': 0, 'tokens': 0}
    try:
        conn = sqlite3.connect(str(WIKI_DB))
        rows = conn.execute('''
            SELECT stem, frequency
            FROM wiki_words
            ORDER BY frequency DESC
            LIMIT ?
        ''', (int(limit),)).fetchall()
        totals = conn.execute('''
            SELECT COUNT(*), COALESCE(SUM(frequency), 0)
            FROM wiki_words
        ''').fetchone()
        conn.close()
    except Exception:
        return {'frequency': {}, 'stems': 0, 'tokens': 0}
    return {
        'frequency': {stem: int(freq or 0) for stem, freq in rows},
        'stems': int(totals[0] or 0) if totals else 0,
        'tokens': int(totals[1] or 0) if totals else 0,
    }


def load_corpus(include_wiki: bool = True) -> Dict:
    if not CORPUS_STATS.exists() or not WORD_FREQUENCY.exists() or not GRADE_VOCABULARY.exists():
        return {'built': False}
    try:
        stats = json.loads(CORPUS_STATS.read_text(encoding='utf-8'))
        freq_raw = json.loads(WORD_FREQUENCY.read_text(encoding='utf-8'))
        vocab_raw = json.loads(GRADE_VOCABULARY.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'built': False, 'error': str(exc)}
    global_freq = {row['stem']: int(row['count']) for row in freq_raw.get('global', [])}
    wiki_meta = {'stems': 0, 'tokens': 0}
    if include_wiki:
        wiki = _load_wiki_frequency()
        wiki_meta = {'stems': wiki.get('stems', 0), 'tokens': wiki.get('tokens', 0)}
        for stem, count in wiki.get('frequency', {}).items():
            # Keep textbook/local corpus as the stronger signal, but let the
            # broad Wikipedia DB prevent common general Tamil from looking rare.
            global_freq[stem] = max(int(global_freq.get(stem, 0)), int(count or 0))
    bands = {
        band: {row['stem']: int(row['count']) for row in rows}
        for band, rows in vocab_raw.items()
    }
    stats = dict(stats)
    if include_wiki and wiki_meta.get('stems'):
        stats['wiki_db'] = {'stems': wiki_meta['stems'], 'tokens': wiki_meta['tokens']}
        sources = dict(stats.get('sources') or {})
        sources['wikipedia_db'] = wiki_meta['stems']
        stats['sources'] = sources
    if VOCAB_INDEX_DB.exists():
        stats['vocabulary_index_db'] = {
            'path': str(VOCAB_INDEX_DB),
            'enabled': True,
        }
    return {'built': True, 'stats': stats, 'global_freq': global_freq, 'bands': bands}


def _lookup_vocab_index_counts(stems: Iterable[str]) -> Dict[str, int]:
    wanted = sorted({s for s in stems if s})
    if not wanted or not VOCAB_INDEX_DB.exists():
        return {}
    out: Dict[str, int] = {}
    try:
        conn = sqlite3.connect(str(VOCAB_INDEX_DB))
        for i in range(0, len(wanted), 900):
            chunk = wanted[i:i + 900]
            placeholders = ','.join('?' for _ in chunk)
            rows = conn.execute(
                f'SELECT stem, total_count FROM stem_frequency WHERE stem IN ({placeholders})',
                chunk,
            ).fetchall()
            out.update({stem: int(count or 0) for stem, count in rows})
        conn.close()
    except Exception:
        return {}
    return out


def _pct(part: int | float, total: int | float) -> float:
    return round((part / total * 100), 1) if total else 0.0


def _estimated_level_from_tavi(
    score: float,
    familiarity: float,
    rarity: float,
    avg_sentence: float,
    compound_density: float,
    formality: float,
) -> Dict:
    """Approximate text level from TAVI signals.

    This is deliberately separate from textbook-based grade fit. It is a
    corpus/Wikipedia-informed estimate for cases where the textbook DB is
    sparse or unavailable.
    """
    if score >= 88 and rarity < 8 and compound_density < 12 and avg_sentence <= 7:
        center = 3
    elif score >= 78 and rarity < 15 and compound_density < 22 and avg_sentence <= 10:
        center = 5
    elif score >= 65 and rarity < 25 and compound_density < 35 and avg_sentence <= 14:
        center = 7
    elif score >= 50 and avg_sentence <= 18:
        center = 9
    else:
        center = 11

    if familiarity < 55:
        center += 1
    if rarity >= 30:
        center += 1
    if compound_density >= 35:
        center += 1
    if formality >= 12:
        center += 1
    if avg_sentence > 18:
        center += 1
    center = max(1, min(12, center))

    if center <= 3:
        band = 'Std 1-3'
    elif center <= 5:
        band = 'Std 4-5'
    elif center <= 8:
        band = 'Std 6-8'
    elif center <= 10:
        band = 'Std 9-10'
    else:
        band = 'Std 11-12'

    confidence_points = 0
    if familiarity >= 75:
        confidence_points += 1
    if rarity <= 22:
        confidence_points += 1
    if avg_sentence <= 14:
        confidence_points += 1
    if compound_density <= 35:
        confidence_points += 1
    confidence = 'High' if confidence_points >= 4 else 'Medium' if confidence_points >= 2 else 'Low'

    reasons = [
        f'TAVI score {score} indicates {"easy" if score >= 78 else "moderate" if score >= 58 else "difficult"} corpus readability.',
        f'{familiarity}% of tokens are known in the local/Wikipedia corpus.',
        f'{rarity}% of tokens are rare or corpus-unknown.',
        f'Average sentence length is {avg_sentence} Tamil words.',
        f'Compound/agglutinated word density is {compound_density}%.',
    ]
    if formality:
        reasons.append(f'Formal/abstract word density is {formality}%.')

    return {
        'estimated_standard': center,
        'estimated_standard_range': band,
        'confidence': confidence,
        'basis': 'TAVI + Wikipedia/general corpus + sentence/morphology signals',
        'reasons': reasons,
    }


def analyze_text(text: str, corpus: Dict | None = None, stem_fn: Callable[[str], str] | None = None) -> Dict:
    corpus = corpus or load_corpus()
    stem_fn = stem_fn or (lambda w: w)
    words = tamil_words(text)
    stems = [stem_fn(w) for w in words if stem_fn(w)]
    total = len(stems)
    if not total:
        return {'enabled': bool(corpus.get('built')), 'message': 'No Tamil text found.'}
    if not corpus.get('built'):
        return {
            'enabled': False,
            'message': 'Tamil corpus has not been built yet.',
            'how_to_build': 'Add/source corpus text files, then run the corpus builder from the app or API.',
        }

    freq = corpus.get('global_freq', {})
    if corpus.get('stats', {}).get('vocabulary_index_db', {}).get('enabled'):
        missing = [s for s in set(stems) if s not in freq]
        if missing:
            db_freq = _lookup_vocab_index_counts(missing)
            if db_freq:
                freq = {**freq, **db_freq}
    known_stems = set(freq.keys())
    morphology = _tamil_morphology.analyze_text(text, known_stems=known_stems, stem_fn=stem_fn)
    bands = corpus.get('bands', {})
    stem_set = set(stems)
    known_global = [s for s in stems if s in freq]
    rare_or_unknown = [s for s in stems if freq.get(s, 0) <= 1]
    formal_words = [w for w in words if w.endswith(FORMAL_SUFFIXES)]
    compound_words = [w for w in words if len(w) >= 10 or any(h in w for h in COMPOUND_HINTS)]
    sents = tamil_sentences(text)
    avg_sentence = round(sum(len(tamil_words(s)) for s in sents) / len(sents), 1) if sents else 0.0

    band_matches = []
    for band, vocab in sorted(bands.items()):
        vocab_set = set(vocab.keys())
        hit = len(stem_set & vocab_set)
        band_matches.append({
            'band': band,
            'known_unique_pct': _pct(hit, len(stem_set)),
            'known_unique': hit,
            'total_unique': len(stem_set),
        })
    best_band = max(band_matches, key=lambda r: r['known_unique_pct'], default=None)

    familiarity = _pct(len(known_global), total)
    rarity = _pct(len(rare_or_unknown), total)
    formality = _pct(len(formal_words), len(words))
    compound_density = _pct(len(compound_words), len(words))
    known_by_parts_pct = morphology.get('known_by_parts_pct', 0) if morphology.get('enabled') else 0
    sentence_ease = max(0.0, min(100.0, 100.0 - max(0, avg_sentence - 8) * 4))
    vocabulary_ease = max(0.0, min(100.0, familiarity + known_by_parts_pct * 0.4 - rarity * 0.35))
    morphology_ease = max(0.0, min(100.0, 100.0 - compound_density * 1.45 - formality * 1.2 + known_by_parts_pct * 0.25))
    score = round(vocabulary_ease * 0.45 + sentence_ease * 0.30 + morphology_ease * 0.25, 1)
    if score >= 78:
        label = 'TAVI easy'
    elif score >= 58:
        label = 'TAVI moderate'
    else:
        label = 'TAVI difficult'

    suggestions = []
    if rarity >= 18:
        suggestions.append('Add glossary support for rare or corpus-unknown words.')
    if avg_sentence > 16:
        suggestions.append('Split long sentences to reduce Tamil processing load.')
    if compound_density >= 22:
        suggestions.append('Review long compound/agglutinated words and explain them near first use.')
    if known_by_parts_pct >= 8:
        suggestions.append('Several words look difficult only because Tamil endings are attached; check base-word familiarity before simplifying.')
    if morphology.get('possible_proper_noun_count', 0):
        suggestions.append('Possible person/place names were detected; treat them as glossary/context items, not ordinary hard vocabulary.')
    if formality >= 12:
        suggestions.append('Formal/abstract Tamil is dense; add examples or simpler paraphrases for younger readers.')
    if not suggestions:
        suggestions.append('TAVI signals look balanced. Keep the current wording unless classroom feedback says otherwise.')

    estimated_level = _estimated_level_from_tavi(
        score, familiarity, rarity, avg_sentence, compound_density, formality
    )
    rare_counts = Counter(rare_or_unknown)
    return {
        'enabled': True,
        'metric_name': 'TAVI',
        'metric_full_name': 'Tamil Adaptive Vocabulary Index',
        'method': 'Corpus familiarity, rare-word penalty, sentence load, and Tamil morphology/formality load.',
        'tavi_score': score,
        'tavi_label': label,
        'estimated_level': estimated_level,
        'score_components': {
            'vocabulary_ease': round(vocabulary_ease, 1),
            'sentence_ease': round(sentence_ease, 1),
            'morphology_ease': round(morphology_ease, 1),
            'weights': {
                'vocabulary_ease': 0.45,
                'sentence_ease': 0.30,
                'morphology_ease': 0.25,
            },
        },
        'score': score,
        'label': label,
        'best_corpus_band': best_band,
        'band_matches': band_matches,
        'word_familiarity_pct': familiarity,
        'rare_or_unknown_pct': rarity,
        'formal_word_pct': formality,
        'compound_word_pct': compound_density,
        'known_by_parts_pct': known_by_parts_pct,
        'morphology': morphology,
        'avg_sentence_words': avg_sentence,
        'corpus_documents': corpus.get('stats', {}).get('documents', 0),
        'corpus_unique_stems': corpus.get('stats', {}).get('unique_stems', 0),
        'corpus_tokens': corpus.get('stats', {}).get('tokens', 0),
        'wiki_corpus_stems': corpus.get('stats', {}).get('wiki_db', {}).get('stems', 0),
        'wiki_corpus_tokens': corpus.get('stats', {}).get('wiki_db', {}).get('tokens', 0),
        'corpus_sources': corpus.get('stats', {}).get('sources', {}),
        'rare_examples': [{'stem': s, 'count': c} for s, c in rare_counts.most_common(30)],
        'suggestions': suggestions,
    }
