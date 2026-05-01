"""Offline suitability/adaptation engine for Tamil children's books.

This module is deliberately independent from the existing readability pipeline.
It consumes the existing analysis outputs plus optional meaning_kb results and
returns explainable class-wise scores, detailed diagnostics, page/chunk
progression, glossary candidates, and adaptation suggestions.
"""
from __future__ import annotations

import json, math, os, re, time, hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple

TAMIL_RE = re.compile(r'[\u0B80-\u0BFF]+')
SENT_SPLIT_RE = re.compile(r'(?<=[.!?।:;।]|[.?!])\s+|[\n]+')

# Conservative Tamil stopwords used for diagnostics only.
STOPWORDS = {
    'ஒரு','இந்த','அந்த','இது','அது','எது','என்','உன்','தன்','நம்','நான்','நீ','அவன்','அவள்','அவர்கள்','அவை','இவை',
    'மற்றும்','ஆகிய','என','என்று','என்ற','உள்ள','இல்லை','ஆம்','ஆகும்','வேண்டும்','முதல்','வரை','அல்லது','ஆனால்',
    'பின்','முன்','மேல்','கீழ்','உடன்','வழி','போல்','போன்ற','மிக','பல','எல்லாம்','யார்','என்ன','எப்படி','ஏன்',
    'கொண்டு','செய்து','வரும்','இருக்கும்','உண்டு','இவர்','அவர்','இவர்கள்','நூல்','பக்கம்','பயிற்சி','மாணவர்','மாணவர்கள்'
}

SIMPLE_GLOSSARY = {
    'வளிமண்டலம்': 'வானத்தைச் சுற்றிய காற்று',
    'சுற்றுச்சூழல்': 'மரம், நீர், காற்று, நிலம் போன்ற நம்மைச் சுற்றியவை',
    'மாசுபாடு': 'நீர், காற்று அல்லது இடம் கெட்டுப்போதல்',
    'பாதுகாப்பு': 'காப்பது',
    'உளவியல்': 'மனநிலை மற்றும் எண்ணங்களைப் பற்றியது',
    'இலக்கியம்': 'கவிதை, கதை போன்ற எழுத்துப் படைப்புகள்',
    'இலக்கணம்': 'மொழியை சரியாகப் பயன்படுத்த உதவும் விதிகள்',
    'சமூகம்': 'மக்கள் சேர்ந்து வாழும் அமைப்பு',
    'தேசியம்': 'நாடு தொடர்பான உணர்வு அல்லது கருத்து',
    'அறிவியல்': 'உலகம் எப்படி இயங்குகிறது என்பதை அறியும் படிப்பு',
}

DEFAULT_WEIGHTS = {
    'word': 0.30,
    'meaning': 0.30,
    'sentence': 0.25,
    'consistency': 0.15,
}


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def verdict_for_score(score: float) -> str:
    if score >= 91:
        return 'Very suitable'
    if score >= 76:
        return 'Suitable'
    if score >= 61:
        return 'Partly suitable'
    if score >= 41:
        return 'Needs major changes'
    return 'Not suitable'


def split_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in SENT_SPLIT_RE.split(text or '')]
    return [p for p in parts if len(TAMIL_RE.findall(p)) >= 2]


def sentence_word_count(sentence: str) -> int:
    return len(TAMIL_RE.findall(sentence or ''))


def tokenize(text: str) -> List[str]:
    return [w for w in TAMIL_RE.findall(text or '') if len(w) > 1]


def chunk_text(text: str, approx_words_per_chunk: int = 180) -> List[Dict[str, Any]]:
    """Create page-like chunks when true page text is unavailable."""
    sents = split_sentences(text)
    chunks, cur, cur_words = [], [], 0
    for s in sents:
        wc = sentence_word_count(s)
        if cur and cur_words + wc > approx_words_per_chunk:
            chunks.append({'index': len(chunks)+1, 'text': ' '.join(cur), 'word_count': cur_words})
            cur, cur_words = [], 0
        cur.append(s)
        cur_words += wc
    if cur:
        chunks.append({'index': len(chunks)+1, 'text': ' '.join(cur), 'word_count': cur_words})
    if not chunks and text:
        chunks = [{'index': 1, 'text': text, 'word_count': len(tokenize(text))}]
    return chunks


def _meaning_for_grade(meaning: Dict[str, Any] | None, grade: int, raw_text: str = '', kb_dir: str = 'data/meaning_kb', tokenize_fn=None, stem_fn=None) -> Dict[str, Any]:
    """Use supplied meaning result for its grade; otherwise recompute if KB is present."""
    if meaning and meaning.get('enabled') and int(meaning.get('target_grade', -1)) == int(grade):
        return meaning
    try:
        from . import meaning_kb
        res = meaning_kb.analyze_text_meaning(raw_text, int(grade), kb_dir, tokenize_fn=tokenize_fn, stem_fn=stem_fn, limit=500)
        if res and res.get('enabled'):
            return res
    except Exception:
        pass
    return {'enabled': False, 'appropriateness_pct': None, 'flagged': [], 'flagged_count': 0}


def _sentence_score_for_grade(sentence_counts: List[int], grade_max: int | float | None, grade_avg: int | float | None = None) -> Dict[str, Any]:
    if not sentence_counts:
        return {'score': 100.0, 'over_count': 0, 'over_pct': 0.0, 'complex_sentences': []}
    max_allowed = float(grade_max or 0)
    if max_allowed <= 0:
        # Sensible fallback thresholds for lower/upper primary.
        max_allowed = 8 + 2 * max(1, min(12, int(grade_avg or 4)))
    over = [c for c in sentence_counts if c > max_allowed]
    over_pct = len(over) / len(sentence_counts) * 100.0
    # Penalty is progressive but capped; a few long sentences should not destroy the score.
    score = clamp(100.0 - over_pct * 1.15)
    return {'score': round(score, 1), 'over_count': len(over), 'over_pct': round(over_pct, 1), 'max_allowed': int(max_allowed)}


def _page_consistency_score(page_scores: List[float]) -> Dict[str, Any]:
    if not page_scores:
        return {'score': 100.0, 'jumps': [], 'hard_chunks': []}
    avg = sum(page_scores) / len(page_scores)
    hard = [i+1 for i, s in enumerate(page_scores) if s < max(50.0, avg - 20.0)]
    jumps = []
    for i in range(1, len(page_scores)):
        delta = page_scores[i] - page_scores[i-1]
        if abs(delta) >= 25:
            jumps.append({'from': i, 'to': i+1, 'delta': round(delta, 1), 'type': 'jump_up' if delta < 0 else 'drop/easier'})
    spread = (max(page_scores) - min(page_scores)) if len(page_scores) > 1 else 0
    penalty = min(45.0, spread * 0.45 + len(jumps) * 6 + len(hard) * 2)
    return {'score': round(clamp(100.0 - penalty), 1), 'jumps': jumps, 'hard_chunks': hard[:30], 'spread': round(spread, 1)}


def build_suitability_report(
    *,
    raw_text: str,
    results: List[Dict[str, Any]],
    target_sentence_counts: List[int],
    meaning: Dict[str, Any] | None = None,
    tokenize_fn: Callable[[str], List[str]] | None = None,
    stem_fn: Callable[[str], str] | None = None,
    kb_dir: str = 'data/meaning_kb',
) -> Dict[str, Any]:
    tokenize_fn = tokenize_fn or tokenize
    stem_fn = stem_fn or (lambda x: x)
    chunks = chunk_text(raw_text)

    # Word-level details by grade are already computed by the legacy analyzer.
    class_rows = []
    per_grade_meaning: Dict[int, Dict[str, Any]] = {}
    for r in results:
        grade = int(r.get('grade', 0))
        if not grade:
            continue
        word_score = float(r.get('known_pct') or r.get('comprehension_pct') or r.get('found_pct') or 0)
        m = _meaning_for_grade(meaning, grade, raw_text, kb_dir, tokenize_fn, stem_fn)
        per_grade_meaning[grade] = m
        meaning_score = m.get('appropriateness_pct')
        if meaning_score is None:
            # If KB unavailable, make it neutral so old installations still work.
            meaning_score = word_score
        sent = _sentence_score_for_grade(target_sentence_counts, r.get('grade_sent_max'), r.get('grade_sent_avg'))

        # Chunk/page scoring for this class. Uses word score plus sentence pressure per chunk.
        chunk_scores = []
        chunk_details = []
        for ch in chunks:
            toks = tokenize_fn(ch['text'])
            stems = [stem_fn(t) for t in toks]
            known_set = set((r.get('unknown_word_list') or []))  # display words, not stems; used only for approx
            # Better estimate: use global word score and local sentence complexity.
            local_counts = [sentence_word_count(s) for s in split_sentences(ch['text'])]
            local_sent = _sentence_score_for_grade(local_counts, r.get('grade_sent_max'), r.get('grade_sent_avg'))
            local_score = clamp(word_score * 0.45 + float(meaning_score) * 0.30 + local_sent['score'] * 0.25)
            chunk_scores.append(local_score)
            chunk_details.append({
                'page': ch['index'],
                'word_count': ch['word_count'],
                'score': round(local_score, 1),
                'level': verdict_for_score(local_score),
                'long_sentences': local_sent['over_count'],
            })
        consistency = _page_consistency_score(chunk_scores)
        overall = clamp(
            word_score * DEFAULT_WEIGHTS['word'] +
            float(meaning_score) * DEFAULT_WEIGHTS['meaning'] +
            sent['score'] * DEFAULT_WEIGHTS['sentence'] +
            consistency['score'] * DEFAULT_WEIGHTS['consistency']
        )
        class_rows.append({
            'grade': grade,
            'overall_pct': round(overall, 1),
            'word_pct': round(word_score, 1),
            'meaning_pct': round(float(meaning_score), 1),
            'sentence_pct': round(sent['score'], 1),
            'consistency_pct': round(consistency['score'], 1),
            'verdict': verdict_for_score(overall),
            'word_unknown_count': int(r.get('new_words') or r.get('unknown_words') or r.get('not_found_words') or 0),
            'sentence_over_count': int(sent['over_count']),
            'meaning_flagged_count': int(m.get('flagged_count') or 0),
            'page_summary': consistency,
        })

    recommended = next((row for row in class_rows if row['overall_pct'] >= 76), None)
    if recommended is None and class_rows:
        recommended = max(class_rows, key=lambda x: x['overall_pct'])

    return {
        'version': 'v11.0',
        'weights': DEFAULT_WEIGHTS,
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'recommended_grade': recommended['grade'] if recommended else None,
        'recommended_age': _age_for_grade(recommended['grade']) if recommended else None,
        'confidence_pct': _confidence(class_rows, recommended),
        'class_suitability': class_rows,
        'progression': build_progression(raw_text, results, per_grade_meaning, target_sentence_counts, tokenize_fn, stem_fn),
        'diagnostics': build_diagnostics(results, per_grade_meaning, raw_text, target_sentence_counts),
        'adaptation': build_adaptation(results, per_grade_meaning, target_sentence_counts),
        'glossary': build_glossary(per_grade_meaning),
        'notes': [
            'Unknown words are reported separately; absence from textbooks is not treated as automatically inappropriate.',
            'Meaning scores use the local meaning_kb when built; otherwise they fall back to word familiarity.',
            'Progression is page-like chunk based when true PDF page text is unavailable.'
        ]
    }


def _age_for_grade(g: int | None) -> str | None:
    if not g:
        return None
    lo = int(g) + 5
    return f'{lo}–{lo+1}'


def _confidence(rows: List[Dict[str, Any]], rec: Dict[str, Any] | None) -> float:
    if not rows or not rec:
        return 0.0
    scores = sorted([r['overall_pct'] for r in rows], reverse=True)
    gap = scores[0] - scores[1] if len(scores) > 1 else 20
    base = 60 + min(25, gap) + (10 if rec['overall_pct'] >= 76 else 0)
    return round(clamp(base), 1)


def build_progression(raw_text: str, results: List[Dict[str, Any]], per_grade_meaning: Dict[int, Dict[str, Any]], sent_counts: List[int], tokenize_fn, stem_fn) -> Dict[str, Any]:
    # Use recommended or closest available grade for progression.
    candidate = next((r for r in results if float(r.get('known_pct') or r.get('comprehension_pct') or 0) >= 80), results[-1] if results else {})
    grade = int(candidate.get('grade') or 1)
    word_score = float(candidate.get('known_pct') or candidate.get('comprehension_pct') or 0)
    meaning_score = per_grade_meaning.get(grade, {}).get('appropriateness_pct')
    if meaning_score is None:
        meaning_score = word_score
    chunks = chunk_text(raw_text)
    pages = []
    for ch in chunks:
        local_counts = [sentence_word_count(s) for s in split_sentences(ch['text'])]
        sent = _sentence_score_for_grade(local_counts, candidate.get('grade_sent_max'), candidate.get('grade_sent_avg'))
        score = clamp(word_score * 0.45 + float(meaning_score) * 0.30 + sent['score'] * 0.25)
        pages.append({
            'page': ch['index'],
            'score': round(score, 1),
            'difficulty': _difficulty_label(score),
            'word_count': ch['word_count'],
            'long_sentences': sent['over_count'],
        })
    consistency = _page_consistency_score([p['score'] for p in pages])
    quality = 'Smooth' if consistency['score'] >= 80 else 'Uneven' if consistency['score'] >= 60 else 'Inconsistent'
    suggestions = []
    if consistency['jumps']:
        suggestions.append('Review pages/chunks with sudden difficulty changes; move advanced content later or simplify it.')
    if consistency['hard_chunks']:
        suggestions.append('Simplify the hard pages/chunks first; they limit the whole-book suitability.')
    return {'target_grade': grade, 'quality': quality, 'consistency_score': consistency['score'], 'pages': pages, 'issues': consistency, 'suggestions': suggestions}


def _difficulty_label(score: float) -> str:
    if score >= 86: return 'Easy'
    if score >= 70: return 'Medium'
    if score >= 55: return 'Hard'
    return 'Very hard'


def build_diagnostics(results: List[Dict[str, Any]], per_grade_meaning: Dict[int, Dict[str, Any]], raw_text: str, sentence_counts: List[int]) -> Dict[str, Any]:
    word_by_grade = {}
    for r in results:
        g = int(r.get('grade') or 0)
        if not g: continue
        unknown = r.get('unknown_word_list') or r.get('new_word_list') or []
        word_by_grade[str(g)] = [{'word': w, 'reason': f'Not found in cumulative Std 1–{g} vocabulary'} for w in unknown[:100]]
    meaning_by_grade = {}
    for g, m in per_grade_meaning.items():
        flags = m.get('flagged') or []
        meaning_by_grade[str(g)] = [{
            'item': f.get('item'), 'type': f.get('type'), 'level': f.get('level'),
            'reason': f"Usually suitable from Std {f.get('level')} / concept: {f.get('concept','general')}",
            'severity': f.get('severity')
        } for f in flags[:100]]
    # Sentence detail: only top longest, since exact original sentence IDs can be noisy.
    sents = split_sentences(raw_text)
    longest = sorted([(sentence_word_count(s), i+1, s[:250]) for i, s in enumerate(sents)], reverse=True)[:50]
    sentence_issues = [{'sentence_no': i, 'words': wc, 'text': txt, 'suggestion': 'Split into shorter sentences and reduce clauses.'} for wc, i, txt in longest if wc >= 18]
    return {'word_level': word_by_grade, 'meaning_level': meaning_by_grade, 'sentence_level': sentence_issues}


def build_adaptation(results: List[Dict[str, Any]], per_grade_meaning: Dict[int, Dict[str, Any]], sentence_counts: List[int]) -> Dict[str, Any]:
    plans = {}
    for r in results:
        g = int(r.get('grade') or 0)
        if not g: continue
        m = per_grade_meaning.get(g, {})
        difficult_words = (r.get('unknown_word_list') or [])[:50]
        flags = (m.get('flagged') or [])[:50]
        sent_over = int(r.get('target_sentences_over_max') or 0)
        actions = []
        if difficult_words:
            actions.append(f'Replace or pre-teach {min(len(difficult_words), 50)} difficult words.')
        if flags:
            actions.append(f'Explain or simplify {min(len(flags), 50)} advanced concepts/phrases.')
        if sent_over:
            actions.append(f'Split {sent_over} long sentences for Std {g}.')
        if not actions:
            actions.append('Minor editing only; this level is broadly suitable.')
        plans[str(g)] = {
            'target_grade': g,
            'main_gap': _main_gap(r, m),
            'actions': actions,
            'sample_word_replacements': [{'word': w, 'suggestion': SIMPLE_GLOSSARY.get(w, 'Use a simpler word or add a picture/example.')} for w in difficult_words[:20]],
            'sample_concept_explanations': [{'item': f.get('item'), 'suggestion': SIMPLE_GLOSSARY.get(f.get('item',''), 'Explain with concrete examples before reading.')} for f in flags[:20]],
        }
    return plans


def _main_gap(r: Dict[str, Any], m: Dict[str, Any]) -> str:
    scores = {
        'word': float(r.get('known_pct') or r.get('comprehension_pct') or 0),
        'meaning': float(m.get('appropriateness_pct') if m.get('appropriateness_pct') is not None else 100),
        'sentence': 100.0 - float(r.get('target_pct_over_max') or 0),
    }
    return min(scores, key=scores.get)


def build_glossary(per_grade_meaning: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for g in sorted(per_grade_meaning):
        for f in per_grade_meaning[g].get('flagged') or []:
            item = f.get('item')
            if not item or item in seen or ' ' in item:
                continue
            seen.add(item)
            out.append({'word': item, 'simple_meaning': SIMPLE_GLOSSARY.get(item, 'Teacher to add simple explanation.'), 'level': f.get('level'), 'concept': f.get('concept','general')})
            if len(out) >= 100:
                return out
    return out


def save_analysis_cache(report: Dict[str, Any], book_name: str, raw_text: str, cache_dir: str = 'data/cache') -> str:
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256((book_name + '\n' + (raw_text or '')[:100000]).encode('utf-8', 'ignore')).hexdigest()[:16]
    path = Path(cache_dir) / f'{h}.json'
    path.write_text(json.dumps({'book_name': book_name, 'report': report}, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(path)


def update_books_index(report: Dict[str, Any], book_name: str, path: str = 'data/books_index.json') -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(p.read_text(encoding='utf-8')) if p.exists() else []
        if not isinstance(data, list): data = []
    except Exception:
        data = []
    row = {
        'book_name': book_name,
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'recommended_grade': report.get('recommended_grade'),
        'recommended_age': report.get('recommended_age'),
        'confidence_pct': report.get('confidence_pct'),
        'class_scores': {str(r['grade']): r['overall_pct'] for r in report.get('class_suitability', [])},
        'word_scores': {str(r['grade']): r['word_pct'] for r in report.get('class_suitability', [])},
        'meaning_scores': {str(r['grade']): r['meaning_pct'] for r in report.get('class_suitability', [])},
        'sentence_scores': {str(r['grade']): r['sentence_pct'] for r in report.get('class_suitability', [])},
        'progression_quality': (report.get('progression') or {}).get('quality'),
    }
    data = [x for x in data if x.get('book_name') != book_name]
    data.insert(0, row)
    p.write_text(json.dumps(data[:500], ensure_ascii=False, indent=2), encoding='utf-8')


def compare_books(index_path: str = 'data/books_index.json', names: List[str] | None = None) -> Dict[str, Any]:
    p = Path(index_path)
    if not p.exists():
        return {'books': [], 'message': 'No saved book analyses yet.'}
    data = json.loads(p.read_text(encoding='utf-8'))
    if names:
        data = [x for x in data if x.get('book_name') in names]
    # Build class-wise comparison table.
    grades = sorted({int(g) for b in data for g in (b.get('class_scores') or {}).keys()})
    rows = []
    for g in grades:
        row = {'grade': g}
        for b in data:
            row[b.get('book_name','Book')] = (b.get('class_scores') or {}).get(str(g))
        rows.append(row)
    return {'books': data, 'class_rows': rows}
