# Tamil Book Readability Analyzer

Analyze Tamil books for grade-level readability. Supports multiple PDF books per grade, folder-based auto-loading, OCR for scanned Tamil PDFs, PDF reports, and Excel exports.

## Quick start

### Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

`start.sh` now does everything automatically on first run: creates `.venv`, installs Python packages, checks/installs OCR tools when possible, runs the health check, then starts the app. You only need `install.sh` if you want to run setup separately.

### Windows

Double-click:

```text
start.bat
```

`start.bat` automatically runs setup on first launch, then starts the app. You only need `install.bat` if you want to run setup separately.

Then open:

```text
http://localhost:5000
```

## What installation does

The installer now handles the full project setup:

1. Checks Python 3.8+
2. Creates a local `.venv` virtual environment
3. Installs all Python packages from `requirements.txt`
4. Creates app folders: `uploads`, `reports`, `logs`
5. Checks OCR dependencies:
   - Tesseract OCR
   - Tamil language pack `tam`
   - Poppler tools for PDF rendering
6. On Linux/macOS, it can install OCR system tools automatically when `apt` or `brew` is available.
7. On Windows, it can try `winget` for Tesseract/Poppler, or it will print clear manual instructions.
8. Runs `doctor.py` to verify the environment.

## Manual OCR dependency install

Normal Unicode text PDFs work with Python packages only. Scanned PDFs need OCR system tools.

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-tam poppler-utils
```

### macOS with Homebrew

```bash
brew install tesseract tesseract-lang poppler
```

### Windows

Install these and make sure they are available in `PATH`:

- Tesseract OCR
- Tamil traineddata file: `tam.traineddata`
- Poppler for Windows

After installing, close and reopen Command Prompt, then run:

```bat
python setup.py
```

## Check installation

Run:

```bash
.venv/bin/python doctor.py
```

On Windows:

```bat
.venv\Scripts\python.exe doctor.py
```

You should see `READY`. If it says `PARTIAL`, the app can still run, but scanned PDF OCR may not work until the missing system dependency is installed.

## Starting the app

### Linux / macOS

```bash
./start.sh
```

### Windows

```text
Double-click start.bat
```

## Folder layout for school books

Place school books in one folder and set it in the Settings tab. Two layouts work:

### Grade subfolders

```text
tamil_books/
  1/   std1_lesson1.pdf   std1_lesson2.docx  std1_lesson3.txt
  2/   std2_part1.pdf     std2_part2.docx
  10/  std10.pdf
  12/  std12_prose.pdf    std12_poetry.pdf
```

### Grade number in filename

```text
tamil_books/
  std1.pdf    class_02.docx    grade3.txt    4th.pdf    10.pdf
```

Drop new files in anytime. The app picks them up automatically within a few seconds.

Supported local input file types: PDF, DOCX, and TXT. DOCX/TXT are read directly
as text; scanned-image OCR is only relevant to scanned PDFs.

## Features

| Feature | Details |
|---|---|
| Multi-file per grade | All PDFs in a grade are merged into one vocabulary |
| Auto folder loading | Point to a folder once and files load automatically |
| Change detection | Re-processes only new or modified files using MD5 hash |
| Tamil OCR | Uses Tesseract Tamil `tam` for scanned PDFs and CID-font PDFs |
| OCR cleanup | Normalizes Tamil Unicode and fixes common OCR mistakes |
| Parallel processing | Processes multiple PDFs for speed |
| Morphological stemming | Treats related Tamil word forms as the same root |
| Proper noun handling | Auto-detects names and places |
| Detailed PDF report | Generates report with tables and word lists |
| Excel export | Exports summary, words, sentences, and proper nouns |
| Word/sentence checker | Checks any Tamil text for grade appropriateness |
| Cross-platform | Linux, macOS, Windows |

## File structure

```text
tamil_analyzer/
  app.py               Flask server and analysis routes
  analytics.py         Readability metrics
  folder_watcher.py    Auto-scan and watchdog integration
  setup.py             Complete installer
  doctor.py            Installation health check
  install.sh           Linux/macOS installer shortcut
  install.bat          Windows installer shortcut
  start.sh             Linux/macOS launcher using .venv
  start.bat            Windows launcher using .venv
  requirements.txt     Python packages
  config.json          App settings
  tamil_analyzer.db    SQLite database, created on first run
  templates/index.html Web UI
  uploads/             Temporary uploads
  reports/             Generated reports
  logs/                Logs
```

## Troubleshooting

### App opens but scanned PDFs have no Tamil text

Run `doctor.py`. If Tamil language pack is missing, install `tesseract-ocr-tam` on Linux, `tesseract-lang` on macOS, or place `tam.traineddata` into your Windows Tesseract `tessdata` folder.

### PDF conversion fails

Install Poppler. On Linux use `poppler-utils`; on macOS use `brew install poppler`; on Windows install Poppler and add its `bin` folder to `PATH`.

### Python package errors

Delete `.venv` and rerun setup:

```bash
rm -rf .venv
python3 setup.py
```

Windows:

```bat
rmdir /s /q .venv
python setup.py
```

## OCR memory mode

This build OCRs scanned PDFs one page at a time to avoid Linux `Killed` / out-of-memory failures.
Optional environment variables:

```bash
export TAMIL_ANALYZER_OCR_DPI=220          # default low-memory DPI
export TAMIL_ANALYZER_OCR_TIMEOUT=90      # seconds per page
export TAMIL_ANALYZER_OCR_MAX_PAGES=0     # 0 = all pages; set e.g. 30 for testing
./start.sh
```

## Optional: Tamil Reading Practice voice scoring

The Reading Practice page can score a student's Tamil read-aloud recording. The
page works immediately with the manual Tamil transcript fallback, but real voice
scoring needs a local/server ASR engine.

On Ubuntu, the easiest setup is:

```bash
./start.sh --install-reading-asr small
```

For better quality on a stronger machine:

```bash
./start.sh --install-reading-asr medium
```

After installation, start normally:

```bash
./start.sh
```

`start.sh` automatically installs `tools/whisper.cpp` with the `small` multilingual model on first startup if no local ASR engine is present. These downloaded/build files are ignored by Git and should not be committed.

To choose a different model for the first automatic install:

```bash
TAMIL_READING_ASR_MODEL=medium ./start.sh
```

`start.sh` automatically detects:

```text
tools/whisper.cpp/build/bin/whisper-cli
tools/whisper.cpp/models/ggml-medium.bin
tools/whisper.cpp/models/ggml-small.bin
tools/whisper.cpp/models/ggml-base.bin
```

Advanced users can point the app at any Tamil ASR command:

```bash
export TAMIL_READING_ASR_CMD="/path/to/asr --lang {lang} --audio {wav}"
./start.sh
```


## Meaning-level appropriateness (offline)

This add-on checks whether a child can understand the *meaning/concept* of words and phrases, not only read them.

Build once after your textbook database is loaded:

```bash
./start.sh --build-meaning
```

Update later when you add/change textbooks:

```bash
./start.sh --update-meaning
```

Data is saved separately under `data/meaning_kb/`. The existing readability database and existing analysis tables are not changed. Teacher overrides can be edited in `data/meaning_kb/teacher_overrides.json`.

---

## v11 Full Suitability System

This build adds a clean offline suitability/adaptation layer for Tamil children's books while preserving the existing readability/OCR flow.

### New outputs after analysis

- **Book Suitability by Class**: Overall, Word, Meaning, Sentence, and Consistency percentage for each class.
- **Recommended class/age**: The lowest class crossing the safe suitability threshold when possible.
- **Detailed diagnostics**: Difficult words, advanced meaning-level items, long sentence issues.
- **Difficulty progression**: Page/chunk-wise difficulty trend and sudden jumps.
- **Adaptation suggestions**: What to simplify for a target class.
- **Glossary candidates**: Simple explanation placeholders for difficult words.
- **Book comparison data**: Saved to `data/books_index.json` for comparing multiple analysed books.

### Data design

Existing tables and existing analysis behaviour are not changed. New data is stored separately:

```text
data/
  meaning_kb/          # meaning-level knowledge base
  cache/               # analysis cache
  books_index.json     # comparison index
```

### Build/update meaning data

```bash
./start.sh --build-meaning
./start.sh --update-meaning
```

The meaning KB is built from the existing textbook database. No paid APIs are used.

### Compare books API

After analysing multiple books, comparison data is available locally at:

```text
/api/books/index
/api/books/compare
```


## v12: Textbook Auto Importer

The app now includes **Textbook Importer** in the left menu. It can scan public textbook listing pages, discover PDF/Google Drive textbook links, download selected files, and process them into the existing grade database.

Typical use:

1. Open the app with `./start.sh`.
2. Go to **Textbook Importer**.
3. Add a source, for example `https://www.tntextbooks.in/p/school-books.html`.
4. Click **Scan Links**.
5. Review discovered PDF links and deselect anything unwanted.
6. Click **Download selected + Process DB**.
7. After import, rebuild meaning data if needed from **School Database → Meaning-level data**.

Notes:

- Existing upload and analysis features are unchanged.
- Imported files are saved under `textbooks_imported/`.
- Source definitions are saved in `data/textbook_sources.json`.
- Recent scan results are saved in `data/textbook_discovered_links.json`.
- Duplicate/unchanged grade files are skipped using the existing hash logic.
- First version scans the given page plus one level of same-site class pages. It does not do unrestricted web search.

## v22 importer notes

The textbook importer now downloads in the background, downloads multiple files in parallel, and checks the local `textbooks_imported/` folder before downloading. If a matching non-empty PDF already exists on disk, it is skipped for download but still processed into the grade database when selected.

Progress now reports downloaded, skipped, failed, and processed counts.


------------------------------------
Version: v28 Optional Local AI Assistant

No paid API is required. AI is optional and disabled by default.
Recommended local setup for Tamil:
  curl -fsSL https://ollama.com/install.sh | sh
  ollama pull qwen2.5:7b-instruct

New endpoints:
  GET  /api/ai/status
  POST /api/ai/settings
  POST /api/ai/rewrite
  POST /api/ai/explain
  POST /api/ai/lesson_plan
  POST /api/ai/questions

The analyzer still works fully offline without AI using v27 rule-based intelligence.
------------------------------------

## v28.1 - Local AI Analyze Book Enrichment
- Adds optional AI enrichment directly on Analyze Book results.
- Normal offline v27 analysis runs first and remains the source of truth.
- If Local AI/Ollama is enabled, users can generate Author suggestions or Teacher lesson help from a compact analysis summary.
- The full book/PDF is not sent to AI; only top difficult words, concepts, sentence examples, scores, and glossary candidates are used.
- AI outputs are cached under data/cache/ai/ per analysis, mode, and model.
