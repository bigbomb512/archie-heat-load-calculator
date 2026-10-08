#!/usr/bin/env python3
"""Test idea 2: let one skill choose the pages it reads, and compare it with the case-file route (idea 1).

Idea 2: the Codex CLI runs as an agent in a read-only folder holding the set's page images, page texts, the pass 1
index (one line per page) and the skill's instructions. It opens what it needs, up to a page budget, and answers.
Idea 1: the same skill gets its case file (pass 1 and 2's readings plus its key pages) in one call.

Both answers, the agent's transcript and a comparison are written to output/evaluations/skill_agentic/<case>/.
Uses the cached pass 1 readings of tools/evaluate_page_inventory.py (run that first) and the Codex CLI signed in
with ChatGPT. Report-only; nothing is written into a project.

  python3 tools/try_skill_agentic.py caseP06 --subskill equipment_evidence
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai.skill_registry import compose_subskill_instructions  # noqa: E402
from backend import page_inventory_service as pass1, skill_workflow_service as skills  # noqa: E402

OUTPUT = ROOT / "output" / "evaluations"
PAGE_BUDGET = 8


def pass1_readings(case_id):
    folder = OUTPUT / "page_inventory" / case_id
    readings = {}
    for path in (folder / "replies").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        readings[row["page"]] = row["reading"]
    if not readings:
        raise SystemExit(f"No pass 1 readings for {case_id}; run tools/evaluate_page_inventory.py --case {case_id} first.")
    return readings, sorted((folder / "pages").glob("p-*.png"))


def contract_text(subskill):
    return ("Reply with ONE JSON object only (no prose, no code fence) with keys: status, affected_ids, observations, inferences, "
            "citations, confidence, alternatives, unresolved_fields, remediation, proposal_fields, pages_viewed.\n"
            f"proposal_fields must be exactly: {json.dumps(subskill['proposal_fields'])}\n"
            "pages_viewed: the page numbers whose image or text you actually opened.")


def build_workspace(folder, case_id, pdf, subskill, readings, renders):
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "pages").mkdir(parents=True)
    (folder / "text").mkdir()
    for path in renders:
        page = int(path.stem.split("-")[1])
        shutil.copy(path, folder / "pages" / f"page_{page:03d}.png")
        text = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
                              capture_output=True, text=True, timeout=300).stdout
        (folder / "text" / f"page_{page:03d}.txt").write_text(text, encoding="utf-8")
    index = ["# Page index (from an AI page-by-page pass; leads, not approved values)", ""]
    for page, row in sorted(readings.items()):
        kinds = ", ".join(sorted({item["kind"] for item in row.get("information", [])})) or "-"
        index.append(f"p{page} | {row.get('page_type', '')} | {row.get('title', '')} | {row.get('level', '')} | {kinds}")
    (folder / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    (folder / "instructions.md").write_text(compose_subskill_instructions(subskill["parent"], subskill), encoding="utf-8")
    (folder / "task.md").write_text(f"# Task: {subskill['id']}\n\n{subskill['task']}\n\nConstraints:\n"
                                    + "".join(f"- {item}\n" for item in subskill["constraints"]) + "\n" + contract_text(subskill) + "\n",
                                    encoding="utf-8")


def run_agentic(folder, subskill):
    prompt = (f"You are working in a folder for one architectural drawing set (case {folder.parent.name}). Files:\n"
              "- index.md: one line per page (type, title, level, kinds of heat-load information found on it).\n"
              "- instructions.md: the rules and method for this task. task.md: the task and the reply format.\n"
              "- pages/page_NNN.png: each page as an image. text/page_NNN.txt: each page's PDF text layer (empty for scans).\n"
              f"Read index.md, instructions.md and task.md first. Then open the pages you need (images with your image viewing "
              f"tool, or text files), at most {PAGE_BUDGET} page images in total. Do not modify any file. "
              f"When finished, reply with only the JSON object described in task.md.")
    started = time.monotonic()
    output = folder.parent / "agentic_reply.json"
    command = ["codex", "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "-C", str(folder),
               "--output-last-message", str(output), "-"]
    result = subprocess.run(command, input=prompt, capture_output=True, text=True, timeout=1800)
    (folder.parent / "agentic_transcript.txt").write_text(result.stdout + "\n--- stderr ---\n" + result.stderr, encoding="utf-8")
    reply = output.read_text(encoding="utf-8") if output.is_file() else ""
    return result.returncode, reply, round(time.monotonic() - started, 1), result.stdout + result.stderr


def run_case_file(case_id, pdf, subskill, readings, renders):
    """Idea 1 on the same set: a stand-in job folder holding pass 1's readings and renders."""
    project_dir = OUTPUT / "skill_agentic" / case_id / "case_file_job"
    if project_dir.exists():
        shutil.rmtree(project_dir)
    (project_dir / pass1.WORK_DIR / "pages").mkdir(parents=True)
    for path in renders:
        shutil.copy(path, project_dir / pass1.WORK_DIR / "pages" / path.name)
    (project_dir / pass1.RESULT_FILE).write_text(json.dumps({"pages": {str(page): row for page, row in readings.items()}}), encoding="utf-8")
    extraction = OUTPUT / "page_extraction" / case_id / "replies" / "equipment_appliances"
    if extraction.is_dir():
        from ai.page_extraction import EXTRACTORS, merge
        rows = [(row["page"], row["section"], row["items"]) for row in
                (json.loads(path.read_text(encoding="utf-8")) for path in extraction.glob("*.json"))]
        findings = merge(EXTRACTORS["equipment_appliances"], sorted(rows, key=lambda row: row[:2]),
                         page_types={page: row.get("page_type") for page, row in readings.items()})
        (project_dir / "page_extraction.json").write_text(json.dumps({"equipment_appliances": {"findings": findings}}), encoding="utf-8")
    record = {"raw_record": {}}
    started = time.monotonic()
    raw = skills._call_case_file_provider(subskill, {"id": case_id, "review_dir": str(project_dir), "pdf": str(pdf)}, {}, {}, record)
    return raw, record, round(time.monotonic() - started, 1)


def items(proposal, field):
    rows = (proposal or {}).get("proposal_fields", {}).get(field, []) if isinstance(proposal, dict) else []
    return rows if isinstance(rows, list) else []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case_id")
    parser.add_argument("--subskill", default="equipment_evidence")
    args = parser.parse_args(argv)
    sources = json.loads((OUTPUT / "page_role_sources.json").read_text(encoding="utf-8"))
    pdf = Path(sources[args.case_id])
    subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == args.subskill)
    field = next(iter(subskill["proposal_fields"]))
    readings, renders = pass1_readings(args.case_id)
    base = OUTPUT / "skill_agentic" / args.case_id
    workspace = base / "workspace"
    build_workspace(workspace, args.case_id, pdf, subskill, readings, renders)

    print("Idea 2 (agent chooses pages)…", flush=True)
    code, reply, seconds, log = run_agentic(workspace, subskill)
    limit = pass1._usage_limit_message(log)
    try:
        agentic = json.loads(reply.strip().strip("`").removeprefix("json").strip()) if reply else None
    except json.JSONDecodeError:
        agentic = None
    print("Idea 1 (case file)…", flush=True)
    try:
        case_file, record, case_seconds = run_case_file(args.case_id, pdf, subskill, readings, renders)
        case_error = ""
    except Exception as error:  # reported, not hidden
        case_file, record, case_seconds, case_error = None, {}, 0, getattr(error, "detail", "") or str(error)
    summary = {
        "case_id": args.case_id, "subskill": args.subskill, "field": field,
        "agentic": {"exit_code": code, "seconds": seconds, "usage_limit": limit, "valid_json": agentic is not None,
                    "pages_viewed": (agentic or {}).get("pages_viewed"), "items": len(items(agentic, field)),
                    "image_views_in_transcript": log.count("view_image")},
        "case_file": {"seconds": case_seconds, "error": case_error, "items": len(items(case_file, field)),
                      "attached_pages": record.get("images") and [row["page"] for row in record["images"]],
                      "prompt_chars": len(record.get("prompt", ""))},
    }
    (base / "agentic_proposal.json").write_text(json.dumps(agentic, indent=2), encoding="utf-8")
    (base / "case_file_proposal.json").write_text(json.dumps(case_file, indent=2), encoding="utf-8")
    (base / "comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
