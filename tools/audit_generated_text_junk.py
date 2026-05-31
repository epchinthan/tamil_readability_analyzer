#!/usr/bin/env python3
"""Audit generated Tamil text files for likely OCR junk tokens.

The report is deliberately conservative: it labels high-confidence junk and
review candidates, but does not edit text files. Run it after extracting
textbooks to produce a single JSON report.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Set

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tamil_readability_analyzer.app import (  # noqa: E402
    _is_malformed_tamil_token,
    _is_useful_tamil_ocr_token,
    get_stem,
)

TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")
GRANTHA_RE = re.compile(r"[ஜஷஸஹ]")


def iter_text_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.txt")):
        name = path.name
        if name.endswith(".raw_ocr.txt") or name.endswith(".qa.txt"):
            continue
        yield path


def load_corpus_words(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out: Set[str] = set()
    for key in ("surface", "global"):
        for row in data.get(key, []):
            word = row.get("word") or row.get("stem")
            if word:
                out.add(str(word))
    return out


def load_grade_words(db_path: Path) -> Set[str]:
    if not db_path.exists():
        return set()
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT word FROM grade_words").fetchall()
        stems = conn.execute("SELECT stem FROM word_grade_map").fetchall()
        conn.close()
    except Exception:
        return set()
    return {str(row[0]) for row in rows + stems if row and row[0]}


def token_features(word: str, known_words: Set[str]) -> List[str]:
    features: List[str] = []
    stem = get_stem(word)
    known = word in known_words or stem in known_words

    if not _is_useful_tamil_ocr_token(word):
        features.append("fails-current-filter")
    if _is_malformed_tamil_token(word):
        features.append("malformed-unicode-shape")

    pulli_count = word.count("\u0BCD")
    if pulli_count >= 2 and len(word) <= 10:
        features.append("dense-pulli-cluster")
    if re.search(r"(.)\1\1", word):
        features.append("triple-repeat")
    # Initial consonant clusters are valid in many loanwords (ஸ்டேஷன்,
    # க்ராம்). Keep them as a weak signal only when no Grantha/loanword
    # starter is involved.
    if re.search(r"^[கஙசஞடணதநபமயரலவழளறன]்[\u0B95-\u0BB9]", word):
        features.append("starts-with-bare-consonant-cluster")
    if len(word) >= 7:
        r_like = sum(word.count(ch) for ch in "ரற்")
        if r_like / max(len(word), 1) >= 0.35:
            features.append("r-heavy")
    if GRANTHA_RE.search(word) and not known:
        features.append("rare-grantha")
    if len(word) >= 8 and not known:
        features.append("rare-long")
    if re.search(r"[௰௱௲]", word):
        features.append("old-number-glyph")
    if re.search(r"[A-Za-z0-9]", word):
        features.append("latin-or-digit")
    return features


def classify(features: List[str], count: int, file_count: int, context_bad: int, known: bool) -> str:
    if not features:
        return "known_or_clean" if known else "likely_valid"
    hard = {
        "fails-current-filter",
        "malformed-unicode-shape",
        "old-number-glyph",
        "latin-or-digit",
    }
    if any(f in hard for f in features):
        return "likely_junk"
    if context_bad >= 3 and count <= 2 and file_count <= 2 and "triple-repeat" in features:
        return "likely_junk"
    if context_bad >= 5 and count <= 2 and file_count <= 2 and "starts-with-bare-consonant-cluster" in features:
        return "likely_junk"
    if known:
        return "likely_valid"
    return "needs_review"


def audit(root: Path, known_words: Set[str], limit: int) -> Dict:
    counts: Counter[str] = Counter()
    file_hits: Dict[str, Set[str]] = defaultdict(set)
    examples: Dict[str, Dict] = {}
    context_scores: Counter[str] = Counter()

    for path in iter_text_files(root):
        lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()]
        words = [line for line in lines if line and TAMIL_RE.search(line)]
        suspicious_flags = [bool(token_features(word, known_words)) for word in words]
        for index, word in enumerate(words):
            counts[word] += 1
            file_hits[word].add(str(path))
            if word not in examples:
                start = max(0, index - 3)
                end = min(len(words), index + 4)
                examples[word] = {
                    "file": str(path),
                    "line": index + 1,
                    "context": words[start:end],
                }
            context_scores[word] = max(
                context_scores[word],
                sum(1 for flag in suspicious_flags[max(0, index - 3): min(len(words), index + 4)] if flag),
            )

    buckets = {
        "likely_junk": [],
        "needs_review": [],
        "likely_valid": [],
    }

    for word, count in counts.items():
        stem = get_stem(word)
        known = word in known_words or stem in known_words
        features = token_features(word, known_words)
        label = classify(features, count, len(file_hits[word]), context_scores[word], known)
        if label == "known_or_clean":
            continue
        row = {
            "word": word,
            "stem": stem,
            "count": count,
            "file_count": len(file_hits[word]),
            "features": features,
            "context_suspicion": int(context_scores[word]),
            "known": known,
            "example": examples[word],
        }
        if label in buckets:
            buckets[label].append(row)

    for rows in buckets.values():
        rows.sort(key=lambda row: (-len(row["features"]), row["count"], row["word"]))
        del rows[limit:]

    return {
        "root": str(root),
        "files_scanned": sum(1 for _ in iter_text_files(root)),
        "unique_words": len(counts),
        "total_tokens": sum(counts.values()),
        "known_word_signals": len(known_words),
        "summary": {key: len(value) for key, value in buckets.items()},
        "buckets": buckets,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit generated Tamil text files for likely OCR junk.")
    parser.add_argument("--root", default="textbooks_imported_text", help="Directory of cleaned generated .txt files.")
    parser.add_argument("--out", default="data/generated_text_junk_audit.json", help="Single JSON report path.")
    parser.add_argument("--limit", type=int, default=500, help="Max rows per report bucket.")
    parser.add_argument("--corpus", default="data/tamil_corpus/word_frequency.json", help="Corpus frequency JSON.")
    parser.add_argument("--db", default="tamil_analyzer.db", help="Analyzer SQLite DB for grade vocabulary.")
    args = parser.parse_args()

    root = Path(args.root)
    known = load_corpus_words(Path(args.corpus)) | load_grade_words(Path(args.db))
    report = audit(root, known, max(1, int(args.limit)))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote {out}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
