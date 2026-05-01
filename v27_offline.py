"""
v27_offline.py — Offline/no-AI intelligence layer for Tamil Analyzer.
"""
from __future__ import annotations
import os, re, sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

TAMIL_WORD_RE = re.compile(r'[\u0B80-\u0BFF]{2,}')
SENT_SPLIT_RE = re.compile(r'(?<=[.!?।\u0964\u0965])\s+|\n+')
CONCEPT_LABELS = {
    'language_literature': 'Language / Literature', 'nature_environment': 'Nature / Environment',
    'science_technology': 'Science / Technology', 'society_civics': 'Society / Civics',
    'health_body': 'Health / Body', 'math_logic': 'Math / Logic', 'history_culture': 'History / Culture',
    'daily_life': 'Daily Life', 'general': 'General',
}
STOPWORDS = {'ஒரு','இந்த','அந்த','இது','அது','எது','என்','உன்','தன்','நம்','நான்','நீ','அவன்','அவள்','அவர்','அவர்கள்','அவை','இவை','மற்றும்','ஆகிய','என','என்று','என்ற','உள்ள','இல்லை','ஆம்','ஆகும்','வேண்டும்','முதல்','வரை','அல்லது','ஆனால்','பின்','முன்','மேல்','கீழ்','உடன்','வழி','போல்','போன்ற','மிக','பல','எல்லாம்','யார்','என்ன','கொண்டு','செய்து','வரும்','இருக்கும்','உண்டு'}
SIMPLE_EXPLANATIONS = {
    'ஒளிச்சேர்க்கை': 'தாவரங்கள் சூரிய ஒளியைப் பயன்படுத்தி உணவு உருவாக்கும் செயல்.',
    'அணு': 'மிகச் சிறிய துகள்.', 'மூலக்கூறு': 'இரண்டு அல்லது அதற்கு மேற்பட்ட அணுக்கள் சேர்ந்த அமைப்பு.',
    'மின்சாரம்': 'விளக்கு, விசிறி போன்றவற்றை இயக்க உதவும் ஆற்றல்.',
    'சுற்றுச்சூழல்': 'நம்மைச் சுற்றியுள்ள நிலம், நீர், காற்று, உயிரினங்கள்.',
    'ஈர்ப்பு': 'ஒரு பொருள் மற்றொரு பொருளை இழுக்கும் விசை.', 'அழுத்தம்': 'ஒரு இடத்தில் கொடுக்கப்படும் தள்ளும் விசை.',
}

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _sentences(text: str) -> List[str]:
    return [p.strip() for p in SENT_SPLIT_RE.split(text or '') if len(p.strip()) > 5]

def _word_count(s: str) -> int:
    return len(TAMIL_WORD_RE.findall(s or ''))

def _top_unknown_words(results: List[Dict[str, Any]], target_grade: int, limit: int = 60) -> List[str]:
    if not results: return []
    row = next((r for r in results if int(r.get('grade', -1)) == int(target_grade)), None) or results[-1]
    words = row.get('unknown_word_list') or row.get('new_word_list') or []
    out, seen = [], set()
    for w in words:
        if not w or w in seen or w in STOPWORDS: continue
        seen.add(w); out.append(w)
        if len(out) >= limit: break
    return out

def _lookup_library(words: List[str], kb_dir: str = 'data/meaning_kb') -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {w: {} for w in words}
    for db_path in ['word_library.db', os.path.join(kb_dir, 'word_library.db')]:
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
                for w in words:
                    row = conn.execute('SELECT display_word, grade_level, concept, definition, example FROM word_library WHERE stem=? OR display_word=? LIMIT 1', (w, w)).fetchone()
                    if row: result[w].update(dict(row))
                conn.close()
            except Exception: pass
    wiki_path = os.path.join(kb_dir, 'tamil_wikipedia_kb.sqlite')
    if os.path.exists(wiki_path):
        try:
            conn = sqlite3.connect(wiki_path)
            for w in words:
                row = conn.execute('SELECT freq FROM words WHERE word=? LIMIT 1', (w,)).fetchone()
                if row: result[w]['wiki_freq'] = row[0]
            conn.close()
        except Exception: pass
    return result

def build_offline_intelligence(raw_text: str, results: List[Dict[str, Any]], target_sentence_counts: List[int], meaning: Optional[Dict[str, Any]] = None, suitability: Optional[Dict[str, Any]] = None, kb_dir: str = 'data/meaning_kb') -> Dict[str, Any]:
    meaning, suitability, results = meaning or {}, suitability or {}, results or []
    rec_grade = suitability.get('recommended_grade')
    if rec_grade is None:
        readable = next((r for r in results if (r.get('comprehension_pct') or 0) >= 80), None)
        rec_grade = readable.get('grade') if readable else (results[-1].get('grade') if results else 12)
    try: rec_grade = int(rec_grade)
    except Exception: rec_grade = 12
    row = next((r for r in results if int(r.get('grade', -1)) == rec_grade), None) or (results[-1] if results else {})
    word_known = float(row.get('comprehension_pct') or row.get('known_pct') or row.get('found_pct') or 0)
    word_penalty = _clamp((100 - word_known) / 100.0, 0, 1)
    avg_sent = (sum(target_sentence_counts) / max(len(target_sentence_counts), 1)) if target_sentence_counts else 0
    long_sentences = sum(1 for c in target_sentence_counts if c >= 18)
    sentence_penalty = _clamp((avg_sent - 8) / 18.0, 0, 1) * 0.65 + _clamp(long_sentences / max(len(target_sentence_counts), 1), 0, 1) * 0.35
    too_adv, slight_adv = float(meaning.get('too_advanced_count') or 0), float(meaning.get('slightly_advanced_count') or 0)
    concept_penalty = _clamp((too_adv + slight_adv * 0.35) / 80.0, 0, 1)
    try: consistency_pct = float(suitability.get('progression', {}).get('consistency_score'))
    except Exception: consistency_pct = None
    consistency_penalty = 0 if consistency_pct is None else _clamp((100 - consistency_pct) / 100.0, 0, 1)
    difficulty_10 = _clamp(round(word_penalty * 3.5 + sentence_penalty * 2.5 + concept_penalty * 2.5 + consistency_penalty * 1.5, 1), 0, 10)
    if difficulty_10 < 3.5: difficulty_label, support_level = 'Easy', 'Low'
    elif difficulty_10 < 6.5: difficulty_label, support_level = 'Moderate', 'Medium'
    else: difficulty_label, support_level = 'Hard', 'High'
    independent = word_known >= 85 and difficulty_10 < 5.5 and too_adv <= 10 and avg_sent <= 14
    unknown_words = _top_unknown_words(results, rec_grade, 80)
    lookup = _lookup_library(unknown_words[:80], kb_dir=kb_dir)
    clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in (meaning.get('flagged') or [])[:150]:
        item = f.get('item') or f.get('word') or ''
        if item:
            clusters[f.get('concept') or 'general'].append({'word': item, 'freq': f.get('freq', 1), 'level': f.get('level'), 'severity': f.get('severity')})
    for w in unknown_words[:80]:
        concept = (lookup.get(w) or {}).get('concept') or 'general'
        if not any(x.get('word') == w for x in clusters[concept]): clusters[concept].append({'word': w, 'freq': None, 'level': (lookup.get(w) or {}).get('grade_level')})
    cluster_list = [{'concept': c, 'label': CONCEPT_LABELS.get(c, c.replace('_',' ').title()), 'count': len(items), 'words': items[:25]} for c, items in sorted(clusters.items(), key=lambda kv: -len(kv[1]))[:10]]
    glossary = []
    for w in unknown_words[:50]:
        meta = lookup.get(w) or {}
        glossary.append({'word': w, 'meaning': meta.get('definition') or SIMPLE_EXPLANATIONS.get(w) or 'Add a simple classroom meaning.', 'class_level': meta.get('grade_level') or rec_grade, 'concept': CONCEPT_LABELS.get(meta.get('concept'), meta.get('concept') or 'general'), 'wiki_freq': meta.get('wiki_freq', 0)})
    rewrite_suggestions = []
    for s in _sentences(raw_text)[:400]:
        wc = _word_count(s)
        if wc >= 22:
            rewrite_suggestions.append({'type': 'split_sentence', 'severity': 'high' if wc >= 30 else 'medium', 'original': s[:350], 'suggestion': 'Split this into 2 shorter sentences. Keep one idea per sentence.', 'reason': f'Long sentence ({wc} Tamil words).'})
        if len(rewrite_suggestions) >= 12: break
    for w in unknown_words[:25]:
        meta = lookup.get(w) or {}
        rewrite_suggestions.append({'type': 'explain_or_replace_word', 'severity': 'medium', 'word': w, 'suggestion': SIMPLE_EXPLANATIONS.get(w) or meta.get('definition') or 'Use a simpler word if available, or add this word to the glossary.', 'reason': f'May be difficult around Std {rec_grade}.'})
        if len(rewrite_suggestions) >= 35: break
    before, during, after = [], [], []
    if unknown_words: before.append(f'Pre-teach {min(len(unknown_words), 15)} difficult words before reading.')
    if cluster_list: before.append('Introduce the main concepts: ' + ', '.join(c['label'] for c in cluster_list[:3]) + '.')
    if long_sentences: during.append('Use guided reading for long sentences; pause and ask students to restate the idea.')
    hard_chunks = suitability.get('progression', {}).get('issues', {}).get('hard_chunks')
    if hard_chunks:
        during.append('Give extra support on difficult pages/chunks: ' + ', '.join(str((p.get('page') if isinstance(p, dict) else p)) for p in hard_chunks[:5]) + '.')
    if glossary: after.append('Use the glossary for a matching or oral explanation activity.')
    after.append('Ask students to summarize the story/concept in their own words.')
    return {'enabled': True, 'version': 'v27-offline', 'recommended_grade': rec_grade, 'difficulty_score_10': difficulty_10, 'difficulty_label': difficulty_label, 'support_level': support_level, 'independent_reading': bool(independent), 'components': {'word_known_pct': round(word_known, 1), 'avg_sentence_words': round(avg_sent, 1), 'long_sentence_count': long_sentences, 'concept_flags': int(too_adv + slight_adv), 'consistency_pct': consistency_pct}, 'lesson_plan': {'support_level': support_level, 'independent_reading': bool(independent), 'before_reading': before or ['Briefly introduce the topic and 3–5 key words.'], 'during_reading': during or ['Read normally; pause only when students ask for support.'], 'after_reading': after}, 'concept_clusters': cluster_list, 'smart_glossary': glossary[:40], 'rewrite_suggestions': rewrite_suggestions}

def collect_home_metrics(main_db_path: str = 'tamil_analyzer.db', kb_dir: str = 'data/meaning_kb', cache_dir: str = 'data/cache') -> Dict[str, Any]:
    metrics = {'books_loaded': 0, 'total_grade_words': 0, 'analyses_saved': 0, 'grades': [], 'wikipedia_words': 0, 'wikipedia_articles': 0, 'word_library_words': 0, 'cache_files': 0, 'cache_size_mb': 0.0, 'meaning_kb_files': 0}
    if os.path.exists(main_db_path):
        try:
            conn = sqlite3.connect(main_db_path); conn.row_factory = sqlite3.Row
            for key, sql in [('books_loaded','SELECT COUNT(*) FROM grade_files'), ('total_grade_words','SELECT COUNT(DISTINCT word) FROM grade_words'), ('analyses_saved','SELECT COUNT(*) FROM analyses')]:
                try: metrics[key] = conn.execute(sql).fetchone()[0]
                except Exception: pass
            try: metrics['grades'] = [dict(r) for r in conn.execute('SELECT grade, word_count, file_count, raw_count, sent_avg FROM grade_meta ORDER BY grade').fetchall()]
            except Exception: pass
            conn.close()
        except Exception: pass
    for wdb in ['word_library.db', os.path.join(kb_dir, 'word_library.db')]:
        if os.path.exists(wdb):
            try:
                conn = sqlite3.connect(wdb); metrics['word_library_words'] = conn.execute('SELECT COUNT(*) FROM word_library').fetchone()[0]; conn.close(); break
            except Exception: pass
    wiki_path = os.path.join(kb_dir, 'tamil_wikipedia_kb.sqlite')
    if os.path.exists(wiki_path):
        try:
            conn = sqlite3.connect(wiki_path); metrics['wikipedia_words'] = conn.execute('SELECT COUNT(*) FROM words').fetchone()[0]; metrics['wikipedia_articles'] = conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]; conn.close()
        except Exception: pass
    try:
        files = [p for p in Path(cache_dir).rglob('*') if p.is_file()] if Path(cache_dir).exists() else []
        metrics['cache_files'], metrics['cache_size_mb'] = len(files), round(sum(p.stat().st_size for p in files)/(1024*1024), 1)
    except Exception: pass
    try:
        metrics['meaning_kb_files'] = len([p for p in Path(kb_dir).rglob('*') if p.is_file()]) if Path(kb_dir).exists() else 0
    except Exception: pass
    return metrics
