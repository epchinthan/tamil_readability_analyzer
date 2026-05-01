"""
folder_watcher.py — Auto-scan and watch a folder of grade Tamil books.

Features:
  - Multiple PDF/TXT/DOCX files per grade — all files in a grade subfolder or matching
    the grade naming pattern are merged into one vocabulary set.
  - Parallel processing — uses a ThreadPoolExecutor so multiple PDFs
    are extracted simultaneously (I/O bound: pdfminer releases the GIL).
  - Change detection — MD5 hash per file; only new or modified files
    are reprocessed on rescan.
  - Cross-platform — works on Linux and Windows (uses os.path everywhere).

Folder layout options (both supported):
  Option A — Grade subfolders:
    books/
      1/  std1_lesson1.pdf  std1_lesson2.docx
      2/  std2.pdf
      10/ std10_part1.pdf  std10_part2.pdf

  Option B — Grade number in filename (flat folder):
    books/
      std1_tamil.pdf   class_02.docx   grade3.txt   10th.pdf

  Option C — Explicit mapping in config.json:
    { "mappings": { "filename.pdf": 5 } }
"""

import os
import re
import json
import hashlib
import threading
import time
import logging
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger('folder_watcher')

CONFIG_PATH = 'config.json'

WATCHER_STATUS = {
    'folder':         None,
    'watching':       False,
    'processing':     [],      # filenames currently in flight
    'processed':      [],      # [{filename, grade, status, words, msg}]
    'errors':         [],
    'last_scan':      None,
    'queue_size':     0,
    # Progress tracking (file-size based — zero cost to compute)
    'scan_active':    False,   # True while a batch scan is running
    'progress_pct':   0,       # 0-100 based on bytes processed / total bytes
    'bytes_total':    0,       # total bytes of files to process this scan
    'bytes_done':     0,       # bytes completed so far
    'files_total':    0,       # total files to process
    'files_done':     0,       # files completed (including skipped)
    'files_new':      0,       # files actually processed (not skipped)
    'scan_started':   None,    # ISO timestamp when scan began
    'elapsed_sec':    0,
    # Per-file stage tracking — updated live from extract_text / _process_grade_file
    'current_files':  {},      # {filename: {stage, stage_detail, started_at}}
    'eta_sec':        None,    # estimated seconds remaining
}

def update_file_stage(filename: str, stage: str, detail: str = '') -> None:
    """Called from app.py extract_text to report per-file progress."""
    import time as _time
    with _status_lock:
        if filename not in WATCHER_STATUS['current_files']:
            WATCHER_STATUS['current_files'][filename] = {
                'stage':      stage,
                'detail':     detail,
                'started_at': _time.time(),
            }
        else:
            WATCHER_STATUS['current_files'][filename]['stage']  = stage
            WATCHER_STATUS['current_files'][filename]['detail'] = detail

def clear_file_stage(filename: str) -> None:
    """Remove a file from current_files tracking once done."""
    with _status_lock:
        WATCHER_STATUS['current_files'].pop(filename, None)

_status_lock = threading.Lock()

# Max parallel PDF extraction workers.
# pdfminer is I/O-heavy so 8 workers handles 259 files well on modern hardware.
# Reduce to 4 if RAM is limited (each worker can use ~200 MB during PDF parse).
MAX_WORKERS = 8

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    'watch_folder':          '',
    'mappings':              {},
    'auto_grade_from_name':  True,
    'use_subfolders':        True,   # treat subfolders named 1-12 as grade folders
    'max_workers':           MAX_WORKERS,   # increase to 12-16 on high-RAM machines
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# ── Grade inference ───────────────────────────────────────────────────────────

def infer_grade_from_path(filepath, cfg):
    """
    Try to determine grade (1-12) from a file path.

    Priority:
      1. Explicit mapping by filename
      2. Any ancestor folder in the path contains a grade indicator:
           - Plain digit folder:     .../5/file.pdf          → 5
           - Class_N folder:         .../Class_05/...        → 5
           - Grade_N folder:         .../Grade_3/...         → 3
           - Std_N / Standard_N:     .../Std_12/...          → 12
         Checks from deepest folder upward so the most specific wins.
      3. Grade number in filename (fallback)
    Returns int or None.
    """
    filename = os.path.basename(filepath)

    # 1. Explicit mapping
    grade = cfg.get('mappings', {}).get(filename)
    if grade is not None:
        return int(grade)

    # 2. Walk ancestor folders deepest-first
    if cfg.get('use_subfolders', True):
        # Build list of folder names from deepest to shallowest
        parts = []
        path = os.path.dirname(os.path.abspath(filepath))
        watch = os.path.abspath(cfg.get('watch_folder', ''))
        while path and path != watch and len(parts) < 10:
            parts.append(os.path.basename(path))
            parent = os.path.dirname(path)
            if parent == path:   # reached fs root
                break
            path = parent

        for part in parts:
            # Plain digit: "5", "05", "12"
            try:
                g = int(part)
                if 1 <= g <= 12:
                    return g
            except ValueError:
                pass
            # Prefixed: Class_01, Grade_3, Std_12, Standard_5, Kalvi_7 etc.
            m = re.search(
                r'(?:class|grade|std|standard|வகுப்பு|தர)[\s_\-]*(\d{1,2})',
                part, re.IGNORECASE
            )
            if m:
                g = int(m.group(1))
                if 1 <= g <= 12:
                    return g

    # 3. Grade number in filename (fallback)
    if cfg.get('auto_grade_from_name', True):
        base = os.path.splitext(filename)[0].lower()
        # Remove common non-grade keywords so their digits don't interfere
        for kw in ['term','part','vol','volume','book','text','school','level',
                   'tamil','english','science','maths','social','history']:
            base = re.sub(r'\b' + kw + r'[_ ]?\d*', ' ', base)
        # Now look for class/std/grade prefix + number
        m = re.search(
            r'(?:std|class|grade|standard)[\s_\-]*(\d{1,2})(?!\d)',
            base, re.IGNORECASE
        )
        if m:
            g = int(m.group(1))
            if 1 <= g <= 12:
                return g
        # Last resort: any standalone 1-12 number
        nums = re.findall(r'(?<!\d)(\d{1,2})(?!\d)', base)
        for n in nums:
            g = int(n)
            if 1 <= g <= 12:
                return g

    return None

# ── File hash ─────────────────────────────────────────────────────────────────

def file_hash(filepath):
    h = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
    except OSError:
        return ''
    return h.hexdigest()

# ── Status helpers ────────────────────────────────────────────────────────────

def _mark_processing(fname):
    with _status_lock:
        if fname not in WATCHER_STATUS['processing']:
            WATCHER_STATUS['processing'].append(fname)

def _mark_done(fname, grade, status, words, msg, file_size=0, filepath=''):
    with _status_lock:
        WATCHER_STATUS['processing'] = [
            x for x in WATCHER_STATUS['processing'] if x != fname
        ]
        WATCHER_STATUS['queue_size'] = max(0, WATCHER_STATUS['queue_size'] - 1)
        # Replace any existing entry for this file
        WATCHER_STATUS['processed'] = [
            x for x in WATCHER_STATUS['processed'] if x.get('filename') != fname
        ]
        entry = {'filename': fname, 'filepath': filepath if status == 'error' else '',
                 'grade': grade, 'status': status, 'words': words, 'msg': msg}
        WATCHER_STATUS['processed'].insert(0, entry)
        WATCHER_STATUS['processed'] = WATCHER_STATUS['processed'][:100]
        if status == 'error':
            WATCHER_STATUS['errors'].append(f'{fname}: {msg}')
            WATCHER_STATUS['errors'] = WATCHER_STATUS['errors'][-20:]
        # Progress — always update, zero-cost integer arithmetic
        WATCHER_STATUS['files_done'] += 1
        WATCHER_STATUS['bytes_done'] += file_size
        if status != 'skipped':
            WATCHER_STATUS['files_new'] += 1
        total = WATCHER_STATUS['bytes_total']
        WATCHER_STATUS['progress_pct'] = (
            round(WATCHER_STATUS['bytes_done'] / total * 100, 1)
            if total > 0 else 0
        )

# ── Core: process one file ────────────────────────────────────────────────────

def _process_one(filepath, grade, process_fn, loaded_hashes):
    """Called in a worker thread. process_fn is injected from app.py."""
    fname = os.path.basename(filepath)
    norm  = os.path.normpath(os.path.abspath(filepath))
    try:
        fsize = os.path.getsize(filepath)
    except OSError:
        fsize = 0

    # Check in-memory hash cache first — avoids computing MD5 for unchanged files
    try:
        from .app import _hash_cache, _hash_cache_lock
        with _hash_cache_lock:
            cached_hash = _hash_cache.get(norm)
        if cached_hash is not None:
            # Compute hash only if cache suggests file may have changed
            fhash = file_hash(filepath)
            if cached_hash == fhash:
                _mark_done(fname, grade, 'skipped', 0, 'unchanged', fsize, filepath)
                return {'skipped': True, 'filename': fname}
        else:
            fhash = file_hash(filepath)
    except ImportError:
        fhash = file_hash(filepath)

    _mark_processing(fname)
    try:
        result = process_fn(filepath, grade)
        # Handle all result types: skipped, error, success
        if result.get('skipped'):
            _mark_done(fname, grade, 'skipped', 0, 'unchanged', fsize, filepath)
        elif 'error' in result:
            _mark_done(fname, grade, 'error', 0, result['error'], fsize, filepath)
        else:
            words = result.get('word_count', 0)
            _mark_done(fname, grade, 'ok', words, f"{words:,} stems loaded", fsize, filepath)
    except Exception as e:
        result = {'error': str(e), 'filename': fname}
        _mark_done(fname, grade, 'error', 0, str(e), fsize, filepath)
    return result

# ── Collect all files to process ─────────────────────────────────────────────

def _collect_files(folder, cfg):
    """
    Walk the watch folder and return [(filepath, grade), ...] for all
    supported files that can have a grade assigned.
    Supports flat folder, grade subfolders, and mixed layouts.
    """
    supported = ('.pdf', '.txt', '.docx')
    pairs = []
    seen  = set()

    for root, dirs, files in os.walk(folder):
        # Support deep subfolder structures like:
        # textbooks_imported/Samacheer_Kalvi/Class_01/Tamil/Term_1/file.pdf
        # That's 4 levels deep — allow up to 6 to be safe
        depth = len(os.path.relpath(root, folder).split(os.sep))
        if depth > 6:
            dirs.clear()
            continue

        for fname in sorted(files):
            if not fname.lower().endswith(supported):
                continue
            filepath = os.path.join(root, fname)
            norm = os.path.normpath(os.path.abspath(filepath))
            if norm in seen:
                continue
            seen.add(norm)

            grade = infer_grade_from_path(filepath, cfg)
            if grade is None:
                log.warning(
                    f'Cannot determine grade for {fname} — skipping. '
                    f'Rename with grade number or add to mappings in config.json.'
                )
                continue
            pairs.append((filepath, grade))

    return pairs

# ── Scan folder (startup + manual rescan) ────────────────────────────────────

def scan_folder(folder, cfg, process_fn, get_loaded_hashes_fn):
    """
    Scan entire folder. Uses a thread pool to process multiple files in
    parallel. Skips unchanged files. Safe to call multiple times.

    Performance note for large folders (259+ files):
    - Calls _load_all_hashes() ONCE before dispatching workers so each
      worker uses the in-memory cache instead of querying the DB per file.
    - Workers only touch the DB when they actually need to write new data.
    """
    if not folder or not os.path.isdir(folder):
        return

    WATCHER_STATUS['last_scan'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    pairs = _collect_files(folder, cfg)

    if not pairs:
        log.info('Scan: no processable files found')
        return

    # Pre-load all known hashes into memory in one query — avoids one DB
    # roundtrip per file (important when processing hundreds of files)
    try:
        from .app import _load_all_hashes
        _load_all_hashes()
        log.info('Hash cache pre-loaded')
    except Exception:
        pass  # fallback: each worker will query DB individually

    workers    = cfg.get('max_workers', MAX_WORKERS)
    total      = len(pairs)
    total_bytes = sum(
        os.path.getsize(fp) for fp, _ in pairs
        if os.path.exists(fp)
    )

    scan_start = time.time()
    with _status_lock:
        WATCHER_STATUS['queue_size']   = total
        WATCHER_STATUS['scan_active']  = True
        WATCHER_STATUS['progress_pct'] = 0
        WATCHER_STATUS['bytes_total']  = total_bytes
        WATCHER_STATUS['bytes_done']   = 0
        WATCHER_STATUS['files_total']  = total
        WATCHER_STATUS['files_done']   = 0
        WATCHER_STATUS['files_new']    = 0
        WATCHER_STATUS['scan_started'] = datetime.datetime.now().isoformat()
        WATCHER_STATUS['elapsed_sec']  = 0

    log.info(f'Scanning {total} files ({total_bytes/1024/1024:.1f} MB) with {workers} workers…')

    # Pass empty dict — workers use _hash_cache instead
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_process_one, fp, g, process_fn, {}): (fp, g)
            for fp, g in pairs
        }
        for future in as_completed(futures):
            fp, g = futures[future]
            try:
                future.result()
            except Exception as e:
                log.error(f'Worker error for {fp}: {e}')
            # Update elapsed time and ETA on every completion
            with _status_lock:
                WATCHER_STATUS['elapsed_sec'] = round(time.time() - scan_start)
                done  = WATCHER_STATUS['files_done']
                total = WATCHER_STATUS['files_total']
                if done > 0 and total > done:
                    rate = WATCHER_STATUS['elapsed_sec'] / done   # sec per file
                    WATCHER_STATUS['eta_sec'] = round(rate * (total - done))
                else:
                    WATCHER_STATUS['eta_sec'] = None

    with _status_lock:
        WATCHER_STATUS['scan_active']  = False
        WATCHER_STATUS['progress_pct'] = 100
        WATCHER_STATUS['elapsed_sec']  = round(time.time() - scan_start)

    log.info(f'Scan complete — {WATCHER_STATUS["files_new"]} new, '
             f'{WATCHER_STATUS["files_done"] - WATCHER_STATUS["files_new"]} skipped')

# ── Watchdog file watcher ─────────────────────────────────────────────────────

_observer     = None
_worker_pool  = None

def start_watcher(folder, cfg, process_fn, get_loaded_hashes_fn):
    global _observer, _worker_pool

    if not folder or not os.path.isdir(folder):
        return

    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        workers = cfg.get('max_workers', MAX_WORKERS)
        _worker_pool = ThreadPoolExecutor(max_workers=workers)

        class Handler(FileSystemEventHandler):
            def _handle(self, path):
                if not path.lower().endswith(('.pdf', '.txt', '.docx')):
                    return
                # Brief delay to let the OS finish writing the file
                time.sleep(2.0)
                if not os.path.exists(path):
                    return
                grade = infer_grade_from_path(path, cfg)
                if grade is None:
                    log.warning(f'Watcher: {os.path.basename(path)} — grade unknown, skipping')
                    return
                loaded = get_loaded_hashes_fn()
                with _status_lock:
                    WATCHER_STATUS['queue_size'] += 1
                _worker_pool.submit(
                    _process_one, path, grade, process_fn, loaded
                )

            def on_created(self, event):
                if not event.is_directory: self._handle(event.src_path)

            def on_modified(self, event):
                if not event.is_directory: self._handle(event.src_path)

        # Stop existing observer if any
        if _observer and _observer.is_alive():
            _observer.stop()
            _observer.join()

        _observer = Observer()
        # Watch root folder and all subdirectories (grade subfolders)
        _observer.schedule(Handler(), folder, recursive=True)
        _observer.start()

        WATCHER_STATUS['watching'] = True
        WATCHER_STATUS['folder']   = folder
        log.info(f'Watching: {folder}')

    except ImportError:
        log.warning('watchdog not installed — live watching disabled.')

def stop_watcher():
    global _observer, _worker_pool
    if _observer and _observer.is_alive():
        _observer.stop()
        _observer.join()
    if _worker_pool:
        _worker_pool.shutdown(wait=False)
    WATCHER_STATUS['watching'] = False
