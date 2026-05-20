"""Local meaning-level knowledge base builder/analyzer.

This module is deliberately independent from the existing readability DB schema.
It reads existing grade_words and, when available, grade_files file paths to build
separate JSON files under data/meaning_kb/.
"""
from __future__ import annotations

import json, os, re, sqlite3, time, math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Tuple, Any

TAMIL_RE = re.compile(r'[\u0B80-\u0BFF]+')
STOPWORDS = {
    'ஒரு','இந்த','அந்த','இது','அது','எது','என்','உன்','தன்','நம்','நான்','நீ','அவன்','அவள்','அவர்கள்','அவை','இவை',
    'மற்றும்','ஆகிய','என','என்று','என்ற','உள்ள','இல்லை','ஆம்','ஆகும்','வேண்டும்','முதல்','வரை','அல்லது','ஆனால்',
    'பின்','முன்','மேல்','கீழ்','உடன்','வழி','போல்','போன்ற','மிக','பல','எல்லாம்','யார்','என்ன','எப்படி','ஏன்',
    'கொண்டு','செய்து','வரும்','இருக்கும்','உண்டு','இவர்','அவர்','இவர்கள்','அவர்கள்','நூல்','பக்கம்','பயிற்சி'
}

DEFAULT_TEACHER_OVERRIDES = {
    "force_easy": [],
    "force_hard": [],
    "ignore": [],
    "manual_levels": {}
}

CONCEPT_SEEDS = {
    "language_literature": ["தமிழ்", "மொழி", "இலக்கியம்", "இலக்கணம்", "கவிதை", "பாடல்", "சொல்", "எழுத்து", "பேச்சு", "உரை"],
    "nature_environment": ["மரம்", "காடு", "நீர்", "காற்று", "மழை", "சுற்றுச்சூழல்", "மாசு", "விலங்கு", "பறவை", "நிலம்"],
    "science_systems": ["வளிமண்டலம்", "ஆற்றல்", "சுழற்சி", "அறிவியல்", "வெப்பம்", "ஒளி", "ஒலி", "மின்சாரம்", "அழுத்தம்"],
    "society_civics": ["நாடு", "அரசு", "சமூகம்", "உரிமை", "சட்டம்", "தேர்தல்", "குடிமை", "சமத்துவம்", "தேசியம்"],
    "health_body": ["உடல்", "நலம்", "உணவு", "நோய்", "சுகாதாரம்", "பாதுகாப்பு", "மருத்துவம்", "உறுப்பு"],
    "math_logic": ["எண்", "கூட்டல்", "கழித்தல்", "வடிவம்", "அளவு", "கோணம்", "வட்டம்", "பெருக்கல்", "வகுத்தல்"],
    "history_culture": ["வரலாறு", "பண்பாடு", "மரபு", "சங்க", "வள்ளல்", "புலவர்", "கலை", "திருவிழா"]
}


def _safe_json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


def load_overrides(kb_dir: str | Path) -> dict:
    p = Path(kb_dir) / 'teacher_overrides.json'
    if not p.exists():
        _safe_json_dump(p, DEFAULT_TEACHER_OVERRIDES)
        return dict(DEFAULT_TEACHER_OVERRIDES)
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        data = {}
    out = dict(DEFAULT_TEACHER_OVERRIDES)
    out.update(data if isinstance(data, dict) else {})
    return out


def normalize_token(w: str) -> str:
    w = (w or '').strip()
    w = re.sub(r'[^\u0B80-\u0BFF]', '', w)
    return w


def default_tokenize(text: str) -> List[str]:
    return [w for w in TAMIL_RE.findall(text or '') if len(w) > 1]


def make_phrases(tokens: List[str], min_count: int = 2, max_n: int = 4) -> Counter:
    toks = [normalize_token(t) for t in tokens]
    toks = [t for t in toks if t and t not in STOPWORDS and len(t) > 1]
    c = Counter()
    for n in range(2, max_n + 1):
        for i in range(0, max(0, len(toks) - n + 1)):
            gram = toks[i:i+n]
            if any(x in STOPWORDS for x in gram):
                continue
            phrase = ' '.join(gram)
            c[phrase] += 1
    return Counter({k:v for k,v in c.items() if v >= min_count})


def _first_significant_grade(counts_by_grade: Dict[int, int], min_count: int = 2) -> int:
    for g in sorted(counts_by_grade):
        if counts_by_grade[g] >= min_count:
            return int(g)
    if counts_by_grade:
        return int(min(counts_by_grade, key=lambda g: (g, -counts_by_grade[g])))
    return 12


def _concept_for_item(item: str) -> str:
    for concept, seeds in CONCEPT_SEEDS.items():
        if any(s in item for s in seeds):
            return concept
    return 'general'


def build_from_existing_db(
    db_path: str = 'tamil_analyzer.db',
    kb_dir: str = 'data/meaning_kb',
    extract_text_fn: Callable[[str], str] | None = None,
    tokenize_fn: Callable[[str], List[str]] | None = None,
    stem_fn: Callable[[str], str] | None = None,
    full_rebuild: bool = True,
    max_files: int | None = None,
) -> dict:
    """Build KB from existing DB. Uses grade_files text if files exist; falls back to grade_words."""
    tokenize_fn = tokenize_fn or default_tokenize
    stem_fn = stem_fn or (lambda x: x)
    kb_path = Path(kb_dir)
    kb_path.mkdir(parents=True, exist_ok=True)
    overrides = load_overrides(kb_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    grade_words_rows = conn.execute('SELECT grade, word FROM grade_words ORDER BY grade, word').fetchall()
    try:
        file_rows = conn.execute('SELECT grade, filepath, filename FROM grade_files ORDER BY grade, filename').fetchall()
    except Exception:
        file_rows = []
    conn.close()

    if not grade_words_rows and not file_rows:
        raise RuntimeError('No existing textbook data found. Upload/process school books first.')

    word_counts_by_grade: Dict[str, Counter] = defaultdict(Counter)
    phrase_counts_by_grade: Dict[str, Counter] = defaultdict(Counter)
    source_files_used = []
    source_files_missing = []

    # Always use existing grade_words as a stable fallback/backbone.
    for r in grade_words_rows:
        w = normalize_token(r['word'])
        if w and w not in STOPWORDS:
            word_counts_by_grade[w][int(r['grade'])] += 1

    # Add phrase data by re-reading available source files. This does not alter DB.
    files_seen = 0
    if extract_text_fn and file_rows:
        for r in file_rows:
            if max_files is not None and files_seen >= max_files:
                break
            fp = r['filepath']
            g = int(r['grade'])
            if not fp or not os.path.exists(fp):
                source_files_missing.append({'grade': g, 'filename': r['filename'], 'filepath': fp})
                continue
            files_seen += 1
            try:
                text = extract_text_fn(fp) or ''
                toks = tokenize_fn(text)
                # Words from original text improve frequency signal.
                for t in toks:
                    s = normalize_token(stem_fn(t))
                    if s and s not in STOPWORDS:
                        word_counts_by_grade[s][g] += 1
                phrases = make_phrases(toks, min_count=2, max_n=4)
                for p, cnt in phrases.items():
                    phrase_counts_by_grade[p][g] += cnt
                source_files_used.append({'grade': g, 'filename': r['filename'], 'tokens': len(toks), 'phrases': len(phrases)})
            except Exception as e:
                source_files_missing.append({'grade': g, 'filename': r['filename'], 'filepath': fp, 'error': str(e)})

    min_word_count = 2
    word_levels = {}
    word_meta = {}
    for w, counts in word_counts_by_grade.items():
        if w in set(overrides.get('ignore', [])):
            continue
        level = _first_significant_grade(dict(counts), min_word_count)
        if w in set(overrides.get('force_easy', [])):
            level = 1
        if w in set(overrides.get('force_hard', [])):
            level = max(level, 6)
        if w in overrides.get('manual_levels', {}):
            try: level = int(overrides['manual_levels'][w])
            except Exception: pass
        word_levels[w] = level
        word_meta[w] = {'counts_by_grade': {str(k): int(v) for k,v in counts.items()}, 'concept': _concept_for_item(w)}

    phrase_levels = {}
    phrase_meta = {}
    for p, counts in phrase_counts_by_grade.items():
        if p in set(overrides.get('ignore', [])):
            continue
        # Phrases need stronger evidence than words.
        if sum(counts.values()) < 2:
            continue
        level = _first_significant_grade(dict(counts), min_count=2)
        if p in overrides.get('manual_levels', {}):
            try: level = int(overrides['manual_levels'][p])
            except Exception: pass
        phrase_levels[p] = level
        phrase_meta[p] = {'counts_by_grade': {str(k): int(v) for k,v in counts.items()}, 'concept': _concept_for_item(p)}

    concept_levels = {}
    concept_items = defaultdict(list)
    for item, lvl in {**word_levels, **phrase_levels}.items():
        concept = _concept_for_item(item)
        concept_items[concept].append((item, lvl))
    for concept, items in concept_items.items():
        if not items: continue
        # Typical concept level: median-ish of its members, capped to first appearance signal.
        levels = sorted(l for _, l in items)
        concept_levels[concept] = int(levels[len(levels)//2])

    metadata = {
        'version': int(time.time()),
        'built_on': time.strftime('%Y-%m-%d %H:%M:%S'),
        'db_path': os.path.abspath(db_path),
        'source': 'existing_db_grade_words_plus_available_grade_files',
        'word_count': len(word_levels),
        'phrase_count': len(phrase_levels),
        'concept_count': len(concept_levels),
        'source_files_used_count': len(source_files_used),
        'source_files_missing_count': len(source_files_missing),
        'source_files_used_sample': source_files_used[:50],
        'source_files_missing_sample': source_files_missing[:50],
        'notes': [
            'Existing readability tables are not modified.',
            'If original textbook files are unavailable, phrase coverage will be limited; word-level data still uses grade_words.',
            'Teacher edits belong in teacher_overrides.json.'
        ]
    }

    _safe_json_dump(kb_path / 'word_levels.json', word_levels)
    _safe_json_dump(kb_path / 'word_meta.json', word_meta)
    _safe_json_dump(kb_path / 'phrase_levels.json', phrase_levels)
    _safe_json_dump(kb_path / 'phrase_meta.json', phrase_meta)
    _safe_json_dump(kb_path / 'concept_levels.json', concept_levels)
    _safe_json_dump(kb_path / 'metadata.json', metadata)
    load_overrides(kb_path)  # ensure override file exists
    return metadata


def load_kb(kb_dir: str = 'data/meaning_kb') -> dict | None:
    kb = Path(kb_dir)
    meta_p = kb / 'metadata.json'
    word_p = kb / 'word_levels.json'
    phrase_p = kb / 'phrase_levels.json'
    if not (meta_p.exists() and word_p.exists() and phrase_p.exists()):
        return None
    try:
        return {
            'metadata': json.loads(meta_p.read_text(encoding='utf-8')),
            'word_levels': json.loads(word_p.read_text(encoding='utf-8')),
            'phrase_levels': json.loads(phrase_p.read_text(encoding='utf-8')),
            'word_meta': json.loads((kb / 'word_meta.json').read_text(encoding='utf-8')) if (kb/'word_meta.json').exists() else {},
            'phrase_meta': json.loads((kb / 'phrase_meta.json').read_text(encoding='utf-8')) if (kb/'phrase_meta.json').exists() else {},
        }
    except Exception:
        return None


def analyze_text_meaning(text: str, target_grade: int, kb_dir: str = 'data/meaning_kb', tokenize_fn=None, stem_fn=None, limit: int = 200) -> dict:
    kb = load_kb(kb_dir)
    if not kb:
        return {'enabled': False, 'error': 'Meaning knowledge base not built yet.'}
    tokenize_fn = tokenize_fn or default_tokenize
    stem_fn = stem_fn or (lambda x: x)
    tokens_orig = tokenize_fn(text or '')
    stems = [normalize_token(stem_fn(t)) for t in tokens_orig]
    word_freq = Counter([s for s in stems if s and s not in STOPWORDS])
    phrases = make_phrases(tokens_orig, min_count=1, max_n=4)

    flags = []
    appropriate = 0
    checked = 0
    for w, freq in word_freq.items():
        lvl = kb['word_levels'].get(w)
        if lvl is None:
            continue
        checked += 1
        if int(lvl) <= target_grade:
            appropriate += 1
        else:
            gap = int(lvl) - target_grade
            flags.append({
                'item': w, 'type': 'word', 'freq': int(freq), 'level': int(lvl), 'gap': gap,
                'severity': 'too_advanced' if gap > 1 else 'slightly_advanced',
                'concept': kb.get('word_meta', {}).get(w, {}).get('concept', 'general')
            })
    for p, freq in phrases.items():
        lvl = kb['phrase_levels'].get(p)
        if lvl is None:
            continue
        checked += 1
        if int(lvl) <= target_grade:
            appropriate += 1
        else:
            gap = int(lvl) - target_grade
            flags.append({
                'item': p, 'type': 'phrase', 'freq': int(freq), 'level': int(lvl), 'gap': gap,
                'severity': 'too_advanced' if gap > 1 else 'slightly_advanced',
                'concept': kb.get('phrase_meta', {}).get(p, {}).get('concept', 'general')
            })
    flags.sort(key=lambda x: (x['gap'], x['freq']), reverse=True)
    total_flags = len(flags)
    score = round((appropriate / checked * 100), 1) if checked else None
    by_severity = Counter(f['severity'] for f in flags)
    by_concept = Counter(f['concept'] for f in flags)
    return {
        'enabled': True,
        'kb_metadata': kb['metadata'],
        'target_grade': target_grade,
        'checked_items': checked,
        'appropriate_items': appropriate,
        'appropriateness_pct': score,
        'flagged_count': total_flags,
        'slightly_advanced_count': int(by_severity.get('slightly_advanced', 0)),
        'too_advanced_count': int(by_severity.get('too_advanced', 0)),
        'concept_summary': [{'concept': k, 'count': int(v)} for k,v in by_concept.most_common(20)],
        'flagged': flags[:limit],
    }
