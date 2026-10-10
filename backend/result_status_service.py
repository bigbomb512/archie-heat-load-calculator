"""What in a job's result is still assumed, a placeholder, or still to find, so a draft is never taken as final.

Each item says what it is, what kind of gap it is, and which tab fixes it:
- placeholder  a generic value waiting for the licensed source (AIRAH DA09 weather and appliance data);
- typical      a typical value used because the drawings and answers don't give one (room densities, ratings);
- assumed      a method chosen by default (e.g. kitchen exhaust replaced through the conditioned space);
- to_find      an item on the What-we-need-to-find list without an answer, or a finding still to review;
- not_set      an input the calculation needs that nobody has set (site address, what is above the tenancy).
Read-only: it gathers from the result and the answer files and changes nothing.
"""

import json
from pathlib import Path

from ai.equipment_heat import PLACEHOLDER, from_data_sheet, heat_w, proposal as heat_proposal

KITCHEN_TYPES = {"cooking", "dishwashing", "hot_drinks"}
ORDER = {"to_find": 0, "not_set": 1, "assumed": 2, "typical": 3, "placeholder": 4}
# The calculation uses a room's own value only from these origins; anything else falls back to the room type's typical
# value (ai.ai_preliminary._resolved_internal_value), so it is listed as typical.
EVIDENCE_ORIGINS = {"direct_project_evidence", "contractor_override", "project_evidence", "ai_interpretation", "ai_estimated"}


def _read(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _mentions(value, text):
    """Whether a name containing text appears anywhere in a report section (e.g. an equipment line's name)."""
    if isinstance(value, dict):
        return any(_mentions(item, text) for item in value.values())
    if isinstance(value, list):
        return any(_mentions(item, text) for item in value)
    return isinstance(value, str) and text in value


def _item(kind, topic, text, tab, detail=""):
    return {"kind": kind, "topic": topic, "text": text, "tab": tab, "detail": detail}


def gather(web, project):
    root = Path(project["review_dir"])
    report = _read(root / "hourly_ai_preliminary_load_report.json", {})
    items = []

    # Design conditions (the report says whether they are site-specific).
    basis = report.get("design_conditions_basis") or {}
    if basis.get("design_day") and not basis["design_day"].get("site_specific"):
        items.append(_item("placeholder", "Weather", "Generic Australian design day, not specific to this site (AIRAH DA09 pending).", "project"))
    elif (basis.get("design_day") or {}).get("origin") == "design_temperature_table_nearest_location":
        day = basis["design_day"]
        items.append(_item("assumed", "Weather", f"Design days for {day.get('location', '')}, the nearest listed location "
                                                 f"({day.get('distance_km', 0):g} km), from the AIRAH Technical Handbook (2007)"
                                                 + ("; it is far from the site, so check it suits." if day.get("far_from_site") else "."),
                           "project", day.get("label", "")))
    if basis.get("sun") and not basis["sun"].get("site_specific"):
        items.append(_item("placeholder", "Sun", "Generic sun values by façade direction, not specific to this site.", "project"))
    if basis.get("site") and not basis["site"].get("confirmed"):
        items.append(_item("not_set", "Site", "The site address isn't confirmed.", "project"))

    # Room inputs still on typical values.
    gains = _read(root / "internal_gains_resolution.json", {})
    labels = {"occupancy_count": "people (typical density)", "lighting_load_w": "lighting (typical W/m²)",
              "equipment": "equipment (typical W/m² allowance)"}
    for record in gains.get("records", []):
        if not isinstance(record, dict) or record.get("status") in {"excluded", "stale"}:
            continue
        fields = record.get("fields") or {}
        typical = [label for name, label in labels.items() if (fields.get(name) or {}).get("origin") not in EVIDENCE_ORIGINS]
        if typical:
            items.append(_item("typical", record.get("original_label", "Room"), "Uses " + ", ".join(typical) + ".", "rooms"))
    for schedule in gains.get("schedules", []):
        if isinstance(schedule, dict) and schedule.get("origin") == "controlled_preliminary_profile":
            room = next((row.get("original_label") for row in gains.get("records", []) if row.get("room_id") == schedule.get("room_id")), "")
            if room and not any(row.get("original_label") == room and row.get("status") == "excluded" for row in gains.get("records", [])):
                items.append(_item("typical", room, "Opening hours are typical for the room type, not the business's own.", "drawings"))

    # Rooms whose listed equipment is below their typical allowance and were topped up to it.
    for room in ((report.get("scenario_results") or [{}])[0].get("rooms") or []):
        if isinstance(room, dict) and _mentions(room.get("hours"), "Typical equipment allowance top-up"):
            items.append(_item("typical", room.get("name", "Room"), "Equipment is topped up to the typical allowance: the listed items "
                                                                   "give less heat than a typical room of this kind.", "drawings"))

    # Equipment accepted with typical ratings or the generic heat-to-room factors.
    equipment = _read(root / "page_extraction.json", {}).get("equipment_appliances", {})
    decisions = _read(root / "page_extraction_decisions.json", {}).get("equipment_appliances", {})
    typical_rating, generic_factor, uncounted = [], [], []
    for finding in equipment.get("findings", []):
        decision = decisions.get(finding.get("id"), {})
        if decision.get("run") != equipment.get("run"):
            decision = {}
        value, suggested = decision.get("value") or finding.get("value") or {}, heat_proposal(finding.get("value") or {})
        name = f"{value.get('code') + ' ' if value.get('code') else ''}{value.get('name', '')}".strip()
        if decision.get("status") != "accepted" or heat_w(value) is None:
            # Kitchen appliances read from the drawings but not in the calculation: their heat needs the data sheet.
            if decision.get("status") != "rejected" and suggested["type"] in KITCHEN_TYPES:
                uncounted.append(name)
            continue
        if from_data_sheet(value):
            continue                                  # its heat is the data sheet's figure, not a typical value
        if suggested["rated_source"] == PLACEHOLDER and value.get("rated_input_w") == suggested["rated_input_w"]:
            typical_rating.append(name)
        if value.get("heat_to_space_factor") == suggested["heat_to_space_factor"]:
            generic_factor.append(name)
    if uncounted:
        items.append(_item("to_find", "Kitchen equipment", f"{len(uncounted)} kitchen appliance(s) on the drawings aren't in the "
                                                           "calculation: enter each one's heat to the room from its data sheet "
                                                           "(or its rated power and heat-to-room factor), or reject it.",
                           "drawings", ", ".join(uncounted[:12])))
    if typical_rating:
        items.append(_item("typical", "Equipment", f"{len(typical_rating)} item(s) use a typical rating, not a spec-sheet one.", "drawings",
                           ", ".join(typical_rating[:12])))
    if generic_factor:
        items.append(_item("placeholder", "Equipment", f"{len(generic_factor)} item(s) use the generic heat-to-room factor (AIRAH DA09 pending).",
                           "drawings", ", ".join(generic_factor[:12])))

    # Methods assumed by default.
    for room, answer in (_read(root / "exhaust_answers.json", {}).get("rooms") or {}).items():
        if isinstance(answer, dict) and answer.get("method_assumed"):
            items.append(_item("assumed", room, f"Kitchen exhaust {answer.get('lps', 0):g} L/s assumed replaced through the "
                                                "conditioned space (the make-up air arrangement wasn't given).", "drawings"))

    # Walls to unconditioned spaces without the temperature beyond them: not counted until answered.
    waiting = sorted({row.get("room_name") or "A room" for row in report.get("unresolved_room_inputs") or []
                      if isinstance(row, dict) and str(row.get("component_id", "")).startswith("unconditioned_wall_temperature")})
    if waiting:
        items.append(_item("to_find", "Walls to unconditioned spaces", "Walls marked as facing an unconditioned space aren't counted: "
                                                                        "answer that space's design temperature (What we need to find).",
                           "drawings", ", ".join(waiting)))

    # Walls and roof on the preliminary U-values (no construction answered).
    components = (report.get("included_scope_peak") or {}).get("components") or {}
    if (components.get("envelope") or {}).get("total_kw"):
        constructions = _read(root / "construction_answers.json", {})
        unanswered = [label for surface, label in (("wall", "external walls"), ("roof", "exposed roof"))
                      if not ((constructions.get(surface) or {}).get("all") or (constructions.get(surface) or {}).get("rooms"))]
        if unanswered:
            from ai.ai_preliminary import load_pack
            opaque = load_pack()["preliminary_envelope"]["opaque_constructions"]
            items.append(_item("typical", "Walls and roof", f"The {' and '.join(unanswered)} use the preliminary U-value (walls "
                                                            f"{opaque['wall']['u_value_w_m2k']:g}, roof {opaque['roof']['u_value_w_m2k']:g} W/m²K): "
                                                            "answer the construction to use its own.", "drawings"))

    # Windows on generic glass.
    glazing = _read(root / "glazing_answers.json", {})
    if (components.get("glazing_solar") or components.get("glazing_conduction")) and not (glazing.get("all") or glazing.get("rooms")):
        items.append(_item("typical", "Windows", "Glass is generic single glazing (U 5.8, SHGC 0.45); its real type isn't known.", "drawings"))
    elif components.get("glazing_solar") and glazing.get("rooms") and not glazing.get("all"):
        items.append(_item("typical", "Windows", "Windows outside " + ", ".join(sorted(glazing["rooms"])) + " use generic single glazing.", "drawings"))

    # What's still to find, and findings still to review.
    try:
        from backend import skill_workflow_service
        review = skill_workflow_service.get(web, project)
    except Exception:
        review = {}
    answers = review.get("answers") or {}
    open_needs = [row for row in review.get("findings", []) if row.get("subskill_id") == "information_needs" and row.get("field") == "needs"
                  and isinstance(row.get("value"), dict) and row["id"] not in answers
                  and not any(item.get("target") == row["value"].get("target") and item.get("field") == row["value"].get("field") for item in answers.values())]
    for row in open_needs:
        items.append(_item("to_find", row["value"].get("target", ""), f"{str(row['value'].get('field', '')).replace('_', ' ')}: "
                                                                      f"{row['value'].get('why', '')}", "drawings", row["value"].get("where_to_look", "")))
    open_findings = [row for row in review.get("findings", []) if row.get("subskill_id") != "information_needs"
                     and row.get("status") not in {"accepted", "rejected"}]
    if open_findings:
        items.append(_item("to_find", "PDF review", f"{len(open_findings)} finding(s) from the drawings are still to review; they aren't in the number.", "drawings"))
    if equipment.get("findings"):
        unreviewed = sum(1 for finding in equipment["findings"] if decisions.get(finding.get("id"), {}).get("run") != equipment.get("run"))
        if unreviewed:
            items.append(_item("to_find", "Equipment", f"{unreviewed} equipment item(s) found in the drawings are still to review; they aren't in the number.", "drawings"))

    # What is above the tenancy.
    try:
        from backend import job_service
        above = job_service.job_setup(project).get("above", "")
    except Exception:
        above = ""
    if above in ("", "not_sure"):
        items.append(_item("not_set", "Roof", "What is above the tenancy isn't answered, so roof heat isn't assessed.", "walls"))

    items.sort(key=lambda row: (ORDER[row["kind"]], row["topic"].casefold()))
    counts = {kind: sum(1 for row in items if row["kind"] == kind) for kind in ORDER}
    return {"items": items, "counts": counts, "calculated": bool(report)}
