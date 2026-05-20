"""Grade-level sentence and paragraph norms built from loaded textbooks.

This database is separate from the main analyzer DB. It is derived from the
textbook files already loaded by the user, so it improves analysis without
requiring a children's-book corpus or teacher-labelled passages.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List

from .. import indic_nlp_adapter as _indic_nlp

NORMS_DB = 'data/level_norms.db'
TAMIL_WORD_RE = re.compile(r'[\u0B80-\u0BFF]{2,}')
SENTENCE_RE = re.compile(r'[.!?।\u0964\u0965\n]+')


def tamil_words(text: str) -> List[str]:
    return _indic_nlp.words(text)


def split_sentences(text: str) -> List[str]:
    return _indic_nlp.sentences(text)


def split_paragraphs(text: str) -> List[str]:
    paras = [p.strip() for p in re.split(r'\n\s*\n+', text or '') if tamil_words(p)]
    if len(paras) <= 1:
        # Some PDF extraction collapses blank lines. Fall back to line groups.
        paras = [p.strip() for p in (text or '').splitlines() if len(tamil_words(p)) >= 4]
    if not paras:
        # Very early-reader OCR/text can be one word per line. Create stable
        # paragraph-like blocks so paragraph norms still have useful coverage.
        words = tamil_words(text)
        paras = [' '.join(words[i:i + 80]) for i in range(0, len(words), 80) if words[i:i + 80]]
    return paras


def _infer_grade_from_path(path: Path) -> int | None:
    text = str(path)
    m = re.search(r'(?:class|grade|std|standard)[_\-\s]?0?(\d{1,2})', text, re.I)
    if m:
        grade = int(m.group(1))
        if 1 <= grade <= 12:
            return grade
    return None


def _iter_extracted_texts(root: str | Path = '.') -> Iterable[tuple[int, Path]]:
    root = Path(root)
    for base in [root / 'textbooks_imported_text', root / 'data' / 'corpus_sources']:
        if not base.exists():
            continue
        for path in sorted(base.rglob('*.txt')):
            grade = _infer_grade_from_path(path)
            if grade:
                yield grade, path


def _pctile(values: List[int | float], pct: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, round((pct / 100.0) * (len(vals) - 1))))
    return round(float(vals[idx]), 1)


def _avg(values: Iterable[int | float]) -> float:
    vals = list(values)
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def get_db() -> sqlite3.Connection:
    Path(NORMS_DB).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(NORMS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS grade_norms (
            grade                    INTEGER PRIMARY KEY,
            file_count               INTEGER DEFAULT 0,
            paragraph_count          INTEGER DEFAULT 0,
            sentence_count           INTEGER DEFAULT 0,
            token_count              INTEGER DEFAULT 0,
            avg_sentence_words       REAL DEFAULT 0,
            p75_sentence_words       REAL DEFAULT 0,
            p90_sentence_words       REAL DEFAULT 0,
            max_sentence_words       INTEGER DEFAULT 0,
            avg_paragraph_words      REAL DEFAULT 0,
            p75_paragraph_words      REAL DEFAULT 0,
            p90_paragraph_words      REAL DEFAULT 0,
            avg_sentences_per_para   REAL DEFAULT 0,
            long_paragraph_pct       REAL DEFAULT 0,
            updated_at               TEXT
        );
        CREATE TABLE IF NOT EXISTS norm_sources (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            grade           INTEGER,
            filepath        TEXT,
            filename        TEXT,
            paragraph_count INTEGER DEFAULT 0,
            sentence_count  INTEGER DEFAULT 0,
            token_count     INTEGER DEFAULT 0,
            updated_at      TEXT
        );
    ''')
    conn.commit()
    conn.close()


def _file_metrics(text: str) -> Dict:
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    sent_lens = [len(tamil_words(s)) for s in sentences if tamil_words(s)]
    para_lens = [len(tamil_words(p)) for p in paragraphs if tamil_words(p)]
    sents_per_para = [len(split_sentences(p)) for p in paragraphs if split_sentences(p)]
    return {
        'sentence_lengths': sent_lens,
        'paragraph_lengths': para_lens,
        'sentences_per_para': sents_per_para,
        'tokens': len(tamil_words(text)),
        'paragraph_count': len(para_lens),
        'sentence_count': len(sent_lens),
    }


def build_from_textbook_db(
    analyzer_db_path: str = 'tamil_analyzer.db',
    extract_text_fn: Callable[[str], str] | None = None,
    root: str | Path = '.',
) -> Dict:
    """Build grade norms from the loaded textbook file list."""
    init_db()
    if extract_text_fn is None:
        raise ValueError('extract_text_fn is required to build level norms.')

    source = sqlite3.connect(analyzer_db_path)
    source.row_factory = sqlite3.Row
    rows = source.execute(
        'SELECT grade, filepath, filename FROM grade_files ORDER BY grade, filename'
    ).fetchall()
    source.close()

    grouped: dict[int, dict[str, list]] = defaultdict(lambda: {
        'files': [],
        'sentence_lengths': [],
        'paragraph_lengths': [],
        'sentences_per_para': [],
        'tokens': 0,
        'paragraph_count': 0,
        'sentence_count': 0,
    })

    conn = get_db()
    conn.execute('DELETE FROM grade_norms')
    conn.execute('DELETE FROM norm_sources')
    now = datetime.datetime.now().isoformat()

    seen_paths = set()
    for row in rows:
        path = row['filepath']
        if not path or not Path(path).exists():
            continue
        seen_paths.add(str(Path(path).resolve()))
        try:
            text = extract_text_fn(path)
        except Exception:
            continue
        metrics = _file_metrics(text)
        grade = int(row['grade'])
        bucket = grouped[grade]
        bucket['files'].append(path)
        bucket['sentence_lengths'].extend(metrics['sentence_lengths'])
        bucket['paragraph_lengths'].extend(metrics['paragraph_lengths'])
        bucket['sentences_per_para'].extend(metrics['sentences_per_para'])
        bucket['tokens'] += metrics['tokens']
        bucket['paragraph_count'] += metrics['paragraph_count']
        bucket['sentence_count'] += metrics['sentence_count']
        conn.execute('''
            INSERT INTO norm_sources
              (grade, filepath, filename, paragraph_count, sentence_count, token_count, updated_at)
            VALUES (?,?,?,?,?,?,?)
        ''', (
            grade, path, row['filename'], metrics['paragraph_count'],
            metrics['sentence_count'], metrics['tokens'], now
        ))

    for grade, path_obj in _iter_extracted_texts(root):
        resolved = str(path_obj.resolve())
        if resolved in seen_paths:
            continue
        try:
            text = path_obj.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        metrics = _file_metrics(text)
        if not metrics['tokens']:
            continue
        bucket = grouped[int(grade)]
        bucket['files'].append(str(path_obj))
        bucket['sentence_lengths'].extend(metrics['sentence_lengths'])
        bucket['paragraph_lengths'].extend(metrics['paragraph_lengths'])
        bucket['sentences_per_para'].extend(metrics['sentences_per_para'])
        bucket['tokens'] += metrics['tokens']
        bucket['paragraph_count'] += metrics['paragraph_count']
        bucket['sentence_count'] += metrics['sentence_count']
        conn.execute('''
            INSERT INTO norm_sources
              (grade, filepath, filename, paragraph_count, sentence_count, token_count, updated_at)
            VALUES (?,?,?,?,?,?,?)
        ''', (
            int(grade), str(path_obj), path_obj.name, metrics['paragraph_count'],
            metrics['sentence_count'], metrics['tokens'], now
        ))

    for grade, bucket in sorted(grouped.items()):
        sent_lens = bucket['sentence_lengths']
        para_lens = bucket['paragraph_lengths']
        spp = bucket['sentences_per_para']
        long_para = sum(1 for n in para_lens if n > 80)
        conn.execute('''
            INSERT OR REPLACE INTO grade_norms
              (grade, file_count, paragraph_count, sentence_count, token_count,
               avg_sentence_words, p75_sentence_words, p90_sentence_words, max_sentence_words,
               avg_paragraph_words, p75_paragraph_words, p90_paragraph_words,
               avg_sentences_per_para, long_paragraph_pct, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            grade, len(bucket['files']), bucket['paragraph_count'], bucket['sentence_count'], bucket['tokens'],
            _avg(sent_lens), _pctile(sent_lens, 75), _pctile(sent_lens, 90), max(sent_lens) if sent_lens else 0,
            _avg(para_lens), _pctile(para_lens, 75), _pctile(para_lens, 90),
            _avg(spp), round((long_para / len(para_lens) * 100), 1) if para_lens else 0.0, now
        ))

    conn.commit()
    stats = get_status(conn=conn)
    conn.close()
    return stats


def get_status(conn: sqlite3.Connection | None = None) -> Dict:
    init_db()
    own = conn is None
    conn = conn or get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM grade_norms ORDER BY grade').fetchall()]
    source_count = conn.execute('SELECT COUNT(*) FROM norm_sources').fetchone()[0]
    if own:
        conn.close()
    return {
        'built': bool(rows),
        'database': NORMS_DB,
        'grades': rows,
        'source_files': source_count,
        'grade_count': len(rows),
        'tokens': sum(int(r.get('token_count') or 0) for r in rows),
        'sentences': sum(int(r.get('sentence_count') or 0) for r in rows),
        'paragraphs': sum(int(r.get('paragraph_count') or 0) for r in rows),
    }


def load_norms() -> Dict[int, Dict]:
    status = get_status()
    return {int(r['grade']): r for r in status.get('grades', [])}


def analyze_text(text: str, target_grade: int | None = None) -> Dict:
    norms = load_norms()
    if not norms:
        return {'enabled': False, 'message': 'Grade-level norms database has not been built yet.'}
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    sent_lens = [len(tamil_words(s)) for s in sentences]
    para_lens = [len(tamil_words(p)) for p in paragraphs]
    spp = [len(split_sentences(p)) for p in paragraphs if split_sentences(p)]

    sparse_norms = len(norms) < 3
    if target_grade and target_grade in norms:
        compare_grade = int(target_grade)
    elif sparse_norms:
        # With only one or two loaded standards, nearest-neighbor matching can
        # be actively misleading. Use the highest available grade and expose a
        # low-confidence warning instead of calling it the nearest level.
        compare_grade = max(norms)
    else:
        compare_grade = min(norms, key=lambda g: abs(float(norms[g].get('avg_sentence_words') or 0) - _avg(sent_lens)))
    norm = norms[compare_grade]
    sent_limit = float(norm.get('p90_sentence_words') or norm.get('max_sentence_words') or 0)
    para_limit = float(norm.get('p90_paragraph_words') or 0)
    sent_over = sum(1 for n in sent_lens if sent_limit and n > sent_limit)
    para_over = sum(1 for n in para_lens if para_limit and n > para_limit)

    return {
        'enabled': True,
        'database': NORMS_DB,
        'compare_grade': compare_grade,
        'confidence': 'Low' if sparse_norms else 'Medium',
        'coverage_warning': (
            f'Only {len(norms)} grade norm(s) are built. Load more standards before treating sentence/paragraph norms as a level estimate.'
            if sparse_norms else ''
        ),
        'target': {
            'avg_sentence_words': _avg(sent_lens),
            'p90_sentence_words': _pctile(sent_lens, 90),
            'max_sentence_words': max(sent_lens) if sent_lens else 0,
            'avg_paragraph_words': _avg(para_lens),
            'p90_paragraph_words': _pctile(para_lens, 90),
            'avg_sentences_per_para': _avg(spp),
            'sentence_count': len(sent_lens),
            'paragraph_count': len(para_lens),
        },
        'norm': norm,
        'sentence_over_p90_count': sent_over,
        'sentence_over_p90_pct': round(sent_over / len(sent_lens) * 100, 1) if sent_lens else 0.0,
        'paragraph_over_p90_count': para_over,
        'paragraph_over_p90_pct': round(para_over / len(para_lens) * 100, 1) if para_lens else 0.0,
    }
