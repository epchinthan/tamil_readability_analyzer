"""Multi-source textbook importer for Tamil Analyzer.

Tamil-first importer: scans public textbook pages, finds PDF/Google Drive links,
excludes English-medium sections and English-language books by default, downloads
selected files, and lets app.py process them into the existing grade database.
"""
from __future__ import annotations

import hashlib
import os
import html
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional

DATA_DIR = Path("data")
SOURCES_FILE = DATA_DIR / "textbook_sources.json"
DISCOVERED_FILE = DATA_DIR / "textbook_discovered_links.json"
IMPORT_ROOT = Path("textbooks_imported")

PDF_EXT_RE = re.compile(r"\.pdf(?:$|[?#])", re.I)
CLASS_RE = re.compile(r"(?:class|std|standard|grade|books?)\s*[-: ]*(1[0-2]|[1-9])|\b(1[0-2]|[1-9])(?:st|nd|rd|th)\b", re.I)
TERM_RE = re.compile(r"(?:term|பருவம்)\s*[-: ]*([123])|term[_ -]?([123])", re.I)


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    IMPORT_ROOT.mkdir(exist_ok=True)


def slugify(value: str, default: str = "source") -> str:
    value = (value or "").strip()
    value = re.sub(r"[^\w\u0B80-\u0BFF.-]+", "_", value, flags=re.UNICODE).strip("_")
    return value[:80] or default


def _filename_from_content_disposition(value: str) -> str:
    """Extract a filename from a Content-Disposition header."""
    if not value:
        return ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", value, re.I)
    if m:
        return urllib.parse.unquote(m.group(1)).strip('"')
    m = re.search(r'filename="?([^";]+)"?', value, re.I)
    if m:
        return urllib.parse.unquote(m.group(1)).strip('"')
    return ""


def looks_like_epub_or_english_medium_filename(name: str) -> bool:
    """Detect remote/local filenames that should not enter the Tamil-medium PDF set."""
    low = (name or "").lower()
    if not low:
        return False
    if low.endswith(".epub") or ".epub" in low:
        return True
    if re.search(r'(^|[_\-\s])em([_\-\s.]|$)', low):
        return True
    if re.search(r'english[_\-\s]*medium|eng[_\-\s]*medium', low):
        return True
    return False


class LinkParser(HTMLParser):
    """Collect links with nearby heading/table-row context.

    TN textbook pages often use simple text headings (not h-tags) followed by
    tables like: Subject Name | PDF | EPUB, with link text only "Download".
    This parser therefore captures nearby visible text before each table/row,
    the first cell as subject, and the column header (PDF vs EPUB).
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: List[Dict] = []
        self.title_parts: List[str] = []
        self.headings: List[str] = []
        self.visible_text: List[str] = []

        self._current_href: Optional[str] = None
        self._text: List[str] = []
        self._in_title = False
        self._heading_tag: Optional[str] = None
        self._heading_text: List[str] = []
        self._current_section = ""

        self._in_table = False
        self._table_context = ""
        self._table_headers: List[str] = []
        self._table_medium: Optional[str] = None

        self._in_tr = False
        self._in_cell = False
        self._cell_text: List[str] = []
        self._row_cells: List[str] = []
        self._row_links: List[Dict] = []
        self._cell_index = -1
        self._row_context = ""

    def _recent_context(self, n: int = 120) -> str:
        return " ".join(self.visible_text[-n:])

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "a" and attrs_d.get("href"):
            self._current_href = attrs_d.get("href")
            self._text = []
        if tag == "title":
            self._in_title = True
        if tag in {"h1", "h2", "h3", "h4", "h5"}:
            self._heading_tag = tag
            self._heading_text = []
        if tag == "table":
            self._in_table = True
            # Keep context very tight. The medium belongs to the table heading
            # immediately before the table, not to the full page.
            self._table_context = " ".join([self._current_section, self._recent_context(18)]).strip()
            self._table_medium = _latest_medium_marker(self._table_context)
            self._table_headers = []
        if tag == "tr":
            self._in_tr = True
            self._row_cells = []
            self._row_links = []
            self._cell_index = -1
            self._row_context = " ".join([self._current_section, self._table_context, self._recent_context(100)]).strip()
        if tag in {"td", "th"} and self._in_tr:
            self._in_cell = True
            self._cell_text = []
            self._cell_index += 1

    def handle_data(self, data):
        clean = " ".join((data or "").split())
        if clean:
            self.visible_text.append(clean)
            if len(self.visible_text) > 1200:
                self.visible_text = self.visible_text[-800:]
            # Medium labels on TN pages are often plain text immediately
            # before a table, not HTML headings. Track the latest explicit
            # medium label so the following table inherits it.
            low = clean.lower()
            if ("tamil medium" in low or "தமிழ் வழி" in clean or "தமிழ் மூலம்" in clean or
                    any(marker in low for marker in NON_TAMIL_MEDIUM_MARKERS)):
                self._current_section = clean
        if self._current_href is not None:
            self._text.append(data)
        if self._in_title:
            self.title_parts.append(data)
        if self._heading_tag:
            self._heading_text.append(data)
        if self._in_cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href is not None:
            text = " ".join("".join(self._text).split())
            col_header = ""
            if 0 <= self._cell_index < len(self._table_headers):
                col_header = self._table_headers[self._cell_index]
            item = {
                "url": self._current_href,
                "text": html.unescape(text),
                "section": self._current_section,
                "table_context": self._table_context,
                "row_context": self._row_context,
                "row_text": " | ".join(self._row_cells),
                "cell_index": self._cell_index,
                "column_header": col_header,
                "table_medium": self._table_medium,
            }
            if self._in_tr:
                self._row_links.append(item)
            else:
                self.links.append(item)
            self._current_href = None
            self._text = []
        if tag == "title":
            self._in_title = False
        if self._heading_tag and tag == self._heading_tag:
            txt = " ".join("".join(self._heading_text).split())
            if txt:
                self.headings.append(txt)
                self._current_section = txt
            self._heading_tag = None
            self._heading_text = []
        if tag in {"td", "th"} and self._in_cell:
            txt = " ".join("".join(self._cell_text).split())
            self._row_cells.append(txt)
            self._in_cell = False
            self._cell_text = []
        if tag == "tr" and self._in_tr:
            row_text = " | ".join(c for c in self._row_cells if c)
            subject = self._row_cells[0] if self._row_cells else ""
            if self._row_cells and not self._row_links:
                lower = " ".join(self._row_cells).lower()
                if "subject" in lower or "pdf" in lower or "epub" in lower:
                    self._table_headers = list(self._row_cells)
            for item in self._row_links:
                item["row_text"] = row_text
                item["subject_hint"] = subject
                ci = item.get("cell_index", -1)
                if 0 <= ci < len(self._table_headers):
                    item["column_header"] = self._table_headers[ci]
                self.links.append(item)
            self._in_tr = False
            self._row_cells = []
            self._row_links = []
            self._cell_index = -1
            self._row_context = ""
        if tag == "table":
            self._in_table = False
            self._table_context = ""
            self._table_headers = []
            self._table_medium = None

def fetch_html(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "TamilAnalyzerTextbookImporter/1.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, errors="replace")


def parse_links(url: str) -> Dict:
    page = fetch_html(url)
    parser = LinkParser()
    parser.feed(page)
    links = []
    for raw in parser.links:
        item = dict(raw)
        item["url"] = urllib.parse.urljoin(url, raw.get("url", ""))
        links.append(item)
    title = " ".join("".join(parser.title_parts).split())
    return {"url": url, "title": title, "headings": parser.headings, "links": links}


def normalize_drive_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "drive.google.com" not in host and "docs.google.com" not in host:
        return url
    file_id = None
    m = re.search(r"/file/d/([^/]+)", parsed.path)
    if m:
        file_id = m.group(1)
    else:
        qs = urllib.parse.parse_qs(parsed.query)
        file_id = (qs.get("id") or [None])[0]
    if file_id:
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url


def is_pdf_like(url: str, text: str = "", column_header: str = "") -> bool:
    u = url.lower()
    t = (text or "").lower()
    h = (column_header or "").lower()
    # EPUB must never be accepted. TN pages often show both PDF and EPUB as
    # identical "Download" links, so rely primarily on the table column header.
    if "epub" in u or "epub" in t or "epub" in h:
        return False
    if "pdf" in h:
        return True
    if PDF_EXT_RE.search(u) or "pdf" in t:
        return True
    # Do not accept Drive links by link text alone. A Drive EPUB link also says
    # "Download"; it must be under a PDF column to be accepted.
    return False


TAMIL_MEDIUM_MARKERS = ("tamil medium", "தமிழ் வழி", "தமிழ் மூலம்")
NON_TAMIL_MEDIUM_MARKERS = (
    "english medium", "kannada medium", "urdu medium", "telugu medium",
    "malayalam medium", "hindi medium", "sanskrit medium",
    "ஆங்கில வழி", "ஆங்கிலம் வழி", "கன்னட வழி", "கன்னடம் வழி",
    "உருது வழி", "தெலுங்கு வழி", "மலையாள வழி", "இந்தி வழி",
)


def _latest_medium_marker(text: str) -> Optional[str]:
    """Return 'tamil', 'other', or None from the nearest/latest medium marker.

    Every table is evaluated using the text inside the table plus the text just
    before it. If both English Medium and Tamil Medium appear in the context,
    the latest one wins. This matches the TN textbook page layout.
    """
    low = (text or "").lower()
    hits = []
    for marker in TAMIL_MEDIUM_MARKERS:
        idx = low.rfind(marker.lower())
        if idx >= 0:
            hits.append((idx, "tamil"))
    for marker in NON_TAMIL_MEDIUM_MARKERS:
        idx = low.rfind(marker.lower())
        if idx >= 0:
            hits.append((idx, "other"))
    if not hits:
        return None
    return max(hits, key=lambda x: x[0])[1]


def is_english_subject_or_book(*parts: str) -> bool:
    """Exclude English-language subject books even inside Tamil-medium tables."""
    text = " ".join([p or "" for p in parts]).lower()
    if re.search(r"(^|[|\s:/_-])english([|\s:/_-]|$)", text):
        return True
    if "ஆங்கிலம்" in text:
        return True
    return False


def is_non_tamil_medium_context(*parts: str) -> bool:
    """True when the nearest/latest explicit medium marker is not Tamil."""
    text = " ".join([p or "" for p in parts])
    return _latest_medium_marker(text) == "other"


def is_tamil_medium_context(*parts: str) -> bool:
    """Accept only tables where the table or immediately preceding text says Tamil Medium."""
    text = " ".join([p or "" for p in parts])
    return _latest_medium_marker(text) == "tamil"


def infer_class(
*parts: str, default: Optional[int] = None) -> Optional[int]:
    text = " ".join([p or "" for p in parts])
    m = CLASS_RE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    return default


def infer_medium(*parts: str, default: str = "Unknown") -> str:
    text = " ".join([p or "" for p in parts]).lower()
    marker = _latest_medium_marker(text)
    if marker == "other":
        return "Other"
    if marker == "tamil":
        return "Tamil"
    return default



def infer_term(*parts: str) -> Optional[int]:
    text = " ".join([p or "" for p in parts])
    m = TERM_RE.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def source_matches_class_page(link: Dict) -> bool:
    text = f"{link.get('text','')} {link.get('url','')}"
    return bool(CLASS_RE.search(text))


def _same_site(a: str, b: str) -> bool:
    return urllib.parse.urlparse(a).netloc.lower() == urllib.parse.urlparse(b).netloc.lower()



def normalize_filename_for_match(name: str) -> str:
    """Normalize filenames so manual copies and importer names can be matched."""
    return re.sub(r"[^a-z0-9\u0B80-\u0BFF]+", "", (name or "").lower())


def file_exists_valid(path: Path, min_size: int = 10 * 1024) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size >= min_size
    except Exception:
        return False


def find_existing_pdf(expected_path: Path, search_root: Path = IMPORT_ROOT) -> Optional[Path]:
    """Find an already downloaded PDF even if it was copied manually.

    Checks the expected destination first, then searches the importer tree for a
    matching normalized filename. Only non-empty PDFs are accepted.
    """
    expected_path = Path(expected_path)
    if file_exists_valid(expected_path):
        return expected_path
    expected_norm = normalize_filename_for_match(expected_path.name)
    if not expected_norm:
        return None
    try:
        root = Path(search_root)
        if not root.exists():
            return None
        for pdf in root.rglob("*.pdf"):
            if file_exists_valid(pdf) and normalize_filename_for_match(pdf.name) == expected_norm:
                return pdf
    except Exception:
        return None
    return None


def cleanup_bad_download(path: Path) -> None:
    try:
        if path.exists() and (not path.is_file() or path.stat().st_size < 10 * 1024):
            path.unlink(missing_ok=True)
    except Exception:
        pass


_REMOTE_FILENAME_CACHE: Dict[str, str] = {}

def remote_filename(url: str, timeout: int = 18) -> str:
    """Best-effort filename lookup, mainly for Google Drive Content-Disposition."""
    if not url:
        return ""
    if url in _REMOTE_FILENAME_CACHE:
        return _REMOTE_FILENAME_CACHE[url]
    name = ""
    headers = {"User-Agent": "TamilAnalyzerTextbookImporter/1.1", "Range": "bytes=0-0"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            name = _filename_from_content_disposition(r.headers.get("Content-Disposition", ""))
            if not name:
                parsed = urllib.parse.urlparse(r.geturl())
                name = Path(urllib.parse.unquote(parsed.path)).name
    except Exception:
        name = ""
    _REMOTE_FILENAME_CACHE[url] = name or ""
    return _REMOTE_FILENAME_CACHE[url]


def scan_source(source: Dict, depth: int = 1, max_pages: int = 30) -> Dict:
    start_url = source["url"]
    source_name = source.get("name") or urllib.parse.urlparse(start_url).netloc
    source_class = source.get("class")
    board = source.get("board", "")
    language = source.get("language", "Tamil") or "Tamil"
    tamil_only = source.get("tamil_only", True)
    exclude_english = source.get("exclude_english", True)

    visited = set()
    queue = [(start_url, 0, source_class)]
    pdfs: List[Dict] = []
    pages_scanned: List[str] = []

    while queue and len(visited) < max_pages:
        url, d, inherited_class = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            page = parse_links(url)
        except Exception as e:
            pages_scanned.append(f"ERROR {url}: {e}")
            continue
        pages_scanned.append(url)
        page_text = " ".join([page.get("title", "")] + page.get("headings", [])[:8])
        page_class = infer_class(url, page_text, default=inherited_class)
        page_medium = infer_medium(page_text, url, default="Unknown")
        page_term = infer_term(page_text, url)

        for link in page["links"]:
            href = link["url"]
            text = link.get("text", "")
            section = link.get("section", "")
            table_context = link.get("table_context", "")
            table_medium = link.get("table_medium")
            row_context = link.get("row_context", "")
            row_text = link.get("row_text", "")
            subject_hint = link.get("subject_hint", "")
            column_header = link.get("column_header", "")
            medium_context = " ".join([section, table_context, row_context, page_text])

            if is_pdf_like(href, text, column_header):
                if tamil_only:
                    # Only accept links from a table whose own heading/context is Tamil Medium.
                    # This prevents English/Kannada/Urdu/Telugu/other medium tables on
                    # the same page from leaking in.
                    if table_medium != "tamil":
                        continue
                if exclude_english and is_english_subject_or_book(row_text, subject_hint, text):
                    continue
                download_url = normalize_drive_url(href)
                # Do not query remote or Google Drive filenames during scan.
                # Filename checks happen during download only.
                remote_name = ""
                cls = infer_class(url, page_text, section, table_context, row_context, row_text, default=page_class)
                med = infer_medium(section, table_context, row_context, row_text, text, href, default=page_medium)
                term = infer_term(section, table_context, row_context, row_text, text, href) or page_term
                subject = subject_hint or text or Path(urllib.parse.urlparse(href).path).stem or "Book"
                pdfs.append({
                    "source": source_name,
                    "board": board,
                    "language": language,
                    "class": cls,
                    "medium": med if med != "Unknown" else "Tamil",
                    "term": term,
                    "subject": subject,
                    "url": href,
                    "download_url": download_url,
                    "remote_filename": remote_name,
                    "page_url": url,
                    "section": section,
                    "selected": True,
                })
            elif d < depth and _same_site(start_url, href) and source_matches_class_page(link):
                cls = infer_class(href, text, default=page_class)
                queue.append((href, d + 1, cls))

    seen = set()
    unique = []
    for item in pdfs:
        key = item["download_url"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    result = {
        "source": source_name,
        "source_url": start_url,
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pages_scanned": pages_scanned,
        "count": len(unique),
        "items": unique,
    }
    save_discovered(result)
    return result


def load_sources() -> List[Dict]:
    _ensure_dirs()
    if not SOURCES_FILE.exists():
        return []
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_sources(sources: List[Dict]) -> None:
    _ensure_dirs()
    SOURCES_FILE.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


def add_source(source: Dict) -> Dict:
    sources = load_sources()
    source = dict(source)
    source.setdefault("id", hashlib.md5((source.get("name", "") + source.get("url", "")).encode()).hexdigest()[:12])
    source.setdefault("active", True)
    source.setdefault("tamil_only", True)
    source.setdefault("exclude_english", True)
    source.setdefault("language", "Tamil")
    sources = [s for s in sources if s.get("id") != source["id"]]
    sources.append(source)
    save_sources(sources)
    return source


def save_discovered(scan_result: Dict) -> None:
    _ensure_dirs()
    existing = []
    if DISCOVERED_FILE.exists():
        try:
            existing = json.loads(DISCOVERED_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    existing.append(scan_result)
    existing = existing[-20:]
    DISCOVERED_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def recent_discovered() -> List[Dict]:
    if not DISCOVERED_FILE.exists():
        return []
    try:
        return json.loads(DISCOVERED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _download_with_confirm(url: str, dest: Path, timeout: int = 120) -> Dict:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    req = urllib.request.Request(url, headers={"User-Agent": "TamilAnalyzerTextbookImporter/1.1"})
    filename = ""
    ctype = ""
    final_url = url
    with opener.open(req, timeout=timeout) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type", "")
        filename = _filename_from_content_disposition(r.headers.get("Content-Disposition", ""))
        final_url = r.geturl()
    if b"confirm=" in data[:200000] and "text/html" in ctype:
        text = data.decode("utf-8", errors="ignore")
        m = re.search(r"confirm=([0-9A-Za-z_\-]+)", text)
        if m:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            qs["confirm"] = [m.group(1)]
            url2 = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(qs, doseq=True)))
            req = urllib.request.Request(url2, headers={"User-Agent": "TamilAnalyzerTextbookImporter/1.1"})
            with opener.open(req, timeout=timeout) as r2:
                data = r2.read()
                ctype = r2.headers.get("Content-Type", ctype)
                filename = _filename_from_content_disposition(r2.headers.get("Content-Disposition", "")) or filename
                final_url = r2.geturl()
    if not filename:
        filename = Path(urllib.parse.unquote(urllib.parse.urlparse(final_url).path)).name
    dest.write_bytes(data)
    return {"filename": filename, "content_type": ctype, "final_url": final_url}



def _download_one_item(item: Dict) -> Dict:
    """Download one textbook item and return metadata. Does not process DB.

    v22: before downloading, check the local filesystem for an existing valid
    PDF with the expected name. If found, skip download but return ok=True so it
    can still be processed into the DB.
    """
    grade = item.get("class") or item.get("grade")
    source_name = item.get("source") or "Source"
    medium = item.get("medium") or "Tamil"
    term = f"Term_{item.get('term')}" if item.get("term") else "No_Term"
    subject = item.get("subject") or "book"
    url = item.get("download_url") or normalize_drive_url(item.get("url", ""))
    if not url:
        return {"ok": False, "status": "failed", "error": "missing url", "item": item}

    class_dir = f"Class_{int(grade):02d}" if grade else "Class_Unknown"
    folder = IMPORT_ROOT / slugify(source_name) / class_dir / slugify(medium, "Medium") / slugify(term, "Term")
    folder.mkdir(parents=True, exist_ok=True)

    # Best expected name without network: subject-based. This lets manually copied
    # files like Mathematics.pdf or Science.pdf be detected before network calls.
    subject_name = slugify(subject, "book")
    if not subject_name.lower().endswith(".pdf"):
        subject_name += ".pdf"
    expected_subject_dest = folder / subject_name
    existing = find_existing_pdf(expected_subject_dest, IMPORT_ROOT)
    if existing:
        return {"ok": True, "status": "skipped_existing", "skipped": True, "file": str(existing), "grade": grade, "processed": None, "item": item}

    # Remote filename check happens only during download stage, never during scan.
    remote_name = item.get("remote_filename") or remote_filename(url)
    if looks_like_epub_or_english_medium_filename(remote_name):
        return {"ok": False, "status": "skipped", "skipped": True, "error": f"excluded by remote filename: {remote_name}", "item": item}

    name_base = slugify(remote_name or subject, "book")
    if not name_base.lower().endswith(".pdf"):
        name_base += ".pdf"
    dest = folder / name_base

    # If a manually downloaded file already has the remote filename, skip it too.
    existing = find_existing_pdf(dest, IMPORT_ROOT)
    if existing:
        return {"ok": True, "status": "skipped_existing", "skipped": True, "file": str(existing), "grade": grade, "processed": None, "item": item}

    # Avoid overwriting, but still detect duplicates by normalized filename above.
    if dest.exists():
        base, ext = dest.stem, dest.suffix
        h = hashlib.md5(url.encode()).hexdigest()[:8]
        dest = folder / f"{base}_{h}{ext}"

    last_error = ""
    for attempt in range(1, 4):
        try:
            meta = _download_with_confirm(url, dest)
            final_name = meta.get("filename") or remote_name or dest.name
            if looks_like_epub_or_english_medium_filename(final_name):
                dest.unlink(missing_ok=True)
                return {"ok": False, "status": "skipped", "skipped": True, "error": f"excluded by downloaded filename: {final_name}", "item": item}
            if not file_exists_valid(dest):
                cleanup_bad_download(dest)
                last_error = "downloaded file is empty/partial"
                continue
            file_hash = hashlib.md5(dest.read_bytes()).hexdigest()
            return {"ok": True, "status": "downloaded", "file": str(dest), "grade": grade, "hash": file_hash, "processed": None, "item": item}
        except Exception as e:
            last_error = str(e)
            cleanup_bad_download(dest)
            time.sleep(min(2 * attempt, 6))
    return {"ok": False, "status": "failed", "error": last_error or "download failed", "item": item}

def download_items(items: Iterable[Dict], process_fn=None, limit: Optional[int] = None) -> Dict:
    """Backward-compatible sequential download + process."""
    _ensure_dirs()
    results = []
    for idx, item in enumerate(items):
        if limit is not None and idx >= limit:
            break
        r = _download_one_item(item)
        if r.get("ok") and process_fn and r.get("grade"):
            try:
                r["processed"] = process_fn(str(r["file"]), int(r["grade"]), source=f"import:{r.get('item',{}).get('source') or 'Source'}")
            except Exception as e:
                r["ok"] = False
                r["error"] = f"processing failed: {e}"
        results.append(r)
    return {
        "ok": True,
        "downloaded": len([r for r in results if r.get("status") == "downloaded"]),
        "skipped": len([r for r in results if r.get("status") == "skipped_existing" or (r.get("skipped") and not r.get("ok"))]),
        "failed": len([r for r in results if not r.get("ok") and not r.get("skipped")]),
        "results": results,
    }


def download_items_parallel(items: Iterable[Dict], process_fn=None, limit: Optional[int] = None, max_workers: int = 3, progress=None) -> Dict:
    """Download selected textbook PDFs in parallel, then process sequentially for DB safety."""
    _ensure_dirs()
    items = list(items)
    if limit is not None:
        items = items[:limit]
    total = len(items)
    max_workers = max(1, min(int(max_workers or 3), 6, total or 1))
    results = []

    def emit(**kw):
        if progress:
            try:
                progress(dict(kw))
            except Exception:
                pass

    emit(phase="download", message=f"Starting {total} download(s)", downloaded=0, total=total)
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_download_one_item, it): it for it in items}
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:
                r = {"ok": False, "error": str(e), "item": futs[fut]}
            completed += 1
            results.append(r)
            emit(phase="download", message=("Downloaded" if r.get("status") == "downloaded" else ("Skipped existing" if r.get("status") == "skipped_existing" else "Skipped/failed")), downloaded=completed, total=total, last=r)

    ok_downloads = [r for r in results if r.get("ok") and r.get("file") and r.get("grade")]
    emit(phase="process", message=f"Processing {len(ok_downloads)} downloaded PDF(s)", processed=0, total=len(ok_downloads))
    for i, r in enumerate(ok_downloads, start=1):
        if process_fn:
            try:
                r["processed"] = process_fn(str(r["file"]), int(r["grade"]), source=f"import:{r.get('item',{}).get('source') or 'Source'}")
            except Exception as e:
                r["ok"] = False
                r["error"] = f"processing failed: {e}"
        emit(phase="process", message="Processed", processed=i, total=len(ok_downloads), last=r)

    return {
        "ok": True,
        "downloaded": len([r for r in results if r.get("status") == "downloaded"]),
        "skipped": len([r for r in results if r.get("status") == "skipped_existing" or (r.get("skipped") and not r.get("ok"))]),
        "failed": len([r for r in results if not r.get("ok") and not r.get("skipped")]),
        "results": results,
    }
