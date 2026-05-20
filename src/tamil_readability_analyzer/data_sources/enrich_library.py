#!/usr/bin/env python3
"""
enrich_library.py — One-time AI enrichment for the Tamil Word Library.

Sends words from word_library.db to an AI API in batches.
For each word, gets: grade level, Tamil definition, part of speech,
concept category, confidence.

Results are written back to word_library.db permanently.
Run this ONCE after building the library from textbooks + Wikipedia.

USAGE
-----
# Gemini (free — recommended, get key at aistudio.google.com):
python enrich_library.py --backend gemini --api-key YOUR_KEY

# Claude API (~$5-10 for full library, needs console.anthropic.com billing):
python enrich_library.py --backend claude --api-key YOUR_KEY

# OpenAI GPT-4o-mini (~$3-8, needs api.openai.com billing):
python enrich_library.py --backend openai --api-key YOUR_KEY

OPTIONS
-------
--backend      gemini | claude | openai  (default: gemini)
--api-key      API key string
--batch-size   Words per API call (default: 100, max: 200)
--limit        Max words to process (default: 0 = all)
--only-empty   Only enrich words with no definition yet (default: true)
--overwrite    Re-enrich words that already have definitions
--dry-run      Show what would be sent, don't call the API
--resume       Skip words already enriched in this session (uses checkpoint file)
--db           Path to word_library.db (default: word_library.db)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import datetime
from pathlib import Path
from typing import Dict, List, Optional


# ── Prompt constants ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Tamil language expert helping build a children's vocabulary library for Tamil Nadu school textbooks (Standards 1–12).

For each Tamil word given, return a JSON array with one object per word:
- "word": the input word exactly as given
- "grade": integer 1–12 (Tamil Nadu standard at which a child typically first learns this word)
- "definition": simple Tamil explanation using only Standard 1–3 vocabulary (max 15 Tamil words, complete sentence)
- "pos": part of speech in Tamil — ONE of: பெயர்ச்சொல், வினைச்சொல், பண்புச்சொல், வினையடை, உரிச்சொல்
- "concept": ONE of: daily_life, nature_environment, language_literature, science_technology, society_civics, health_body, math_logic, history_culture, general
- "confidence": high | medium | low

Tamil Nadu grade guidelines:
• Std 1–2: family, body, basic objects, colors, animals (அம்மா, கண், சிவப்பு, நாய்)
• Std 3–4: common actions, nature, simple places (மரம், படி, வீடு, கடல், மலை)
• Std 5–6: school, environment, community, food (ஆசிரியர், சுற்றுச்சூழல், நூலகம், காய்கறி)
• Std 7–9: abstract concepts, science, history, civics (விஞ்ஞானம், வரலாறு, சமூகம், சட்டம்)
• Std 10–12: advanced, technical, literary (ஆராய்ச்சி, தொழில்நுட்பம், பொருளாதாரம், இலக்கணம்)

Return ONLY the raw JSON array. No markdown, no explanation, no preamble."""

USER_TEMPLATE = "Analyze these Tamil words:\n\n{words}"


# ── Response parser ───────────────────────────────────────────────────────────

def parse_response(text: str) -> Optional[List[Dict]]:
    """Parse AI response into a list of word dicts. Handles markdown wrapping."""
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ```
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    # Find the JSON array
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        if not isinstance(data, list):
            return None
        return data
    except json.JSONDecodeError:
        return None


# ── API backends ──────────────────────────────────────────────────────────────

def call_gemini(words: List[str], api_key: str, model: str = 'gemini-1.5-flash') -> Optional[str]:
    """Call Google Gemini API."""
    import urllib.request
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}'
    payload = {
        'system_instruction': {'parts': [{'text': SYSTEM_PROMPT}]},
        'contents': [{'parts': [{'text': USER_TEMPLATE.format(words='\n'.join(words))}]}],
        'generationConfig': {
            'temperature': 0.1,
            'maxOutputTokens': 8192,
            'responseMimeType': 'application/json',
        },
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
        return resp['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f'  Gemini error: {e}', file=sys.stderr)
        return None


def call_claude(words: List[str], api_key: str, model: str = 'claude-haiku-4-5-20251001') -> Optional[str]:
    """Call Anthropic Claude API."""
    import urllib.request
    url = 'https://api.anthropic.com/v1/messages'
    payload = {
        'model': model,
        'max_tokens': 8192,
        'system': SYSTEM_PROMPT,
        'messages': [{'role': 'user', 'content': USER_TEMPLATE.format(words='\n'.join(words))}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
        return resp['content'][0]['text']
    except Exception as e:
        print(f'  Claude error: {e}', file=sys.stderr)
        return None


def call_openai(words: List[str], api_key: str, model: str = 'gpt-4o-mini') -> Optional[str]:
    """Call OpenAI API."""
    import urllib.request
    url = 'https://api.openai.com/v1/chat/completions'
    payload = {
        'model': model,
        'temperature': 0.1,
        'max_tokens': 8192,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user',   'content': USER_TEMPLATE.format(words='\n'.join(words))},
        ],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
        text = resp['choices'][0]['message']['content']
        # OpenAI with json_object may wrap in {"words": [...]}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        return json.dumps(v)
            return text
        except Exception:
            return text
    except Exception as e:
        print(f'  OpenAI error: {e}', file=sys.stderr)
        return None


BACKENDS = {
    'gemini': call_gemini,
    'claude': call_claude,
    'openai': call_openai,
}

# Rate limits (requests per minute)
RATE_LIMITS = {
    'gemini': 15,   # free tier: 15 rpm
    'claude': 50,
    'openai': 500,
}


# ── DB helpers ────────────────────────────────────────────────────────────────

VALID_CONCEPTS = {
    'daily_life', 'nature_environment', 'language_literature',
    'science_technology', 'society_civics', 'health_body',
    'math_logic', 'history_culture', 'general',
}
VALID_POS = {
    'பெயர்ச்சொல்', 'வினைச்சொல்', 'பண்புச்சொல்',
    'வினையடை', 'உரிச்சொல்', 'சொல்லடை',
}


def validate_item(item: Dict) -> Dict:
    """Sanitise one AI response item."""
    grade = item.get('grade')
    try:
        grade = max(1, min(12, int(grade)))
    except (TypeError, ValueError):
        grade = None

    concept = item.get('concept', 'general')
    if concept not in VALID_CONCEPTS:
        concept = 'general'

    pos = item.get('pos', '')
    if pos not in VALID_POS:
        pos = ''

    definition = (item.get('definition') or '').strip()[:300]
    confidence = item.get('confidence', 'medium')
    if confidence not in ('high', 'medium', 'low'):
        confidence = 'medium'

    return {
        'grade':      grade,
        'definition': definition,
        'pos':        pos,
        'concept':    concept,
        'confidence': confidence,
    }


def write_enrichments(db_path: str, enrichments: List[Dict]) -> int:
    """Write a batch of enriched words to the library DB."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    now = datetime.datetime.now().isoformat()
    written = 0
    for item in enrichments:
        stem = item.get('stem')
        if not stem:
            continue
        grade     = item.get('grade')
        defn      = item.get('definition', '')
        pos       = item.get('pos', '')
        concept   = item.get('concept', 'general')
        confidence = item.get('confidence', 'medium')
        confirmed = 1 if confidence == 'high' else 0

        # Only update fields that are not already set by a higher-trust source
        # (manual entries are never overwritten)
        existing = conn.execute(
            'SELECT grade_source, confirmed FROM word_library WHERE stem=?', (stem,)
        ).fetchone()

        if existing and existing[0] == 'manual':
            continue  # never overwrite manual entries

        if grade:
            conn.execute('''
                UPDATE word_library SET
                  definition   = COALESCE(NULLIF(definition,''), ?),
                  part_of_speech = COALESCE(NULLIF(part_of_speech,''), ?),
                  concept      = CASE WHEN concept='general' THEN ? ELSE concept END,
                  grade_level  = CASE
                                   WHEN grade_source IN ('textbook','manual') THEN grade_level
                                   ELSE ?
                                 END,
                  grade_source = CASE
                                   WHEN grade_source IN ('textbook','manual') THEN grade_source
                                   ELSE 'ai_enriched'
                                 END,
                  confirmed    = MAX(confirmed, ?),
                  updated_at   = ?
                WHERE stem=?
            ''', (defn, pos, concept, grade, confirmed, now, stem))
        else:
            conn.execute('''
                UPDATE word_library SET
                  definition   = COALESCE(NULLIF(definition,''), ?),
                  part_of_speech = COALESCE(NULLIF(part_of_speech,''), ?),
                  concept      = CASE WHEN concept='general' THEN ? ELSE concept END,
                  updated_at   = ?
                WHERE stem=?
            ''', (defn, pos, concept, now, stem))

        written += 1

    conn.commit()
    conn.close()
    return written


# ── Checkpoint ────────────────────────────────────────────────────────────────

def load_checkpoint(path: str) -> set:
    p = Path(path)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()))
    except Exception:
        return set()


def save_checkpoint(path: str, done: set) -> None:
    Path(path).write_text(json.dumps(list(done)))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Enrich Tamil word library with AI-generated grades, definitions, POS.'
    )
    parser.add_argument('--backend',    default='gemini',
                        choices=['gemini', 'claude', 'openai'])
    parser.add_argument('--api-key',    default=os.environ.get('AI_API_KEY', ''))
    parser.add_argument('--model',      default='',
                        help='Override model name (default: best for each backend)')
    parser.add_argument('--batch-size', type=int, default=100,
                        help='Words per API call (default: 100)')
    parser.add_argument('--limit',      type=int, default=0,
                        help='Max words to process (0 = all)')
    parser.add_argument('--only-empty', action='store_true', default=True,
                        help='Only enrich words with no definition (default)')
    parser.add_argument('--overwrite',  action='store_true',
                        help='Re-enrich words that already have definitions')
    parser.add_argument('--dry-run',    action='store_true',
                        help='Show batches but do not call API')
    parser.add_argument('--resume',     action='store_true',
                        help='Skip words in checkpoint file')
    parser.add_argument('--db',         default='word_library.db')
    parser.add_argument('--delay',      type=float, default=0,
                        help='Extra seconds between API calls (auto-set for free tiers)')
    args = parser.parse_args()

    if not args.api_key and not args.dry_run:
        print('ERROR: --api-key is required (or set AI_API_KEY env variable)')
        print()
        print('Get a free Gemini key at: https://aistudio.google.com/apikey')
        print('Get Claude API key at:    https://console.anthropic.com')
        print('Get OpenAI API key at:    https://platform.openai.com/api-keys')
        sys.exit(1)

    if not Path(args.db).exists():
        print(f'ERROR: {args.db} not found. Build the library first.')
        print('Run the app, go to Word Library → Add/Import → Build from textbooks')
        sys.exit(1)

    # Load words to enrich
    conn = sqlite3.connect(args.db)
    if args.overwrite:
        query = 'SELECT stem, display_word FROM word_library ORDER BY grade_level, frequency DESC'
        params = []
    else:
        query = ('SELECT stem, display_word FROM word_library '
                 'WHERE (definition IS NULL OR definition = "") '
                 'ORDER BY grade_level, frequency DESC')
        params = []

    rows = conn.execute(query, params).fetchall()
    conn.close()

    total_in_db = sqlite3.connect(args.db).execute(
        'SELECT COUNT(*) FROM word_library'
    ).fetchone()[0]

    words_todo = [(r[0], r[1] or r[0]) for r in rows]
    if args.limit:
        words_todo = words_todo[:args.limit]

    print(f'\nTamil Word Library Enrichment')
    print(f'{'='*50}')
    print(f'  Database:       {args.db}')
    print(f'  Total in DB:    {total_in_db:,} words')
    print(f'  To enrich:      {len(words_todo):,} words')
    print(f'  Backend:        {args.backend}')
    print(f'  Batch size:     {args.batch_size}')
    print(f'  Dry run:        {args.dry_run}')

    # Cost estimate
    approx_tokens = len(words_todo) * 120  # ~120 tokens per word (in+out)
    cost_map = {'gemini': 0, 'claude': 0.0008, 'openai': 0.00015}
    est_cost = approx_tokens / 1000 * cost_map.get(args.backend, 0)
    if args.backend == 'gemini':
        est_calls = len(words_todo) // args.batch_size + 1
        est_days  = est_calls / 1500  # 1500 req/day free
        print(f'  Estimated cost: FREE (Gemini free tier)')
        print(f'  Estimated time: {est_days:.1f} days at 1500 req/day free tier')
        print(f'                  or {est_calls * 4:.0f}s if you have paid Gemini')
    else:
        print(f'  Estimated cost: ~${est_cost:.2f} USD')
        est_mins = len(words_todo) / args.batch_size / RATE_LIMITS[args.backend] * 60
        print(f'  Estimated time: ~{est_mins:.0f} minutes')

    print()

    if args.dry_run:
        print('DRY RUN — showing first 2 batches:\n')
        for i in range(min(2, len(words_todo) // args.batch_size + 1)):
            batch = [w for _, w in words_todo[i*args.batch_size:(i+1)*args.batch_size]]
            print(f'Batch {i+1}: {len(batch)} words')
            print(f'  First 5: {batch[:5]}')
            prompt = USER_TEMPLATE.format(words='\n'.join(batch[:3]))
            print(f'  Sample prompt:\n{prompt}\n')
        return

    # Load checkpoint
    checkpoint_file = f'.enrich_checkpoint_{args.backend}.json'
    done_stems = load_checkpoint(checkpoint_file) if args.resume else set()
    if done_stems:
        print(f'Resuming: {len(done_stems)} already done from checkpoint\n')
        words_todo = [(s, w) for s, w in words_todo if s not in done_stems]

    # Choose model
    model_defaults = {
        'gemini': 'gemini-1.5-flash',
        'claude': 'claude-haiku-4-5-20251001',
        'openai': 'gpt-4o-mini',
    }
    model = args.model or model_defaults[args.backend]
    call_fn = BACKENDS[args.backend]

    # Rate limit delay
    rpm = RATE_LIMITS[args.backend]
    min_delay = 60.0 / rpm + args.delay
    if args.backend == 'gemini' and not args.delay:
        min_delay = 4.5  # 15 rpm free tier → 4s between calls

    # Process
    total_written = 0
    failed_batches = []
    n_batches = (len(words_todo) + args.batch_size - 1) // args.batch_size

    print(f'Starting enrichment — {n_batches} batches of {args.batch_size} words\n')

    for batch_i in range(n_batches):
        batch_pairs = words_todo[batch_i * args.batch_size:(batch_i + 1) * args.batch_size]
        batch_stems  = [s for s, _ in batch_pairs]
        batch_words  = [w for _, w in batch_pairs]

        print(f'Batch {batch_i+1}/{n_batches} ({len(batch_words)} words) … ', end='', flush=True)

        t0 = time.time()
        raw = call_fn(batch_words, args.api_key, model)

        if raw is None:
            print('FAILED (API error)')
            failed_batches.append(batch_i)
            time.sleep(min_delay * 2)
            continue

        parsed = parse_response(raw)
        if parsed is None:
            print(f'FAILED (parse error)')
            print(f'  Raw response: {raw[:200]}')
            failed_batches.append(batch_i)
            time.sleep(min_delay)
            continue

        # Map parsed results back to stems
        word_to_stem = {w: s for s, w in batch_pairs}
        enrichments = []
        for item in parsed:
            w = item.get('word', '')
            stem = word_to_stem.get(w, w)
            validated = validate_item(item)
            validated['stem'] = stem
            enrichments.append(validated)

        written = write_enrichments(args.db, enrichments)
        total_written += written

        # Update checkpoint
        done_stems.update(batch_stems)
        if args.resume:
            save_checkpoint(checkpoint_file, done_stems)

        elapsed = time.time() - t0
        print(f'{written} written ({elapsed:.1f}s)')

        # Rate limit
        wait = max(0, min_delay - elapsed)
        if wait > 0:
            time.sleep(wait)

    # Retry failed batches once
    if failed_batches:
        print(f'\nRetrying {len(failed_batches)} failed batches…')
        time.sleep(30)
        for batch_i in failed_batches:
            batch_pairs = words_todo[batch_i * args.batch_size:(batch_i + 1) * args.batch_size]
            batch_words  = [w for _, w in batch_pairs]
            batch_stems  = [s for s, _ in batch_pairs]
            print(f'  Retry batch {batch_i+1} … ', end='', flush=True)
            raw = call_fn(batch_words, args.api_key, model)
            if raw:
                parsed = parse_response(raw)
                if parsed:
                    word_to_stem = {w: s for s, w in batch_pairs}
                    enrichments = []
                    for item in parsed:
                        w = item.get('word', '')
                        stem = word_to_stem.get(w, w)
                        validated = validate_item(item)
                        validated['stem'] = stem
                        enrichments.append(validated)
                    written = write_enrichments(args.db, enrichments)
                    total_written += written
                    print(f'{written} written')
                    continue
            print('failed again — skipping')
            time.sleep(min_delay)

    # Final stats
    conn = sqlite3.connect(args.db)
    with_def = conn.execute(
        "SELECT COUNT(*) FROM word_library WHERE definition != '' AND definition IS NOT NULL"
    ).fetchone()[0]
    ai_enriched = conn.execute(
        "SELECT COUNT(*) FROM word_library WHERE grade_source='ai_enriched'"
    ).fetchone()[0]
    conn.close()

    print(f'\n{"="*50}')
    print(f'Enrichment complete')
    print(f'  Words written this run: {total_written:,}')
    print(f'  Total with definitions: {with_def:,} / {total_in_db:,}')
    print(f'  AI-enriched grade:      {ai_enriched:,}')
    if failed_batches:
        print(f'  Permanently failed:     {len(failed_batches)} batches')
    print()
    print('Next steps:')
    print('  1. Open the app → Word Library → Browse to review results')
    print('  2. Click any word to confirm its grade (marks it as teacher-confirmed)')
    print('  3. Word Library → Sources tab to see enrichment stats')
    print('  4. Word Library → Sync to Analyzer to feed enriched vocab into readability scores')


if __name__ == '__main__':
    main()
