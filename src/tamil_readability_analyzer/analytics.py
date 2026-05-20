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

from . import indic_nlp_adapter as _indic_nlp


# ── Tamil text helpers ────────────────────────────────────────────────────────

def _tamil_words(text):
    """All Tamil Unicode tokens ≥ 2 characters."""
    return _indic_nlp.words(text)

def _paragraphs(text):
    """Split on blank lines; filter empty."""
    return [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]

def _sentences(text):
    """Split on Tamil / Latin sentence-ending punctuation."""
    return _indic_nlp.sentences(text)

# Dialogue markers in Tamil: opening/closing quote styles, உரையாடல் dash
_DIALOGUE_OPEN  = re.compile(r'["\u201C\u2018\u00AB]')
_DIALOGUE_CLOSE = re.compile(r'["\u201D\u2019\u00BB]')
_DIALOGUE_DASH  = re.compile(r'(?:^|\n)\s*[\u2013\u2014\-]\s+[\u0B80-\u0BFF]')

ABSTRACT_SUFFIXES = (
    'மை', 'த்துவம்', 'வியல்', 'வாதம்', 'நிலை', 'பாடு', 'முறை',
    'ஆட்சி', 'உரிமை', 'கொள்கை',
)
COMPOUND_HINTS = (
    'சுற்றுச்சூழல்', 'பாதுகாப்பு', 'பொருளாதாரம்', 'அறிவியல்',
    'கல்வி', 'வரலாறு', 'சமூகம்', 'நீர்வளம்', 'உயிரின',
)
COMMON_INFLECTED_ENDINGS = (
    'கிறது', 'கிறார்கள்', 'கிறார்', 'கிறாள்', 'கிறான்',
    'ப்பட்டது', 'ப்பட்டன', 'ப்பட்டார்', 'ப்பட்டார்கள்',
    'விட்டார்', 'விட்டார்கள்', 'விட்டது',
    'ுக்கு', 'ுக்கும்', 'ுடன்', 'ினால்', 'ிலிருந்து', 'ில்தான்',
    'த்தான்', 'ச்சு', 'ற்குள்',
)
SUBORDINATE_MARKERS = (
    'ஆனால்', 'எனவே', 'அதனால்', 'ஏனெனில்', 'என்றாலும்', 'போது',
    'பொழுது', 'முன்பு', 'பிறகு', 'வரை', 'என்பதால்', 'ஆகையால்',
)
COMPLEX_VERB_ENDINGS = (
    'க்கொண்டிருந்தார்கள்', 'க்கொண்டிருந்தான்', 'க்கொண்டிருந்தாள்',
    'இருக்கிறார்கள்', 'இருந்தார்கள்', 'கின்றனர்', 'ப்படுகின்ற',
    'ப்பட்டிருந்த', 'விட்டார்கள்', 'விட்டான்', 'விட்டாள்',
)
PROPER_NAME_SUFFIXES = (
    'நகர்', 'நகரம்', 'புரம்', 'பூர்', 'மலை', 'நாடு', 'மாவட்டம்',
    'மாநிலம்', 'குமார்', 'குமாரி', 'ராஜ்', 'ராஜா', 'தேவி',
    'முருகன்', 'ராமன்', 'கிருஷ்ணன்', 'பிள்ளை',
)
FOREIGN_NAME_MARKERS = ('ஸ', 'ஜ', 'ஹ', 'ஷ', 'க்ஷ', 'ஃ', 'க்ஸ்', 'ட்ர', 'ப்ர')
KNOWN_PROPER_WORDS = {
    'பிரான்ஸ்', 'பாரிஸ்', 'ஈஃபில்', 'டவர்', 'அலைஸ்', 'கீ',
    'லியோன்', 'கார்மண்', 'லூமியர்', 'ஜூலை', 'அமெரிக்கா',
    'கலிபோர்னியா', 'ஹாலிவுட்', 'நியூ', 'ஜெர்சி', 'சோலாக்ஸ்',
    'ஸ்டுடியோஸ்', 'குரோனோகிராம்', 'ப்ரொஜெக்டர்', 'ஸ்பேனிஷ்',
    'ஃப்ளூ', 'கொரோனா',
}

PROPER_NOUN_PHRASE_HINTS = (
    'நாடு', 'நகரம்', 'நகரில்', 'நகரத்தில்தான்', 'சகோதரர்கள்',
    'நிறுவனம்', 'நிறுவனத்தின்',
)
GENERIC_PLACE_WORDS = {'நாடு', 'நகரம்', 'நகரில்', 'நிறுவனம்', 'நிறுவனத்தின்'}

TECHNICAL_PHRASES = {
    'சினிமா இயக்குநர்': 5,
    'பெண் இயக்குநர்': 5,
    'தட்டச்சு இயந்திரம்': 5,
    'அலுவலக உதவியாளர்': 5,
    'புகைப்படம் எடுக்கும்': 5,
    'வீடியோ காட்சி': 4,
    'வீடியோ கேமரா': 4,
    'வீடியோ கேமராக்கள்': 4,
    'பதிவு செய்த காட்சி': 5,
    'புதிய கண்டுபிடிப்பு': 5,
    'காட்சியைப் பதிவு': 5,
    'திரையில் காட்ட': 4,
    'திரையரங்கம்': 4,
    'திரையரங்குகள்': 4,
    'பேசும் படம்': 5,
    'பேசும் படங்கள்': 5,
    'கருப்பு-வெள்ளைத் திரைப்படங்கள்': 5,
    'பிலிம் ரோல்': 5,
    'ஃபிரேம்': 5,
    'ப்ரொஜெக்டர்': 5,
    'குரோனோகிராம்': 6,
    'ஆடியோ': 4,
    'ஸ்டுடியோ': 4,
    'ஸ்டுடியோஸ்': 4,
    'திரைப்படத் தயாரிப்பு': 6,
    'தொழில்நுட்ப நுணுக்கங்கள்': 6,
    'கேமராக்கள்': 4,
    'கணினி': 4,
    'அலைபேசி': 4,
}

DATE_TIME_PATTERNS = (
    r'\d{3,4}\s*-\s*ஆம்\s+ஆண்டு',
    r'\d{1,2}\s*-\s*ஆம்\s+தேதி',
    r'\d+\s+வயது',
    r'(?<![\u0B80-\u0BFF])(?:ஜனவரி|பிப்ரவரி|மார்ச்|ஏப்ரல்|மே|ஜூன்|ஜூலை|ஆகஸ்ட்|செப்டம்பர்|அக்டோபர்|நவம்பர்|டிசம்பர்)(?![\u0B80-\u0BFF])',
)

TOPIC_KEYWORDS = {
    'family_home': {
        'label': 'Family / home / daily life',
        'level': 'early',
        'words': {'அம்மா', 'அப்பா', 'வீடு', 'குடும்பம்', 'தம்பி', 'அக்கா', 'பாட்டி', 'தாத்தா', 'நண்பன்', 'பள்ளி'},
    },
    'animals_nature': {
        'label': 'Animals / nature',
        'level': 'early',
        'words': {'பூனை', 'நாய்', 'மாடு', 'பறவை', 'மரம்', 'மழை', 'பூ', 'காடு', 'கடல்', 'மலை'},
    },
    'science_environment': {
        'label': 'Science / environment',
        'level': 'middle',
        'words': {'அறிவியல்', 'சுற்றுச்சூழல்', 'உயிரினங்கள்', 'மாசுபாடு', 'ஆற்றல்', 'வளிமண்டலம்', 'பரிசோதனை', 'நீர்வளம்'},
    },
    'history_civics': {
        'label': 'History / civics',
        'level': 'middle',
        'words': {'வரலாறு', 'அரசு', 'சுதந்திரம்', 'ஜனநாயகம்', 'உரிமை', 'கடமை', 'மன்னர்', 'சமூகம்', 'பேரரசு'},
    },
    'ethics_society': {
        'label': 'Ethics / society',
        'level': 'higher',
        'words': {'பொறுப்பு', 'நீதி', 'அநீதி', 'சமத்துவம்', 'பாகுபாடு', 'பொருளாதாரம்', 'கொள்கை', 'மனிதநேயம்'},
    },
    'biography': {
        'label': 'Biography / life story',
        'level': 'middle',
        'words': {'பிறந்தது', 'வயது', 'வாழ்க்கை', 'அப்பா', 'அம்மா', 'வேலை', 'குடும்பம்', 'பணியாக', 'இறுதியாக'},
    },
    'technology_invention': {
        'label': 'Technology / invention',
        'level': 'middle',
        'words': {'இயந்திரம்', 'தட்டச்சு', 'கேமரா', 'கேமராக்கள்', 'வீடியோ', 'பதிவு', 'திரை', 'கண்டுபிடிப்பு', 'கணினி', 'அலைபேசி'},
    },
    'cinema_media': {
        'label': 'Cinema / visual media',
        'level': 'middle',
        'words': {'காட்சி', 'வீடியோ', 'திரை', 'கேமரா', 'படமெடுக்க', 'படமெடுத்த', 'பதிவு', 'ஒளி'},
    },
    'work_profession': {
        'label': 'Work / profession',
        'level': 'middle',
        'words': {'வேலை', 'அலுவலகம்', 'அலுவலகத்தில்', 'உதவியாளர்', 'முதலாளி', 'நிறுவனம்', 'நிறுவனத்தின்', 'கடிதங்களை'},
    },
}


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

    char_lens = [len(w) for w in words]
    syl_lens  = [_indic_nlp.syllable_count(w) for w in words]

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
        'syllable_source': 'indic-nlp-library' if _indic_nlp.available() else 'regex-fallback',
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

    # Method a: extract text inside quote pairs, including smart Tamil/Unicode quotes.
    quoted_words = 0
    for pattern in [
        r'"([^"]*)"', r"'([^']*)'", r'«([^»]*)»',
        r'“([^”]*)”', r'‘([^’]*)’',
    ]:
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

    if top50_pct >= 80:   label = 'Highly repetitive — early-reader pattern'
    elif top50_pct >= 65: label = 'Repetitive — controlled vocabulary pattern'
    elif top50_pct >= 50: label = 'Moderate variety'
    else:                 label = 'Broad vocabulary variety'

    # Top 20 most frequent words for display
    top20 = [{'stem': s, 'count': c, 'pct': round(c/total*100, 1)}
             for s, c in freq.most_common(20)]

    return {
        'top50_pct': top50_pct,
        'top10_pct': top10_pct,
        'label':     label,
        'top20':     top20,
    }


# ── 6. Children-book class-level feature signals ─────────────────────────────

def _pct(part, total):
    return round(part / total * 100, 1) if total else 0.0


def _unique_preserve_order(items, limit=20):
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _phrase_hits(text, phrase_levels):
    hits = []
    for phrase, level in phrase_levels.items():
        count = (text or '').count(phrase)
        if count:
            hits.append({'phrase': phrase, 'level': level, 'count': count})
    return sorted(hits, key=lambda x: (-x['count'], -x['level'], x['phrase']))


def _date_time_signals(text):
    hits = []
    for pattern in DATE_TIME_PATTERNS:
        hits.extend(re.findall(pattern, text or ''))
    numerals = re.findall(r'\d+', text or '')
    return {
        'date_time_count': len(hits),
        'number_count': len(numerals),
        'examples': _unique_preserve_order(hits + numerals, 12),
    }


def _looks_foreign_or_name(word):
    return (
        word in KNOWN_PROPER_WORDS
        or any(m in word for m in FOREIGN_NAME_MARKERS)
        or any(word.endswith(s) for s in PROPER_NAME_SUFFIXES)
    )


def _proper_noun_phrases(text, raw_words, limit=30):
    phrases = []
    words = list(raw_words)
    for i, word in enumerate(words):
        prev_w = words[i - 1] if i else ''
        next_w = words[i + 1] if i + 1 < len(words) else ''
        next2_w = words[i + 2] if i + 2 < len(words) else ''

        if _looks_foreign_or_name(word):
            if next_w and (next_w in PROPER_NOUN_PHRASE_HINTS or _looks_foreign_or_name(next_w)):
                phrases.append(f'{word} {next_w}')
            else:
                phrases.append(word)

        if word in {'என்கிற', 'என்பவர்', 'என்பவரின்'} and prev_w:
            phrase = prev_w
            if i >= 2 and _looks_foreign_or_name(words[i - 2]):
                phrase = f'{words[i - 2]} {prev_w}'
            phrases.append(phrase)

        if _looks_foreign_or_name(word) and next_w in {'நாடு', 'நகரம்', 'நகரில்', 'நகரத்தில்தான்'}:
            phrases.append(f'{word} {next_w}')
        if next_w and next2_w in {'என்பவர்', 'என்பவரின்'}:
            phrases.append(f'{word} {next_w}')

    # Catch known multi-word foreign/object names that are common in children nonfiction.
    known_patterns = (
        r'ஈஃபில்\s+டவர்',
        r'அலைஸ்\s+கீ',
        r'லியோன்\s+கார்மண்',
        r'லூமியர்\s+சகோதரர்கள்',
    )
    for pattern in known_patterns:
        phrases.extend(re.findall(pattern, text or ''))

    filtered = _unique_preserve_order([
        p for p in phrases
        if p and len(p) >= 2 and p not in GENERIC_PLACE_WORDS and not p.endswith(' என்கிற')
    ], limit)
    multi_tokens = set()
    for phrase in filtered:
        parts = _tamil_words(phrase)
        if len(parts) > 1:
            multi_tokens.update(parts)
    cleaned = [
        phrase for phrase in filtered
        if len(_tamil_words(phrase)) > 1 or phrase not in multi_tokens
    ]
    collapsed = []
    noise = {'கூட்டம்', 'முட்டைக்கோஸ்', 'ரோஜாப்', 'ரோஜாப்பூ', 'கார்மண் அலைஸைப்'}
    for phrase in cleaned:
        if phrase in noise:
            continue
        if phrase.startswith('அலைஸ') and len(_tamil_words(phrase)) == 1:
            phrase = 'அலைஸ்'
        if phrase.startswith('கார்மண') and len(_tamil_words(phrase)) == 1:
            phrase = 'கார்மண்'
        if phrase == 'அலைஸ் பிரான்ஸ்':
            continue
        if phrase not in collapsed:
            collapsed.append(phrase)
    return collapsed[:limit]


def child_level_features(text, stem_fn=None):
    """
    Signals useful for estimating a Tamil children's book standard/class level.

    This is an explainable feature bundle, not a final verdict. The main
    analyzer still uses grade vocabulary and corpus scores for the final class
    recommendation.
    """
    raw_words = _tamil_words(text)
    if not raw_words:
        return {
            'enabled': False,
            'message': 'No Tamil text found.',
        }

    stems = [stem_fn(w) for w in raw_words] if stem_fn else list(raw_words)
    sentences = [s for s in _sentences(text) if _tamil_words(s)]
    sentence_lengths = [len(_tamil_words(s)) for s in sentences]
    paragraphs = _paragraphs(text)
    lines = [ln.strip() for ln in (text or '').splitlines() if ln.strip()]
    total = len(raw_words)
    proper_phrases = _proper_noun_phrases(text, raw_words)
    proper_phrase_tokens = set()
    for phrase in proper_phrases:
        proper_phrase_tokens.update(_tamil_words(phrase))
    technical_hits = _phrase_hits(text, TECHNICAL_PHRASES)
    date_time = _date_time_signals(text)

    # Vocabulary pacing: high new-word rate usually means higher class load.
    first_seen = set()
    running_new = []
    for i in range(0, total, 100):
        chunk = stems[i:i + 100]
        new_count = sum(1 for s in chunk if s not in first_seen)
        first_seen.update(chunk)
        running_new.append(new_count)
    new_word_rate = round(sum(running_new) / len(running_new), 1) if running_new else 0.0
    unique_pct = _pct(len(set(stems)), total)

    abstract_words = [
        w for w in raw_words
        if w.endswith(ABSTRACT_SUFFIXES) or any(h in w for h in COMPOUND_HINTS)
    ]
    long_surface_words = [w for w in raw_words if len(w) >= 10]
    long_compounds = [
        w for w in raw_words
        if (len(w) >= 10 or any(h in w for h in COMPOUND_HINTS))
        and w not in proper_phrase_tokens
        and not w.endswith(COMMON_INFLECTED_ENDINGS)
    ]
    proper_like = [
        w for w in raw_words
        if any(w.endswith(s) for s in PROPER_NAME_SUFFIXES)
        or any(m in w for m in FOREIGN_NAME_MARKERS)
        or w in proper_phrase_tokens
    ]
    complex_verbs = [w for w in raw_words if any(w.endswith(e) for e in COMPLEX_VERB_ENDINGS)]
    subordinate_sentences = [
        s for s in sentences
        if any(marker in s for marker in SUBORDINATE_MARKERS) or len(_tamil_words(s)) >= 14
    ]

    punct_count = len(re.findall(r'[,;:()\[\]"\u201C\u201D\u2018\u2019\u00AB\u00BB]', text or ''))
    punctuation_per_100 = round(punct_count / total * 100, 1)
    avg_line_words = round(sum(len(_tamil_words(ln)) for ln in lines) / len(lines), 1) if lines else 0.0
    short_line_pct = _pct(sum(1 for ln in lines if 1 <= len(_tamil_words(ln)) <= 6), len(lines))
    avg_sentence = round(sum(sentence_lengths) / len(sentence_lengths), 1) if sentence_lengths else 0.0
    p90_sentence = 0
    if sentence_lengths:
        p90_sentence = sorted(sentence_lengths)[min(len(sentence_lengths) - 1, round(0.9 * (len(sentence_lengths) - 1)))]

    topic_hits = []
    stem_set = set(stems)
    raw_set = set(raw_words)
    for key, topic in TOPIC_KEYWORDS.items():
        hits = sorted((topic['words'] & raw_set) | (topic['words'] & stem_set))
        if hits:
            topic_hits.append({
                'key': key,
                'label': topic['label'],
                'level': topic['level'],
                'count': sum(1 for w in raw_words if w in topic['words']),
                'examples': hits[:10],
            })
    topic_hits.sort(key=lambda x: x['count'], reverse=True)
    advanced_topic_count = sum(1 for t in topic_hits if t['level'] in {'middle', 'higher'})

    signals = {
        'unique_word_pct': unique_pct,
        'new_words_per_100': new_word_rate,
        'abstract_concept_pct': _pct(len(abstract_words), total),
        'compound_word_pct': _pct(len(long_compounds), total),
        'long_surface_word_pct': _pct(len(long_surface_words), total),
        'complex_verb_pct': _pct(len(complex_verbs), total),
        'subordinate_sentence_pct': _pct(len(subordinate_sentences), len(sentences)),
        'proper_noun_pct': _pct(len(proper_like), total),
        'proper_noun_phrase_count': len(proper_phrases),
        'technical_phrase_count': sum(h['count'] for h in technical_hits),
        'date_time_count': date_time['date_time_count'],
        'number_count': date_time['number_count'],
        'punctuation_per_100_words': punctuation_per_100,
        'avg_sentence_words': avg_sentence,
        'p90_sentence_words': p90_sentence,
        'avg_line_words': avg_line_words,
        'short_line_pct': short_line_pct,
        'topic_count': len(topic_hits),
        'advanced_topic_count': advanced_topic_count,
    }

    # A compact heuristic class estimate from the feature bundle.
    difficulty = 0.0
    difficulty += max(0, avg_sentence - 5) * 3.0
    difficulty += min(25, signals['abstract_concept_pct'] * 1.6)
    difficulty += min(20, signals['compound_word_pct'] * 1.2)
    difficulty += min(15, signals['subordinate_sentence_pct'] * 0.35)
    difficulty += min(12, signals['complex_verb_pct'] * 1.5)
    difficulty += min(10, max(0, new_word_rate - 35) * 0.35)
    difficulty += min(8, punctuation_per_100 * 0.8)
    difficulty += min(10, advanced_topic_count * 2)
    difficulty += min(6, signals['technical_phrase_count'] * 0.8)
    difficulty += min(4, signals['date_time_count'] * 1.0)
    difficulty += min(2, signals['proper_noun_phrase_count'] * 0.15)
    difficulty -= min(14, max(0, short_line_pct - 45) * 0.25)
    # Names raise orientation load, but should not be treated like ordinary vocabulary.
    difficulty -= min(5, signals['proper_noun_pct'] * 0.25)
    difficulty = max(0.0, min(100.0, round(difficulty, 1)))

    if difficulty <= 18:
        estimated_band = 'Std 1-2'
        center = 2
    elif difficulty <= 32:
        estimated_band = 'Std 3-4'
        center = 4
    elif difficulty <= 48:
        estimated_band = 'Std 5-6'
        center = 6
    elif difficulty <= 66:
        estimated_band = 'Std 7-9'
        center = 8
    else:
        estimated_band = 'Std 10-12'
        center = 10

    reasons = []
    if avg_sentence >= 12:
        reasons.append(f'Average sentence length is {avg_sentence} words.')
    else:
        reasons.append(f'Average sentence length is {avg_sentence} words, which is manageable.')
    if signals['abstract_concept_pct'] >= 6:
        reasons.append(f'Abstract/concept vocabulary is {signals["abstract_concept_pct"]}%.')
    if signals['compound_word_pct'] >= 12:
        reasons.append(f'True long/compound Tamil words are {signals["compound_word_pct"]}% of tokens.')
    if signals['subordinate_sentence_pct'] >= 20:
        reasons.append(f'{signals["subordinate_sentence_pct"]}% of sentences have subordinate/long patterns.')
    if technical_hits:
        reasons.append('Technical phrase clues: ' + ', '.join(h['phrase'] for h in technical_hits[:3]) + '.')
    if proper_phrases:
        reasons.append(f'{len(proper_phrases)} possible name/place phrases need orientation support.')
    if date_time['date_time_count']:
        reasons.append(f'{date_time["date_time_count"]} date/time references add biography or history load.')
    if short_line_pct >= 55:
        reasons.append('Many short lines suggest early-reader or poem-like layout.')
    if topic_hits:
        reasons.append('Main topic clues: ' + ', '.join(t['label'] for t in topic_hits[:3]) + '.')

    return {
        'enabled': True,
        'feature_name': 'Children-book class-level signals',
        'estimated_band': estimated_band,
        'estimated_standard': center,
        'difficulty_score': difficulty,
        'signals': signals,
        'topics': topic_hits[:8],
        'technical_phrases': technical_hits[:12],
        'date_time': date_time,
        'proper_noun_phrases': proper_phrases,
        'examples': {
            'abstract_concepts': _unique_preserve_order(abstract_words, 18),
            'compound_words': _unique_preserve_order(long_compounds, 18),
            'long_surface_words': _unique_preserve_order(long_surface_words, 18),
            'complex_verbs': _unique_preserve_order(complex_verbs, 18),
            'proper_nouns': _unique_preserve_order(proper_like, 18),
        },
        'reasons': reasons,
        'note': 'Use this with grade vocabulary coverage, TAVI, and textbook sentence norms. It is an explainable support signal, not a teacher-labelled ground truth.',
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
            'இறந்த', 'இறந்து', 'மரண', 'மரணம்', 'இறப்பு', 'மடிந்த',
            'சாவு', 'இறந்துவிட்ட', 'துக்கம்', 'அழுகை', 'புதைக்க',
            'சவம்', 'ஆவி', 'பேய்', 'சோகம்', 'சோக',
        ],
        'raw': [
            'மரணம்', 'இறந்தார்', 'இறந்துவிட்ட', 'சாவு', 'துக்கம்',
            'சோக', 'உடல்நிலை சரியில்ல', 'சரியில்லாமல்',
        ],
    },
    {
        'category':  'Fear / horror',
        'icon':      '○',
        'severity':  'caution',
        'min_age':   8,
        'stems': [
            'பயம்', 'பயந்த', 'திகில்', 'அச்சம்', 'நடுங்கு',
            'பேய்', 'பூதம்', 'அரக்கன்', 'பிசாசு', 'கோரம்',
            'இரவு பயம்',
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
            'மயக்கம்',
        ],
        'raw': ['காதல்', 'முத்தம்', 'காமம்'],
    },
    {
        'category':  'Substance / addiction',
        'icon':      '⚠',
        'severity':  'warning',
        'min_age':   12,
        'stems': [
            'மது', 'மதுபானம்', 'சிகரெட்',
            'போதைப்பொருள்', 'கஞ்சா', 'மருந்து துஷ்பிரயோகம்',
        ],
        'raw': ['மது', 'மதுபானம்', 'போதைப்பொருள்', 'சிகரெட்', 'கஞ்சா'],
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
      child_level_features, content_flags, readability_score
    """
    words = _tamil_words(text)

    lex    = lexical_diversity(words, stem_fn)
    wl     = word_length_stats(words)
    dial   = dialogue_ratio(text)
    para   = paragraph_stats(text)
    rep    = repetition_score(words, stem_fn)
    child  = child_level_features(text, stem_fn)
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
        'child_level_features': child,
        'content_flags':    flags,
        'readability_score': score,
    }
