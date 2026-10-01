"""Focused lifecycle checks for the local Codex preliminary-model handoff."""

import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import ai_preliminary_service


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


class LocalWeb:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name

    def update_project(self, _project):
        return None


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "project-local-codex", "name": "Local Codex fixture", "review_dir": str(root)}
        web = LocalWeb()
        write(root / "building_evidence.json", {
            "spaces": [{"name": "Dining", "level_name": "Level 1", "area": "48 m2", "evidence": [{"page": 2, "excerpt": "Dining"}]}],
            "source_pdf": "/private/source.pdf",
        })
        prepared = ai_preliminary_service.post(web, project, {"action": "prepare_codex_handoff"})
        handoff_path = root / "codex_preliminary_handoff.json"
        response_path = root / "codex_preliminary_response.json"
        check("local Codex handoff writes request and response files", handoff_path.exists() and response_path.exists())
        check("local Codex handoff exposes opaque local artifact links", prepared["artifact_links"]["codex_handoff"] == "/artifact/codex_preliminary_handoff.json")
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        response = json.loads(response_path.read_text(encoding="utf-8"))
        response["proposal"] = {"rooms": [{
            "kind": "room", "label": "Dining", "level_name": "Level 1", "area_m2": 48,
            "preliminary_profile_id": "hospitality", "confidence": 0.8, "page": 2,
        }], "surfaces": [], "openings": [], "issues": []}
        write(response_path, response)
        applied = ai_preliminary_service.post(web, project, {"action": "apply_codex_response"})
        check("bound local Codex response becomes a draft-only proposal", applied["run"]["status"] == "local_codex_response_ready")
        write(root / "building_evidence.json", {"spaces": [{"name": "Dining", "level_name": "Level 1", "area": "49 m2", "evidence": [{"page": 2}]}]})
        try:
            ai_preliminary_service.post(web, project, {"action": "apply_codex_response"})
        except ValueError as error:
            check("changed evidence makes a local Codex handoff stale", "stale" in str(error))
        else:
            raise AssertionError("changed evidence must reject the local Codex response")
        check("handoff source fingerprint remains explicit", bool(handoff["source_fingerprints"]))


if __name__ == "__main__":
    main()
