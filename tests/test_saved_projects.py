#!/usr/bin/env python3

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.web_app as web_app


class Request:
    path = "/api/analysis?id=project-1"


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def project(pdf_path, root, **overrides):
    data = {
        "id": "project-1",
        "pdf": str(pdf_path),
        "analysed": True,
        "analysis_version": web_app.ANALYSIS_VERSION,
        "packet": str(root / "packet.json"),
        "review_dir": str(root),
        "ai_input": str(root / "ai_input.json"),
    }
    data.update(overrides)
    return data


def main():
    originals = web_app.project_by_id, web_app.analyse_project, web_app.analysis_response
    try:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "drawing.pdf"
            pdf_path.write_bytes(b"pdf")
            (root / "packet.json").write_text("{}", encoding="utf-8")
            (root / "ai_input.json").write_text("{}", encoding="utf-8")

            current = project(pdf_path, root)
            check("current project stays current", web_app.needs_analysis_rebuild(current), False)
            check("unanalysed project rebuilds", web_app.needs_analysis_rebuild(project(pdf_path, root, analysed=False)), True)
            check("legacy project without ai input rebuilds", web_app.needs_analysis_rebuild(project(pdf_path, root, ai_input="")), True)

            rebuilt = []
            legacy = project(pdf_path, root, ai_input="")
            web_app.project_by_id = lambda _project_id: legacy
            web_app.analyse_project = lambda saved: rebuilt.append(saved) or current
            web_app.analysis_response = lambda saved: {"id": saved["id"]}
            check("legacy project opens after rebuild", web_app.api_saved_analysis(Request()), {"id": "project-1"})
            check("legacy project rebuilt once", len(rebuilt), 1)

            missing_pdf = project(root / "missing.pdf", root, analysed=False)
            web_app.project_by_id = lambda _project_id: missing_pdf
            try:
                web_app.api_saved_analysis(Request())
            except ValueError as error:
                check("missing PDF has clear error", str(error), "This saved project cannot be rebuilt because its original PDF is missing. Upload it again.")
            else:
                raise AssertionError("missing PDF should not open")
    finally:
        web_app.project_by_id, web_app.analyse_project, web_app.analysis_response = originals


if __name__ == "__main__":
    main()
