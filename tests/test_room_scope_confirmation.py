"""Card N: a reviewer confirms the room list before a draft cooling number."""

from copy import deepcopy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import room_scope_confirmation as scope
from ai import room_use_resolution
from backend import ai_preliminary_service
from backend import model_input_resolution_service
from backend import reviewer_room_geometry_service
from backend import room_use_resolution_service


class Web:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name

    def update_project(self, _project):
        return None


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def expect_error(name, call, fragment):
    try:
        call()
    except ValueError as error:
        check(name, fragment in str(error))
        return
    raise AssertionError(name + " (no error raised)")


def input_set():
    """Smallest preliminary input set shape the confirmation reads."""
    return {
        "input_fingerprint": "fixture",
        "material": {"hourly_load_model": {
            "floors": [{"floor_id": "f1", "name": "Level 2"}],
            "zones": [{"zone_id": "z-office", "floor_id": "f1"}, {"zone_id": "z-retail", "floor_id": "f1"}],
            "rooms": [{"room_id": "r-office", "name": "Office", "zone_id": "z-office", "area_m2": 9.0},
                      {"room_id": "r-retail", "name": "Retail space", "zone_id": "z-retail", "area_m2": 27.9}],
        }},
        "materialized_fields": [
            {"room_id": "r-office", "field": "area_m2", "value": 9.0, "origin": "pdf_evidence", "evidence": [{"page": 5}]},
            {"room_id": "r-retail", "field": "area_m2", "value": 27.9, "origin": "pdf_evidence", "evidence": [{"page": 25}]},
            {"room_id": "r-office", "field": "space_scope", "value": "comfort_hvac"},
            {"room_id": "r-retail", "field": "space_scope", "value": "comfort_hvac"},
        ],
        "review_queue": [{"room_id": "r-retail", "field": "area_m2"}],
        "exclusions": [],
        "excluded_spaces": [
            {"room_name": "Service Counter", "level": "Level 2", "scope": "unresolved_scope",
             "reason": "AI could not resolve whether this room belongs in comfort-HVAC scope.", "evidence": [{"page": 5}]},
            {"room_name": "Coolroom", "level": "Level 2", "scope": "refrigeration_process", "reason": "Refrigeration", "evidence": []},
        ],
    }


def keys(rows):
    return {row["label"]: row["key"] for row in rows}


def pure_checks():
    data = input_set()
    rows = scope.candidates(data)
    check("candidates list calculated rooms and rooms needing a use, not refrigeration rooms",
          [(row["label"], row["status"]) for row in rows]
          == [("Office", "calculated"), ("Retail space", "calculated"), ("Service Counter", "needs_use")])
    office = next(row for row in rows if row["label"] == "Office")
    check("candidate rows carry area, origin and source pages",
          office["area_m2"] == 9.0 and office["area_origin"] == "pdf_evidence" and office["source_pages"] == [5])
    check("unconfirmed state blocks the calculation", scope.state(data, None)["status"] == scope.NOT_CONFIRMED)
    expect_error("apply without confirmation is refused", lambda: scope.apply(data, None), "Confirm the room list")

    k = keys(rows)
    fp = scope.candidate_fingerprint(rows)
    good = {"reviewer": "QA", "candidate_fingerprint": fp, "rows": [
        {"key": k["Office"], "include": True},
        {"key": k["Retail space"], "include": False, "reason": "Render-page text, not a room"},
        {"key": k["Service Counter"], "include": False}]}
    expect_error("a reviewer is required", lambda: scope.confirm(data, None, {**good, "reviewer": " "}, "t"), "reviewer")
    expect_error("an included room without a use cannot be confirmed",
                 lambda: scope.confirm(data, None, {**good, "rows": [*good["rows"][:2], {"key": k["Service Counter"], "include": True}]}, "t"),
                 "Choose a use for Service Counter")
    expect_error("every room must be decided", lambda: scope.confirm(data, None, {**good, "rows": good["rows"][:2]}, "t"),
                 "Include or exclude every room")
    expect_error("excluding every room is refused",
                 lambda: scope.confirm(data, None, {**good, "rows": [{**row, "include": False} for row in good["rows"]]}, "t"),
                 "Include at least one room")
    expect_error("a list that changed while reviewing is refused",
                 lambda: scope.confirm(data, None, {**good, "candidate_fingerprint": "old"}, "t"), "changed while you were reviewing")

    confirmation = scope.confirm(data, None, good, "2026-10-03T00:00:00Z")
    check("confirmation defaults an empty exclusion reason",
          next(row for row in confirmation["rows"] if row["label"] == "Service Counter")["reason"] == "Excluded by reviewer")
    check("confirmed state is current", scope.state(data, confirmation)["status"] == scope.CONFIRMED)
    applied, confirmed_rooms, summary = scope.apply(data, confirmation)
    model = applied["material"]["hourly_load_model"]
    check("excluded room and its zone leave the model",
          [room["name"] for room in model["rooms"]] == ["Office"] and [zone["zone_id"] for zone in model["zones"]] == ["z-office"])
    check("excluded room is listed with the reviewer reason",
          any(space["room_name"] == "Retail space" and space["reason"] == "Excluded by reviewer: Render-page text, not a room"
              for space in applied["excluded_spaces"]))
    check("excluded unresolved room carries the reviewer reason",
          any(space["room_name"] == "Service Counter" and space["reason"].startswith("Excluded by reviewer")
              for space in applied["excluded_spaces"]))
    check("excluded room fields leave the ledger and review queue",
          not any(row.get("room_id") == "r-retail" for row in applied["materialized_fields"] + applied["review_queue"]))
    check("confirmed rooms and reviewer are reported",
          [row["label"] for row in confirmed_rooms] == ["Office"] and summary["reviewer"] == "QA")
    check("the original input set is not modified", len(data["material"]["hourly_load_model"]["rooms"]) == 2)

    changed = deepcopy(data)
    changed["material"]["hourly_load_model"]["rooms"][0]["area_m2"] = 12.0
    check("an area change makes the confirmation stale", scope.state(changed, confirmation)["status"] == scope.STALE)
    expect_error("a stale confirmation blocks the calculation", lambda: scope.apply(changed, confirmation), "confirm it again")
    added = deepcopy(data)
    added["material"]["hourly_load_model"]["rooms"].append({"room_id": "r-bar", "name": "Bar", "zone_id": "z-office", "area_m2": 5})
    check("a new room makes the confirmation stale", scope.state(added, confirmation)["status"] == scope.STALE)


def taxonomy_checks():
    categories = room_use_resolution.load_taxonomy()["categories"]
    check("taxonomy offers a not-a-room choice", categories["not_a_room"]["space_scope"] == "not_a_room")


def service_checks():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "room-scope", "name": "Room scope fixture", "review_dir": str(root)}
        spaces = [
            {"name": "Office", "level_name": "Level 1", "area": 40, "evidence": [{"page": 1}]},
            {"name": "Bar", "level_name": "Level 1", "area": 20, "evidence": [{"page": 1}]},
            {"name": "Plant room", "level_name": "Level 1", "area": 9.6, "evidence": [{"page": 1}]},
        ]
        rooms = [{"kind": "room", "label": row["name"], "level_name": "Level 1", "area_m2": row["area"], "page": 1} for row in spaces]
        (root / "building_evidence.json").write_text(json.dumps({"spaces": spaces}), encoding="utf-8")
        (root / "ai_preliminary_run.json").write_text(json.dumps({"manual_placeholder_proposal": {"rooms": rooms}}), encoding="utf-8")
        model_input_resolution_service.post(Web(), project, {"action": "resolve"})
        state = ai_preliminary_service.get(Web(), project)["room_scope"]
        status = {row["label"]: row["status"] for row in state["candidates"]}
        check("service lists calculated rooms and the plant room needing a use",
              status == {"Bar": "calculated", "Office": "calculated", "Plant room": "needs_use"})
        check("service offers the room-use choices including not a room", state["uses"].get("not_a_room", "").startswith("Not a room"))
        expect_error("service refuses to calculate before confirmation",
                     lambda: ai_preliminary_service.post(Web(), project, {"action": "calculate"}), "Confirm the room list")

        plant = next(row for row in state["candidates"] if row["label"] == "Plant room")
        room_use_resolution_service.post(Web(), project, {"action": "apply_override", "room_id": plant["key"],
                                                         "taxonomy_id": "not_a_room", "reviewer": "QA"})
        ai_preliminary_service.post(Web(), project, {"action": "assemble"})
        state = ai_preliminary_service.get(Web(), project)["room_scope"]
        check("a not-a-room override removes the detection from the confirmation list",
              sorted(row["label"] for row in state["candidates"]) == ["Bar", "Office"])
        traced = reviewer_room_geometry_service._rooms(reviewer_room_geometry_service._paths(project))
        check("a not-a-room detection is not offered for tracing", "Plant room" not in {row["label"] for row in traced})
        registry = reviewer_room_geometry_service.current_artifact_input(root)
        check("the room registry lists the excluded identity for the calculator draft",
              plant["key"] in registry.get("excluded_room_identities", []))

        k = keys(state["candidates"])
        ai_preliminary_service.post(Web(), project, {
            "action": "confirm_room_scope", "reviewer": "QA", "candidate_fingerprint": state["candidate_fingerprint"],
            "rows": [{"key": k["Office"], "include": True}, {"key": k["Bar"], "include": False, "reason": "Outside the tenancy"}]})
        report = ai_preliminary_service.post(Web(), project, {"action": "calculate"})["hourly_ai_preliminary_load_report"]
        check("the draft is calculated from the confirmed rooms only",
              [row["label"] for row in report["confirmed_rooms"]] == ["Office"]
              and [room["name"] for room in report["scenario_results"][0]["rooms"]] == ["Office"])
        check("the excluded room appears with the reviewer reason",
              any(row["room_name"] == "Bar" and row["reason"] == "Excluded by reviewer: Outside the tenancy"
                  for row in report["refrigeration_process_exclusions"]))
        check("the confirmation is recorded on the report", report["room_scope_confirmation"]["reviewer"] == "QA")


def main():
    pure_checks()
    taxonomy_checks()
    service_checks()


if __name__ == "__main__":
    main()
