#!/usr/bin/env python3
"""Local check page for the page-role answer sheets (evaluations/page_roles/).

Runs on this computer only (127.0.0.1). The PDFs stay where they are; page images and the
answers are kept in the git-ignored output/evaluations/page_role_check/ folder.

  python3 tools/page_role_check.py            # start the check page and open it in the browser
  python3 tools/page_role_check.py --apply    # write the saved answers into the answer sheets

Uses the same local case-to-PDF map as the scorecard: output/evaluations/page_role_sources.json.
"""

import argparse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.page_role_check import apply_answers, case_view  # noqa: E402
from ai.page_role_evaluation import validate_answer_sheet  # noqa: E402
from tools.evaluate_page_roles import analyse_fast, cached_extraction  # noqa: E402

CASES_DIR = ROOT / "evaluations" / "page_roles"
SOURCES = ROOT / "output" / "evaluations" / "page_role_sources.json"
CACHE_DIR = ROOT / "output" / "evaluations" / "page_role_cache"
WORK_DIR = ROOT / "output" / "evaluations" / "page_role_check"
ANSWERS = WORK_DIR / "answers.json"
PAGE_HTML = Path(__file__).with_name("page_role_check.html")
CASE_ID = re.compile(r"^caseP\d{2}$")
THUMB_DPI, LARGE_DPI = 40, 110


def load_sheets():
    return {sheet["case_id"]: sheet for sheet in
            (validate_answer_sheet(json.loads(path.read_text(encoding="utf-8")))
             for path in sorted(CASES_DIR.glob("*.json")))}


def load_sources():
    return json.loads(SOURCES.read_text(encoding="utf-8")) if SOURCES.is_file() else {}


def read_answers():
    try:
        data = json.loads(ANSWERS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(handle.name, path)


class Pages:
    """Page images rendered on first request, a whole set at a time for thumbnails."""

    def __init__(self, sources):
        self.sources, self.locks, self.lock = sources, {}, threading.Lock()

    def _lock(self, key):
        with self.lock:
            return self.locks.setdefault(key, threading.Lock())

    def image(self, case_id, page, large):
        pdf = self.sources[case_id]
        folder = WORK_DIR / ("large" if large else "thumbs") / case_id
        path = folder / f"page_{page:03d}.png"
        with self._lock(f"{case_id}:{large}"):
            if not path.is_file():
                folder.mkdir(parents=True, exist_ok=True)
                if large:
                    subprocess.run(["pdftoppm", "-png", "-singlefile", "-f", str(page), "-l", str(page),
                                    "-r", str(LARGE_DPI), pdf, str(path.with_suffix(""))], check=True, capture_output=True)
                else:
                    subprocess.run(["pdftoppm", "-png", "-r", str(THUMB_DPI), pdf, str(folder / "p")],
                                   check=True, capture_output=True)
                    for rendered in folder.glob("p-*.png"):
                        rendered.replace(folder / f"page_{int(rendered.stem.split('-')[1]):03d}.png")
        return path.read_bytes() if path.is_file() else None


def build_data(sheets, sources):
    cases, missing = [], []
    for case_id, sheet in sheets.items():
        pdf = sources.get(case_id)
        if not pdf or not Path(pdf).is_file():
            missing.append(case_id)
            continue
        texts, _title, _visual = cached_extraction(pdf, CACHE_DIR)
        view = case_view(sheet, analyse_fast(pdf, CACHE_DIR), len(texts))
        view["file_name"] = Path(pdf).name
        cases.append(view)
        print(f"  {case_id}: {view['page_count']} pages", flush=True)
    return {"cases": cases, "missing": missing}


def make_handler(data, pages, case_pages):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, kind="application/json"):
            body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if kind != "image/png" else "max-age=3600")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                return self.send(200, PAGE_HTML.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/data":
                return self.send(200, {**data, "answers": read_answers()})
            match = re.fullmatch(r"/(thumb|large)/(caseP\d{2})/(\d{1,4})", path)
            if match and match[2] in case_pages and 1 <= int(match[3]) <= case_pages[match[2]]:
                try:
                    image = pages.image(match[2], int(match[3]), match[1] == "large")
                except subprocess.CalledProcessError:
                    image = None
                return self.send(200, image, "image/png") if image else self.send(404, {"error": "No image"})
            return self.send(404, {"error": "Not found"})

        def do_POST(self):
            if self.path != "/api/answers":
                return self.send(404, {"error": "Not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > 2_000_000:
                return self.send(413, {"error": "Too large"})
            try:
                answers = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(answers, dict) or any(key not in case_pages for key in answers):
                    raise ValueError("Unknown set")
            except ValueError as error:
                return self.send(400, {"error": str(error)})
            write_json(ANSWERS, answers)
            return self.send(200, {"saved": True})

    return Handler


def apply_all():
    sheets, answers, today = load_sheets(), read_answers(), date.today().isoformat()
    sources = load_sources()
    if not answers:
        print(f"No saved answers in {ANSWERS}.")
        return 1
    for case_id, answer in sorted(answers.items()):
        if case_id not in sheets:
            continue
        texts, _title, _visual = cached_extraction(sources[case_id], CACHE_DIR)
        updated, problems = apply_answers(sheets[case_id], answer, len(texts), today)
        write_json(CASES_DIR / f"{case_id}.json", updated)
        state = "confirmed" if updated["confirmed"] else "not confirmed: " + "; ".join(problems)
        print(f"{case_id}: {state}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local check page for the page-role answer sheets.")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-open", action="store_true", help="Don't open the browser.")
    parser.add_argument("--apply", action="store_true", help="Write the saved answers into the answer sheets.")
    args = parser.parse_args(argv)
    if args.apply:
        return apply_all()
    sources = load_sources()
    print("Reading the drawing sets…", flush=True)
    data = build_data(load_sheets(), sources)
    case_pages = {row["case_id"]: row["page_count"] for row in data["cases"]}
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(data, Pages(sources), case_pages))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Check page: {url}  (answers save to {ANSWERS.relative_to(ROOT)}; Ctrl+C to stop)", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
