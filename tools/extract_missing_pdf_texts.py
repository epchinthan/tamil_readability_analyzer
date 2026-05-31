#!/usr/bin/env python3
"""Extract only PDFs that do not yet have mirrored text outputs."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ONE = ROOT / "tools" / "extract_one_pdf_text.py"


def missing_pairs(pdf_root: Path, txt_root: Path):
    for pdf in sorted(pdf_root.rglob("*.pdf")):
        rel = pdf.relative_to(pdf_root)
        out = txt_root / rel.with_suffix(".txt")
        if not out.exists():
            yield pdf, out, rel


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-root", default="textbooks_imported")
    parser.add_argument("--txt-root", default="textbooks_imported_text")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    pdf_root = ROOT / args.pdf_root
    txt_root = ROOT / args.txt_root
    pairs = list(missing_pairs(pdf_root, txt_root))
    total = len(pairs)
    print(f"Missing PDFs to extract: {total}", flush=True)

    ok_count = 0
    errors = []
    for index, (pdf, out, rel) in enumerate(pairs, start=1):
        cmd = [
            sys.executable,
            str(ONE),
            str(pdf),
            str(out),
            "--backend",
            args.backend,
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            msg = f"timeout after {args.timeout}s"
            errors.append((str(rel), msg))
            print(f"[{index}/{total}] TIMEOUT {rel}: {msg}", flush=True)
            continue

        output = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode == 0:
            ok_count += 1
            print(f"[{index}/{total}] OK {rel}: {output}", flush=True)
        else:
            msg = output or f"exit {proc.returncode}"
            errors.append((str(rel), msg))
            print(f"[{index}/{total}] ERROR {rel}: {msg}", flush=True)

    if errors:
        failed = ROOT / "data" / "missing_pdf_extraction_errors.txt"
        failed.parent.mkdir(parents=True, exist_ok=True)
        failed.write_text(
            "\n".join(f"{rel}\t{msg}" for rel, msg in errors) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {failed}", flush=True)

    print(f"Done: {ok_count} processed, {len(errors)} errors", flush=True)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
