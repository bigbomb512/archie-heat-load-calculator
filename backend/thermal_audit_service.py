"""Read-only thermal audit over a precomputed preliminary report and its element sidecar.

The sidecar is captured before the normal report compaction. No load is recalculated here.
"""
import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

COMPONENTS = ("envelope", "solar", "glazing_conduction", "glazing_solar")
SCHEMA_VERSION = 1
TOLERANCE_KW = 0.002  # summed 4-decimal surface rows versus rounded component totals


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


@lru_cache(maxsize=6)
def _read_current(path, modified_ns, size):
    """Cache immutable report/model/sidecar JSON for rapid hourly scrubbing; file changes change the key."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _heavy(path):
    path = Path(path)
    try:
        stat = path.stat()
        return _read_current(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        return {}


def _number(value):
    return round(float(value), 6) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def build_elements(report):
    """Capture every available component row, including stored-heat element loads, before slimming."""
    scenarios = []
    for scenario in report.get("scenario_results", []):
        rooms = []
        for room in scenario.get("rooms", []):
            hours = []
            for hour in room.get("hours", []):
                elements = []
                totals = {}
                for name in COMPONENTS:
                    component = hour.get("components", {}).get(name) or {}
                    if not component:
                        continue
                    totals[name] = _number(component.get("sensible_kw"))
                    for input_row in component.get("input_rows", []):
                        rows = input_row.get("surfaces") if name in {"envelope", "solar"} else [input_row]
                        for row in rows or []:
                            element_id = row.get("surface_id")
                            if not element_id:
                                continue
                            # The engine attaches per-surface cooling load when stored heat is active.
                            # Otherwise the surface gain itself is the load. A missing stored-heat
                            # breakdown is unknown, never allocated in proportion to gain.
                            load = row.get("cooling_load_kw")
                            if load is None and name in {"glazing_conduction", "glazing_solar"}:
                                load = input_row.get("contribution_sensible_kw")
                            if load is None and "heat_gain_sensible_kw" not in component:
                                load = row.get("gain_kw")
                            elements.append({"id": element_id, "component": name,
                                             "gain_kw": _number(row.get("gain_kw") if name in {"envelope", "solar"}
                                                                else (input_row.get("stored_heat") or {}).get("heat_gain_sensible_kw",
                                                                     input_row.get("contribution_sensible_kw"))),
                                             "earlier_hour_load_kw": _number(row.get("earlier_hour_load_kw") if name in {"envelope", "solar"}
                                                                            else (input_row.get("stored_heat") or {}).get("earlier_hour_load_kw")),
                                             "load_kw": _number(load),
                                             "missing_load_reason": None if load is not None else "Element cooling-load split was not recorded by the calculation.",
                                             "boundary_temperature_c": _number(row.get("boundary_temperature_c")),
                                             "incident_solar_w_m2": _number(row.get("incident_solar_w_m2")),
                                             "solar_design_w_m2": _number(row.get("solar_design_w_m2")),
                                             "solar_gain_factor": _number(row.get("solar_gain_factor")),
                                             "shading_factor": _number(row.get("shading_factor")),
                                             "corrected_glass_area_m2": _number(input_row.get("corrected_glass_area_m2")),
                                             "stored_heat_method": (room.get("stored_heat") or {}).get("method_id")})
                hours.append({"hour": hour.get("hour"), "elements": elements, "component_totals_kw": totals})
            rooms.append({"room_id": room.get("room_id"), "hours": hours})
        scenarios.append({"scenario_id": scenario.get("scenario_id"), "month": scenario.get("representative_month"), "rooms": rooms})
    solar = (report.get("design_conditions_basis") or {}).get("sun") or {}
    sun_hours = {}
    if all(solar.get(key) is not None for key in ("latitude_deg", "longitude_deg", "timezone")):
        try:
            from ai.sun import design_days
            sun_hours = design_days(solar["latitude_deg"], solar["longitude_deg"], solar["timezone"])
        except (ValueError, ImportError):
            pass
    return {"schema_version": SCHEMA_VERSION, "preliminary_input_set_fingerprint": report.get("preliminary_input_set_fingerprint"),
            "scenarios": scenarios, "sun_hours": sun_hours, "sun_source": solar.get("source")}


FLAGS = {"measured_from_drawing", "read_from_schedule", "ai_estimate", "standard_value",
         "assumed_default", "edited_by_user"}
ORIGIN_FLAGS = {
    "reviewer_traced": "measured_from_drawing", "geometry_proof": "measured_from_drawing",
    "pdf_evidence": "measured_from_drawing", "printed (read from image)": "measured_from_drawing",
    "room_specific": "measured_from_drawing", "scoped_zone": "measured_from_drawing",
    "scoped_level": "measured_from_drawing", "drawing_text": "measured_from_drawing",
    "ai_determined": "ai_estimate", "ai_fallback": "ai_estimate",
    "ai_geometry": "ai_estimate", "ai_profile": "ai_estimate",
    "household_rule": "assumed_default",  # Archie's own occupancy rule, not the AI's
    "ai_interpretation": "ai_estimate", "ai_estimated": "ai_estimate",
    "contractor_override": "edited_by_user", "manual_override": "edited_by_user",
    "edited": "edited_by_user", "reviewer": "edited_by_user", "user_edit": "edited_by_user",
    "operator_answer": "edited_by_user",
    "preliminary_fallback": "assumed_default", "controlled_preliminary_profile": "assumed_default",
    "controlled_fallback": "assumed_default", "typical_home_value": "assumed_default",
    "controlled_profile": "assumed_default", "standard_value": "standard_value",
    "certificate": "read_from_schedule", "read_from_schedule": "read_from_schedule",
    "data_sheet": "read_from_schedule", "matched_model": "read_from_schedule",
}


def _flag(origin):
    return origin if origin in FLAGS else ORIGIN_FLAGS.get(origin)


def _provenance(value, source="", citations=None, *, origin=None, evidence=None, edited_by=None, edited_at=None):
    flag = _flag(origin) if value is not None else None
    result = {"value": value, "flag": flag, "source": source or None,
              "citations": citations or evidence or [],
              "reason": ("Value was not recorded." if value is None else
                         None if flag else "No structured origin was recorded for this value.")}
    if flag == "edited_by_user":
        result.update(edited_by=edited_by, edited_at=edited_at,
                      edit_reason=None if edited_by and edited_at else "Editor identity or time was not recorded with this value.")
    return result


def _north_origin(row, survey):
    if not row:
        return None
    # North refined from the survey's boundary bearings is read from the drawing's text. The basis records the axis it
    # used; else compare with the job's saved axis. An axis is the same either way round, so compare modulo 180 degrees
    # (set 15's north 306.1 is its 126.1 axis turned round; review 2026-11-17).
    if isinstance((row.get("north_basis") or {}).get("survey_axis_deg"), (int, float)):
        return "measured_from_drawing"
    axis = (survey.get("axis") or {}).get("axis_deg")
    bearing = row.get("plan_up_azimuth_deg")
    if isinstance(axis, (int, float)) and isinstance(bearing, (int, float)) and abs((axis - bearing + 90) % 180 - 90) < 0.01:
        return "measured_from_drawing"
    if row.get("declaration_source") in {"ai_determined", "ai_fallback"}:
        return "ai_estimate"
    if row.get("source") == "reviewer_typed_page_up_bearing":
        return "edited_by_user"
    if row.get("source") == "reviewer_read_north_arrow":
        return "measured_from_drawing"
    return _flag(row.get("declaration_source"))


def _worked(element, surface):
    kind = element["component"]
    gain = element.get("gain_kw")
    load = element.get("load_kw")
    area = _number(surface.get("area_m2") if kind in {"envelope", "solar"} else surface.get("explicit_opening_area_m2"))
    u = _number(surface.get("u_value_w_m2k") if kind in {"envelope", "solar"} else (surface.get("window") or {}).get("u_value_w_m2k"))
    boundary = element.get("boundary_temperature_c")
    # Formula is descriptive of recorded operands. The gain/load come only from the engine row.
    if kind == "envelope":
        formula = "area × U × (boundary temperature − indoor temperature) / 1000"
        substituted = f"{area:g} m² × {u:g} W/m²K × ({boundary:g} − {{indoor}}) K / 1000" if None not in (area, u, boundary) else None
    elif kind == "glazing_conduction":
        formula = "opening area × U × (boundary temperature − indoor temperature) / 1000"
        substituted = f"{area:g} m² × {u:g} W/m²K × ({boundary:g} − {{indoor}}) K / 1000" if None not in (area, u, boundary) else None
    elif kind == "solar":
        formula = "area × incident solar × solar gain factor × shading / 1000"
        incident, factor, shading = (element.get("solar_design_w_m2"), element.get("solar_gain_factor"),
                                     element.get("shading_factor"))
        substituted = (f"{area:g} m² × {incident:g} W/m² × {factor:g} × {shading:g} / 1000"
                       if None not in (area, incident, factor, shading) else None)
    else:
        formula = "corrected glass area × incident solar × SHGC × external shading × internal shading / 1000"
        corrected, incident = element.get("corrected_glass_area_m2"), element.get("incident_solar_w_m2")
        window = surface.get("window") or {}
        property_value = _number(window.get("shgc", window.get("solar_transmission_factor")))
        external = _number((surface.get("manual_solar") or {}).get("external_shading_factor"))
        internal = _number(window.get("internal_shading_factor"))
        substituted = (f"{corrected:g} m² × {incident:g} W/m² × {property_value:g} × {external:g} × {internal:g} / 1000"
                       if None not in (corrected, incident, property_value, external, internal) else None)
    if substituted is not None and gain is not None:
        substituted += f" = {gain * 1000:.3f} W heat gain"
    return {"formula": formula, "substituted": substituted, "recorded_gain_w": round(gain * 1000, 3) if gain is not None else None,
            "recorded_load_w": round(load * 1000, 3) if load is not None else None,
            "stored_heat": {"method_id": element.get("stored_heat_method"),
                            "earlier_hour_load_kw": element.get("earlier_hour_load_kw"),
                            "earlier_hour_share": (round(element["earlier_hour_load_kw"] / load, 6)
                                                   if load not in (None, 0) and element.get("earlier_hour_load_kw") is not None else None),
                            "reason": None if element.get("earlier_hour_load_kw") is not None else
                                      "The calculation did not record an earlier-hour share for this element."}}


def _month_scenario(sidecar, month):
    from ai.sun import MONTHS
    for row in sidecar.get("scenarios", []):
        if row.get("month") in {month, MONTHS[month - 1]}:
            return row
    return next((row for row in sidecar.get("scenarios", []) if row.get("scenario_id") == "ai_preliminary_cooling_day"), None) if month == 1 else None


def read_audit(root, month, hour):
    root = Path(root)
    if not 1 <= month <= 12 or not 0 <= hour <= 23:
        raise ValueError("month must be 1–12 and hour must be 0–23")
    sidecar = _heavy(root / "thermal_audit_elements.json")
    report = _heavy(root / "hourly_ai_preliminary_load_report.json")
    model = _heavy(root / "ai_preliminary_model.json")
    if not sidecar or not report or not model:
        raise ValueError("Current preliminary report, model, or thermal audit elements are unavailable; recalculate the draft.")
    if sidecar.get("preliminary_input_set_fingerprint") != report.get("preliminary_input_set_fingerprint") or model.get("input_fingerprint") != report.get("preliminary_input_set_fingerprint"):
        raise ValueError("Thermal audit inputs are stale; recalculate the draft.")
    scenario = _month_scenario(sidecar, month)
    if scenario is None:
        raise ValueError("The requested month was not calculated.")
    rooms_by_id = {row["room_id"]: row for row in model.get("topology", {}).get("rooms", [])}
    report_scenario = next((row for row in report.get("scenario_results", []) if row.get("scenario_id") == scenario["scenario_id"]), {})
    weather_set = _read(root / "design_day_scenarios.json", {}) or {}
    weather_scenario = next((row for row in weather_set.get("scenarios", [])
                             if row.get("scenario_id") == scenario["scenario_id"]), {})
    weather_hour = next((row for row in weather_scenario.get("hours", []) if row.get("hour") == hour), {})
    outdoor = weather_hour.get("outdoor_dry_bulb_c") or {}
    report_rooms = {row["room_id"]: row for row in report_scenario.get("rooms", [])}
    quick_fixes = _read(root / "quick_fixes.json", {}) or {}
    window_edits = quick_fixes.get("windows") or {}
    area_overrides = (_read(root / "room_area_overrides.json", {}) or {}).get("overrides") or []
    height_overrides = (_read(root / "room_height_overrides.json", {}) or {}).get("overrides") or []
    glazing_answers = _read(root / "glazing_answers.json", {}) or {}
    materialized = {(row.get("room_id"), row.get("field")): row for row in model.get("materialized_fields", [])}
    traces = _read(root / "reviewer_room_geometry.json", {})
    page_north = traces.get("page_north") or {}
    survey = _read(root / "survey_bearings.json", {}) or {}
    pages = defaultdict(list)
    trace_edges = {}
    trace_origins = {}
    for trace in traces.get("records", []):
        page = trace.get("page")
        if page is None:
            continue
        trace_origins[trace.get("trace_id")] = trace.get("declaration_source") or "reviewer_traced"
        points = trace.get("points_image_px") or []
        calibration = trace.get("calibration") or {}
        mm_per_px = calibration.get("mm_per_px") or calibration.get("mm_per_image_px")
        edges = []
        north_row = page_north.get(str(page)) or page_north.get(page) or {}
        for edge in trace.get("edges", []):
            index = edge.get("index")
            bearing = None
            if north_row.get("plan_up_azimuth_deg") is not None and isinstance(index, int) and 0 <= index < len(points)-1:
                from ai.reviewer_room_geometry import oriented_edge_cardinal
                _, bearing = oriented_edge_cardinal(points, index, north_row["plan_up_azimuth_deg"])
            length = round(math.dist(points[index], points[index + 1]) * mm_per_px / 1000, 4) if isinstance(index, int) and 0 <= index < len(points)-1 and isinstance(mm_per_px, (int,float)) else None
            edges.append({"index": index, "class": edge.get("boundary"), "length_m": length,
                          "length_reason": None if length is not None else "Trace calibration was not recorded.",
                          "length_provenance": _provenance(length, calibration.get("source") or "Calibrated room trace",
                                                           origin=trace.get("declaration_source") or "reviewer_traced"),
                          "outward_bearing_deg": _number(bearing),
                          "bearing_provenance": _provenance(_number(bearing), north_row.get("source"),
                                                           [north_row.get("north_basis")] if north_row.get("north_basis") else [],
                                                           origin=_north_origin(north_row, survey),
                                                           edited_by=north_row.get("reviewer"), edited_at=north_row.get("declared_at")),
                          "bearing_reason": None if bearing is not None else "No validated north bearing or edge geometry."})
            trace_edges[(trace.get("trace_id"), index)] = edges[-1]
        windows = []
        edge_lengths = {edge["index"]: edge["length_m"] for edge in edges}
        for opening in trace.get("openings", []):
            window = dict(opening)
            head, sill = window.get("head_height_m"), window.get("sill_height_m")
            window["height_m"] = round(head - sill, 4) if isinstance(head, (int, float)) and isinstance(sill, (int, float)) else None
            length = edge_lengths.get(window.get("edge_index"))
            if window.get("offset_m") is None and length is not None and window.get("width_m") is not None:
                window["offset_m"] = round(max(0.0, (length - window["width_m"]) / 2), 4)
                window["position_basis"] = "assumed_centred"
            source = window.get("declaration_source") or ""
            cited = window.get("evidence") or []
            window["values"] = {key: _provenance(window.get(key), source, cited,
                                                  origin="assumed_default" if key == "offset_m" and window.get("position_basis") == "assumed_centred" else source,
                                                  edited_by=trace.get("reviewer"), edited_at=trace.get("created_at"))
                                for key in ("width_m", "height_m", "offset_m")}
            windows.append(window)
        pages[page].append({"trace_id": trace.get("trace_id"), "room_id": trace.get("room_id"),
                            "points_image_px": points, "edges": edges, "calibration": calibration,
                            "windows": windows})
    room_rows = []
    for row in scenario["rooms"]:
        room_id = row["room_id"]
        model_room = rooms_by_id.get(room_id, {})
        report_room = report_rooms.get(room_id, {})
        report_hour = next((item for item in report_room.get("hours", []) if item.get("hour") == hour), {})
        load = model_room.get("cooling_load") or {}
        surfaces = {s.get("surface_id"): s for s in load.get("envelope_surfaces", []) + load.get("glazing_surfaces", [])}
        current = next((item for item in row["hours"] if item.get("hour") == hour), {})
        elements = []
        for item in current.get("elements", []):
            surface = surfaces.get(item["id"], {})
            worked = _worked(item, surface)
            indoor = model_room.get("indoor_cooling_setpoint_c")
            if worked["substituted"] and indoor is not None:
                worked["substituted"] = worked["substituted"].replace("{indoor}", f"{indoor:g}")
            room_name = str(model_room.get("name", ""))
            edited_window = bool(window_edits.get("*") or window_edits.get(room_name.casefold()))
            glass_edit = (glazing_answers.get("rooms") or {}).get(room_name) or glazing_answers.get("all")
            area_source = (surface.get("source_pages") or surface.get("evidence") or [])
            edge = trace_edges.get((surface.get("reviewer_trace_id"), surface.get("edge_index")), {})
            area_prov = _provenance(surface.get("area_m2", surface.get("explicit_opening_area_m2")),
                                    "Calibrated room trace" if surface.get("reviewer_trace_id") else surface.get("source"), area_source,
                                    origin=surface.get("area_value_origin") or (edge.get("length_provenance") or {}).get("flag") or
                                           trace_origins.get(surface.get("reviewer_trace_id")))
            property_source = surface.get("source") or ""
            u_prov = _provenance(surface.get("u_value_w_m2k", (surface.get("window") or {}).get("u_value_w_m2k")),
                                 property_source, surface.get("citations"),
                                 origin=("edited_by_user" if glass_edit and surface.get("window") else
                                         surface.get("glazing_value_origin") if surface.get("window") else
                                         surface.get("construction_value_origin") or "assumed_default"),
                                 edited_by=(glass_edit or {}).get("by"), edited_at=(glass_edit or {}).get("at"))
            shgc_prov = _provenance((surface.get("window") or {}).get("shgc"), property_source,
                                    surface.get("citations"), origin="edited_by_user" if glass_edit else surface.get("glazing_value_origin"),
                                    edited_by=(glass_edit or {}).get("by"), edited_at=(glass_edit or {}).get("at"))
            orientation = edge.get("outward_bearing_deg")
            if orientation is None:
                orientation = surface.get("azimuth_deg")
            bearing_prov = edge.get("bearing_provenance") or {}
            record = {**item, "room_id": room_id, "surface": {key: surface.get(key) for key in ("kind", "label", "reviewer_trace_id", "edge_index", "offset_m", "position_basis", "host_surface_id", "orientation", "azimuth_deg")},
                      "workings": worked, "values": {"area_m2": area_prov, "u_value_w_m2k": u_prov,
                                                   "shgc": shgc_prov,
                                                   "orientation_deg": _provenance(orientation, bearing_prov.get("source") or surface.get("orientation_source"),
                                                                                  bearing_prov.get("citations"), origin=bearing_prov.get("flag") or surface.get("orientation_value_origin"),
                                                                                  edited_by=bearing_prov.get("edited_by"), edited_at=bearing_prov.get("edited_at")),
                                                   "shading_factor": _provenance((surface.get("manual_solar") or {}).get("external_shading_factor", surface.get("shading_factor")),
                                                                                 property_source, origin="edited_by_user" if edited_window else surface.get("shading_value_origin") or "assumed_default",
                                                                                 edited_by=(window_edits.get(room_name.casefold()) or window_edits.get("*") or {}).get("by"),
                                                                                 edited_at=(window_edits.get(room_name.casefold()) or window_edits.get("*") or {}).get("at"))}}
            elements.append(record)
        gain_record = next((record for record in (model.get("internal_gains_resolution") or {}).get("records", [])
                            if str(record.get("original_label", "")).casefold() == str(model_room.get("name", "")).casefold()), {})
        gain_fields = gain_record.get("fields") or {}
        gain_field_names = {"people": "occupancy_count", "lighting": "lighting_load_w",
                            "equipment_refrigeration": "equipment"}
        from ai import ai_preliminary
        pack_sources = (ai_preliminary.load_pack().get("value_sources") or {})
        try:
            from ai import house_profiles
            house_sources = house_profiles.load().get("value_sources") or {}
        except (ImportError, OSError, ValueError):
            house_sources = {}
        def gain_origin(field_name):
            field = gain_fields.get(field_name) or {}
            origin = field.get("origin")
            if field_name == "equipment":
                statuses = {row.get("status") or row.get("catalogue_status") for row in (gain_record.get("equipment") or [])
                            if isinstance(row, dict)}
                if statuses and statuses <= {"data_sheet", "matched_model"}:
                    return "read_from_schedule"
                if statuses and statuses <= {"typical", "generic"}:
                    return "assumed_default"
            if field_name == "lighting_load_w" and (origin in {None, "", "unresolved"}):
                recorded = (model_room.get("cooling_load") or {}).get("lighting_value_origin")
                if recorded:
                    return recorded
                if (model_room.get("cooling_load") or {}).get("lighting_w_m2") is not None:
                    return "standard_value" if (house_sources if str(gain_record.get("preliminary_profile_id", "")).startswith("house_") else pack_sources).get("lighting_w_m2") else "assumed_default"
            if origin == "controlled_preliminary_profile":
                source_key = {"lighting_load_w": "lighting_w_m2", "occupancy_count": "occupancy_density_people_m2"}.get(field_name)
                if field_name == "occupancy_count" and gain_record.get("preliminary_profile_id") == "generic_conditioned_room":
                    return origin
                citation = house_sources.get(source_key) if str(gain_record.get("preliminary_profile_id", "")).startswith("house_") else pack_sources.get(source_key)
                if citation:
                    return "standard_value"
            return origin
        def gain_citations(field_name):
            field = gain_fields.get(field_name) or {}
            if field_name == "lighting_load_w" and (model_room.get("cooling_load") or {}).get("lighting_value_citation"):
                return [{"reference": model_room["cooling_load"]["lighting_value_citation"]}] + list(field.get("evidence") or [])
            if field_name == "lighting_load_w" and field.get("origin") in {None, "", "unresolved"}:
                citation = (house_sources if str(gain_record.get("preliminary_profile_id", "")).startswith("house_") else pack_sources).get("lighting_w_m2")
                return ([{"reference": citation}] if citation else []) + list(field.get("evidence") or [])
            source_key = {"lighting_load_w": "lighting_w_m2", "occupancy_count": "occupancy_density_people_m2"}.get(field_name)
            if field_name == "occupancy_count" and gain_record.get("preliminary_profile_id") == "generic_conditioned_room":
                return list(field.get("evidence") or [])
            cited = house_sources.get(source_key) if str(gain_record.get("preliminary_profile_id", "")).startswith("house_") else pack_sources.get(source_key)
            return ([{"reference": cited}] if field.get("origin") == "controlled_preliminary_profile" and cited else []) + list(field.get("evidence") or [])
        internal = {name: {"sensible_kw": component.get("sensible_kw"), "latent_kw": component.get("latent_kw"),
                           "total_kw": component.get("total_kw"), "formula": component.get("formula"),
                           "input_rows": component.get("input_rows", []),
                           "source": _provenance(component.get("total_kw"),
                               (gain_fields.get(gain_field_names.get(name)) or {}).get("rationale") or
                               (model_room.get("cooling_load") or {}).get("source"),
                               gain_citations(gain_field_names.get(name)),
                               origin=gain_origin(gain_field_names.get(name)))}
                    for name, component in report_hour.get("components", {}).items() if name not in COMPONENTS}
        for name, details in internal.items():
            override = (gain_record.get("override") or {}).get(gain_field_names.get(name)) or {}
            if override and details["source"].get("flag") == "edited_by_user":
                details["source"].update(edited_by=override.get("reviewer"), edited_at=override.get("updated_at"),
                                         edit_reason=None)
        area_source = materialized.get((room_id, "area_m2"), {})
        area_provenance = _provenance(model_room.get("area_m2"), area_source.get("rationale") or model_room.get("source"), area_source.get("evidence"),
                                      origin=area_source.get("origin"), edited_by=area_source.get("reviewer") or area_source.get("edited_by"),
                                      edited_at=area_source.get("edited_at") or area_source.get("updated_at"))
        area_override = next((override for override in area_overrides
                              if str(override.get("room_label", "")).casefold() == str(model_room.get("name", "")).casefold()
                              and override.get("area_m2") == model_room.get("area_m2")), None)
        if area_override:
            area_provenance = _provenance(model_room.get("area_m2"), area_provenance.get("source"), area_provenance.get("citations"),
                                          origin="edited_by_user", edited_by=area_override.get("edited_by"), edited_at=area_override.get("edited_at"))
        height_override = next((override for override in height_overrides
                                if str(override.get("room_label", "")).casefold() == str(model_room.get("name", "")).casefold()
                                and override.get("ceiling_height_mm") == model_room.get("ceiling_height_mm")), None)
        height_record = next((record for record in (model.get("ceiling_volume_resolution") or {}).get("records", [])
                              if str(record.get("original_label", "")).casefold() == str(model_room.get("name", "")).casefold()
                              and record.get("ceiling_height_mm") == model_room.get("ceiling_height_mm")), {})
        height_provenance = _provenance(model_room.get("ceiling_height_mm"),
                                        height_record.get("rationale") or height_record.get("source") or model_room.get("source"),
                                        height_record.get("citations") or height_record.get("evidence"),
                                        origin=height_record.get("origin") or materialized.get((room_id, "ceiling_height_mm"), {}).get("origin"))
        if height_override:
            height_provenance = _provenance(model_room.get("ceiling_height_mm"), height_provenance.get("source"),
                                            height_provenance.get("citations"), origin="edited_by_user",
                                            edited_by=height_override.get("edited_by"), edited_at=height_override.get("edited_at"))
        occupancy_provenance = _provenance(model_room.get("occupancy"),
                                            (gain_fields.get("occupancy_count") or {}).get("rationale") or model_room.get("source"),
                                            gain_citations("occupancy_count"),
                                            origin=gain_origin("occupancy_count") or
                                                   materialized.get((room_id, "occupancy"), {}).get("origin") or
                                                   materialized.get((room_id, "occupancy_count"), {}).get("origin"))
        occupancy_override = (gain_record.get("override") or {}).get("occupancy_count") or {}
        if occupancy_override and occupancy_provenance.get("flag") == "edited_by_user":
            occupancy_provenance.update(edited_by=occupancy_override.get("reviewer"),
                                        edited_at=occupancy_override.get("updated_at"), edit_reason=None)
        room_rows.append({"room_id": room_id, "name": report_room.get("name", model_room.get("name")), "area_m2": area_provenance,
                          "height_mm": height_provenance,
                          "occupancy": occupancy_provenance,
                          "indoor_design_c": _provenance(model_room.get("indoor_cooling_setpoint_c"), (model_room.get("cooling_load_conditions") or {}).get("source"),
                                                          origin="assumed_default"),
                          "hour": hour, "components": {name: {key: component.get(key) for key in ("sensible_kw", "latent_kw", "total_kw")}
                                                         for name, component in report_hour.get("components", {}).items()},
                          "internal_loads": internal,
                          "outdoor_design_c": _provenance(outdoor.get("value"), weather_scenario.get("source") or outdoor.get("source"),
                                                            outdoor.get("citations") or weather_scenario.get("citations"),
                                                            origin=outdoor.get("value_origin") or weather_scenario.get("value_origin") or
                                                                   ("standard_value" if (report.get("design_conditions_basis") or {}).get("design_day", {}).get("site_specific") else "assumed_default")),
                          "subtotal_kw": report_hour.get("subtotal_kw"), "elements": elements})
    peak = report.get("included_scope_peak") or {}
    groups = defaultdict(float)
    assumed_loads = defaultdict(float)
    peak_scenario = next((row for row in sidecar.get("scenarios", [])
                          if row.get("scenario_id") == peak.get("scenario_id")), scenario)
    peak_hour = peak.get("hour") if isinstance(peak.get("hour"), int) else hour
    for peak_room in peak_scenario.get("rooms", []):
        model_room = rooms_by_id.get(peak_room.get("room_id"), {})
        load_model = model_room.get("cooling_load") or {}
        surfaces = {surface.get("surface_id"): surface for surface in
                    load_model.get("envelope_surfaces", []) + load_model.get("glazing_surfaces", [])}
        peak_row = next((row for row in peak_room.get("hours", []) if row.get("hour") == peak_hour), {})
        for item in peak_row.get("elements", []):
            value = item.get("load_kw")
            if value is None:
                continue
            surface = surfaces.get(item.get("id"), {})
            orientation = surface.get("orientation") or "unoriented"
            group = f"{orientation} {item['component']}" if orientation != "horizontal" else f"roof {item['component']}"
            groups[group] += value
            if surface.get("preliminary_assumption"):
                assumed_loads[surface.get("source") or "Controlled preliminary assumption"] += abs(value)
    drivers = sorted(({"group": name, "kw": round(value, 3)} for name, value in groups.items()),
                     key=lambda x: -abs(x["kw"]))[:3]
    assumptions = sorted(({"source": source, "load_share_kw": round(value, 3)} for source, value in assumed_loads.items()),
                         key=lambda x: -x["load_share_kw"])[:3]
    area_by_origin = defaultdict(float)
    for row in room_rows:
        area_by_origin[row["area_m2"]["flag"] or "unknown"] += row["area_m2"]["value"] or 0
    total_area = sum(area_by_origin.values())
    window_scan = _read(root / "house_windows.json", {}) or {}
    scope_exceptions = report.get("excluded_components", [])
    calculated_components = {name for row in report_rooms.values() for hour_row in row.get("hours", [])
                             for name, component in hour_row.get("components", {}).items()
                             if isinstance(component, dict) and component.get("total_kw")}
    not_included = [name for name in scope_exceptions
                    if not (name == "glazing and façade solar" and
                            calculated_components & {"glazing_conduction", "glazing_solar"})
                    and name not in {"room area", "room scope", "shading review"}]
    summary = {"cooled_area_m2": round(total_area, 2),
               "cooled_area_by_provenance": {key: {"m2": round(value, 2), "percent": round(100*value/total_area, 1) if total_area else None}
                                              for key, value in area_by_origin.items()},
               "windows_scheduled": window_scan.get("windows_in_schedule"),
               "windows_placed": sum(len(row.get("glazing_surfaces", [])) for row in (r.get("cooling_load") or {} for r in rooms_by_id.values())),
               "north": _provenance(page_north or None, "Reviewer geometry page north" if page_north else "",
                                     origin=(next(iter({_north_origin(row, survey) for row in page_north.values()}))
                                             if page_north and len({_north_origin(row, survey) for row in page_north.values()}) == 1 else None)),
               "north_by_page": {str(page): _provenance(row.get("plan_up_azimuth_deg"), row.get("source"),
                                                         [row.get("north_basis")] if row.get("north_basis") else [],
                                                         origin=_north_origin(row, survey),
                                                         edited_by=row.get("reviewer"), edited_at=row.get("declared_at"))
                                 for page, row in page_north.items() if isinstance(row, dict)},
               "top_load_drivers": drivers, "highest_impact_assumptions": assumptions,
               "ceiling_height_sources": sorted({row.get("origin") or "unknown" for row in
                   (model.get("ceiling_volume_resolution") or {}).get("records", [])}),
               "not_included": not_included, "partial_scope_exceptions": [name for name in scope_exceptions if name not in not_included],
               "sizing_hour": peak.get("hour"), "sizing_scenario_id": peak.get("scenario_id")}
    return {"schema_version": SCHEMA_VERSION, "month": month, "hour": hour, "scenario_id": scenario["scenario_id"],
            "pages": [{"page": page, "north": _provenance(page_north.get(str(page)) or page_north.get(page), "Reviewer geometry page north",
                                                        origin=_north_origin(page_north.get(str(page)) or page_north.get(page), survey),
                                                        edited_by=(page_north.get(str(page)) or page_north.get(page) or {}).get("reviewer"),
                                                        edited_at=(page_north.get(str(page)) or page_north.get(page) or {}).get("declared_at")),
                       "outlines": outlines} for page, outlines in sorted(pages.items())],
            "rooms": room_rows, "summary": summary,
            "sun": {"hours": (sidecar.get("sun_hours") or {}).get(str(month)),
                    "source": sidecar.get("sun_source"),
                    "reason": None if (sidecar.get("sun_hours") or {}).get(str(month)) else "Sun position was not persisted with the calculation."}}
