#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

print_header() {
  echo ""
  echo "  ╔══════════════════════════════════════════╗"
  echo "  ║   Tamil Book Readability Analyzer        ║"
  echo "  ╚══════════════════════════════════════════╝"
  echo ""
}

print_header

if ! command -v python3 >/dev/null 2>&1; then
  echo "  ✗ Python 3 not found. Please install Python 3.8+ and run ./start.sh again."
  exit 1
fi

if [ "${1:-}" = "--install-reading-asr" ]; then
  MODEL="${2:-small}"
  echo "  Installing optional Tamil Reading Practice ASR engine: whisper.cpp ($MODEL)"
  echo ""
  python3 setup.py --install-reading-asr "$MODEL"
  echo ""
  echo "  Done. Start the app with: ./start.sh"
  exit 0
fi

detect_whisper_cpp() {
  if [ -z "${TAMIL_READING_ASR_CMD:-}" ] && [ -z "${WHISPER_CPP_BIN:-}" ]; then
    DEFAULT_WHISPER_BIN="$SCRIPT_DIR/tools/whisper.cpp/build/bin/whisper-cli"
    if [ -x "$DEFAULT_WHISPER_BIN" ]; then
      export WHISPER_CPP_BIN="$DEFAULT_WHISPER_BIN"
    fi
  fi

  if [ -z "${TAMIL_READING_ASR_CMD:-}" ] && [ -z "${WHISPER_CPP_MODEL:-}" ]; then
    for model in \
      "$SCRIPT_DIR/tools/whisper.cpp/models/ggml-medium.bin" \
      "$SCRIPT_DIR/tools/whisper.cpp/models/ggml-small.bin" \
      "$SCRIPT_DIR/tools/whisper.cpp/models/ggml-base.bin"; do
      if [ -f "$model" ]; then
        export WHISPER_CPP_MODEL="$model"
        break
      fi
    done
  fi
}

NEED_SETUP=0
if [ ! -x ".venv/bin/python" ]; then
  NEED_SETUP=1
fi
if [ ! -f ".install_complete" ]; then
  NEED_SETUP=1
fi
if [ -f ".install_complete" ]; then
  if [ "requirements.txt" -nt ".install_complete" ]; then
    NEED_SETUP=1
  fi
  if [ "setup.py" -nt ".install_complete" ]; then
    NEED_SETUP=1
  fi
fi
if [ "$NEED_SETUP" = "0" ]; then
  if ! .venv/bin/python - <<'PY' >/dev/null 2>&1
import importlib
mods = [
    'flask', 'pdfminer', 'pdfplumber', 'openpyxl', 'werkzeug',
    'snowballstemmer', 'reportlab', 'watchdog', 'pytesseract',
    'pdf2image', 'PIL', 'docx'
]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
raise SystemExit(1 if missing else 0)
PY
  then
    NEED_SETUP=1
  fi
fi

if [ "$NEED_SETUP" = "1" ]; then
  echo "  First run or setup update detected. Preparing everything now..."
  echo ""
  python3 setup.py --no-prompt
  touch .install_complete
  echo ""
else
  echo "  ✓ Installation already prepared"
fi

TAM_OK=0
if command -v tesseract >/dev/null 2>&1; then
  if tesseract --list-langs 2>/dev/null | grep -qx tam; then
    TAM_OK=1
  fi
fi

if [ "$TAM_OK" = "1" ]; then
  echo "  ✓ Tamil OCR available"
else
  echo "  ⚠ Tamil OCR is not fully available. Normal text PDFs still work."
  echo "    For scanned PDFs, install Tesseract + Tamil language pack + Poppler."
fi

detect_whisper_cpp

if [ -z "${TAMIL_READING_ASR_CMD:-}" ] && { [ -z "${WHISPER_CPP_BIN:-}" ] || [ -z "${WHISPER_CPP_MODEL:-}" ]; }; then
  ASR_MODEL="${TAMIL_READING_ASR_MODEL:-small}"
  echo "  First Reading Practice ASR startup detected."
  echo "  Installing whisper.cpp locally with model: $ASR_MODEL"
  echo "  This can take a while and the files will stay out of Git."
  echo ""
  python3 setup.py --install-reading-asr "$ASR_MODEL"
  echo ""
  detect_whisper_cpp
fi

if [ -n "${TAMIL_READING_ASR_CMD:-}" ]; then
  echo "  ✓ Tamil Reading Practice ASR: custom command"
elif [ -n "${WHISPER_CPP_BIN:-}" ] && [ -n "${WHISPER_CPP_MODEL:-}" ]; then
  echo "  ✓ Tamil Reading Practice ASR: whisper.cpp"
  echo "    Model: $(basename "$WHISPER_CPP_MODEL")"
else
  echo "  ⚠ Tamil Reading Practice ASR is not installed yet."
  echo "    Voice scoring page still opens; manual transcript scoring works."
  echo "    To install offline ASR on Ubuntu: ./start.sh --install-reading-asr small"
  echo "    For better quality on stronger machines: ./start.sh --install-reading-asr medium"
fi

echo ""
echo "  Starting server..."
echo "  Open your browser at: http://localhost:5000"
echo "  Press Ctrl+C to stop"
echo ""
exec .venv/bin/python app.py
