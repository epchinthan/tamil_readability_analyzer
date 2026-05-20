#!/usr/bin/env python3
"""Import transcript text from parambharat/tamil_asr_corpus.

This intentionally downloads only JSONL metadata/transcripts, not the multi-GB
audio archives. The output can enrich general/spoken Tamil familiarity signals
or provide prompts for ASR smoke tests.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable

DATASET_ID = "parambharat/tamil_asr_corpus"
DEFAULT_OUT = Path("data/corpus_sources/tamil_asr_corpus_transcripts")
TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")


def tamil_char_ratio(text: str) -> float:
    chars = [ch for ch in text or "" if not ch.isspace()]
    if not chars:
        return 0.0
    return sum(1 for ch in chars if TAMIL_RE.match(ch)) / len(chars)


def metadata_url(split: str) -> str:
    return f"https://huggingface.co/datasets/{DATASET_ID}/resolve/main/data/{split}.jsonl"


def iter_rows(split: str) -> Iterable[Dict[str, Any]]:
    import requests

    with requests.get(metadata_url(split), stream=True, timeout=60) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            yield json.loads(line)


def write_outputs(rows: Iterable[Dict[str, Any]], out_dir: Path, chunk_size: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk = []
    manifest_rows = []
    chunk_idx = 1
    sentence_count = 0
    approx_words = 0
    duration_seconds = 0.0
    length_buckets: Counter[str] = Counter()

    def flush() -> None:
        nonlocal chunk, chunk_idx
        if not chunk:
            return
        path = out_dir / f"tamil_asr_transcripts_{chunk_idx:04d}.txt"
        path.write_text("\n".join(chunk) + "\n", encoding="utf-8")
        chunk = []
        chunk_idx += 1

    for row in rows:
        sentence = (row.get("sentence") or "").strip()
        if not sentence:
            continue
        length = float(row.get("length") or 0.0)
        chunk.append(sentence)
        manifest_rows.append({
            "path": row.get("path") or "",
            "sentence": sentence,
            "length": length,
        })
        sentence_count += 1
        approx_words += len(sentence.split())
        duration_seconds += length
        if length < 8:
            length_buckets["short_under_8s"] += 1
        elif length <= 16:
            length_buckets["medium_8_16s"] += 1
        else:
            length_buckets["long_over_16s"] += 1
        if len(chunk) >= chunk_size:
            flush()
    flush()

    manifest_path = out_dir / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as f:
        for row in manifest_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    meta = {
        "dataset": DATASET_ID,
        "dataset_license": "cc-by-4.0",
        "mode": "transcripts_only_no_audio",
        "sentences_imported": sentence_count,
        "approx_words": approx_words,
        "duration_hours": round(duration_seconds / 3600.0, 2),
        "chunk_files": chunk_idx - 1,
        "length_buckets": dict(length_buckets),
        "manifest": str(manifest_path),
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["test", "train"], default="test")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--min-chars", type=int, default=8)
    parser.add_argument("--min-tamil-ratio", type=float, default=0.70)
    args = parser.parse_args()

    selected = []
    for row in iter_rows(args.split):
        sentence = (row.get("sentence") or "").strip()
        if len(sentence) < args.min_chars:
            continue
        if tamil_char_ratio(sentence) < args.min_tamil_ratio:
            continue
        selected.append(row)
        if args.max_rows and len(selected) >= args.max_rows:
            break

    meta = write_outputs(selected, args.out, args.chunk_size)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
