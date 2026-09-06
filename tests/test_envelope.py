#!/usr/bin/env python3

"""Focused checks for reviewed envelope artifacts and cooling normalization."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.envelope import (
    apply_reviewed_envelope_to_requirements,
    empty_envelope_library,
    migrate_legacy_envelope,
    normalize_surfaces,
    validate_envelope_library,
    validate_envelope_model,
)
from ai.heat_loads import envelope_load, solar_load
import backend.web_app as web_app


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def library():
    return validate_envelope_library({"constructions": [
        {"record_id": "wall-a", "title": "Reviewed wall", "revision": 2, "kind": "opaque_wall", "u_value_w_m2k": 0.5, "absorptivity": 0.7, "review_status": "confirmed", "source": "Engineer reviewed schedule", "citations": []},
        {"record_id": "partition-a", "title": "Reviewed partition", "revision": 1, "kind": "partition", "u_value_w_m2k": 1.0, "absorptivity": None, "review_status": "confirmed", "source": "Engineer reviewed schedule", "citations": []},
    ]})


def model():
    return {
        "active_for_calculation": True,
        "surfaces": [
            {"surface_id": "wall-n", "owner_zone_id": "zone_001", "kind": "opaque_wall", "orientation": "N", "area_m2": 10, "construction_id": "wall-a", "window_id": "", "shading_record_ids": [], "boundary_method": "external", "review_status": "confirmed", "source": "Reviewed drawings", "citations": [], "manual_solar": {"enabled": True, "solar_design_w_m2": 300, "solar_gain_factor": 0.5, "shading_factor": 0.8, "review_status": "confirmed", "source": "Reviewed manual solar basis", "citations": []}},
            {"surface_id": "partition-east", "owner_zone_id": "zone_001", "kind": "partition", "orientation": "internal", "area_m2": 12, "construction_id": "partition-a", "window_id": "", "shading_record_ids": [], "boundary_method": "fixed_adjacent_temperature", "adjacent_temperature_c": 30, "review_status": "confirmed", "source": "Reviewed adjacent room basis", "citations": [], "manual_solar": {"enabled": False}},
        ],
    }


def requirements():
    return {"zones": [{"zone_id": "zone_001", "cooling_load": {"envelope_surfaces": [{"surface_id": "legacy", "area_m2": 99}], "envelope_not_applicable": False}}]}


class Request:
    def __init__(self, body, path):
        self.path = path
        self.headers = {"Content-Length": str(len(body.encode("utf-8")))}
        self.rfile = BytesIO(body.encode("utf-8"))


def main():
    reviewed_library = library()
    reviewed_model = validate_envelope_model(model(), reviewed_library)
    included, blocked, stored = normalize_surfaces(reviewed_library, reviewed_model)
    check("reviewed opaque external and adjacent surfaces are eligible", len(included) == 2 and not blocked and not stored)
    conduction = envelope_load(included, 35, 24)
    expected = (10 * 0.5 * (35 - 24) + 12 * 1.0 * (30 - 24)) / 1000
    check("external and fixed adjacent boundary temperatures calculate independently", conduction["total_kw"] == round(expected, 4) and conduction["inputs"]["surfaces"][1]["boundary_temperature_c"] == 30)
    check("manual solar retains its reviewed basis", solar_load(included)["total_kw"] == 1.2)

    base = requirements()
    migrated_library, migrated_model = migrate_legacy_envelope(base)
    check("legacy migration does not alter requirements", base == requirements())
    check("legacy migration remains inactive and provisional", not migrated_model["active_for_calculation"] and migrated_model["surfaces"][0]["review_status"] == "provisional")
    unchanged, envelope_state = apply_reviewed_envelope_to_requirements(base, migrated_library, migrated_model)
    check("inactive reviewed model preserves legacy surface path", envelope_state["source"] == "legacy" and unchanged["zones"][0]["cooling_load"]["envelope_surfaces"][0]["surface_id"] == "legacy")

    provisional = deepcopy(model())
    provisional["surfaces"][0]["review_status"] = "provisional"
    eligible, blocked, stored = normalize_surfaces(reviewed_library, validate_envelope_model(provisional, reviewed_library))
    check("provisional surface cannot silently contribute", len(eligible) == 1 and blocked[0]["reason"] == "surface review status is not confirmed")

    glazing = deepcopy(model())
    glazing["surfaces"][0]["kind"] = "glazing"
    glazing["surfaces"][0]["window_id"] = ""
    eligible, blocked, stored = normalize_surfaces(reviewed_library, validate_envelope_model(glazing, reviewed_library))
    check("glazing is stored but not calculated", len(eligible) == 1 and len(stored) == 1 and "detailed glazing" in stored[0]["reason"])

    unsupported = deepcopy(model())
    unsupported["surfaces"][0]["boundary_method"] = "outdoor_offset"
    eligible, blocked, stored = normalize_surfaces(reviewed_library, validate_envelope_model(unsupported, reviewed_library))
    check("unsupported boundary methods are blocked", len(eligible) == 1 and "unsupported boundary method" in blocked[0]["reason"])

    try:
        validate_envelope_model({"active_for_calculation": True, "surfaces": [{"surface_id": "bad", "owner_zone_id": "zone_001", "kind": "partition", "orientation": "internal", "area_m2": 1, "construction_id": "partition-a", "boundary_method": "fixed_adjacent_temperature", "review_status": "confirmed", "source": "x", "citations": [], "manual_solar": {"enabled": False}}]}, reviewed_library)
        raise AssertionError("fixed adjacent temperature should be required")
    except ValueError as error:
        check("fixed adjacent boundary requires temperature", "adjacent temperature" in str(error))

    originals = web_app.project_by_id, web_app.update_project
    try:
        with TemporaryDirectory() as tmp:
            project = {"id": "p1", "review_dir": tmp, "updated_at": "before"}
            (Path(tmp) / "design_requirements.json").write_text(json.dumps({"zones": [{"zone_id": "zone_001"}]}), encoding="utf-8")
            web_app.project_by_id = lambda project_id: project if project_id == "p1" else None
            web_app.update_project = lambda saved: None
            saved_library = web_app.api_save_envelope_library(Request(json.dumps({"project_id": "p1", "envelope_library": library()}), "/api/envelope-library"))
            saved_model = web_app.api_save_envelope_model(Request(json.dumps({"project_id": "p1", "envelope_model": model()}), "/api/envelope-model"))
            read_model = web_app.api_envelope_model(Request("", "/api/envelope-model?project_id=p1"))
            check("envelope API persists versioned project artifacts", saved_library["url"].endswith("envelope_library.json") and saved_model["url"].endswith("envelope_model.json") and read_model["envelope_model"]["active_for_calculation"])
    finally:
        web_app.project_by_id, web_app.update_project = originals


if __name__ == "__main__":
    main()
