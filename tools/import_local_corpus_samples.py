#!/usr/bin/env python3
"""Sample huge local Tamil corpus files into data/corpus_sources.

The project corpus/ directory may contain multi-GB files. This tool streams
them line by line, keeps Tamil-heavy rows, and writes bounded text chunks that
the existing TAVI builder can process safely.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")
DEFAULT_IN = Path("corpus")
DEFAULT_OUT = Path("data/corpus_sources/local_large_corpus")


def tamil_char_ratio(text: str) -> float:
    chars = [ch for ch in text or "" if not ch.isspace()]
    if not chars:
        return 0.0
    return sum(1 for ch in chars if TAMIL_RE.match(ch)) / len(chars)


def source_name(path: Path) -> str:
    name = path.stem.lower()
    if "dinamalar" in name:
        return "dinamalar"
    if "murasu" in name:
        return "tamilmurasu"
    if name == "ta":
        return "ta_text_corpus"
    if "article" in name:
        return "tamil_articles_corpus"
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_") or "local_corpus"


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


def iter_texts(path: Path):
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield text_from_csv_row(row)
    else:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                yield line.strip()


def sample_file(
    path: Path,
    out_root: Path,
    *,
    max_rows: int,
    min_chars: int,
    min_tamil_ratio: float,
    chunk_size: int,
) -> dict:
    src = source_name(path)
    out_dir = out_root / src
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk = []
    chunk_idx = 1
    rows_seen = 0
    rows_kept = 0
    approx_words = 0

    def flush() -> None:
        nonlocal chunk, chunk_idx
        if not chunk:
            return
        out = out_dir / f"{src}_{chunk_idx:04d}.txt"
        out.write_text("\n\n".join(chunk) + "\n", encoding="utf-8")
        chunk = []
        chunk_idx += 1

    for text in iter_texts(path):
        rows_seen += 1
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) < min_chars:
            continue
        if tamil_char_ratio(text) < min_tamil_ratio:
            continue
        chunk.append(text)
        rows_kept += 1
        approx_words += len(text.split())
        if len(chunk) >= chunk_size:
            flush()
        if max_rows and rows_kept >= max_rows:
            break
    flush()

    meta = {
        "source_file": str(path),
        "source": src,
        "rows_seen": rows_seen,
        "rows_kept": rows_kept,
        "approx_words": approx_words,
        "chunk_files": chunk_idx - 1,
        "max_rows": max_rows,
        "min_chars": min_chars,
        "min_tamil_ratio": min_tamil_ratio,
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_IN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-rows-per-file", type=int, default=5000)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--min-chars", type=int, default=80)
    parser.add_argument("--min-tamil-ratio", type=float, default=0.70)
    args = parser.parse_args()

    files = sorted(
        p for p in args.input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".txt", ".csv"}
    )
    summaries = [
        sample_file(
            path,
            args.out,
            max_rows=args.max_rows_per_file,
            min_chars=args.min_chars,
            min_tamil_ratio=args.min_tamil_ratio,
            chunk_size=args.chunk_size,
        )
        for path in files
    ]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metadata.json").write_text(
        json.dumps({"sources": summaries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"sources": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
