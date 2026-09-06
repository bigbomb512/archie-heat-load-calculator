#!/usr/bin/env python3

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.web_app as web_app
from ai.site_design_conditions import empty_site_design_conditions, site_design_conditions_summary, validate_site_design_conditions


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def field(value, status="confirmed", source="Engineer design-condition schedule"):
    return {"value": value, "status": status, "source": source, "citations": [{"reference": source, "page": None, "excerpt": ""}]}


def complete_packet(status="confirmed"):
    return {
        "site": {
            "project_name": field("Example retail tenancy", status),
            "address": field("1 Example Street, Sydney NSW", status),
            "location_description": field("Sydney CBD", status),
            "weather_station_reference": field("Engineer-selected Sydney station", status),
            "elevation_m": field(30, status),
            "north_orientation_note": field("North shown on architectural site plan", status),
        },
        "design_basis": {
            "name": "Engineer design conditions",
            "reference_version_or_date": "2026-09-04",
            "status": status,
            "source": "Mechanical engineer design brief",
            "citations": [{"reference": "Mechanical engineer design brief", "page": 2, "excerpt": "Design conditions"}],
        },
        "summer": {
            "outdoor_dry_bulb_c": field(35, status),
            "outdoor_wet_bulb_c": field(24, status),
            "indoor_dry_bulb_c": field(24, status),
            "indoor_relative_humidity_percent": field(50, status),
            "atmospheric_pressure_kpa": field(101.325, status),
        },
        "winter": {
            "outdoor_dry_bulb_c": field(5, status),
            "outdoor_relative_humidity_percent": field(80, status),
            "indoor_dry_bulb_c": field(20, status),
            "indoor_relative_humidity_percent": field(50, status),
        },
    }


class Request:
    def __init__(self, body, path="/api/site-design-conditions?project_id=project-1"):
        self.path = path
        self.headers = {"Content-Length": str(len(body.encode("utf-8")))}
        from io import BytesIO
        self.rfile = BytesIO(body.encode("utf-8"))


def main():
    empty = empty_site_design_conditions()
    check("empty packet needs review", site_design_conditions_summary(empty)["status"] == "review_required")

    confirmed = validate_site_design_conditions(complete_packet())
    check("confirmed packet is ready", site_design_conditions_summary(confirmed)["status"] == "confirmed")

    provisional = validate_site_design_conditions(complete_packet("provisional"))
    provisional_summary = site_design_conditions_summary(provisional)
    check("provisional packet requires review", provisional_summary["status"] == "review_required" and provisional_summary["completion_status"] == "ready_for_engineer_confirmation" and provisional_summary["requires_engineer_review"])

    missing_source = complete_packet()
    missing_source["summer"]["outdoor_dry_bulb_c"]["source"] = ""
    try:
        validate_site_design_conditions(missing_source)
        raise AssertionError("confirmed field without source should fail")
    except ValueError as error:
        check("missing source rejected", "needs a source" in str(error))

    invalid_range = complete_packet()
    invalid_range["winter"]["indoor_relative_humidity_percent"]["value"] = 120
    try:
        validate_site_design_conditions(invalid_range)
        raise AssertionError("invalid humidity should fail")
    except ValueError as error:
        check("invalid numeric range rejected", "between 0 and 100" in str(error))

    invalid_wet_bulb = complete_packet()
    invalid_wet_bulb["summer"]["outdoor_wet_bulb_c"]["value"] = 36
    try:
        validate_site_design_conditions(invalid_wet_bulb)
        raise AssertionError("wet bulb above dry bulb should fail")
    except ValueError as error:
        check("invalid DB/WB rejected", "cannot exceed" in str(error))

    originals = web_app.project_by_id, web_app.update_project
    try:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            requirements_path = root / "design_requirements.json"
            requirements_path.write_text('{"sentinel": "unchanged"}', encoding="utf-8")
            project = {"id": "project-1", "review_dir": str(root), "updated_at": "before"}
            updates = []
            web_app.project_by_id = lambda project_id: project if project_id == "project-1" else None
            web_app.update_project = lambda saved: updates.append(dict(saved))
            body = json.dumps({"project_id": "project-1", "site_design_conditions": complete_packet()})
            saved = web_app.api_save_site_design_conditions(Request(body))
            packet_path = root / "site_design_conditions.json"
            check("API persists dedicated artifact", packet_path.exists() and saved["url"].endswith("site_design_conditions.json"))
            check("API returns confirmed readiness", saved["readiness"]["status"] == "confirmed")
            check("save does not change design requirements", requirements_path.read_text(encoding="utf-8") == '{"sentinel": "unchanged"}')
            check("project stores dedicated artifact", updates[-1]["site_design_conditions"] == str(packet_path))

            loaded = web_app.api_site_design_conditions(Request("", "/api/site-design-conditions?project_id=project-1"))
            check("API retrieves saved packet", loaded["site_design_conditions"]["site"]["address"]["value"] == "1 Example Street, Sydney NSW")

            packet_path = root / "packet.json"
            packet_path.write_text(json.dumps({"primary_pages": [], "reference_pages": [], "discarded_pages": []}), encoding="utf-8")
            analysis = web_app.analysis_response({
                "id": "project-1", "name": "example.pdf", "pages": 1,
                "packet": str(packet_path), "review_dir": str(root),
                "site_design_conditions": str(root / "site_design_conditions.json"),
            })
            check("analysis exposes site-condition discovery", analysis["site_design_conditions_url"].endswith("site_design_conditions.json") and analysis["site_design_conditions_status"] == "confirmed")
    finally:
        web_app.project_by_id, web_app.update_project = originals


if __name__ == "__main__":
    main()
