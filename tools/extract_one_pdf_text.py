#!/usr/bin/env python3
"""Extract one Tamil textbook PDF into the mirrored text-output path."""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tamil_readability_analyzer.app import _tamil_words_only_text, extract_text  # noqa: E402


def extract_with_pdftotext(pdf: Path) -> str:
    with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(pdf), tmp.name],
            text=True,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return ""
        return Path(tmp.name).read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("out")
    parser.add_argument("--backend", default="auto")
    args = parser.parse_args()

    pdf = Path(args.pdf)
    out = Path(args.out)
    raw_text = extract_with_pdftotext(pdf)
    text = _tamil_words_only_text(raw_text)
    if not text.strip():
        text = _tamil_words_only_text(extract_text(str(pdf), ocr_backend=args.backend))
    if not text.strip():
        print("no text extracted", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"{len(text.splitlines())} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
