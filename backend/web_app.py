#!/usr/bin/env python3

import argparse
import html
import json
import re
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ai.ai_packet import build_ai_packet, load_json
from ai.design_pipeline import create_confirmed_ai_packet
from ai.design_requirements import compatibility_requirements, empty_design_requirements, requirements_summary, validate_design_requirements
from ai.envelope import (
    apply_reviewed_envelope_to_hourly_model,
    apply_reviewed_envelope_to_requirements,
    empty_envelope_library,
    empty_envelope_model,
    envelope_summary,
    migrate_legacy_envelope,
    validate_envelope_library,
    validate_envelope_model,
)
from ai.site_design_conditions import empty_site_design_conditions, site_design_conditions_summary, validate_site_design_conditions
from ai.hourly_loads import (
    build_hourly_load_model,
    calculate_hourly_load_report,
    design_day_summary,
    empty_design_day_scenarios,
    empty_hourly_load_model,
    empty_schedule_library,
    hourly_model_summary,
    schedule_library_summary,
    validate_design_day_scenarios,
    validate_hourly_load_model,
    validate_schedule_library,
)
from ai.cooling_readiness import assess_cooling_readiness
from ai.drawing_coverage import build_drawing_coverage
from ai.building_evidence import build_building_evidence
from ai.parity_harness import archie_results_from_heat_report, archie_results_from_hourly_load_report, compare_case, render_markdown, validate_benchmark_case
from ai.thermal_model import apply_thermal_model, build_thermal_evidence, build_thermal_model
from ai.ventilation import calculate_ventilation_report
from ai.geometry_review import normalise_vision
from ai.reasoning_packet import create_reasoning_packet_from_vision
from pdf_pipeline.extractors import count_pdf_pages
from pdf_pipeline.review import create_review_packet, safe_folder_name


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
UPLOADS = ROOT / "output" / "uploads"
WEB_REVIEW = ROOT / "output" / "web_review"
PROJECTS_FILE = ROOT / "output" / "web_projects.json"
ANALYSIS_VERSION = "drawing_set_coverage_v7"


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        if self.path == "/" or self.path.startswith("/frontend/"):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_GET(self):
        if self.path == "/":
            return self.send_file(FRONTEND / "index.html", "text/html")
        if self.path == "/api/projects":
            return self.send_json(project_list())
        if self.path.startswith("/api/site-design-conditions"):
            return self.send_json(api_site_design_conditions(self))
        if self.path.startswith("/api/schedules"):
            return self.send_json(api_schedules(self))
        if self.path.startswith("/api/design-day-scenarios"):
            return self.send_json(api_design_day_scenarios(self))
        if self.path.startswith("/api/hourly-load-model"):
            return self.send_json(api_hourly_load_model(self))
        if self.path.startswith("/api/hourly-load-report"):
            return self.send_json(api_hourly_load_report(self))
        if self.path.startswith("/api/envelope-library"):
            try:
                return self.send_json(api_envelope_library(self))
            except Exception as error:
                return self.send_json({"error": str(error)}, 404)
        if self.path.startswith("/api/envelope-model"):
            try:
                return self.send_json(api_envelope_model(self))
            except Exception as error:
                return self.send_json({"error": str(error)}, 404)
        if self.path.startswith("/api/design-requirements"):
            return self.send_json(api_design_requirements(self))
        if self.path.startswith("/api/heat-load"):
            return self.send_json(api_heat_load(self))
        if self.path.startswith("/api/ventilation"):
            return self.send_json(api_ventilation(self))
        if self.path.startswith("/api/thermal-model"):
            try:
                return self.send_json(api_thermal_model(self))
            except Exception as error:
                return self.send_json({"error": str(error)}, 404)
        if self.path.startswith("/api/parity-report"):
            try:
                return self.send_json(api_parity_report(self))
            except Exception as error:
                return self.send_json({"error": str(error)}, 404)
        if self.path.startswith("/api/analysis"):
            return self.send_analysis()
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/upload":
            return self.upload_pdf()
        if self.path == "/api/analyse":
            return self.analyse_pdf()
        if self.path == "/api/decisions":
            return self.save_decisions()
        if self.path == "/api/vision-response":
            return self.save_vision_response()
        if self.path == "/api/site-design-conditions":
            return self.save_site_design_conditions()
        if self.path == "/api/schedules":
            return self.save_schedules()
        if self.path == "/api/design-day-scenarios":
            return self.save_design_day_scenarios()
        if self.path == "/api/hourly-load-model":
            return self.save_hourly_load_model()
        if self.path == "/api/hourly-load-report":
            return self.save_hourly_load_report()
        if self.path == "/api/envelope-library":
            return self.save_envelope_library()
        if self.path == "/api/envelope-model":
            return self.save_envelope_model()
        if self.path == "/api/design-requirements":
            return self.save_design_requirements()
        if self.path == "/api/heat-load":
            return self.save_heat_load()
        if self.path == "/api/ventilation":
            return self.save_ventilation()
        if self.path == "/api/thermal-model":
            return self.save_thermal_model()
        if self.path == "/api/parity-report":
            return self.save_parity_report()
        if self.path == "/process":
            return self.process_pdf()
        return self.send_error(404, "Not found")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_common_headers()
        self.end_headers()

    def upload_pdf(self):
        try:
            result = api_upload(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def analyse_pdf(self):
        try:
            result = api_analyse(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_decisions(self):
        try:
            result = api_save_decisions(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_vision_response(self):
        try:
            result = api_save_vision_response(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_design_requirements(self):
        try:
            result = api_save_design_requirements(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_site_design_conditions(self):
        try:
            result = api_save_site_design_conditions(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_schedules(self):
        try:
            result = api_save_schedules(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_design_day_scenarios(self):
        try:
            result = api_save_design_day_scenarios(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_hourly_load_model(self):
        try:
            result = api_save_hourly_load_model(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_hourly_load_report(self):
        try:
            result = api_save_hourly_load_report(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_envelope_library(self):
        try:
            result = api_save_envelope_library(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_envelope_model(self):
        try:
            result = api_save_envelope_model(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_heat_load(self):
        self.send_json({
            "error": "The legacy cooling calculation endpoint is retired. Save hourly inputs and use /api/hourly-load-report.",
            "deprecated": True,
            "replacement": "/api/hourly-load-report",
        }, 410)

    def save_ventilation(self):
        try:
            result = api_save_ventilation(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_thermal_model(self):
        try:
            result = api_save_thermal_model(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def save_parity_report(self):
        try:
            result = api_save_parity_report(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json(result)

    def send_analysis(self):
        try:
            result = api_saved_analysis(self)
        except Exception as error:
            return self.send_json({"error": str(error)}, 404)
        self.send_json(result)

    def process_pdf(self):
        try:
            result = process_upload(self)
        except Exception as error:
            return self.send_html(error_page(error), 500)

        self.send_html(result_page(result))

    def translate_path(self, path):
        return str(ROOT / urlparse(path).path.lstrip("/"))

    def send_file(self, path, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_common_headers()
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def send_html(self, body, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_common_headers()
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_common_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def send_common_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def api_upload(request):
    filename, content = read_pdf_upload(request)
    if not filename:
        raise ValueError("No PDF was uploaded.")
    if Path(filename).suffix.lower() != ".pdf":
        raise ValueError("Upload must be a PDF.")

    UPLOADS.mkdir(parents=True, exist_ok=True)
    project_id = unique_project_id(filename)
    pdf_path = UPLOADS / f"{project_id}.pdf"
    pdf_path.write_bytes(content)

    now = timestamp()
    project = {
        "id": project_id,
        "name": Path(filename).name,
        "pdf": str(pdf_path),
        "pages": pdf_page_count(pdf_path),
        "size_bytes": len(content),
        "analysed": False,
        "created_at": now,
        "updated_at": now,
    }
    update_project(project)
    return upload_response(project)


def api_analyse(request):
    project = project_by_id(read_json_body(request).get("id", ""))
    return analysis_response(analyse_project(project))


def analyse_project(project):
    pdf_path = Path(project["pdf"])
    review_dir = WEB_REVIEW / project["id"]
    result = create_review_packet(pdf_path, review_dir, include_structure=True)

    packet = load_json(result["packet"])
    ai_output = Path(result["review_dir"]) / "ai_input.json"
    ai_input = build_ai_packet(packet)
    ai_output.write_text(json.dumps(ai_input, indent=2), encoding="utf-8")
    coverage_output = Path(result["review_dir"]) / "drawing_coverage.json"
    coverage_output.write_text(json.dumps(build_drawing_coverage(ai_input), indent=2), encoding="utf-8")
    building_output = Path(result["review_dir"]) / "building_evidence.json"
    building_output.write_text(json.dumps(build_building_evidence(ai_input, load_json(coverage_output)), indent=2), encoding="utf-8")

    project.update(
        {
            "analysed": True,
            "review_dir": result["review_dir"],
            "packet": result["packet"],
            "html": result["html"],
            "ai_input": str(ai_output),
            "drawing_coverage": str(coverage_output),
            "building_evidence": str(building_output),
            "pages": result["kept_count"],
            "relevant": result["primary_count"],
            "analysis_version": ANALYSIS_VERSION,
            "updated_at": timestamp(),
        }
    )
    update_project(project)
    return project


def api_save_decisions(request):
    data = read_json_body(request)
    project = project_by_id(data.get("id", ""))
    if not project.get("packet"):
        raise ValueError("Analyse the PDF before saving page decisions.")

    review_dir = Path(project["review_dir"])
    decisions_path = review_dir / "reviewed_decisions.json"
    decisions = {
        "source_pdf": data.get("source_pdf", project["name"]),
        "reviewed_at": data.get("reviewed_at", timestamp()),
        "pages": data.get("pages", []),
    }
    decisions_path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    requirements_path = review_dir / "design_requirements.json"
    if requirements_path.exists():
        requirements_path.unlink()

    pipeline = create_confirmed_ai_packet(
        project["packet"],
        decisions_path,
        review_dir / "ai_input.json",
    )

    project["decisions"] = str(decisions_path)
    for key in [
        "page_triage", "page_triage_validation", "page_triage_ai_input", "page_triage_packet",
        "ai_input", "spatial_ocr", "vector_geometry",
        "wall_classification", "wall_classification_validation", "wall_classification_packet",
        "wall_classification_candidate_review", "dimension_wall_matches", "candidate_review",
        "dimension_review_packet", "vision_response", "vision_validation", "coordinate_review",
        "geometry_confirmation", "reasoning_packet", "design_requirements", "drawing_coverage",
        "thermal_evidence", "thermal_model", "building_evidence", "heat_load_report", "ventilation_report", "parity_report",
    ]:
        project.pop(key, None)
    project["ai_input"] = pipeline["ai_input"]
    coverage_path = review_dir / "drawing_coverage.json"
    coverage_path.write_text(json.dumps(build_drawing_coverage(load_json(pipeline["ai_input"])), indent=2), encoding="utf-8")
    project["drawing_coverage"] = str(coverage_path)
    building_path = review_dir / "building_evidence.json"
    building_path.write_text(json.dumps(build_building_evidence(load_json(pipeline["ai_input"]), load_json(coverage_path)), indent=2), encoding="utf-8")
    project["building_evidence"] = str(building_path)
    project["spatial_ocr"] = pipeline["spatial_ocr"]
    project["vector_geometry"] = pipeline["vector_geometry"]
    project["dimension_wall_matches"] = pipeline["dimension_wall_matches"]
    project["candidate_review"] = pipeline["candidate_review"]
    project["chatgpt_packet"] = pipeline["chatgpt_packet"]
    project["updated_at"] = timestamp()
    update_project(project)
    return {
        "id": project["id"],
        "decisions_url": link(decisions_path),
        "ai_input_url": link(pipeline["ai_input"]),
        "chatgpt_packet": link_pipeline_files(pipeline["chatgpt_packet"]),
    }


def api_save_vision_response(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    raw_json = data.get("vision_json") or data.get("json") or data.get("vision_response")
    if not raw_json:
        raise ValueError("Paste the ChatGPT vision JSON before submitting.")

    result = save_project_vision_response(project, raw_json, data.get("source_label", "manual_chatgpt"))
    project["vision_response"] = result["vision_response_path"]
    project["vision_validation"] = result["vision_validation_path"]
    project["coordinate_review"] = result["coordinate_review_path"]
    if result.get("geometry_confirmation_path"):
        project["geometry_confirmation"] = result["geometry_confirmation_path"]
    project["reasoning_packet"] = result["reasoning_packet_raw"]
    project["updated_at"] = timestamp()
    update_project(project)
    return result["response"]


def api_site_design_conditions(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    path = Path(project["review_dir"]) / "site_design_conditions.json"
    conditions = load_json(path) if path.exists() else empty_site_design_conditions()
    return {
        "id": project["id"],
        "site_design_conditions": conditions,
        "readiness": site_design_conditions_summary(conditions),
        "url": safe_link(path) if path.exists() else "",
    }


def api_save_site_design_conditions(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    if not project.get("review_dir"):
        raise ValueError("Analyse the PDF before saving site design conditions.")
    conditions = validate_site_design_conditions(data.get("site_design_conditions", data.get("conditions", data)))
    path = Path(project["review_dir"]) / "site_design_conditions.json"
    path.write_text(json.dumps(conditions, indent=2), encoding="utf-8")
    project["site_design_conditions"] = str(path)
    project["updated_at"] = timestamp()
    update_project(project)
    return {
        "id": project["id"],
        "site_design_conditions": conditions,
        "readiness": site_design_conditions_summary(conditions),
        "url": safe_link(path),
    }


def hourly_paths(project):
    review_dir = Path(project["review_dir"])
    return {
        "requirements": review_dir / "design_requirements.json",
        "schedules": review_dir / "schedule_library.json",
        "scenarios": review_dir / "design_day_scenarios.json",
        "model": review_dir / "hourly_load_model.json",
        "report": review_dir / "hourly_load_report.json",
        "coverage": review_dir / "drawing_coverage.json",
        "envelope_library": review_dir / "envelope_library.json",
        "envelope_model": review_dir / "envelope_model.json",
    }


def envelope_artifacts(project):
    paths = hourly_paths(project)
    raw_library = load_json(paths["envelope_library"]) if paths["envelope_library"].exists() else empty_envelope_library()
    library = validate_envelope_library(raw_library)
    if paths["envelope_library"].exists():
        library["updated_at"] = raw_library.get("updated_at", "")
    else:
        library["updated_at"] = ""
    raw_model = load_json(paths["envelope_model"]) if paths["envelope_model"].exists() else empty_envelope_model()
    model = validate_envelope_model(raw_model, library)
    if paths["envelope_model"].exists():
        model["updated_at"] = raw_model.get("updated_at", "")
    else:
        model["updated_at"] = ""
    return library, model


def api_envelope_library(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    library, model = envelope_artifacts(project)
    path = hourly_paths(project)["envelope_library"]
    return artifact_response(project, "envelope_library", library, envelope_summary(library, model), path)


def api_envelope_model(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    library, model = envelope_artifacts(project)
    path = hourly_paths(project)["envelope_model"]
    response = artifact_response(project, "envelope_model", model, envelope_summary(library, model), path)
    response["envelope_library_url"] = safe_link(hourly_paths(project)["envelope_library"]) if hourly_paths(project)["envelope_library"].exists() else ""
    return response


def api_save_envelope_library(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    ensure_review_dir(project)
    paths = hourly_paths(project)
    library = validate_envelope_library(data.get("envelope_library", data.get("library", data)))
    existing_model = load_json(paths["envelope_model"]) if paths["envelope_model"].exists() else empty_envelope_model()
    # A changed library can invalidate model references; reject rather than silently dropping them.
    model = validate_envelope_model(existing_model, library)
    write_artifact(paths["envelope_library"], library)
    project["envelope_library"] = str(paths["envelope_library"])
    project["updated_at"] = timestamp()
    update_project(project)
    return artifact_response(project, "envelope_library", library, envelope_summary(library, model), paths["envelope_library"])


def api_save_envelope_model(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    ensure_review_dir(project)
    paths = hourly_paths(project)
    action = data.get("action", "save")
    if action == "migrate_legacy":
        if not paths["requirements"].exists():
            raise ValueError("Save design requirements before migrating legacy envelope surfaces.")
        library, model = migrate_legacy_envelope(load_json(paths["requirements"]))
        write_artifact(paths["envelope_library"], library)
        project["envelope_library"] = str(paths["envelope_library"])
    elif action == "save":
        library = load_json(paths["envelope_library"]) if paths["envelope_library"].exists() else empty_envelope_library()
        library = validate_envelope_library(library)
        model = validate_envelope_model(data.get("envelope_model", data.get("model", data)), library)
    else:
        raise ValueError("Envelope model action must be save or migrate_legacy.")
    validate_active_envelope_owners(model, paths)
    write_artifact(paths["envelope_model"], model)
    project["envelope_model"] = str(paths["envelope_model"])
    project["updated_at"] = timestamp()
    update_project(project)
    return artifact_response(project, "envelope_model", model, envelope_summary(library, model), paths["envelope_model"])


def validate_active_envelope_owners(model, paths):
    if not model["active_for_calculation"]:
        return
    if not paths["requirements"].exists():
        raise ValueError("Save design requirements with HVAC zones before activating the reviewed envelope model.")
    zone_ids = {item.get("zone_id", "") for item in load_json(paths["requirements"]).get("zones", [])}
    unknown = sorted({item["owner_zone_id"] for item in model["surfaces"] if item["owner_zone_id"] not in zone_ids})
    if unknown:
        raise ValueError("Reviewed envelope surfaces reference unknown HVAC zones: " + ", ".join(unknown))


def api_schedules(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    path = hourly_paths(project)["schedules"]
    library = load_json(path) if path.exists() else empty_schedule_library()
    return artifact_response(project, "schedule_library", library, schedule_library_summary(library), path)


def api_save_schedules(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    ensure_review_dir(project)
    library = validate_schedule_library(data.get("schedule_library", {"schedules": data.get("schedules", [])}))
    path = hourly_paths(project)["schedules"]
    write_artifact(path, library)
    project["schedule_library"] = str(path)
    project["updated_at"] = timestamp()
    update_project(project)
    return artifact_response(project, "schedule_library", library, schedule_library_summary(library), path)


def api_design_day_scenarios(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    path = hourly_paths(project)["scenarios"]
    scenarios = load_json(path) if path.exists() else empty_design_day_scenarios()
    return artifact_response(project, "design_day_scenarios", scenarios, design_day_summary(scenarios), path)


def api_save_design_day_scenarios(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    ensure_review_dir(project)
    scenarios = validate_design_day_scenarios(data.get("design_day_scenarios", {"scenarios": data.get("scenarios", [])}))
    path = hourly_paths(project)["scenarios"]
    write_artifact(path, scenarios)
    project["design_day_scenarios"] = str(path)
    project["updated_at"] = timestamp()
    update_project(project)
    return artifact_response(project, "design_day_scenarios", scenarios, design_day_summary(scenarios), path)


def api_hourly_load_model(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    paths = hourly_paths(project)
    raw_model = load_json(paths["model"]) if paths["model"].exists() else empty_hourly_load_model()
    model = artifact_snapshot(raw_model, validate_hourly_load_model)
    requirements = load_json(paths["requirements"]) if paths["requirements"].exists() else None
    return artifact_response(project, "hourly_load_model", model, hourly_model_summary(model, requirements), paths["model"])


def api_save_hourly_load_model(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    ensure_review_dir(project)
    paths = hourly_paths(project)
    if not paths["requirements"].exists():
        raise ValueError("Save design requirements before building an hourly room model.")
    requirements = load_json(paths["requirements"])
    action = data.get("action", "build")
    if action == "build":
        model = build_hourly_load_model(requirements)
    elif action == "save":
        model = validate_hourly_load_model(data.get("hourly_load_model", data.get("model", {})))
    else:
        raise ValueError("Hourly load model action must be build or save.")
    write_artifact(paths["model"], model)
    project["hourly_load_model"] = str(paths["model"])
    project["updated_at"] = timestamp()
    update_project(project)
    return artifact_response(project, "hourly_load_model", model, hourly_model_summary(model, requirements), paths["model"])


def api_hourly_load_report(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    paths = hourly_paths(project)
    current = current_hourly_load_report_path(project)
    report = load_json(current) if current else (load_json(paths["report"]) if paths["report"].exists() else {})
    return {
        "id": project["id"],
        "hourly_load_report": report,
        "readiness": report.get("readiness", {"status": report.get("status", "blocked"), "issues": []}),
        "url": safe_link(current) if current else "",
        "artifact_url": safe_link(paths["report"]) if paths["report"].exists() else "",
        "status": "current" if current else ("stale" if paths["report"].exists() else "not_calculated"),
    }


def api_save_hourly_load_report(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    ensure_review_dir(project)
    paths = hourly_paths(project)
    required_paths = ("requirements", "schedules", "scenarios", "model")
    missing = [name for name in required_paths if not paths[name].exists()]
    if missing:
        raise ValueError("Save " + ", ".join(missing) + " before calculating an hourly cooling report.")
    coverage = load_json(paths["coverage"]) if paths["coverage"].exists() else {}
    library, envelope_model = envelope_artifacts(project)
    requirements, envelope_inputs = apply_reviewed_envelope_to_requirements(load_json(paths["requirements"]), library, envelope_model)
    model = apply_reviewed_envelope_to_hourly_model(load_json(paths["model"]), library, envelope_model)
    report = calculate_hourly_load_report(requirements, load_json(paths["schedules"]), load_json(paths["scenarios"]), model,
        data.get("selected_scenario_ids", data.get("scenario_ids", [])), coverage)
    report["input_fingerprints"]["envelope_library_updated_at"] = library.get("updated_at", "")
    report["input_fingerprints"]["envelope_model_updated_at"] = envelope_model.get("updated_at", "")
    report["envelope_input"] = envelope_inputs
    report["excluded_components"] = sorted(set(report["excluded_components"] + [
        "dynamic thermal mass", "detailed glazing physics", "geometric shading", "AHU coil effects", "fan/duct effects", "heat recovery", "plant loads", "heating",
    ]))
    readiness = assess_cooling_readiness(report, model, requirements.get("updated_at", ""), coverage, envelope_inputs)
    report["readiness"] = {"status": readiness["status"], "issues": readiness["issues"]}
    report["scope_summary"] = readiness["scope_summary"]
    report["status"] = readiness["status"]
    if envelope_model["active_for_calculation"] and (envelope_inputs["blocked"] or envelope_inputs["stored_not_calculated"]):
        report["status"] = "blocked"
        report["blocked_reasons"].append("Reviewed envelope model has blocked or stored-not-calculated surfaces; resolve or deactivate it before calculation.")
        report["readiness"]["status"] = "blocked"
    report["input_artifacts"] = {
        "design_requirements": {"artifact_url": safe_link(paths["requirements"]), "updated_at": report["input_fingerprints"]["requirements_updated_at"]},
        "schedule_library": {"artifact_url": safe_link(paths["schedules"]), "updated_at": report["input_fingerprints"]["schedule_library_updated_at"]},
        "design_day_scenarios": {"artifact_url": safe_link(paths["scenarios"]), "updated_at": report["input_fingerprints"]["design_day_scenarios_updated_at"]},
        "hourly_load_model": {"artifact_url": safe_link(paths["model"]), "updated_at": report["input_fingerprints"]["hourly_load_model_updated_at"]},
        "envelope_library": {"artifact_url": safe_link(paths["envelope_library"]) if paths["envelope_library"].exists() else "", "updated_at": library.get("updated_at", "")},
        "envelope_model": {"artifact_url": safe_link(paths["envelope_model"]) if paths["envelope_model"].exists() else "", "updated_at": envelope_model.get("updated_at", "")},
        "drawing_coverage": {"artifact_url": safe_link(paths["coverage"]) if paths["coverage"].exists() else "", "updated_at": coverage.get("updated_at", "")},
    }
    write_artifact(paths["report"], report)
    project["hourly_load_report"] = str(paths["report"])
    project["updated_at"] = timestamp()
    update_project(project)
    current = current_hourly_load_report_path(project)
    return {
        "id": project["id"], "hourly_load_report": report,
        "readiness": report["readiness"],
        "url": safe_link(current) if current else "", "artifact_url": safe_link(paths["report"]),
        "status": "current" if current else "stale",
    }


def ensure_review_dir(project):
    if not project.get("review_dir"):
        raise ValueError("Analyse the PDF before saving hourly design-day inputs.")


def write_artifact(path, artifact):
    path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")


def artifact_response(project, key, artifact, summary, path):
    return {
        "id": project["id"], key: artifact, "readiness": summary,
        "url": safe_link(path) if path.exists() else "", "artifact_url": safe_link(path) if path.exists() else "",
        "status": "current" if path.exists() else "not_saved",
    }


def api_design_requirements(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    path = Path(project["review_dir"]) / "design_requirements.json"
    requirements = compatibility_requirements(load_json(path)) if path.exists() else empty_design_requirements()
    heat_load_path = current_heat_load_report_path(project, path)
    legacy_heat_load_path = existing_path(project.get("heat_load_report"), path.with_name("heat_load_report.json"))
    ventilation_path = current_ventilation_report_path(project, path)
    return {
        "id": project["id"],
        "requirements": requirements,
        "readiness": requirements_summary(requirements),
        "room_suggestions": project_room_suggestions(project),
        "heat_load_report": load_json(legacy_heat_load_path) if legacy_heat_load_path else {},
        "heat_load_report_url": safe_link(legacy_heat_load_path) if legacy_heat_load_path else "",
        "heat_load_status": "current" if heat_load_path else ("stale" if legacy_heat_load_path else "not_calculated"),
        "ventilation_report": load_json(ventilation_path) if ventilation_path else {},
        "ventilation_report_url": safe_link(ventilation_path) if ventilation_path else "",
        "ventilation_status": "current" if ventilation_path else ("stale" if (Path(project["review_dir"]) / "ventilation_report.json").exists() else "not_calculated"),
        "url": safe_link(path) if path.exists() else "",
    }


def api_thermal_model(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    review_dir = Path(project["review_dir"])
    evidence_path = review_dir / "thermal_evidence.json"
    model_path = review_dir / "thermal_model.json"
    coverage_path = review_dir / "drawing_coverage.json"
    building_path = review_dir / "building_evidence.json"
    return {
        "id": project["id"],
        "evidence": load_json(evidence_path) if evidence_path.exists() else {},
        "model": load_json(model_path) if model_path.exists() else {},
        "evidence_url": safe_link(evidence_path) if evidence_path.exists() else "",
        "model_url": safe_link(model_path) if model_path.exists() else "",
        "drawing_coverage": load_json(coverage_path) if coverage_path.exists() else {},
        "drawing_coverage_url": safe_link(coverage_path) if coverage_path.exists() else "",
        "building_evidence": load_json(building_path) if building_path.exists() else {},
        "building_evidence_url": safe_link(building_path) if building_path.exists() else "",
    }


def api_parity_report(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    parity_dir = benchmark_case_dir(project)
    case_path = parity_dir / "benchmark_case.json"
    report_path = parity_dir / "reports" / "parity_report.json"
    markdown_path = parity_dir / "reports" / "parity_report.md"
    return {
        "id": project["id"],
        "case": load_json(case_path) if case_path.exists() else {},
        "report": load_json(report_path) if report_path.exists() else {},
        "case_url": safe_link(case_path) if case_path.exists() else "",
        "report_url": safe_link(report_path) if report_path.exists() else "",
        "markdown_url": safe_link(markdown_path) if markdown_path.exists() else "",
        "status": "current" if project.get("parity_report") and report_path.exists() else ("stale" if report_path.exists() else "not_run"),
    }


def api_save_parity_report(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    case = data.get("benchmark_case", data.get("case"))
    if not case:
        raise ValueError("Provide a benchmark_case reconciliation before running a parity report.")
    case = validate_benchmark_case(case)
    review_dir = Path(project["review_dir"])
    parity_dir = benchmark_case_dir(project)
    for name in ("source", "archie", "reference", "reports"):
        (parity_dir / name).mkdir(parents=True, exist_ok=True)
    case_path = parity_dir / "benchmark_case.json"
    report_path = parity_dir / "reports" / "parity_report.json"
    markdown_path = parity_dir / "reports" / "parity_report.md"
    heat_path = current_heat_load_report_path(project, review_dir / "design_requirements.json")
    hourly_path = current_hourly_load_report_path(project)
    archie_results = data.get("archie_results")
    if archie_results is None:
        archie_results = (
            archie_results_from_hourly_load_report(load_json(hourly_path)) if hourly_path
            else (archie_results_from_heat_report(load_json(heat_path)) if heat_path else {"peak": {}, "rooms": [], "zones": []})
        )
    report = compare_case(case, archie_results)
    case_path.write_text(json.dumps(case, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    project["parity_case"] = str(case_path)
    project["parity_report"] = str(report_path)
    project["updated_at"] = timestamp()
    update_project(project)
    return {
        "id": project["id"],
        "case": case,
        "report": report,
        "case_url": safe_link(case_path),
        "report_url": safe_link(report_path),
        "markdown_url": safe_link(markdown_path),
    }


def benchmark_case_dir(project):
    return ROOT / "output" / "benchmark_cases" / safe_folder_name(project["id"])


def api_save_thermal_model(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    if not project.get("vision_response"):
        raise ValueError("Create a reasoning packet before building a thermal model.")
    review_dir = Path(project["review_dir"])
    evidence_path = review_dir / "thermal_evidence.json"
    model_path = review_dir / "thermal_model.json"
    coverage_path = review_dir / "drawing_coverage.json"
    building_path = review_dir / "building_evidence.json"
    action = data.get("action", "build")
    if action == "build":
        evidence = build_thermal_evidence(
            load_json(project["ai_input"]),
            load_json(review_dir / "spatial_ocr.json") if (review_dir / "spatial_ocr.json").exists() else {},
            load_json(project["vision_response"]),
            load_json(coverage_path) if coverage_path.exists() else {},
            load_json(building_path) if building_path.exists() else None,
        )
        model = build_thermal_model(evidence)
        evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        model_path.write_text(json.dumps(model, indent=2), encoding="utf-8")
    elif action == "save":
        if not model_path.exists():
            raise ValueError("Build the thermal model before saving review decisions.")
        model = load_json(model_path)
        requirements_path = review_dir / "design_requirements.json"
        current = load_json(requirements_path) if requirements_path.exists() else empty_design_requirements()
        decisions = data.get("decisions", {})
        requirements = apply_thermal_model(model, decisions.get("facts", decisions), current)
        requirements_path.write_text(json.dumps(requirements, indent=2), encoding="utf-8")
        project["design_requirements"] = str(requirements_path)
        model["review_decisions"] = decisions.get("review_items", {})
        model_path.write_text(json.dumps(model, indent=2), encoding="utf-8")
    else:
        raise ValueError("Thermal-model action must be build or save.")
    reasoning = rebuild_reasoning_packet(project, review_dir / "design_requirements.json")
    project.pop("parity_report", None)
    project["thermal_evidence"] = str(evidence_path)
    project["thermal_model"] = str(model_path)
    project["building_evidence"] = str(building_path)
    project["reasoning_packet"] = reasoning["reasoning_packet_raw"]
    project["updated_at"] = timestamp()
    update_project(project)
    response = reasoning["response"]
    response.update({
        "thermal_evidence": load_json(evidence_path),
        "thermal_model": load_json(model_path),
        "thermal_evidence_url": safe_link(evidence_path),
        "thermal_model_url": safe_link(model_path),
        "drawing_coverage": load_json(coverage_path) if coverage_path.exists() else {},
        "drawing_coverage_url": safe_link(coverage_path) if coverage_path.exists() else "",
        "building_evidence": load_json(building_path) if building_path.exists() else {},
        "building_evidence_url": safe_link(building_path) if building_path.exists() else "",
        "requirements": load_json(review_dir / "design_requirements.json") if (review_dir / "design_requirements.json").exists() else empty_design_requirements(),
    })
    return response


def api_save_design_requirements(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    if not project.get("vision_response"):
        raise ValueError("Create a reasoning packet before saving design inputs.")
    requirements = validate_design_requirements(data.get("requirements", data))
    review_dir = Path(project["review_dir"])
    requirements_path = review_dir / "design_requirements.json"
    requirements_path.write_text(json.dumps(requirements, indent=2), encoding="utf-8")
    result = rebuild_reasoning_packet(project, requirements_path)
    project.pop("parity_report", None)
    project["design_requirements"] = str(requirements_path)
    project["reasoning_packet"] = result["reasoning_packet_raw"]
    project["updated_at"] = timestamp()
    update_project(project)
    response = result["response"]
    response.update({
        "requirements_url": safe_link(requirements_path),
        "requirements": requirements,
        "requirements_readiness": requirements_summary(requirements),
        "room_suggestions": project_room_suggestions(project),
        "heat_load_status": "stale" if (review_dir / "heat_load_report.json").exists() else "not_calculated",
        "ventilation_status": "stale" if (review_dir / "ventilation_report.json").exists() else "not_calculated",
    })
    return response


def api_heat_load(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    requirements_path = Path(project["review_dir"]) / "design_requirements.json"
    heat_load_path = current_heat_load_report_path(project, requirements_path)
    legacy_heat_load_path = existing_path(project.get("heat_load_report"), requirements_path.with_name("heat_load_report.json"))
    return {
        "id": project["id"],
        "report": load_json(legacy_heat_load_path) if legacy_heat_load_path else {},
        "status": "current" if heat_load_path else ("stale" if legacy_heat_load_path else "not_calculated"),
        "url": safe_link(legacy_heat_load_path) if legacy_heat_load_path else "",
        "legacy": True,
        "deprecated": True,
        "message": "Legacy flat cooling report is read-only. New cooling calculations use /api/hourly-load-report.",
    }


def api_save_heat_load(request):
    raise ValueError("The legacy cooling calculation endpoint is retired. Use /api/hourly-load-report.")


def api_ventilation(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("project_id", [""])[0])
    requirements_path = Path(project["review_dir"]) / "design_requirements.json"
    report_path = current_ventilation_report_path(project, requirements_path)
    return {
        "id": project["id"],
        "report": load_json(report_path) if report_path else {},
        "status": "current" if report_path else ("stale" if (Path(project["review_dir"]) / "ventilation_report.json").exists() else "not_calculated"),
        "url": safe_link(report_path) if report_path else "",
    }


def api_save_ventilation(request):
    data = read_json_body(request)
    project = project_by_id(data.get("project_id") or data.get("id", ""))
    if not project.get("vision_response"):
        raise ValueError("Create a reasoning packet before calculating ventilation.")
    review_dir = Path(project["review_dir"])
    requirements_path = review_dir / "design_requirements.json"
    if data.get("requirements") is not None:
        requirements = validate_design_requirements(data["requirements"])
    elif requirements_path.exists():
        requirements = validate_design_requirements(load_json(requirements_path))
    else:
        raise ValueError("Enter design inputs before calculating ventilation.")
    requirements_path.write_text(json.dumps(requirements, indent=2), encoding="utf-8")
    report = calculate_ventilation_report(requirements)
    report_path = review_dir / "ventilation_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    project["ventilation_report"] = str(report_path)
    result = rebuild_reasoning_packet(project, requirements_path)
    project["reasoning_packet"] = result["reasoning_packet_raw"]
    project["updated_at"] = timestamp()
    update_project(project)
    response = result["response"]
    response.update({
        "ventilation_report": report,
        "ventilation_report_url": safe_link(report_path),
        "ventilation_status": "current",
    })
    return response


def project_room_suggestions(project):
    ai_input_path = project.get("ai_input")
    if not ai_input_path or not Path(ai_input_path).exists():
        return []
    ai_input = load_json(ai_input_path)
    building_rooms = ai_input.get("building_model", {}).get("rooms", [])
    extracted_rooms = ai_input.get("design_inputs", {}).get("rooms", [])
    suggestions = []
    seen = set()
    for room in building_rooms + extracted_rooms:
        if not isinstance(room, dict):
            continue
        label = str(room.get("name", "")).strip()
        if not label:
            continue
        source_page = room.get("source_page", "")
        key = (label.lower(), source_page)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append({
            "label": label,
            "area": room.get("area", ""),
            "source_page": source_page,
        })
    return suggestions


def save_project_vision_response(project, raw_json, source_label="manual_chatgpt"):
    if not project.get("ai_input") or not project.get("chatgpt_packet"):
        raise ValueError("Create the ChatGPT packet before pasting a vision response.")

    review_dir = Path(project["review_dir"])
    vision = parse_pasted_json(raw_json)
    if not isinstance(vision, dict):
        raise ValueError("Vision response must be a JSON object.")
    vision.setdefault("provider", "chatgpt_manual")
    vision.setdefault("model", "manual_vision_review")
    vision["source"] = source_label or "manual_chatgpt"
    candidate_review_path = existing_path(project.get("candidate_review"), review_dir / "candidate_review.json")
    candidate_review = load_json(candidate_review_path) if candidate_review_path else {}
    vision = normalise_vision(vision, candidate_review)

    vision_path = review_dir / "vision_response.json"
    vision_path.write_text(json.dumps(vision, indent=2), encoding="utf-8")

    reasoning = rebuild_reasoning_packet(project, review_dir / "design_requirements.json")
    return reasoning


def rebuild_reasoning_packet(project, requirements_path=None):
    review_dir = Path(project["review_dir"])
    vision_path = Path(project.get("vision_response") or review_dir / "vision_response.json")
    chatgpt_packet_dir = Path(project["chatgpt_packet"]["folder"])
    reasoning = create_reasoning_packet_from_vision(
        project["ai_input"],
        vision_path,
        chatgpt_packet_dir,
        review_dir / "reasoning_packet",
        zip_packet=True,
        vector_geometry_path=existing_path(project.get("vector_geometry"), review_dir / "vector_geometry.json"),
        dimension_wall_matches_path=existing_path(project.get("dimension_wall_matches"), review_dir / "dimension_wall_matches.json"),
        candidate_review_path=existing_path(project.get("candidate_review"), review_dir / "candidate_review.json"),
        design_requirements_path=requirements_path if requirements_path and Path(requirements_path).exists() else existing_path(project.get("design_requirements"), review_dir / "design_requirements.json"),
        heat_load_report_path=current_heat_load_report_path(project, requirements_path or review_dir / "design_requirements.json"),
        ventilation_report_path=current_ventilation_report_path(project, requirements_path or review_dir / "design_requirements.json"),
        thermal_evidence_path=existing_path(project.get("thermal_evidence"), review_dir / "thermal_evidence.json"),
        thermal_model_path=existing_path(project.get("thermal_model"), review_dir / "thermal_model.json"),
    )

    validation_path = vision_path.with_name("vision_validation.json")
    coordinate_path = vision_path.with_name("coordinate_review.json")
    geometry_confirmation_path = vision_path.with_name("geometry_confirmation.json")
    manifest = load_json(reasoning["manifest"])
    requirements = load_json(requirements_path) if requirements_path and Path(requirements_path).exists() else empty_design_requirements()
    heat_load_path = current_heat_load_report_path(project, requirements_path or review_dir / "design_requirements.json")
    ventilation_path = current_ventilation_report_path(project, requirements_path or review_dir / "design_requirements.json")
    response = {
        "id": project["id"],
        "status": "created",
        "geometry_verification_status": manifest.get("geometry_verification_status", "geometry_not_vision_verified"),
        "vision_response_url": safe_link(vision_path),
        "vision_validation_url": safe_link(validation_path),
        "coordinate_review_url": safe_link(coordinate_path),
        "geometry_confirmation_url": safe_link(geometry_confirmation_path) if geometry_confirmation_path.exists() else "",
        "reasoning_packet": link_pipeline_files(reasoning),
        "reasoning_manifest_url": safe_link(reasoning["manifest"]),
        "reasoning_prompt_url": safe_link(reasoning["prompt"]),
        "reasoning_zip_url": safe_link(reasoning["zip"]) if reasoning.get("zip") else "",
        "issue_count": load_json(validation_path).get("issue_count", 0),
        "requirements": requirements,
        "requirements_readiness": requirements_summary(requirements),
        "heat_load_report_url": safe_link(heat_load_path) if heat_load_path else "",
        "heat_load_status": "current" if heat_load_path else ("stale" if (review_dir / "heat_load_report.json").exists() else "not_calculated"),
        "ventilation_report_url": safe_link(ventilation_path) if ventilation_path else "",
        "ventilation_status": "current" if ventilation_path else ("stale" if (review_dir / "ventilation_report.json").exists() else "not_calculated"),
    }
    return {
        "response": response,
        "reasoning_packet_raw": reasoning,
        "vision_response_path": str(vision_path),
        "vision_validation_path": str(validation_path),
        "coordinate_review_path": str(coordinate_path),
        "geometry_confirmation_path": str(geometry_confirmation_path) if geometry_confirmation_path.exists() else "",
    }


def current_heat_load_report_path(project, requirements_path):
    requirements_path = Path(requirements_path)
    candidate = existing_path(project.get("heat_load_report"), requirements_path.with_name("heat_load_report.json"))
    if not candidate or not requirements_path.exists():
        return None
    report = load_json(candidate)
    requirements = load_json(requirements_path)
    if report.get("requirements_updated_at") != requirements.get("updated_at"):
        return None
    library, model = envelope_artifacts(project)
    return candidate if report.get("envelope_library_updated_at", "") == library.get("updated_at", "") and report.get("envelope_model_updated_at", "") == model.get("updated_at", "") else None


def current_ventilation_report_path(project, requirements_path):
    requirements_path = Path(requirements_path)
    candidate = existing_path(project.get("ventilation_report"), requirements_path.with_name("ventilation_report.json"))
    if not candidate or not requirements_path.exists():
        return None
    report = load_json(candidate)
    requirements = load_json(requirements_path)
    return candidate if report.get("requirements_updated_at") == requirements.get("updated_at") else None


def current_hourly_load_report_path(project):
    if not project.get("review_dir"):
        return None
    paths = hourly_paths(project)
    candidate = existing_path(project.get("hourly_load_report"), paths["report"])
    if not candidate or any(not paths[name].exists() for name in ("requirements", "schedules", "scenarios", "model")):
        return None
    report = load_json(candidate)
    fingerprints = report.get("input_fingerprints", {})
    expected = {
        "requirements_updated_at": load_json(paths["requirements"]).get("updated_at", ""),
        "schedule_library_updated_at": load_json(paths["schedules"]).get("updated_at", ""),
        "design_day_scenarios_updated_at": load_json(paths["scenarios"]).get("updated_at", ""),
        "hourly_load_model_updated_at": load_json(paths["model"]).get("updated_at", ""),
    }
    library, model = envelope_artifacts(project)
    expected["envelope_library_updated_at"] = library.get("updated_at", "")
    expected["envelope_model_updated_at"] = model.get("updated_at", "")
    return candidate if fingerprints == expected else None


def parse_pasted_json(raw_json):
    text = raw_json if isinstance(raw_json, str) else json.dumps(raw_json)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Vision response is not valid JSON: {error.msg}") from error


def existing_path(value, fallback):
    path = Path(value) if value else Path(fallback)
    return path if path.exists() else None


def api_saved_analysis(request):
    query = parse_qs(urlparse(request.path).query)
    project = project_by_id(query.get("id", [""])[0])
    if needs_analysis_rebuild(project):
        if not Path(project.get("pdf", "")).is_file():
            raise ValueError("This saved project cannot be rebuilt because its original PDF is missing. Upload it again.")
        project = analyse_project(project)
    return analysis_response(project)


def process_upload(request):
    filename, content = read_pdf_upload(request)
    if not filename:
        raise ValueError("No PDF was uploaded.")

    if Path(filename).suffix.lower() != ".pdf":
        raise ValueError("Upload must be a PDF.")

    UPLOADS.mkdir(parents=True, exist_ok=True)
    pdf_path = UPLOADS / safe_folder_name(Path(filename).name)
    with pdf_path.open("wb") as file:
        file.write(content)

    review_dir = WEB_REVIEW / safe_folder_name(pdf_path.stem)
    result = create_review_packet(pdf_path, review_dir, include_structure=True)

    packet = load_json(result["packet"])
    ai_output = Path(result["review_dir"]) / "ai_input.json"
    ai_input = build_ai_packet(packet)
    ai_output.write_text(json.dumps(ai_input, indent=2), encoding="utf-8")
    coverage_output = Path(result["review_dir"]) / "drawing_coverage.json"
    coverage_output.write_text(json.dumps(build_drawing_coverage(ai_input), indent=2), encoding="utf-8")
    building_output = Path(result["review_dir"]) / "building_evidence.json"
    building_output.write_text(json.dumps(build_building_evidence(ai_input, load_json(coverage_output)), indent=2), encoding="utf-8")

    result["ai_input"] = str(ai_output)
    result["drawing_coverage"] = str(coverage_output)
    result["building_evidence"] = str(building_output)
    result["uploaded_pdf"] = str(pdf_path)
    return result


def analysis_response(project):
    packet = load_json(project["packet"])
    sheets = sheets_from_packet(packet, Path(project["review_dir"]))
    review_dir = Path(project["review_dir"])
    site_conditions_path = existing_path(project.get("site_design_conditions"), review_dir / "site_design_conditions.json")
    site_conditions = load_json(site_conditions_path) if site_conditions_path else empty_site_design_conditions()
    paths = hourly_paths(project)
    hourly_current = current_hourly_load_report_path(project)
    return {
        "id": project["id"],
        "name": project["name"],
        "pages_analysed": project["pages"],
        "relevant_count": len([sheet for sheet in sheets if sheet["kept_for_review"]]),
        "selected_count": len([sheet for sheet in sheets if sheet["relevant"]]),
        "sheets": sheets,
        "warnings": analysis_warnings(packet),
        "review_url": optional_link(project.get("html")),
        "packet_url": optional_link(project.get("packet")),
        "ai_input_url": optional_link(project.get("ai_input")),
        "drawing_coverage_url": optional_link(project.get("drawing_coverage")),
        "chatgpt_packet": link_pipeline_files(project.get("chatgpt_packet", {})),
        "reasoning_packet": link_pipeline_files(project.get("reasoning_packet", {})),
        "has_reasoning_packet": bool(project.get("reasoning_packet")),
        "design_requirements": load_json(project["design_requirements"]) if project.get("design_requirements") and Path(project["design_requirements"]).exists() else empty_design_requirements(),
        "site_design_conditions_url": safe_link(site_conditions_path) if site_conditions_path else "",
        "site_design_conditions_status": site_design_conditions_summary(site_conditions)["status"],
        "schedule_library_url": safe_link(paths["schedules"]) if paths["schedules"].exists() else "",
        "schedule_library_status": schedule_library_summary(load_json(paths["schedules"]) if paths["schedules"].exists() else empty_schedule_library())["status"],
        "design_day_scenarios_url": safe_link(paths["scenarios"]) if paths["scenarios"].exists() else "",
        "design_day_scenarios_status": design_day_summary(load_json(paths["scenarios"]) if paths["scenarios"].exists() else empty_design_day_scenarios())["status"],
        "hourly_load_model_url": safe_link(paths["model"]) if paths["model"].exists() else "",
        "hourly_load_model_status": hourly_model_summary(load_json(paths["model"]) if paths["model"].exists() else empty_hourly_load_model(), load_json(paths["requirements"]) if paths["requirements"].exists() else None)["status"],
        "hourly_load_report_url": safe_link(hourly_current) if hourly_current else "",
        "hourly_load_report_status": "current" if hourly_current else ("stale" if paths["report"].exists() else "not_calculated"),
    }


def needs_analysis_rebuild(project):
    required_paths = ("packet", "review_dir", "ai_input")
    return (
        not project.get("analysed")
        or project.get("analysis_version") != ANALYSIS_VERSION
        or any(not saved_path_exists(project.get(key)) for key in required_paths)
    )


def saved_path_exists(value):
    return bool(value) and Path(value).exists()


def sheets_from_packet(packet, review_dir):
    sheets = []
    kept_pages = packet.get("kept_pages", packet["primary_pages"] + packet["reference_pages"])
    kept_numbers = {page["page"] for page in kept_pages}

    for page in kept_pages:
        sheets.append(sheet_summary(page, review_dir))

    for page in packet.get("discarded_pages", []):
        if page["page"] not in kept_numbers:
            sheets.append(discarded_sheet(page))

    return sorted(sheets, key=lambda sheet: sheet["page"])


def sheet_summary(page, review_dir):
    extracted = page.get("extracted", {})
    visual = page.get("visual_features") or {}
    thermal_role = page.get("thermal_role", "not_calculation_evidence")
    selected_by_default = thermal_role != "not_calculation_evidence" or page.get("packet_role") in {
        "symbol_key_context",
        "equipment_schedule_context",
    }
    return {
        "page": page["page"],
        "type": page.get("type", ""),
        "title": page.get("title", f"Page {page['page']}"),
        "reason": page_reason(page),
        "confidence": page.get("confidence", 0.0),
        "relevant": page.get("review_bucket") == "primary",
        "selected_by_default": selected_by_default,
        "packet_role": page.get("packet_role", ""),
        "plan_role": page.get("plan_role", ""),
        "sheet_classification": page.get("sheet_classification", page.get("type", "other")),
        "thermal_role": thermal_role,
        "classification_evidence": page.get("classification_evidence", ""),
        "kept_for_review": True,
        "review_bucket": page.get("review_bucket", ""),
        "scale": extracted.get("scale"),
        "dimension_count": len(extracted.get("written_dimensions", [])),
        "room_count": len(extracted.get("rooms", [])),
        "level_name": extracted.get("level_name", ""),
        "level_status": "detected" if extracted.get("level_name") else "needs_confirmation",
        "visual": visual,
        "thumbnail": thumbnail_url(page, review_dir),
    }


def discarded_sheet(page):
    return {
        "page": page["page"],
        "type": page.get("type", "discarded"),
        "title": page.get("title", "Discarded page"),
        "reason": "Discarded as obvious non-HVAC context.",
        "confidence": 0.0,
        "relevant": False,
        "kept_for_review": False,
        "thumbnail": "",
    }


def page_reason(page):
    if page.get("packet_role") == "symbol_key_context":
        return "Supporting context: HVAC/RCP legend or symbol key."
    if page.get("packet_role") == "equipment_schedule_context":
        return "Supporting context: equipment, grille, diffuser, or fixture schedule."
    if page.get("review_bucket") == "unclassified":
        return "Kept for review because it was not confidently irrelevant."
    title_hits = page.get("matched_title_words", [])
    if title_hits:
        return "Matched drawing title: " + ", ".join(title_hits)
    support_hits = page.get("matched_support_words", [])
    if support_hits:
        return "Matched HVAC/context terms: " + ", ".join(support_hits[:4])
    return "Kept as possible HVAC design context."


def thumbnail_url(page, review_dir):
    path = page.get("thumbnail_path")
    return link(review_dir / path) if path else ""


def analysis_warnings(packet):
    warnings = []
    if not packet.get("primary_pages"):
        warnings.append("No essential HVAC design pages were confidently identified.")
    if packet.get("discarded_pages"):
        warnings.append("Pages marked non-calculation evidence are still retained in the drawing-set register for review.")
    return warnings


def link_pipeline_files(status):
    linked = {}
    for key, value in status.items():
        if key in {"folder", "prompt", "context", "ai_input", "manifest", "zip"} or key.endswith("_url"):
            linked[key] = safe_link(value)
        elif key in {"screenshots", "vision_evidence"}:
            linked[key] = [safe_link(path) for path in value if path]
        else:
            linked[key] = value
    return linked


def upload_response(project):
    return {
        "id": project["id"],
        "name": project["name"],
        "pages": project["pages"],
        "size_bytes": project["size_bytes"],
    }


def project_list():
    projects = sorted(load_projects().values(), key=lambda item: item.get("updated_at", ""), reverse=True)
    return [
        {
            "id": project["id"],
            "name": project["name"],
            "pages": project.get("pages", 0),
            "analysed": project.get("analysed", False),
            "relevant": project.get("relevant", 0),
        }
        for project in projects
    ]


def project_by_id(project_id):
    project = load_projects().get(project_id)
    if not project:
        raise ValueError("Project was not found.")
    return project


def update_project(project):
    projects = load_projects()
    projects[project["id"]] = project
    save_projects(projects)


def load_projects():
    if not PROJECTS_FILE.exists():
        return {}
    with PROJECTS_FILE.open(encoding="utf-8") as file:
        return json.load(file)


def save_projects(projects):
    PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROJECTS_FILE.write_text(json.dumps(projects, indent=2), encoding="utf-8")


def unique_project_id(filename):
    base = safe_folder_name(Path(filename).stem).replace(" ", "-").lower()
    return f"{base}-{int(time.time())}"


def timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def pdf_page_count(pdf_path):
    return count_pdf_pages(pdf_path)


def read_json_body(request):
    length = int(request.headers.get("Content-Length", "0"))
    if not length:
        return {}
    return json.loads(request.rfile.read(length).decode("utf-8"))


def read_form_fields(request):
    length = int(request.headers.get("Content-Length", "0"))
    body = request.rfile.read(length).decode("utf-8")
    fields = {}
    for pair in body.split("&"):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        fields[unquote_form(key)] = unquote_form(value)
    return fields


def unquote_form(value):
    from urllib.parse import unquote_plus

    return unquote_plus(value)


def read_pdf_upload(request):
    content_type = request.headers.get("Content-Type", "")
    boundary_match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type)
    if not boundary_match:
        raise ValueError("Upload form is missing a multipart boundary.")

    length = int(request.headers.get("Content-Length", "0"))
    body = request.rfile.read(length)
    boundary = ("--" + boundary_match.group("boundary").strip('"')).encode()

    for part in body.split(boundary):
        if b'name="pdf"' not in part or b"filename=" not in part:
            continue
        headers, content = part.split(b"\r\n\r\n", 1)
        filename = filename_from_headers(headers.decode("utf-8", errors="ignore"))
        return filename, content.removesuffix(b"\r\n")

    return "", b""


def filename_from_headers(headers):
    match = re.search(r'filename="([^"]+)"', headers)
    return Path(match.group(1)).name if match else ""


def result_page(result):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Processing Complete</title>
</head>
<body>
  <h1>Processing Complete</h1>
  <p>Uploaded PDF: {escape(result["uploaded_pdf"])}</p>
  <ul>
    <li><a href="{link(result["html"])}">Open review page</a></li>
    <li><a href="{link(result["packet"])}">Open packet.json</a></li>
    <li><a href="{link(result["ai_input"])}">Open ai_input.json</a></li>
  </ul>
  <h2>Summary</h2>
  <ul>
    <li>Thumbnails: {result["thumbnail_count"]}</li>
    <li>Primary pages: {result["primary_count"]}</li>
    <li>Reference pages: {result["reference_count"]}</li>
    <li>Kept pages: {result["kept_count"]}</li>
    <li>Discarded pages: {result["discarded_count"]}</li>
  </ul>
  <p><a href="/">Process another PDF</a></p>
</body>
</html>
"""


def error_page(error):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Processing Error</title>
</head>
<body>
  <h1>Processing Error</h1>
  <p>{escape(error)}</p>
  <p><a href="/">Try again</a></p>
</body>
</html>
"""


def link(path):
    return "/" + quote(Path(path).resolve().relative_to(ROOT).as_posix(), safe="/")


def safe_link(path):
    try:
        return link(path)
    except ValueError:
        return str(path)


def optional_link(path):
    return safe_link(path) if path else ""


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def escape(value):
    return html.escape(str(value), quote=True)


def main():
    parser = argparse.ArgumentParser(description="Run the Mech Page Finder web app.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Open http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
