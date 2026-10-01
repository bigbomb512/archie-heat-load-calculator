"""Lifecycle checks for the project-local value-resolution API service."""

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
        project = {"id": "resolution-project", "name": "Resolver fixture", "review_dir": str(root)}
        web = LocalWeb()
        write(root / "building_evidence.json", {"spaces": [{"name": "Dining", "level_name": "Level 1", "area": 60,
                                                               "evidence": [{"page": 2, "excerpt": "Dining"}]}]})
        proposal = {"rooms": [{"kind": "room", "label": "Dining", "level_name": "Level 1", "area_m2": 60,
                               "preliminary_profile_id": "hospitality", "confidence": 0.8, "page": 2}],
                    "surfaces": [], "openings": [], "issues": []}
        ai_preliminary_service.post(web, project, {"action": "save_placeholder_proposal", "placeholder_proposal": proposal})
        resolved = ai_preliminary_service.post(web, project, {"action": "resolve_from_packs", "research_consent": False})
        path = root / "value_resolution.json"
        check("resolution action writes a project-local source artifact", path.exists() and resolved["value_resolution"]["records"])
        check("resolution remains separate from reviewed calculator artifacts", not (root / "calculator_input_set.json").exists())
        try:
            ai_preliminary_service.post(web, project, {"action": "queue_missing_source_research", "research_consent": False})
        except ValueError:
            blocked = True
        else:
            blocked = False
        check("source lookup jobs require opt-in", blocked)
        queued = ai_preliminary_service.post(web, project, {"action": "queue_missing_source_research", "research_consent": True})
        check("consented lookup queue stores manifests rather than fetching", queued["value_resolution"]["research_jobs"] and queued["value_resolution"]["research_consent"])
        rebuilt = ai_preliminary_service.post(web, project, {"action": "rebuild_preliminary_model"})
        check("rebuild produces only a draft preliminary input set", rebuilt["input_set"]["status"] == "draft" and rebuilt["input_set"]["value_resolution"]["records"])
        check("resolver artifact has an opaque download link", rebuilt["artifact_links"]["value_resolution"] == "/artifact/value_resolution.json")


if __name__ == "__main__":
    main()
