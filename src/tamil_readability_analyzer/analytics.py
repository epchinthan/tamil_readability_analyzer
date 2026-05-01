"""
analytics.py — Extended readability metrics, all computed locally.

Metrics:
  1. Lexical diversity       — Type-Token Ratio (TTR) and corrected TTR (CTTR)
  2. Word length complexity  — Average Tamil character length per word
  3. Dialogue ratio          — % of text that is direct speech
  4. Paragraph complexity    — Average sentences and words per paragraph
  5. Repetition score        — Top-50 stems as % of total word count
  6. Content age flags       — Pattern-match Tamil keywords by concern category
  7. Overall readability     — Composite index combining all dimensions

All functions accept raw text strings and return plain dicts.
No external dependencies beyond the standard library and the
stem helper imported from app context.
"""

import re
import math
from collections import Counter


# ── Tamil text helpers ────────────────────────────────────────────────────────

def _tamil_words(text):
    """All Tamil Unicode tokens ≥ 2 characters."""
    return re.findall(r'[\u0B80-\u0BFF]{2,}', text)

def _paragraphs(text):
    """Split on blank lines; filter empty."""
    return [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

def _sentences(text):
    """Split on Tamil / Latin sentence-ending punctuation."""
    parts = re.split(r'[.!?।\u0964\u0965\n]+', text)
    return [p.strip() for p in parts if p.strip()]

# Dialogue markers in Tamil: opening/closing quote styles, உரையாடல் dash
_DIALOGUE_OPEN  = re.compile(r'["\u201C\u2018\u00AB]')
_DIALOGUE_CLOSE = re.compile(r'["\u201D\u2019\u00BB]')
_DIALOGUE_DASH  = re.compile(r'(?:^|\n)\s*[\u2013\u2014\-]\s+[\u0B80-\u0BFF]')


# ── 1. Lexical diversity ──────────────────────────────────────────────────────

def lexical_diversity(words, stem_fn=None):
    """
    Returns:
      ttr        — Type-Token Ratio (unique / total). Sensitive to text length.
      cttr       — Carroll's Corrected TTR: types / sqrt(2 * tokens).
                   More stable across different text lengths.
      hapax_pct  — % of stems that appear exactly once (hapax legomena).
                   High hapax % = rich, varied vocabulary.
      top10_pct  — % of total words covered by just the 10 most frequent stems.
                   High top10 % = repetitive text (typical of early-grade books).
    """
    if not words:
        return {'ttr': 0.0, 'cttr': 0.0, 'hapax_pct': 0.0, 'top10_pct': 0.0}

    if stem_fn:
        stems = [stem_fn(w) for w in words]
    else:
        stems = words

    total  = len(stems)
    types  = len(set(stems))
    freq   = Counter(stems)

    ttr       = round(types / total * 100, 1)
    cttr      = round(types / math.sqrt(2 * total) * 10, 1)   # scaled ×10 for readability
    hapax     = sum(1 for c in freq.values() if c == 1)
    hapax_pct = round(hapax / types * 100, 1) if types else 0.0
    top10     = sum(c for _, c in freq.most_common(10))
    top10_pct = round(top10 / total * 100, 1)

    # Grade-band interpretation of TTR
    if ttr >= 60:   ttr_label = 'Very rich vocabulary'
    elif ttr >= 45: ttr_label = 'Rich vocabulary'
    elif ttr >= 30: ttr_label = 'Moderate vocabulary'
    elif ttr >= 15: ttr_label = 'Repetitive (appropriate for early grades)'
    else:           ttr_label = 'Highly repetitive'

    return {
        'ttr':        ttr,
        'cttr':       cttr,
        'hapax_pct':  hapax_pct,
        'top10_pct':  top10_pct,
        'ttr_label':  ttr_label,
        'total_words':   total,
        'unique_words':  types,
    }


# ── 2. Word length complexity ─────────────────────────────────────────────────

def word_length_stats(words):
    """
    Average and distribution of Tamil word character lengths.
    Longer words = more morphological complexity = harder to decode.

    Tamil syllable approximation: each vowel marker (ா, ி, ீ, ு, ூ, ெ, ே, ை, ொ, ோ, ௌ)
    or inherent-vowel consonant counts as one syllable.
    """
    if not words:
        return {'avg_chars': 0.0, 'avg_syllables': 0.0, 'long_word_pct': 0.0}

    # Tamil vowel signs (matras) — each marks a syllable
    VOWEL_SIGNS = re.compile(r'[\u0BBE-\u0BCD\u0BD7]')

    char_lens = [len(w) for w in words]
    syl_lens  = []
    for w in words:
        # Consonants with inherent vowel = chars minus vowel signs minus pulli
        consonants = len(re.findall(r'[\u0B95-\u0BB9]', w))
        signs      = len(VOWEL_SIGNS.findall(w))
        # Rough syllable count: each consonant is one syllable,
        # vowel signs replace the inherent vowel (no extra count)
        syl_lens.append(max(1, consonants))

    avg_chars = round(sum(char_lens) / len(char_lens), 1)
    avg_syl   = round(sum(syl_lens)  / len(syl_lens),  1)
    # "Long words" = more than 10 Tamil characters (typically 4+ syllables)
    long_words  = sum(1 for l in char_lens if l > 10)
    long_pct    = round(long_words / len(words) * 100, 1)

    if avg_chars < 5:      len_label = 'Short words — easy to decode'
    elif avg_chars < 8:    len_label = 'Medium length words'
    elif avg_chars < 11:   len_label = 'Long words — moderate difficulty'
    else:                  len_label = 'Very long words — complex morphology'

    return {
        'avg_chars':    avg_chars,
        'avg_syllables': avg_syl,
        'long_word_pct': long_pct,
        'len_label':    len_label,
    }


# ── 3. Dialogue ratio ─────────────────────────────────────────────────────────

def dialogue_ratio(text):
    """
    Estimates the percentage of text that is direct speech.
    Direct speech is easier to read (conversational, familiar sentence patterns).

    Detection methods:
      a) Quoted blocks between " " / ' ' / « »
      b) Dialogue dashes at line start (— or – followed by Tamil text)
      c) Common Tamil speech verbs near sentence ends: கூறினார், சொன்னார், etc.
    """
    total_words = len(_tamil_words(text))
    if total_words == 0:
        return {'dialogue_pct': 0.0, 'dialogue_words': 0, 'label': 'No Tamil text'}

    # Method a: extract text inside quote pairs
    quoted_words = 0
    # Match content between matching quote characters
    for pattern in [r'"([^"]*)"', r'"([^"]*)"', r"'([^']*)'", r'«([^»]*)»']:
        for m in re.finditer(pattern, text):
            quoted_words += len(_tamil_words(m.group(1)))

    # Method b: dialogue dash lines
    dash_words = 0
    for line in text.split('\n'):
        stripped = line.strip()
        if re.match(r'^[\u2013\u2014\-]\s+[\u0B80-\u0BFF]', stripped):
            dash_words += len(_tamil_words(stripped))

    dialogue_words = min(quoted_words + dash_words, total_words)
    pct = round(dialogue_words / total_words * 100, 1)

    if pct >= 50:   label = 'Dialogue-heavy — easier to read'
    elif pct >= 25: label = 'Balanced dialogue and narration'
    elif pct >= 10: label = 'Mostly narrative with some dialogue'
    else:           label = 'Primarily narrative text'

    return {
        'dialogue_pct':   pct,
        'dialogue_words': dialogue_words,
        'total_words':    total_words,
        'label':          label,
    }


# ── 4. Paragraph complexity ───────────────────────────────────────────────────

def paragraph_stats(text):
    """
    Average sentences per paragraph and words per paragraph.
    Long paragraphs = higher cognitive load, harder for younger readers.
    """
    paras = _paragraphs(text)
    if not paras:
        return {'avg_sents_per_para': 0.0, 'avg_words_per_para': 0.0,
                'total_paragraphs': 0, 'label': 'No paragraphs detected'}

    sents_per  = [len(_sentences(p))   for p in paras]
    words_per  = [len(_tamil_words(p)) for p in paras]

    avg_sents = round(sum(sents_per) / len(sents_per), 1)
    avg_words = round(sum(words_per) / len(words_per), 1)
    max_words = max(words_per) if words_per else 0
    long_paras = sum(1 for w in words_per if w > 80)   # > 80 Tamil words = long
    long_pct   = round(long_paras / len(paras) * 100, 1)

    if avg_sents <= 2:   label = 'Short paragraphs — easy to follow'
    elif avg_sents <= 4: label = 'Medium paragraphs — moderate'
    else:                label = 'Long paragraphs — higher cognitive load'

    return {
        'avg_sents_per_para': avg_sents,
        'avg_words_per_para': avg_words,
        'max_words_in_para':  max_words,
        'long_para_pct':      long_pct,
        'total_paragraphs':   len(paras),
        'label':              label,
    }


# ── 5. Repetition score ───────────────────────────────────────────────────────

def repetition_score(words, stem_fn=None, top_n=50):
    """
    What % of total words are covered by the top-N most frequent stems?
    High repetition = simpler, more predictable (good for early grades).
    Low repetition = richer, more varied (appropriate for higher grades).
    """
    if not words:
        return {'top50_pct': 0.0, 'top10_pct': 0.0, 'label': 'No words'}

    stems = [stem_fn(w) for w in words] if stem_fn else list(words)
    total = len(stems)
    freq  = Counter(stems)

    top50 = sum(c for _, c in freq.most_common(top_n))
    top10 = sum(c for _, c in freq.most_common(10))
    top50_pct = round(top50 / total * 100, 1)
    top10_pct = round(top10 / total * 100, 1)

    if top50_pct >= 80:   label = 'Highly repetitive — Std 1–3 level'
    elif top50_pct >= 65: label = 'Repetitive — Std 4–6 level'
    elif top50_pct >= 50: label = 'Moderate variety — Std 7–9 level'
    else:                 label = 'Rich variety — Std 10–12 level'

    # Top 20 most frequent words for display
    top20 = [{'stem': s, 'count': c, 'pct': round(c/total*100, 1)}
             for s, c in freq.most_common(20)]

    return {
        'top50_pct': top50_pct,
        'top10_pct': top10_pct,
        'label':     label,
        'top20':     top20,
    }


# ── 6. Content age flags ──────────────────────────────────────────────────────
#
# Pattern-match against curated Tamil keyword lists.
# Each category has:
#   stems   — root forms (checked against stemmed text)
#   raw     — exact surface forms (checked against raw text)
#   min_age — minimum appropriate age
#   severity — 'info', 'caution', 'warning'
#
# Limitation: this is surface-level detection. It flags occurrences of
# violence-related words in, say, a history lesson about wars — which may
# be fully appropriate for the grade. The user should always review flags
# in context. We show the count and the matched words; we do NOT give a
# pass/fail verdict.

CONTENT_FLAGS = [
    {
        'category':  'Violence / physical harm',
        'icon':      '⚠',
        'severity':  'warning',
        'min_age':   10,
        'stems': [
            'கொலை', 'கொல்', 'இரத்த', 'இரத்தம்', 'காயம்', 'வலி',
            'அடி', 'குத்து', 'வெட்டு', 'அடிக்க', 'சாகடி', 'படுகொலை',
            'போர்', 'யுத்தம்', 'சண்டை', 'தாக்கு', 'துப்பாக்கி',
            'ஆயுதம்', 'குண்டு', 'வெடிகுண்டு', 'விஷம்', 'நஞ்சு',
        ],
        'raw': ['கொலை', 'இரத்தம்', 'போர்', 'படுகொலை', 'யுத்தம்'],
    },
    {
        'category':  'Death / grief',
        'icon':      '○',
        'severity':  'caution',
        'min_age':   7,
        'stems': [
            'இறந்த', 'இற', 'மரண', 'மரணம்', 'இறப்பு', 'மடிந்த',
            'சாவு', 'இறந்துவிட்ட', 'துக்கம்', 'அழுகை', 'புதைக்க',
            'சவம்', 'ஆவி', 'பேய்',
        ],
        'raw': ['மரணம்', 'இறந்தார்', 'சாவு', 'துக்கம்'],
    },
    {
        'category':  'Fear / horror',
        'icon':      '○',
        'severity':  'caution',
        'min_age':   8,
        'stems': [
            'பயம்', 'பயந்த', 'திகில்', 'அச்சம்', 'நடுங்கு',
            'பேய்', 'பூதம்', 'அரக்கன்', 'பிசாசு', 'கோரம்',
            'இரவு பயம்', 'தேவை',
        ],
        'raw': ['திகில்', 'பூதம்', 'பிசாசு', 'பேய்'],
    },
    {
        'category':  'Romance / love',
        'icon':      '○',
        'severity':  'info',
        'min_age':   13,
        'stems': [
            'காதல்', 'காதலன்', 'காதலி', 'முத்தம்', 'கட்டிப்பிடி',
            'திருமணம்', 'கல்யாணம்', 'மனைவி', 'கணவன்', 'காமம்',
            'ஆசை', 'மயக்கம்',
        ],
        'raw': ['காதல்', 'முத்தம்', 'காமம்'],
    },
    {
        'category':  'Substance / addiction',
        'icon':      '⚠',
        'severity':  'warning',
        'min_age':   12,
        'stems': [
            'மது', 'மதுபானம்', 'குடி', 'புகை', 'சிகரெட்',
            'போதை', 'போதைப்பொருள்', 'கஞ்சா', 'மருந்து துஷ்பிரயோகம்',
        ],
        'raw': ['மது', 'போதை', 'சிகரெட்', 'கஞ்சா'],
    },
    {
        'category':  'Discrimination / hatred',
        'icon':      '⚠',
        'severity':  'warning',
        'min_age':   11,
        'stems': [
            'இனவெறி', 'சாதி', 'தீண்டாமை', 'வெறுப்பு',
            'பாகுபாடு', 'அடிமை', 'ஒடுக்கு',
        ],
        'raw': ['தீண்டாமை', 'இனவெறி'],
    },
    {
        'category':  'Complex moral / ethical themes',
        'icon':      '○',
        'severity':  'info',
        'min_age':   10,
        'stems': [
            'நீதி', 'அநீதி', 'ஊழல்', 'பொய்', 'வஞ்சனை',
            'திருட்டு', 'தண்டனை', 'சிறை', 'குற்றம்', 'தப்பு',
            'பழிவாங்கு', 'நிர்பந்தம்',
        ],
        'raw': ['ஊழல்', 'சிறை', 'குற்றம்'],
    },
    {
        'category':  'Religious / spiritual content',
        'icon':      '○',
        'severity':  'info',
        'min_age':   5,
        'stems': [
            'கடவுள்', 'தெய்வம்', 'வழிபாடு', 'பூஜை', 'கோவில்',
            'மசூதி', 'தேவாலயம்', 'வேதம்', 'திருக்குர்ஆன்',
            'பிரார்த்தனை', 'யாகம்',
        ],
        'raw': ['கோவில்', 'மசூதி', 'தேவாலயம்'],
    },
]


def content_age_flags(text, stem_fn=None):
    """
    Scan text for content concern patterns.
    Returns a list of flagged categories with match counts and examples.
    Also returns an overall minimum recommended age based on the worst flag.
    """
    raw_words  = _tamil_words(text)
    if stem_fn:
        stemmed = [stem_fn(w) for w in raw_words]
        stemmed_set = set(stemmed)
        stemmed_list = stemmed
    else:
        stemmed_set  = set(raw_words)
        stemmed_list = raw_words

    raw_text_lower = text  # Tamil has no case

    flags = []
    max_min_age = 0

    for rule in CONTENT_FLAGS:
        matched_stems = []
        matched_raw   = []

        # Check stem list against stemmed words
        for kw in rule['stems']:
            if kw in stemmed_set:
                count = stemmed_list.count(kw)
                matched_stems.append({'word': kw, 'count': count})

        # Check raw list for exact surface matches
        for kw in rule['raw']:
            if kw in raw_text_lower:
                count = raw_text_lower.count(kw)
                matched_raw.append({'word': kw, 'count': count})

        all_matched = matched_stems + [
            m for m in matched_raw
            if not any(s['word'] == m['word'] for s in matched_stems)
        ]

        if all_matched:
            total_matches = sum(m['count'] for m in all_matched)
            # Sort by frequency, cap display at 5 examples
            examples = sorted(all_matched, key=lambda x: -x['count'])[:5]
            flags.append({
                'category':     rule['category'],
                'icon':         rule['icon'],
                'severity':     rule['severity'],
                'min_age':      rule['min_age'],
                'total_matches': total_matches,
                'examples':     examples,
            })
            max_min_age = max(max_min_age, rule['min_age'])

    # Overall content age recommendation
    if not flags:
        content_age_label = 'No content concerns detected'
        content_min_age   = 5
    elif max_min_age <= 7:
        content_age_label = 'Generally appropriate for all ages'
        content_min_age   = max_min_age
    elif max_min_age <= 10:
        content_age_label = f'Recommended age {max_min_age}+ (some mature themes)'
        content_min_age   = max_min_age
    elif max_min_age <= 13:
        content_age_label = f'Recommended age {max_min_age}+ (contains mature content)'
        content_min_age   = max_min_age
    else:
        content_age_label = f'Adults / older teens ({max_min_age}+)'
        content_min_age   = max_min_age

    return {
        'flags':             flags,
        'flag_count':        len(flags),
        'content_min_age':   content_min_age,
        'content_age_label': content_age_label,
        'warning_count':     sum(1 for f in flags if f['severity'] == 'warning'),
        'caution_count':     sum(1 for f in flags if f['severity'] == 'caution'),
        'info_count':        sum(1 for f in flags if f['severity'] == 'info'),
    }


# ── 7. Overall readability score ──────────────────────────────────────────────
#
# A composite 0–100 score that combines all dimensions into a single
# "reading difficulty" index. Higher = harder to read.
#
# Weights are calibrated heuristically against typical Tamil school book
# characteristics. The score is NOT a grade-level prediction — it's a
# relative difficulty indicator to compare books against each other.
#
# Component weights (must sum to 1.0):
#   vocabulary coverage   0.35  — most important predictor
#   sentence length       0.25  — well-established in literature
#   word length           0.15  — Tamil-specific morphological signal
#   lexical diversity     0.15  — vocabulary richness/repetition
#   paragraph complexity  0.10  — cognitive load

def overall_readability_score(
    comprehension_pct,   # float 0-100: comprehension at best-fit grade
    sent_avg,            # float: average sentence length in words
    sent_max_grade,      # int: max sentence length in best-fit grade book
    avg_word_chars,      # float: average Tamil word character length
    ttr,                 # float: type-token ratio 0-100
    avg_sents_per_para,  # float: average sentences per paragraph
):
    """
    Returns a difficulty score 0-100 and a grade-band label.
    0 = very easy (Std 1), 100 = very hard (university level).
    """
    # Vocabulary difficulty: 0 = all known, 100 = nothing known
    vocab_difficulty = max(0.0, min(100.0, 100.0 - comprehension_pct))

    # Sentence difficulty: compare target avg to grade max
    if sent_max_grade > 0:
        sent_ratio = min(sent_avg / sent_max_grade, 2.0)
    else:
        # Fallback heuristic: 5 words = easy, 25 words = hard
        sent_ratio = min(sent_avg / 15.0, 2.0)
    sent_difficulty = min(100.0, sent_ratio * 50.0)

    # Word length difficulty: 4 chars = easy, 12 chars = hard
    wl_difficulty = min(100.0, max(0.0, (avg_word_chars - 4) / 8 * 100))

    # Lexical diversity: high TTR = harder (more varied vocabulary)
    # TTR 10 = very repetitive (easy), TTR 70 = very diverse (hard)
    lex_difficulty = min(100.0, max(0.0, (ttr - 10) / 60 * 100))

    # Paragraph complexity: 1 sent/para = easy, 8 sents/para = hard
    para_difficulty = min(100.0, max(0.0, (avg_sents_per_para - 1) / 7 * 100))

    score = (
        vocab_difficulty   * 0.35 +
        sent_difficulty    * 0.25 +
        wl_difficulty      * 0.15 +
        lex_difficulty     * 0.15 +
        para_difficulty    * 0.10
    )
    score = round(score, 1)

    if score <= 20:   label = 'Very easy — Std 1–2'
    elif score <= 35: label = 'Easy — Std 3–4'
    elif score <= 50: label = 'Moderate — Std 5–6'
    elif score <= 65: label = 'Challenging — Std 7–9'
    elif score <= 80: label = 'Hard — Std 10–12'
    else:             label = 'Very hard — beyond Std 12'

    return {
        'score':       score,
        'label':       label,
        'components': {
            'vocabulary':   round(vocab_difficulty, 1),
            'sentence':     round(sent_difficulty, 1),
            'word_length':  round(wl_difficulty, 1),
            'diversity':    round(lex_difficulty, 1),
            'paragraph':    round(para_difficulty, 1),
        }
    }


# ── Combined: run all analytics on a text ────────────────────────────────────

def full_analytics(text, stem_fn=None, comprehension_pct=None,
                   sent_avg=None, sent_max_grade=None):
    """
    Run all analytics in one call. Returns a dict with keys:
      lexical, word_length, dialogue, paragraphs, repetition,
      content_flags, readability_score
    """
    words = _tamil_words(text)

    lex    = lexical_diversity(words, stem_fn)
    wl     = word_length_stats(words)
    dial   = dialogue_ratio(text)
    para   = paragraph_stats(text)
    rep    = repetition_score(words, stem_fn)
    flags  = content_age_flags(text, stem_fn)

    score = None
    if comprehension_pct is not None:
        score = overall_readability_score(
            comprehension_pct = comprehension_pct,
            sent_avg          = sent_avg or 0,
            sent_max_grade    = sent_max_grade or 0,
            avg_word_chars    = wl['avg_chars'],
            ttr               = lex['ttr'],
            avg_sents_per_para= para['avg_sents_per_para'],
        )

    return {
        'lexical':          lex,
        'word_length':      wl,
        'dialogue':         dial,
        'paragraphs':       para,
        'repetition':       rep,
        'content_flags':    flags,
        'readability_score': score,
    }
