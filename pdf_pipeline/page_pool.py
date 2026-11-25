"""Run independent per-page work in worker processes, keeping results in page order.

Page preparation spends most of its time in loops that handle one drawing page at a
time (spatial OCR, vector extraction, overlay and crop rendering). The pages do not
depend on each other and the work is CPU-bound Python, so threads would only take
turns on the GIL; separate processes let the pages run side by side.

map_items(function, items) returns exactly [function(item) for item in items].
map_pdf_pages(function, source_pdf, items) returns
[function(pdf.pages[item["page"] - 1], item) for item in items], with each worker
opening the PDF once. Functions must be importable top-level functions (or
functools.partial of one) and their results must be picklable.

ARCHIE_PAGE_WORKERS sets the number of worker processes; 1 runs everything in this
process, the same as the loops did before. Errors raised by the page function reach
the caller unchanged. Processes use the "spawn" start method, which is safe from the
web server's threads; any script that triggers this work must keep its top-level
code under `if __name__ == "__main__":`.
"""

from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os

DEFAULT_MAX_WORKERS = 6

_worker_pdf = {}  # per worker process: {"path": str, "pdf": pdfplumber.PDF}


def worker_count(item_count):
    """How many processes to use for item_count independent pages (1 means run in this process)."""
    configured = os.environ.get("ARCHIE_PAGE_WORKERS", "").strip()
    try:
        limit = int(configured) if configured else min(DEFAULT_MAX_WORKERS, max(1, (os.cpu_count() or 2) - 1))
    except ValueError:
        limit = 1
    return max(1, min(limit, item_count))


def _pool(workers):
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))


def map_items(function, items):
    items = list(items)
    workers = worker_count(len(items))
    if workers == 1:
        return [function(item) for item in items]
    with _pool(workers) as pool:
        return list(pool.map(function, items))


def _open_worker_pdf(path):
    import pdfplumber

    if _worker_pdf.get("path") != path:
        if _worker_pdf.get("pdf") is not None:
            _worker_pdf["pdf"].close()
        _worker_pdf.update(path=path, pdf=pdfplumber.open(path))
    return _worker_pdf["pdf"]


def _run_pdf_page(task):
    function, path, item = task
    pdf = _open_worker_pdf(path)
    return function(pdf.pages[item["page"] - 1], item)


def map_pdf_pages(function, source_pdf, items):
    items = list(items)
    workers = worker_count(len(items))
    if workers == 1:
        import pdfplumber

        with pdfplumber.open(source_pdf) as pdf:
            return [function(pdf.pages[item["page"] - 1], item) for item in items]
    path = str(source_pdf)
    with _pool(workers) as pool:
        return list(pool.map(_run_pdf_page, [(function, path, item) for item in items]))
