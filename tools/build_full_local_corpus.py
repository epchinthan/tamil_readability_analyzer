#!/usr/bin/env python3
"""Build TAVI corpus files from all local corpus/ data by streaming.

This is for very large local corpora. It avoids copying corpus/ into
data/corpus_sources and avoids reading multi-GB files into memory.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from tamil_readability_analyzer.app import get_stem  # noqa: E402

TAMIL_WORD_RE = re.compile(r"[\u0B80-\u0BFF]{2,}")
SENTENCE_RE = re.compile(r"[.!?।\u0964\u0965\n]+")
OUT_DIR = ROOT / "data" / "tamil_corpus"
CORPUS_DIR = ROOT / "corpus"
VOCAB_DB = OUT_DIR / "vocabulary_index.db"


def tamil_words(text: str) -> list[str]:
    return TAMIL_WORD_RE.findall(text or "")


def tamil_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.split(text or "") if tamil_words(s)]


def infer_grade(path: Path) -> int | None:
    text = str(path)
    m = re.search(r"(?:class|grade|std|standard)[_\-\s]?0?(\d{1,2})", text, re.I)
    if not m:
        return None
    grade = int(m.group(1))
    return grade if 1 <= grade <= 12 else None


def band_for_grade(grade: int | None) -> str:
    if grade is None:
        return "ungraded"
    if grade <= 3:
        return "school_grade_1_3"
    if grade <= 5:
        return "school_grade_4_5"
    if grade <= 8:
        return "school_grade_6_8"
    if grade <= 10:
        return "school_grade_9_10"
    return "school_grade_11_12"


def source_name(path: Path) -> str:
    p = str(path).lower()
    name = path.stem.lower()
    if "textbooks_imported_text" in p or "samacheer" in p or "kalvi" in p:
        return "school_textbook"
    if "mozhi_ai_tamil_corpus" in p:
        return "mozhi_ai_tamil_corpus"
    if "tamil_asr_corpus_transcripts" in p:
        return "tamil_asr_corpus_transcripts"
    if "dinamalar" in name:
        return "dinamalar_full"
    if "murasu" in name:
        return "tamilmurasu_full"
    if name == "ta":
        return "ta_text_corpus_full"
    if "article" in name:
        return "tamil_articles_corpus_full"
    return "local_corpus_full"


def iter_existing_text_files(root: Path):
    bases = [
        root / "textbooks_imported_text",
        root / "data" / "corpus_sources",
        root / "data" / "wiki_text",
    ]
    for base in bases:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.txt")):
            if "local_large_corpus" in str(path):
                continue
            yield path


def text_from_csv_row(row: dict[str, str]) -> str:
    preferred = [
        "news_title", "news_article", "title", "article", "text",
        "content", "body", "sentence",
    ]
    parts = []
    for key in preferred:
        value = (row.get(key) or "").strip()
        if value:
            parts.append(value)
    if parts:
        return "\n".join(parts)
    return "\n".join(v for v in row.values() if isinstance(v, str) and v.strip())


def iter_corpus_rows(path: Path):
    if path.suffix.lower() == ".csv":
        csv.field_size_limit(sys.maxsize)
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield text_from_csv_row(row)
    else:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                yield line.strip()


def update_from_text(
    text: str,
    *,
    source: str,
    band: str,
    global_freq: Counter,
    global_surface: Counter,
    source_freq: dict[str, Counter],
    band_freq: dict[str, Counter],
    band_surface: dict[str, Counter],
    sentence_lengths: dict[str, list[int]],
    collect_sentence_lengths: bool,
) -> int:
    words = tamil_words(text)
    if not words:
        return 0
    stems = []
    for word in words:
        stem = get_stem(word)
        if stem:
            stems.append(stem)
    global_freq.update(stems)
    global_surface.update(words)
    source_freq[source].update(stems)
    band_freq[band].update(stems)
    band_surface[band].update(words)
    if collect_sentence_lengths:
        sentence_lengths[band].extend(len(tamil_words(s)) for s in tamil_sentences(text))
    return len(stems)


def familiarity_level(rank: int, count: int) -> str:
    if rank <= 2000:
        return "core_common_tamil"
    if rank <= 10000:
        return "general_common_tamil"
    if rank <= 50000:
        return "general_familiar_tamil"
    if count >= 20:
        return "general_uncommon_tamil"
    if count >= 2:
        return "rare_tamil"
    return "very_rare_or_noisy"


def write_vocabulary_db(
    *,
    global_freq: Counter,
    source_freq: dict[str, Counter],
    stats: dict,
) -> None:
    if VOCAB_DB.exists():
        VOCAB_DB.unlink()
    conn = sqlite3.connect(str(VOCAB_DB))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.executescript("""
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE stem_frequency (
            stem TEXT PRIMARY KEY,
            total_count INTEGER NOT NULL,
            frequency_rank INTEGER NOT NULL,
            general_level TEXT NOT NULL
        );
        CREATE TABLE stem_source_frequency (
            stem TEXT NOT NULL,
            source TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (stem, source)
        );
        CREATE INDEX idx_stem_frequency_rank ON stem_frequency(frequency_rank);
        CREATE INDEX idx_stem_frequency_count ON stem_frequency(total_count DESC);
        CREATE INDEX idx_stem_frequency_level ON stem_frequency(general_level);
        CREATE INDEX idx_stem_source_source_count ON stem_source_frequency(source, count DESC);
    """)
    conn.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        [
            ("builder", "tools/build_full_local_corpus.py"),
            ("tokens", str(stats["tokens"])),
            ("unique_stems", str(stats["unique_stems"])),
            ("sources", json.dumps(stats["sources"], ensure_ascii=False)),
            ("elapsed_seconds", str(stats["elapsed_seconds"])),
        ],
    )

    ranked = global_freq.most_common()
    batch = []
    for rank, (stem, count) in enumerate(ranked, start=1):
        batch.append((stem, int(count), rank, familiarity_level(rank, int(count))))
        if len(batch) >= 100000:
            conn.executemany(
                "INSERT INTO stem_frequency(stem, total_count, frequency_rank, general_level) VALUES (?, ?, ?, ?)",
                batch,
            )
            batch = []
    if batch:
        conn.executemany(
            "INSERT INTO stem_frequency(stem, total_count, frequency_rank, general_level) VALUES (?, ?, ?, ?)",
            batch,
        )

    batch = []
    for source, freq in source_freq.items():
        for stem, count in freq.items():
            batch.append((stem, source, int(count)))
            if len(batch) >= 100000:
                conn.executemany(
                    "INSERT INTO stem_source_frequency(stem, source, count) VALUES (?, ?, ?)",
                    batch,
                )
                batch = []
    if batch:
        conn.executemany(
            "INSERT INTO stem_source_frequency(stem, source, count) VALUES (?, ?, ?)",
            batch,
        )
    conn.commit()
    conn.execute("VACUUM")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--progress-every", type=int, default=100000)
    parser.add_argument("--sentence-lengths-for-full", action="store_true")
    parser.add_argument("--no-sqlite", action="store_true")
    args = parser.parse_args()

    started = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    global_freq: Counter[str] = Counter()
    global_surface: Counter[str] = Counter()
    source_freq: dict[str, Counter[str]] = defaultdict(Counter)
    band_freq: dict[str, Counter[str]] = defaultdict(Counter)
    band_surface: dict[str, Counter[str]] = defaultdict(Counter)
    sentence_lengths: dict[str, list[int]] = defaultdict(list)
    source_counts: Counter[str] = Counter()
    file_rows = []

    for path in iter_existing_text_files(ROOT):
        text = path.read_text(encoding="utf-8", errors="ignore")
        grade = infer_grade(path)
        band = band_for_grade(grade)
        source = source_name(path)
        tokens = update_from_text(
            text,
            source=source,
            band=band,
            global_freq=global_freq,
            global_surface=global_surface,
            source_freq=source_freq,
            band_freq=band_freq,
            band_surface=band_surface,
            sentence_lengths=sentence_lengths,
            collect_sentence_lengths=True,
        )
        if tokens:
            source_counts[source] += 1
            file_rows.append({
                "path": str(path.relative_to(ROOT)),
                "source": source,
                "grade": grade,
                "band": band,
                "words": tokens,
                "unique_stems": len(set(get_stem(w) for w in tamil_words(text))),
                "sentences": len(tamil_sentences(text)),
            })

    corpus_files = sorted(
        p for p in CORPUS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".txt", ".csv"}
    )
    for path in corpus_files:
        source = source_name(path)
        band = "ungraded"
        rows_seen = 0
        tokens = 0
        before_unique = len(global_freq)
        source_started = time.time()
        for text in iter_corpus_rows(path):
            rows_seen += 1
            tokens += update_from_text(
                text,
                source=source,
                band=band,
                global_freq=global_freq,
                global_surface=global_surface,
                source_freq=source_freq,
                band_freq=band_freq,
                band_surface=band_surface,
                sentence_lengths=sentence_lengths,
                collect_sentence_lengths=args.sentence_lengths_for_full,
            )
            if args.progress_every and rows_seen % args.progress_every == 0:
                elapsed = time.time() - source_started
                print(
                    f"{source}: rows={rows_seen:,} tokens={tokens:,} "
                    f"unique+={len(global_freq) - before_unique:,} elapsed={elapsed:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
        source_counts[source] += 1
        file_rows.append({
            "path": str(path.relative_to(ROOT)),
            "source": source,
            "grade": None,
            "band": band,
            "words": tokens,
            "unique_stems": len(global_freq) - before_unique,
            "sentences": len(sentence_lengths[band]) if args.sentence_lengths_for_full else 0,
            "rows_seen": rows_seen,
            "streamed_full_file": True,
        })
        print(
            f"finished {source}: rows={rows_seen:,} tokens={tokens:,} "
            f"unique+={len(global_freq) - before_unique:,}",
            file=sys.stderr,
            flush=True,
        )

    grade_vocabulary = {
        band: [{"stem": stem, "count": count} for stem, count in freq.most_common(25000)]
        for band, freq in band_freq.items()
    }
    word_frequency = {
        "global": [{"stem": stem, "count": count} for stem, count in global_freq.most_common(50000)],
        "surface": [{"word": word, "count": count} for word, count in global_surface.most_common(50000)],
    }

    band_stats = {}
    for band, freq in band_freq.items():
        lengths = sentence_lengths[band]
        band_stats[band] = {
            "documents": sum(1 for f in file_rows if f["band"] == band),
            "tokens": sum(freq.values()),
            "unique_stems": len(freq),
            "avg_sentence_words": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "max_sentence_words": max(lengths) if lengths else 0,
            "top_words": [
                {"word": word, "count": count}
                for word, count in band_surface[band].most_common(40)
            ],
        }

    stats = {
        "built": True,
        "builder": "tools/build_full_local_corpus.py",
        "documents": len(file_rows),
        "tokens": sum(global_freq.values()),
        "unique_stems": len(global_freq),
        "sources": dict(source_counts),
        "bands": band_stats,
        "files": file_rows[:1000],
        "elapsed_seconds": round(time.time() - started, 1),
    }

    (OUT_DIR / "corpus_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "word_frequency.json").write_text(json.dumps(word_frequency, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "grade_vocabulary.json").write_text(json.dumps(grade_vocabulary, ensure_ascii=False), encoding="utf-8")
    if not args.no_sqlite:
        write_vocabulary_db(global_freq=global_freq, source_freq=source_freq, stats=stats)
    print(json.dumps({
        "tokens": stats["tokens"],
        "unique_stems": stats["unique_stems"],
        "vocabulary_db": str(VOCAB_DB.relative_to(ROOT)) if not args.no_sqlite else None,
        "sources": stats["sources"],
        "elapsed_seconds": stats["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
