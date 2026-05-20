#!/usr/bin/env python3
"""Import a filtered slice of mozhi-ai/tamil-corpus for local TAVI building.

The importer writes plain .txt chunks under data/corpus_sources so the existing
corpus builder can consume them without knowing anything about Hugging Face.
It also writes a small metadata JSON file for audit/licensing review.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable

DATASET_ID = "mozhi-ai/tamil-corpus"
DEFAULT_OUT = Path("data/corpus_sources/mozhi_ai_tamil_corpus")
TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")


def tamil_char_ratio(text: str) -> float:
    chars = [ch for ch in text or "" if not ch.isspace()]
    if not chars:
        return 0.0
    tamil = sum(1 for ch in chars if TAMIL_RE.match(ch))
    return tamil / len(chars)


def good_row(
    row: Dict[str, Any],
    *,
    min_quality: float,
    min_language: float,
    min_chars: int,
    min_tamil_ratio: float,
    allowed_registers: set[str] | None,
    allowed_domains: set[str] | None,
) -> bool:
    text = (row.get("text") or "").strip()
    if len(text) < min_chars:
        return False
    if float(row.get("quality_score") or 0.0) < min_quality:
        return False
    if float(row.get("language_score") or 0.0) < min_language:
        return False
    if tamil_char_ratio(text) < min_tamil_ratio:
        return False
    if allowed_registers and (row.get("register") or "").strip() not in allowed_registers:
        return False
    if allowed_domains and (row.get("domain") or "").strip() not in allowed_domains:
        return False
    return True


def iter_dataset(streaming: bool) -> Iterable[Dict[str, Any]]:
    try:
        from datasets import load_dataset
    except Exception as exc:
        raise SystemExit(
            "Missing optional dependency. Install with: "
            "python -m pip install -r requirements-huggingface.txt"
        ) from exc

    ds = load_dataset(DATASET_ID, split="train", streaming=streaming)
    return ds


def write_chunks(rows: Iterable[Dict[str, Any]], out_dir: Path, chunk_size: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk = []
    chunk_idx = 1
    row_count = 0
    tokenish_count = 0
    source_counts: Counter[str] = Counter()
    license_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    register_counts: Counter[str] = Counter()

    def flush() -> None:
        nonlocal chunk, chunk_idx
        if not chunk:
            return
        path = out_dir / f"mozhi_tamil_corpus_{chunk_idx:04d}.txt"
        path.write_text("\n\n".join(chunk) + "\n", encoding="utf-8")
        chunk = []
        chunk_idx += 1

    for row in rows:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        chunk.append(text)
        row_count += 1
        tokenish_count += len(text.split())
        source_counts[row.get("source") or "unknown"] += 1
        license_counts[row.get("license") or "unknown"] += 1
        domain_counts[row.get("domain") or "unknown"] += 1
        register_counts[row.get("register") or "unknown"] += 1
        if len(chunk) >= chunk_size:
            flush()
    flush()

    meta = {
        "dataset": DATASET_ID,
        "dataset_license": "cc-by-sa-4.0",
        "rows_imported": row_count,
        "approx_tokens": tokenish_count,
        "chunk_files": chunk_idx - 1,
        "source_counts": dict(source_counts.most_common()),
        "row_license_counts": dict(license_counts.most_common()),
        "domain_counts": dict(domain_counts.most_common()),
        "register_counts": dict(register_counts.most_common()),
    }
    (out_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--min-quality", type=float, default=0.90)
    parser.add_argument("--min-language", type=float, default=0.95)
    parser.add_argument("--min-chars", type=int, default=80)
    parser.add_argument("--min-tamil-ratio", type=float, default=0.70)
    parser.add_argument("--register", action="append", help="Keep only this register; repeatable.")
    parser.add_argument("--domain", action="append", help="Keep only this domain; repeatable.")
    parser.add_argument("--no-streaming", action="store_true")
    args = parser.parse_args()

    allowed_registers = set(args.register) if args.register else None
    allowed_domains = set(args.domain) if args.domain else None
    selected = []

    for row in iter_dataset(streaming=not args.no_streaming):
        if good_row(
            row,
            min_quality=args.min_quality,
            min_language=args.min_language,
            min_chars=args.min_chars,
            min_tamil_ratio=args.min_tamil_ratio,
            allowed_registers=allowed_registers,
            allowed_domains=allowed_domains,
        ):
            selected.append(row)
            if args.max_rows and len(selected) >= args.max_rows:
                break

    meta = write_chunks(selected, args.out, args.chunk_size)
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
