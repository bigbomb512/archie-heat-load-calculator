#!/usr/bin/env python3
"""Score a PDF-only house result against a hand-checked one, both recalculated with this checkout's code and no AI calls.

    python3 tools/score_house_pdf_only.py --cases output/house_cases.json [--case house15 ...]

The cases file (kept under the ignored output/ folder, because job folder names carry addresses) maps a neutral label to
the two jobs of the same PDF:

    {"house15": {"checked": "/abs/path/to/checked/job", "ai": "/abs/path/to/pdf-only/job",
                 "level_pages": {"Lower Ground Floor": 4}}}

"level_pages" says which plan page a checked level belongs to when its rooms were traced on another, aligned sheet.
"checked_pdf" / "ai_pdf" give a copy of the same drawing PDF for a job whose own upload is gone (a job saved in a
since-removed worktree). It is used only in the copy, and only when its SHA-256 matches the fingerprint the job's
outlines were drawn on; otherwise the case stops, because the outlines can't be checked against the drawing.

Each job is copied (saved jobs are never touched), its model rebuilt from the saved answers and its full load
calculated, so both numbers come from the same code. What is compared:
- the whole-house design load, and whether the PDF-only result called itself complete;
- floor: how much of the checked cooled floor the PDF-only cooled outlines cover on the same plan page, and how much
  PDF-only cooled floor lies outside it; rooms with an area but no outline are listed separately;
- each checked room: the PDF-only room covering most of it, with both areas and peak loads;
- glass area by facing (N/E/S/W) and the north each plan page was given.
A hand-checked answer is a careful check, not engineering validation; the report says so.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP = shutil.ignore_patterns("*_overlays", "thumbnails", "reasoning_packet.zip", "chatgpt_packet.zip", "chatgpt_runs",
                              "ai_preliminary_input_sets", "page_analysis_cache", "drawing_reply_cache", "skill_reply_cache")
FALSE_COMPLETE_LOAD = 0.20      # a result more than 20 % off the checked load,
FALSE_COMPLETE_FLOOR = 0.80     # or covering under 80 % of the checked cooled floor, should not call itself complete


# ------------------------------------------------------------------ recalculation (runs in a child process)
def use_stand_in_pdf(target, project, pdf):
    """Point the copied job at an identical copy of its drawing PDF (same SHA-256 as its outlines record)."""
    import hashlib
    pdf = Path(pdf)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    geometry_file = target / "reviewer_room_geometry.json"
    records = json.loads(geometry_file.read_text(encoding="utf-8")).get("records", []) if geometry_file.exists() else []
    expected = {(row.get("source_fingerprints") or {}).get("source_pdf") for row in records} - {None, ""}
    if expected and expected != {digest}:
        raise SystemExit(f"{pdf.name} is not the PDF this job's outlines were drawn on (SHA-256 {digest[:12]}…).")
    ai_input_file = target / "ai_input.json"
    if ai_input_file.exists():
        ai_input = json.loads(ai_input_file.read_text(encoding="utf-8"))
        ai_input["source_pdf"] = str(pdf)
        ai_input_file.write_text(json.dumps(ai_input), encoding="utf-8")
    project["pdf"] = str(pdf)


def recalc(job_dir, copy_root, out_file, pdf=""):
    """Copy one job, rebuild its model from the saved answers and calculate the full load; write the facts to compare."""
    sys.path.insert(0, str(REPO))
    from backend import ai_provider

    def no_ai(*_args, **_kwargs):
        raise RuntimeError("No AI calls while scoring.")
    ai_provider.get = ai_provider.recorded = no_ai
    from backend import ai_preliminary_service as model, calculation_service
    from backend import reviewer_room_geometry_service as geometry

    source = Path(job_dir)
    target = Path(copy_root) / source.name
    shutil.copytree(source, target, ignore=SKIP)
    projects = json.loads((source.parent.parent / "web_projects.json").read_text(encoding="utf-8"))
    project = json.loads(json.dumps(projects[source.name]).replace(json.dumps(str(source))[1:-1], json.dumps(str(target))[1:-1]))
    if pdf:
        use_stand_in_pdf(target, project, pdf)

    class Web:
        WEB_REVIEW = str(target.parent)

        def project_by_id(self, _project_id):
            return project

        def update_project(self, updated):
            project.update(updated)
            return project

        def safe_link(self, path):
            return str(path)

        def workflow_required_artifacts(self, *_args, **_kwargs):
            return []

    web = Web()
    model._assemble(web, project)
    phase1 = model._calculate(web, project, scope="phase1_base_room")
    paths = model._paths(project)
    scope = model._room_scope_state(paths, model._read(paths["current_set"], {}))
    model._confirm_room_scope(paths, {"reviewer": "House scoreboard", "rows": calculation_service._rows(scope, {}),
                                      "candidate_fingerprint": scope.get("candidate_fingerprint", "")})
    report = model._calculate(web, project, scope="full")
    built = json.loads((target / "ai_preliminary_model.json").read_text(encoding="utf-8"))
    artifact = json.loads((target / "reviewer_room_geometry.json").read_text(encoding="utf-8")) \
        if (target / "reviewer_room_geometry.json").exists() else {}
    traces = geometry.current_records(geometry._paths(project), artifact) if artifact else []
    Path(out_file).write_text(json.dumps(facts(built, report, phase1, traces, artifact.get("page_north") or {}), default=str),
                              encoding="utf-8")


def facts(built, report, phase1, traces, page_north):
    """The comparable facts of one calculated job."""
    topology = built.get("topology") or {}
    floors = {row["floor_id"]: row.get("name", "") for row in topology.get("floors", [])}
    zones = {row["zone_id"]: floors.get(row.get("floor_id"), "") for row in topology.get("zones", [])}
    peak = report.get("included_scope_peak") or {}
    room_peaks = {}
    for scenario in report.get("scenario_results", []):
        for room in scenario.get("rooms", []):
            best = max((sum(c.get("total_kw") or 0 for c in (hour.get("components") or {}).values() if isinstance(c, dict))
                        for hour in room.get("hours") or []), default=0.0)
            room_peaks[room["room_id"]] = max(room_peaks.get(room["room_id"], 0.0), best)
    outlines = {}
    for trace in traces:
        key = (str(trace.get("room_label", "")).casefold(), str(trace.get("level_name") or "").casefold())
        if trace.get("points_image_px") and len(trace["points_image_px"]) >= 4:
            outlines.setdefault(key, []).append({"page": trace.get("page"), "points": trace["points_image_px"]})
    rooms = []
    for room in topology.get("rooms", []):
        level = zones.get(room.get("zone_id"), "")
        glass = {}
        for row in (room.get("cooling_load") or {}).get("glazing_surfaces", []):
            side = row.get("orientation") or "?"
            glass[side] = glass.get(side, 0.0) + float(row.get("explicit_opening_area_m2") or 0)
        # A zero-load row (a driveway left in the model) isn't cooled floor.
        rooms.append({"room_id": room["room_id"], "name": room.get("name", ""), "level": level,
                      "area_m2": room.get("area_m2"), "ceiling_mm": room.get("ceiling_height_mm"),
                      "cooled": room_peaks.get(room["room_id"], 0.0) > 0,
                      "peak_kw": round(room_peaks.get(room["room_id"], 0.0), 3),
                      "glass_m2": {k: round(v, 2) for k, v in glass.items()},
                      "outlines": outlines.get((str(room.get("name", "")).casefold(), level.casefold()), [])})
    coverage = (phase1 or {}).get("phase1_coverage") or report.get("phase1_coverage") or {}
    return {"total_kw": peak.get("final_design_total_kw", peak.get("design_total_kw")), "month": peak.get("month"),
            "hour": peak.get("hour"), "status": report.get("status"),
            "complete": coverage.get("complete"), "partial_label": coverage.get("partial_label", ""),
            "north": {str(page): row.get("plan_up_azimuth_deg") for page, row in page_north.items() if isinstance(row, dict)},
            "rooms": rooms}


# ------------------------------------------------------------------ comparison (pure; tested)
def _polygon(points):
    from shapely.geometry import Polygon
    shape = Polygon([tuple(p) for p in points])
    return shape if shape.is_valid else shape.buffer(0)


def compare(checked, ai, level_pages=None, mm_per_px=None):
    """Score the PDF-only facts `ai` against the hand-checked facts `checked` (both from facts())."""
    from shapely.ops import unary_union
    level_pages = {str(k).casefold(): v for k, v in (level_pages or {}).items()}

    def shapes(facts_, cooled_only=True, remap=None):
        by_page = {}
        for room in facts_["rooms"]:
            if cooled_only and not room["cooled"]:
                continue
            for outline in room["outlines"]:
                page = (remap or {}).get(room["level"].casefold(), outline["page"])
                by_page.setdefault(page, []).append((room, _polygon(outline["points"])))
        return by_page

    ref_shapes, ai_shapes = shapes(checked, remap=level_pages), shapes(ai)
    scale = None
    rows, covered, ref_total, ai_outside, ai_total = [], 0.0, 0.0, 0.0, 0.0
    for page, ref_rooms in ref_shapes.items():
        ref_union = unary_union([shape for _room, shape in ref_rooms])
        ai_rooms = ai_shapes.get(page, [])
        ai_union = unary_union([shape for _room, shape in ai_rooms]) if ai_rooms else None
        # m² per px², from the checked rooms' own areas and outlines on this page
        sized = [(room["area_m2"], shape.area) for room, shape in ref_rooms if room.get("area_m2") and shape.area]
        k = (sum(a for a, _ in sized) / sum(s for _, s in sized)) if sized else ((mm_per_px or 14.11) / 1000) ** 2
        scale = scale or k
        ref_total += ref_union.area * k
        if ai_union is not None:
            covered += ref_union.intersection(ai_union).area * k
            ai_outside += ai_union.difference(ref_union).area * k
            ai_total += ai_union.area * k
        for room, shape in ref_rooms:
            best, best_overlap = None, 0.0
            for other, other_shape in ai_rooms:
                overlap = shape.intersection(other_shape).area
                if overlap > best_overlap:
                    best, best_overlap = other, overlap
            share = best_overlap / shape.area if shape.area else 0.0
            rows.append({"room": room["name"], "level": room["level"], "page": page, "area_m2": room["area_m2"],
                         "peak_kw": room["peak_kw"], "ai_room": best["name"] if best and share >= 0.25 else None,
                         "ai_area_m2": best["area_m2"] if best and share >= 0.25 else None,
                         "ai_peak_kw": best["peak_kw"] if best and share >= 0.25 else None, "covered": round(share, 2)})
    for page, ai_rooms in ai_shapes.items():      # PDF-only cooled outlines on pages with no checked rooms at all
        if page not in ref_shapes:
            area = unary_union([shape for _room, shape in ai_rooms]).area * (scale or ((mm_per_px or 14.11) / 1000) ** 2)
            ai_outside += area
            ai_total += area
    no_outline = [{"room": r["name"], "level": r["level"], "area_m2": r["area_m2"], "peak_kw": r["peak_kw"]}
                  for r in ai["rooms"] if r["cooled"] and not r["outlines"]]

    def glass(facts_):
        sides = {}
        for room in facts_["rooms"]:
            if room["cooled"]:
                for side, area in room["glass_m2"].items():
                    sides[side] = round(sides.get(side, 0.0) + area, 1)
        return sides

    def cooled_area(facts_):
        return round(sum(r["area_m2"] or 0 for r in facts_["rooms"] if r["cooled"]), 1)

    ref_kw, ai_kw = checked.get("total_kw") or 0.0, ai.get("total_kw") or 0.0
    load_error = (ai_kw - ref_kw) / ref_kw if ref_kw else None
    floor_covered = covered / ref_total if ref_total else 0.0
    north = {}
    for page, value in checked.get("north", {}).items():
        other = ai.get("north", {}).get(page)
        north[page] = {"checked": value, "ai": other,
                       "error_deg": round(abs((other - value + 180) % 360 - 180), 1) if isinstance(other, (int, float)) and isinstance(value, (int, float)) else None}
    wrong = (load_error is not None and abs(load_error) > FALSE_COMPLETE_LOAD) or floor_covered < FALSE_COMPLETE_FLOOR
    return {"load": {"checked_kw": round(ref_kw, 2), "ai_kw": round(ai_kw, 2),
                     "error": round(load_error, 3) if load_error is not None else None,
                     "checked_peak": [checked.get("month"), checked.get("hour")], "ai_peak": [ai.get("month"), ai.get("hour")]},
            "honesty": {"ai_complete": ai.get("complete"), "ai_label": ai.get("partial_label", ""),
                        "should_not_be_complete": wrong, "false_complete": bool(ai.get("complete")) and wrong},
            "floor": {"checked_cooled_m2": cooled_area(checked), "ai_cooled_m2": cooled_area(ai),
                      "checked_outlined_m2": round(ref_total, 1), "covered_m2": round(covered, 1),
                      "covered_share": round(floor_covered, 3), "ai_outlined_m2": round(ai_total, 1),
                      "ai_outside_checked_m2": round(ai_outside, 1), "ai_cooled_without_outline": no_outline},
            "rooms": rows, "glass_m2": {"checked": glass(checked), "ai": glass(ai)}, "north": north,
            "cooled_rooms": {"checked": sum(r["cooled"] for r in checked["rooms"]), "ai": sum(r["cooled"] for r in ai["rooms"])}}


def markdown(label, score):
    load, floor, honesty = score["load"], score["floor"], score["honesty"]
    error = f"{load['error'] * 100:+.0f}%" if load["error"] is not None else "n/a"
    no_outline = ", ".join(f"{r['room']} {r['area_m2']:.0f} m²" for r in floor["ai_cooled_without_outline"] if r["area_m2"]) or "none"
    north = ", ".join(f"p{page} {row['ai']} vs {row['checked']}" for page, row in score["north"].items()) or "none declared"
    lines = [f"## {label}", "",
             f"- Load: PDF-only {load['ai_kw']} kW vs checked {load['checked_kw']} kW ({error}); peaks {load['ai_peak']} vs {load['checked_peak']}",
             f"- Called itself complete: {honesty['ai_complete']} ({honesty['ai_label'] or 'no partial label'}); "
             f"should not be complete: {honesty['should_not_be_complete']}; **false complete: {honesty['false_complete']}**",
             f"- Cooled floor: PDF-only {floor['ai_cooled_m2']} m² vs checked {floor['checked_cooled_m2']} m²; outlines cover "
             f"{floor['covered_share'] * 100:.0f}% of the checked cooled floor; {floor['ai_outside_checked_m2']} m² of PDF-only outline lies outside it",
             f"- Cooled rooms: PDF-only {score['cooled_rooms']['ai']} vs checked {score['cooled_rooms']['checked']}; "
             f"cooled without an outline: {no_outline}",
             f"- Glass m² by facing: PDF-only {score['glass_m2']['ai']} vs checked {score['glass_m2']['checked']}",
             f"- North (sheet-up bearing) by page: {north}",
             "", "| Checked room | Level | m² | kW | PDF-only room | m² | kW | covered |", "|---|---|---|---|---|---|---|---|"]
    for row in sorted(score["rooms"], key=lambda r: -(r["peak_kw"] or 0)):
        lines.append(f"| {row['room']} | {row['level']} | {row['area_m2'] or 0:.0f} | {row['peak_kw']:.2f} | {row['ai_room'] or '—'} | "
                     f"{row['ai_area_m2'] or 0:.0f} | {row['ai_peak_kw'] or 0:.2f} | {row['covered'] * 100:.0f}% |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", default=str(REPO / "output" / "house_cases.json"))
    parser.add_argument("--case", action="append", help="score only these labels")
    parser.add_argument("--out", default=str(REPO / "output" / "evaluations"))
    parser.add_argument("--recalc", nargs="+", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.recalc:
        return recalc(*args.recalc)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results, sections = {}, ["# PDF-only house scoreboard", "",
                             f"Code: {subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()}. "
                             "Both jobs of each case are recalculated with this code and no AI calls. The checked answers are a careful "
                             "hand check of the drawings, not engineering validation.", ""]
    with tempfile.TemporaryDirectory() as temp:
        for label, case in cases.items():
            if args.case and label not in args.case:
                continue
            got = {}
            for side in ("checked", "ai"):
                result = Path(temp) / f"{label}-{side}.json"
                copy = Path(temp) / f"{label}-{side}"
                copy.mkdir()
                started = time.time()
                run = subprocess.run([sys.executable, __file__, "--recalc", case[side], str(copy), str(result),
                                      *([case[f"{side}_pdf"]] if case.get(f"{side}_pdf") else [])],
                                     capture_output=True, text=True, cwd=REPO)
                if run.returncode or not result.exists():
                    # A job that can't be calculated is a result too (a PDF-only run that stopped early has no number).
                    reason = (run.stderr.strip().splitlines() or ["no output"])[-1][:300]
                    got[side] = {"failed": reason}
                    print(f"{label} {side}: could not be calculated: {reason}", flush=True)
                    break
                got[side] = json.loads(result.read_text(encoding="utf-8"))
                print(f"{label} {side}: {got[side]['total_kw']} kW ({time.time() - started:.0f} s)", flush=True)
            failed = {side: row["failed"] for side, row in got.items() if "failed" in row}
            if failed:
                results[label] = {"not_scored": failed}
                sections.append(f"## {label}\n\nNot scored: " + "; ".join(f"{side} job could not be calculated ({why})"
                                                                         for side, why in failed.items()) + "\n")
                continue
            score = compare(got["checked"], got["ai"], case.get("level_pages"))
            results[label] = score
            sections.append(markdown(label, score))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"house_scoreboard-{stamp}.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    (out / f"house_scoreboard-{stamp}.md").write_text("\n".join(sections), encoding="utf-8")
    print("\n".join(sections))
    print(f"Written to {out}/house_scoreboard-{stamp}.md")


if __name__ == "__main__":
    main()
