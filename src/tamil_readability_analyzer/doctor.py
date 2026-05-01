"""Environment health check for Tamil Book Readability Analyzer."""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys

PACKAGES = [
    "flask", "pdfminer", "pdfplumber", "openpyxl", "werkzeug",
    "reportlab", "watchdog", "pytesseract", "pdf2image", "PIL",
]

ok = True
print("Python:", sys.version.split()[0])

print("\nPython packages:")
for pkg in PACKAGES:
    try:
        importlib.import_module(pkg)
        print(f"  ✓ {pkg}")
    except Exception as exc:
        ok = False
        print(f"  ✗ {pkg}: {exc}")

print("\nSystem OCR tools:")
for cmd in ["tesseract", "pdfinfo", "pdftoppm"]:
    path = shutil.which(cmd)
    if path:
        print(f"  ✓ {cmd}: {path}")
    else:
        ok = False if cmd == "tesseract" else ok
        print(f"  ⚠ {cmd}: not found")

if shutil.which("tesseract"):
    try:
        out = subprocess.check_output(["tesseract", "--list-langs"], stderr=subprocess.STDOUT, text=True)
        langs = {line.strip() for line in out.splitlines() if line.strip() and "List of" not in line}
        if "tam" in langs:
            print("  ✓ Tamil language pack: tam")
        else:
            ok = False
            print("  ✗ Tamil language pack missing: tam")
    except Exception as exc:
        ok = False
        print(f"  ✗ Could not read Tesseract languages: {exc}")

print("\nResult:", "READY" if ok else "PARTIAL - see warnings above")
sys.exit(0 if ok else 1)
