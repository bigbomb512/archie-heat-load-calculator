"""The page sizes and words of a job's source PDF, parsed once per (PDF fingerprint, page).

A rebuild reads the words of the same plan sheets in several places (plan registration, room-use evidence, seating),
and each pdfplumber open walks the whole page tree again (~4 s for a 38-page set whose objects sit in object streams)
before a page is parsed (1-3 s). Here each page is read from the PDF once and kept in the job folder
(pdf_page_text_cache/<sha256 of the PDF>/, JSON text in files not named *.json so the job-state signatures and
the project export leave them out), and in memory for this process; a miss opens the PDF once per saved version of it
for the whole process. Writing a new PDF's cache removes the old PDF's.

The words are exactly what seating_service._words gives ({"text", "bbox": [x0, top, x1, bottom]}, in PDF points)
and the sizes are pdfplumber's page width and height; JSON keeps both exactly (floats round-trip by repr).
"""

from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import threading

FORMAT = 1
_DIGESTS = {}
_LOCK = threading.RLock()            # guards the memos and the shared open document (pdfminer isn't thread-safe)
_WORDS = OrderedDict()               # (saved version of the PDF, fingerprint, page) -> words
_WORDS_LIMIT = 96
_SIZES = {}                          # (saved version of the PDF, fingerprint) -> [[width, height], ...]
_OPEN = {}                           # "version" -> (version, pdfplumber document): one open PDF at a time
_STATS = {"pdf_opens": 0, "pages_parsed": 0}


def stats():
    """How many times this process opened a PDF and parsed a page here (for the performance regression test)."""
    with _LOCK:
        return dict(_STATS)


def clear_memory():
    """Forget the in-memory copies and close the shared document (tests; the on-disk cache stays)."""
    with _LOCK:
        _WORDS.clear()
        _SIZES.clear()
        _DIGESTS.clear()
        _close_open()


def _version(path):
    stat = path.stat()
    return (str(path), stat.st_size, stat.st_mtime_ns, getattr(stat, "st_ino", 0))


def _pdf_fingerprint(path):
    path = Path(path).resolve()
    version = _version(path)
    with _LOCK:
        hit = _DIGESTS.get(str(path))
        if hit and hit[0] == version:
            return hit[1]
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    result = digest.hexdigest()
    with _LOCK:
        _DIGESTS[str(path)] = (version, result)
    return result


def _close_open():
    for _version_key, document in _OPEN.values():
        try:
            # pdfplumber's close() walks every page; only flush the pages it has read, then close the file.
            for page in getattr(document, "_pages", []):
                page.close()
            document.flush_cache()
            if not document.stream_is_external:
                document.stream.close()
        except Exception:
            pass
    _OPEN.clear()


@contextmanager
def shared_document(source):
    """The open pdfplumber document of this saved version of the PDF, shared by the whole process so its page tree
    is walked once; used under a lock, and the pages read are let go of afterwards (their words are kept here)."""
    import pdfplumber
    path = Path(source).resolve()
    version = _version(path)
    with _LOCK:
        held = _OPEN.get("pdf")
        if held is None or held[0] != version:
            _close_open()
            _OPEN["pdf"] = (version, pdfplumber.open(path))
            _STATS["pdf_opens"] += 1
        document = _OPEN["pdf"][1]
        try:
            yield document
        finally:
            for page in getattr(document, "_pages", []):
                page.close()


_document = shared_document


def _write(path, value):
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        for other in path.parent.parent.iterdir():          # an earlier version of the PDF's pages
            if other.is_dir() and other != path.parent:
                shutil.rmtree(other, ignore_errors=True)
    temporary = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _load(path, fingerprint, **expected):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get("format") != FORMAT or value.get("fingerprint") != fingerprint
            or any(value.get(key) != item for key, item in expected.items())):
        return None
    return value


class PdfText:
    """One job's source PDF: its page count and sizes, and each page's words."""

    def __init__(self, root, source):
        self.source = Path(source)
        self.fingerprint = _pdf_fingerprint(self.source)
        self.version = (_version(self.source.resolve()), self.fingerprint)
        self.folder = Path(root) / "pdf_page_text_cache" / self.fingerprint

    def sizes(self):
        """[[width, height], ...] of every page, in PDF points."""
        with _LOCK:
            if self.version in _SIZES:
                return _SIZES[self.version]
        saved = _load(self.folder / "pages.sizes", self.fingerprint)
        sizes = saved.get("sizes") if saved else None
        if not (isinstance(sizes, list) and all(isinstance(row, list) and len(row) == 2 for row in sizes)):
            with _document(self.source) as document:
                sizes = [[page.width, page.height] for page in document.pages]
            _write(self.folder / "pages.sizes", {"format": FORMAT, "fingerprint": self.fingerprint, "sizes": sizes})
        with _LOCK:
            _SIZES[self.version] = sizes
        return sizes

    @property
    def page_count(self):
        return len(self.sizes())

    def size(self, number):
        """(width, height) of a page (1-based), or None when the PDF has no such page."""
        sizes = self.sizes()
        return tuple(sizes[number - 1]) if isinstance(number, int) and 0 < number <= len(sizes) else None

    def words(self, number):
        """The page's words as seating_service._words gives them (a fresh list each call), or None for no such page."""
        if self.size(number) is None:
            return None
        key = (*self.version, number)
        with _LOCK:
            words = _WORDS.get(key)
            if words is not None:
                _WORDS.move_to_end(key)
        if words is None:
            path = self.folder / f"page_{number:04d}.words"
            saved = _load(path, self.fingerprint, page=number)
            words = saved.get("words") if saved and isinstance(saved.get("words"), list) else None
            if words is None:
                with _document(self.source) as document:
                    page = document.pages[number - 1]
                    words = [{"text": word["text"], "bbox": [word["x0"], word["top"], word["x1"], word["bottom"]]}
                             for word in page.extract_words()]
                    _STATS["pages_parsed"] += 1
                _write(path, {"format": FORMAT, "fingerprint": self.fingerprint, "page": number, "words": words})
            with _LOCK:
                _WORDS[key] = words
                while len(_WORDS) > _WORDS_LIMIT:
                    _WORDS.popitem(last=False)
        return [{"text": word["text"], "bbox": list(word["bbox"])} for word in words]


def page_words(root, source, page_number):
    """(words, page width) for one PDF page, or None when the PDF has no such page."""
    pdf = PdfText(root, source)
    words = pdf.words(int(page_number))
    return None if words is None else (words, pdf.size(int(page_number))[0])
