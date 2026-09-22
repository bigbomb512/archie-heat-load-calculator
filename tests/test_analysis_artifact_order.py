#!/usr/bin/env python3
"""Regression test for initial-analysis artifact ordering."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import web_app


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def main():
    originals = {
        name: getattr(web_app, name)
        for name in ("create_review_packet", "create_spatial_ocr", "build_ai_packet", "_rebuild_evidence_chain", "update_project", "WEB_REVIEW")
    }
    original_preliminary = web_app.ai_preliminary_service.after_pdf_analysis
    try:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            review_dir = root / "web_review" / "project-1"
            review_dir.mkdir(parents=True)
            packet_path = review_dir / "packet.json"
            packet_path.write_text("{}", encoding="utf-8")
            pdf_path = root / "drawing.pdf"
            pdf_path.write_bytes(b"%PDF-test")
            calls = []

            web_app.WEB_REVIEW = root / "web_review"
            web_app.create_review_packet = lambda *_args, **_kwargs: {
                "packet": str(packet_path), "review_dir": str(review_dir), "html": str(review_dir / "review.html"),
                "kept_count": 1, "primary_count": 1,
            }

            def create_spatial(packet, output):
                calls.append("spatial")
                Path(output).write_text(json.dumps({"pages": []}), encoding="utf-8")
                return Path(output)

            def build_packet(_packet, *, spatial_ocr):
                calls.append("packet")
                check("spatial OCR is available before AI input assembly", spatial_ocr == {"pages": [], "source": str(review_dir / "spatial_ocr.json")})
                return {"pages": []}

            def rebuild(project):
                calls.append("rebuild")
                check("spatial OCR survives through evidence rebuild", Path(project["spatial_ocr"]).is_file())
                return {}

            web_app.create_spatial_ocr = create_spatial
            web_app.build_ai_packet = build_packet
            web_app._rebuild_evidence_chain = rebuild
            web_app.ai_preliminary_service.after_pdf_analysis = lambda *_args: None
            web_app.update_project = lambda _project: None

            project = {"id": "project-1", "pdf": str(pdf_path), "name": "drawing.pdf"}
            result = web_app.analyse_project(project)
            check("initial analysis creates spatial OCR", Path(result["spatial_ocr"]).is_file())
            check("initial analysis runs spatial OCR before rebuild", calls == ["spatial", "packet", "rebuild"])

        # Page confirmation generates vector evidence, but a provider response
        # and reviewed geometry confirmation do not exist until later.  The
        # evidence rebuild must preserve that intentional absence rather than
        # making confirmation fail with FileNotFoundError.
        web_app._rebuild_evidence_chain = originals["_rebuild_evidence_chain"]
        with TemporaryDirectory() as folder:
            root = Path(folder)
            ai_input = {
                "source_pdf": "fixture.pdf", "drawing_set": {"pages": []},
                "confirmed_pages": {}, "page_triage": {},
            }
            (root / "ai_input.json").write_text(json.dumps(ai_input), encoding="utf-8")
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": []}), encoding="utf-8")
            rebuilt = web_app._rebuild_evidence_chain({"review_dir": str(root)})
            check("confirmation rebuild tolerates pending AI artifacts", (root / "calculator_draft.json").is_file())
            check("pending AI response remains absent evidence", rebuilt["building"]["sources"]["vision_response"] is False)
    finally:
        for name, value in originals.items():
            setattr(web_app, name, value)
        web_app.ai_preliminary_service.after_pdf_analysis = original_preliminary


if __name__ == "__main__":
    main()
