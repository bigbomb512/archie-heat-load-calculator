#!/usr/bin/env python3
"""Project persistence checks for the Australian ventilation rules context."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import au_ventilation_rules_service as service
from ai.design_requirements import empty_design_requirements, empty_zone, validate_design_requirements


class LocalWeb:
    def update_project(self, _project):
        return None


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "rules-context-test", "review_dir": str(root)}
        (root / "site_location_resolution.json").write_text(json.dumps({
            "location": {"state": "NSW", "locality": "Sydney"}, "fingerprint": "location-fingerprint",
        }), encoding="utf-8")
        saved = service.post(LocalWeb(), project, {"project_regulatory_context": {
            "building_approval_application_date": "2025-03-15", "building_class": "class_6",
            "building_use": "Hospitality", "project_specific_ventilation_basis": "",
        }})
        assert saved["project_regulatory_context"]["building_approval_application_date"] == "2025-03-15"
        assert saved["ventilation_rules_resolution"]["jurisdiction"] == "NSW"
        assert saved["ventilation_rules_resolution"]["status"] == "needs_review"
        assert "not been reviewed" in " ".join(saved["ventilation_rules_resolution"]["conflicts"])
        assert (root / "project_regulatory_context.json").exists()
        assert (root / "ventilation_rules_resolution.json").exists()
        print("PASS - project context and rules resolution persist locally with jurisdiction and explicit coverage gap")

        current = service.get(LocalWeb(), project)
        assert current["project_regulatory_context"]["building_class"] == "class_6"
        assert current["ruleset_status"] == "candidate"
        print("PASS - current rules preview is recomputed from project context without claiming released coverage")

        try:
            service.post(LocalWeb(), project, {"project_regulatory_context": {
                "building_approval_application_date": "15/03/2025", "building_class": "class_6",
            }})
            raise AssertionError("non-ISO application date was accepted")
        except ValueError:
            print("PASS - invalid application date is rejected")

        zone = empty_zone()
        zone.update({"zone_id": "dining", "name": "Dining", "usage": "Dining", "area_m2": 40, "occupancy": 20})
        zone["cooling_load"].update({"outside_air_lps": 40, "source": "Outside-air flow from retired-rule: old citation"})
        vent = zone["ventilation_requirements"]
        vent.update({"outside_air_method": "combined", "people_rate_lps_per_person": 2.0, "area_rate_lps_per_m2": 0.3,
                     "basis_name": "Old NCC / AS edition", "basis_source": "Old clause", "source": "Old source",
                     "verification_status": "confirmed", "rule_id": "retired-rule", "rule_pack_version": "0.9.0",
                     "rule_citation": "Old source", "rule_applied_values": {
                         "previous": {"outside_air_method": "combined", "people_rate_lps_per_person": None, "area_rate_lps_per_m2": None,
                                      "fixed_minimum_lps": None, "basis_name": "", "basis_source": "", "source": "",
                                      "verification_status": "missing", "cooling_load.outside_air_lps": None, "cooling_load.source": ""},
                         "applied": {"outside_air_method": "combined", "people_rate_lps_per_person": 2.0, "area_rate_lps_per_m2": 0.3,
                                     "fixed_minimum_lps": None, "basis_name": "Old NCC / AS edition", "basis_source": "Old clause",
                                     "source": "Old source", "verification_status": "confirmed",
                                     "cooling_load.outside_air_lps": 40, "cooling_load.source": "Outside-air flow from retired-rule: old citation"},
                     }})
        requirements = empty_design_requirements()
        requirements["zones"] = [zone]
        (root / "design_requirements.json").write_text(json.dumps(validate_design_requirements(requirements)), encoding="utf-8")
        (root / "project_regulatory_context.json").write_text(json.dumps({
            "building_approval_application_date": "2025-03-15", "building_class": "class_6",
            "building_use": "Hospitality", "project_specific_ventilation_basis": "",
        }), encoding="utf-8")
        refreshed = service.refresh_requirements_for_context(LocalWeb(), project)
        saved_zone = refreshed["requirements"]["zones"][0]
        assert saved_zone["ventilation_requirements"]["rule_id"] == ""
        assert saved_zone["ventilation_requirements"]["people_rate_lps_per_person"] is None
        assert saved_zone["cooling_load"]["outside_air_lps"] is None
        print("PASS - context change restores unedited prior auto-filled values while invalidating their source rule")


if __name__ == "__main__":
    main()
