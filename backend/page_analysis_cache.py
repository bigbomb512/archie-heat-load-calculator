"""Content-addressed cache for expensive page analysis used by Card P.

The cache is local to a project's derived review data. It stores only the
parsed vector primitives and the full-page render needed by room analysis;
task crops and replies remain owned by the task service.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading

CACHE_VERSION = 1
RENDER_VERSION = "pdfplumber-page-to-image-72x-scale-v1"

_ACTIVE = ContextVar("archie_page_analysis_cache", default=None)
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_FILE_DIGESTS = {}
_DIGESTS_GUARD = threading.Lock()


def _project_lock(root):
    key = str(Path(root).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _digest_file(path):
    path = Path(path)
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, getattr(stat, "st_ino", 0))
    key = str(path.resolve())
    with _DIGESTS_GUARD:
        cached = _FILE_DIGESTS.get(key)
        if cached and cached[0] == signature:
            return cached[1]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    value = digest.hexdigest()
    with _DIGESTS_GUARD:
        _FILE_DIGESTS[key] = (signature, value)
    return value


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".stage", delete=False) as handle:
            stage = Path(handle.name)
            json.dump(value, handle, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, path)
    finally:
        if stage and stage.exists():
            stage.unlink()


def _source_pdf(root):
    try:
        ai_input = json.loads((Path(root) / "ai_input.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    source = Path(str(ai_input.get("source_pdf", "")))
    return source if source.is_file() else None


class PageAnalysisCache:
    def __init__(self, root):
        self.root = Path(root)
        self.source_pdf = _source_pdf(self.root)
        self.stats = {"cache_hits": {}, "cache_misses": {}, "pdf_page_opens": {},
                      "page_object_extractions": {}, "page_renders": {}}
        self._memory_page = None
        self._memory_context = None
        if self.source_pdf is None:
            self.generation = "unavailable"
            self.cache_root = self.root / "page_analysis_cache" / self.generation
            return

        dependencies = {"version": CACHE_VERSION, "renderer": RENDER_VERSION,
                        "source_pdf": _digest_file(self.source_pdf)}
        for name in ("ai_input.json", "spatial_ocr.json", "vector_geometry.json"):
            path = self.root / name
            dependencies[name] = _digest_file(path) if path.is_file() else "missing"
        self.generation = hashlib.sha256(json.dumps(
            dependencies, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.cache_root = self.root / "page_analysis_cache" / self.generation
        self.cache_root.parent.mkdir(parents=True, exist_ok=True)
        # Operations for one project are serialized by operation(). Removing
        # prior generations bounds disk use and prevents stale results being
        # mistaken for current data.
        for child in self.cache_root.parent.iterdir():
            if child.is_dir() and child != self.cache_root:
                shutil.rmtree(child, ignore_errors=True)

    @staticmethod
    def _count(table, page):
        key = str(page)
        table[key] = table.get(key, 0) + 1

    def get(self, page, builder):
        page = int(page)
        if self._memory_page == page and self._memory_context is not None:
            self._count(self.stats["cache_hits"], page)
            return self._memory_context

        directory = self.cache_root / f"page_{page:04d}"
        metadata_path, image_path = directory / "context.json", directory / "page.png"
        if metadata_path.is_file() and image_path.is_file():
            try:
                from PIL import Image
                context = json.loads(metadata_path.read_text(encoding="utf-8"))
                context["objects"] = [dict(row, style=tuple(row["style"]),
                                           points=[tuple(point) for point in row["points"]])
                                      for row in context["objects"]]
                for field in ("viewport", "page_origin", "page_bbox"):
                    if field in context:
                        context[field] = tuple(context[field])
                with Image.open(image_path) as image:
                    context["image"] = image.copy()
                self._count(self.stats["cache_hits"], page)
                self._memory_page, self._memory_context = page, context
                return context
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                shutil.rmtree(directory, ignore_errors=True)

        self._count(self.stats["cache_misses"], page)
        self._count(self.stats["pdf_page_opens"], page)
        self._count(self.stats["page_object_extractions"], page)
        self._count(self.stats["page_renders"], page)
        context = builder()
        image = context.pop("image")
        directory.mkdir(parents=True, exist_ok=True)
        metadata = dict(context)
        metadata["objects"] = [dict(row, style=list(row["style"])) for row in context["objects"]]
        _atomic_json(metadata_path, metadata)
        stage = directory / f".page.{os.getpid()}.{threading.get_ident()}.stage"
        try:
            image.save(stage, format="PNG", compress_level=1)
            os.replace(stage, image_path)
        finally:
            if stage.exists():
                stage.unlink()
        context["image"] = image
        self._memory_page, self._memory_context = page, context
        return context


@contextmanager
def operation(root):
    """Share one cache session across a backend operation, with one-page RAM reuse."""
    active = _ACTIVE.get()
    if active is not None and active.root.resolve() == Path(root).resolve():
        yield active
        return
    lock = _project_lock(root)
    with lock:
        cache = PageAnalysisCache(root)
        token = _ACTIVE.set(cache)
        try:
            yield cache
        finally:
            _ACTIVE.reset(token)


def get_context(root, page, builder):
    """Return a cached page context, opening an implicit one-page operation if needed."""
    active = _ACTIVE.get()
    if active is not None and active.root.resolve() == Path(root).resolve():
        return active.get(page, builder)
    with operation(root) as cache:
        return cache.get(page, builder)

