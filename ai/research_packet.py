"""Create a cited external-research handoff from reviewed PDF evidence.

This module deliberately does not make network requests or choose engineering
values. It packages project evidence for a web-enabled AI or researcher, and
requires every returned external fact to carry a direct source citation and
review status before it can be considered by the calculator.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ai.ai_packet import load_json


DEFAULT_QUESTIONS = [
    "Identify the project location, climate station candidates, and the source supporting each candidate.",
    "List only construction, glazing, occupancy, operating-hours, and equipment facts supported by cited project evidence or an external source.",
    "Record missing cooling-load inputs that require an engineer decision; do not propose a numerical default.",
]


def create_research_packet(ai_input_path, questions=None):
    """Write a manual research package beside a reviewed ``ai_input.json`` file."""
    ai_input_path = Path(ai_input_path)
    ai_input = load_json(ai_input_path)
    packet_dir = ai_input_path.parent / "research_packet"
    packet_dir.mkdir(parents=True, exist_ok=True)

    research_questions = list(questions or DEFAULT_QUESTIONS)
    request = {
        "schema_version": 1,
        "created_at": timestamp(),
        "research_status": "external_research_required",
        "project_evidence_path": "ai_input.json",
        "questions": research_questions,
        "rules": research_rules(),
    }
    result_template = {
        "schema_version": 1,
        "status": "draft_external_research",
        "facts": [],
        "unresolved_questions": research_questions,
        "review": {
            "status": "requires_engineer_review",
            "reviewer": "",
            "reviewed_at": "",
            "notes": "",
        },
    }
    manifest = {
        "schema_version": 1,
        "created_at": request["created_at"],
        "purpose": "Package reviewed PDF evidence for cited external research; no values are imported automatically.",
        "files": ["ai_input.json", "research_request.json", "prompt.md", "result_template.json"],
        "source_pages": [item.get("page") for item in ai_input.get("pages", []) if item.get("page") is not None],
        "network_access": "manual web-enabled AI or researcher only; this code performs no network calls",
    }

    shutil.copy2(ai_input_path, packet_dir / "ai_input.json")
    write_json(packet_dir / "research_request.json", request)
    write_json(packet_dir / "result_template.json", result_template)
    write_json(packet_dir / "manifest.json", manifest)
    (packet_dir / "prompt.md").write_text(research_prompt(request), encoding="utf-8")
    return {"packet_dir": str(packet_dir), "manifest": manifest, "request": request}


def research_rules():
    return [
        "Treat the supplied PDF packet as project evidence, not as proof of missing values.",
        "For every external fact, record title, publisher, direct URL, publication/version date when available, and access date.",
        "Use official, manufacturer, standards-owner, government, or client-authorised sources where available.",
        "Do not invent, interpolate, or silently default numerical design inputs.",
        "Mark conflicts and unsupported claims as unresolved.",
        "External research is evidence for engineer review; it is not an engineering approval or a final calculation input.",
    ]


def research_prompt(request):
    rules = "\n".join(f"- {rule}" for rule in request["rules"])
    questions = "\n".join(f"{index}. {question}" for index, question in enumerate(request["questions"], start=1))
    return f"""# Archie Heat Load Calculator — External Research Handoff

You have reviewed PDF evidence in `ai_input.json`. Use a web-enabled research workflow only to answer the questions below.

## Questions

{questions}

## Rules

{rules}

## Required response format

Return JSON matching `result_template.json`. Each fact must include:

```json
{{
  "field": "stable_field_name",
  "value": null,
  "unit": "",
  "status": "proposed | unresolved | not_applicable",
  "basis": "project_pdf | external_source | engineer_statement",
  "project_page_citations": [1],
  "external_citations": [
    {{"title": "", "publisher": "", "url": "", "version_or_date": "", "accessed_at": "", "supporting_excerpt": ""}}
  ],
  "conflicts": [],
  "review_note": ""
}}
```

Do not describe an uncited conclusion as confirmed. Leave a numerical value null when the source or calculation basis is insufficient.
"""


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser(description="Create a cited manual external-research packet from reviewed PDF evidence.")
    parser.add_argument("ai_input", help="Path to reviewed ai_input.json")
    parser.add_argument("--question", action="append", dest="questions", help="Replace default questions with one or more research questions.")
    args = parser.parse_args()
    print(json.dumps(create_research_packet(args.ai_input, args.questions), indent=2))


if __name__ == "__main__":
    main()
