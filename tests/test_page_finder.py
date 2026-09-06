#!/usr/bin/env python3

import base64
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.chatgpt_packet import DEFAULT_DPI, build_prompt, create_chatgpt_packet, selected_images
from ai.coordinate_review import create_coordinate_review, image_point_to_plan_px
from ai.ai_packet import AI_CONTEXT_CONFIDENCE_THRESHOLD, build_ai_packet
from ai.reasoning_packet import create_reasoning_packet
from backend.web_app import sheet_summary
from pdf_pipeline.extractors import extract_level_name, extract_rooms, extract_scale, extract_text_pages, extract_written_dimensions
from pdf_pipeline.page_finder import analyze_pages, classify_page, classify_reference_page, has_top_view_signal, is_primary_discard
from pdf_pipeline.renderer import page_number_from_path
from pdf_pipeline.review import safe_folder_name
from pdf_pipeline.spatial_ocr import dimension_candidates as spatial_dimension_candidates
from pdf_pipeline.spatial_ocr import region_summary, scale_candidates, title_block_regions
from pdf_pipeline.spatial_ocr import useful_pages as spatial_useful_pages
from pdf_pipeline.structured_pdf import table_markdown, useful_page_structure
from pdf_pipeline.visual_features import image_features
from ai.vision_validator import validate_vision


def check(name, text, expected_type):
    result = classify_page(text, 1)
    actual_type = None if result is None else result["type"]
    if actual_type != expected_type:
        raise AssertionError(f"{name}: expected {expected_type}, got {actual_type}")
    print(f"PASS - {name}")


def check_value(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def check_reference(name, text, expected_type):
    result = classify_reference_page(text, 1)
    actual_type = None if result is None else result["type"]
    if actual_type != expected_type:
        raise AssertionError(f"{name}: expected {expected_type}, got {actual_type}")
    print(f"PASS - {name}")


def check_page_with_document_title(name, text, document_title, expected_type):
    result = classify_page(text, 1, document_title)
    actual_type = None if result is None else result["type"]
    if actual_type != expected_type:
        raise AssertionError(f"{name}: expected {expected_type}, got {actual_type}")
    print(f"PASS - {name}")


def sample_packet():
    return {
        "pdf": "/tmp/sample.pdf",
        "primary_pages": [
            {
                "page": 1,
                "type": "floor_plan",
                "importance": "essential",
                "title": "General Arrangement Plan",
                "confidence": 0.9,
                "thumbnail_path": "thumbnails/page_001.png",
                "extracted": {
                    "scale": "1:100",
                    "level_name": "Ground Floor",
                    "written_dimensions": [{"value": 1200, "unit": "mm"}],
                    "rooms": [{"name": "Office", "area": "9 m²"}],
                    "ceiling_constraints": ["lighting"],
                    "hvac_terms": [],
                    "drawing_number": "A1.01",
                    "notes_for_ai": ["Use this page for walls."],
                },
            },
            {
                "page": 2,
                "type": "reflected_ceiling_plan",
                "importance": "essential",
                "title": "Reflected Ceiling Plan",
                "confidence": 0.9,
                "thumbnail_path": "thumbnails/page_002.png",
                "extracted": {
                    "scale": "1:100",
                    "level_name": "Ground Floor",
                    "written_dimensions": [],
                    "rooms": [],
                    "ceiling_constraints": ["sprinkler"],
                    "hvac_terms": ["diffuser"],
                    "drawing_number": "A1.02",
                    "notes_for_ai": ["Use this page for diffuser placement."],
                },
            },
        ],
        "reference_pages": [],
        "kept_pages": [
            {
                "page": 1,
                "type": "floor_plan",
                "importance": "essential",
                "title": "General Arrangement Plan",
                "thumbnail_path": "thumbnails/page_001.png",
                "review_bucket": "primary",
                "extracted": {},
            },
            {
                "page": 2,
                "type": "reflected_ceiling_plan",
                "importance": "essential",
                "title": "Reflected Ceiling Plan",
                "thumbnail_path": "thumbnails/page_002.png",
                "review_bucket": "primary",
                "extracted": {},
            },
            {
                "page": 3,
                "type": "unclassified_context",
                "importance": "possible_context",
                "title": "Possibly Useful Detail",
                "thumbnail_path": "thumbnails/page_003.png",
                "review_bucket": "unclassified",
                "extracted": {},
            },
        ],
        "discarded_pages": [],
        "structured_pages": [
            {
                "page": 1,
                "width": 100,
                "height": 100,
                "word_count": 12,
                "table_count": 1,
                "needs_ocr": False,
                "markdown": "General Arrangement Plan",
                "elements": [{"type": "word", "text": "General", "bbox": [1, 2, 3, 4]}],
            }
        ],
    }


def analyze_pages_texts(texts):
    import pdf_pipeline.page_finder as page_finder

    original = page_finder.extract_text_pages
    page_finder.extract_text_pages = lambda path: texts
    try:
        return analyze_pages("fake.pdf")
    finally:
        page_finder.extract_text_pages = original


def sample_ai_input(image_path):
    return {
        "source_pdf": "/tmp/sample.pdf",
        "source_files": {
            "pdf": "/tmp/sample.pdf",
            "page_images": [
                {"page": 1, "type": "floor_plan", "title": "Layout Plan", "path": image_path},
            ],
        },
        "review_status": {"human_reviewed": False},
        "confirmed_pages": {
            "floor_plans": [{"page": 1, "title": "Layout Plan"}],
            "reflected_ceiling_plans": [],
            "existing_hvac_or_services_plans": [],
            "reference_pages": [],
        },
        "design_inputs": {"scales": ["1:100"], "scale_status": "single_scale_found"},
        "questions_for_user": [],
    }


def tiny_png(path):
    data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    path.write_bytes(base64.b64decode(data))


def draw_plan_image(path):
    from PIL import Image, ImageDraw

    image = Image.new("L", (420, 300), 255)
    draw = ImageDraw.Draw(image)
    for x in range(50, 320, 45):
        draw.line((x, 30, x, 230), fill=0, width=2)
    for y in range(30, 240, 38):
        draw.line((40, y, 330, y), fill=0, width=2)
    draw.rectangle((250, 235, 410, 292), outline=0, width=2)
    draw.rectangle((70, 65, 150, 125), outline=0, width=2)
    draw.rectangle((180, 65, 285, 160), outline=0, width=2)
    image.save(path)


def draw_side_image(path):
    from PIL import Image, ImageDraw

    image = Image.new("L", (420, 300), 255)
    draw = ImageDraw.Draw(image)
    for y in range(55, 210, 18):
        draw.line((35, y, 380, y), fill=0, width=2)
    draw.line((55, 45, 90, 220), fill=0, width=2)
    draw.line((365, 45, 330, 220), fill=0, width=2)
    image.save(path)


def sample_geometry():
    texts = [
        {"text": "4328", "x0": 45, "top": 20, "x1": 70, "bottom": 30, "upright": True},
        {"text": "A2.10", "x0": 10, "top": 180, "x1": 40, "bottom": 190, "upright": True},
    ]
    return {
        "page": 1,
        "width": 200,
        "height": 200,
        "texts": texts,
        "lines": [
            {"x0": 40, "y0": 35, "x1": 140, "y1": 35, "orientation": "horizontal", "length": 100},
            {"x0": 40, "y0": 70, "x1": 140, "y1": 70, "orientation": "horizontal", "length": 100},
            {"x0": 170, "y0": 170, "x1": 195, "y1": 170, "orientation": "horizontal", "length": 25},
        ],
        "dimension_texts": [{"text": "4328", "value_mm": 4328}],
        "wall_candidates": [
            {"x0": 40, "y0": 70, "x1": 140, "y1": 70, "orientation": "horizontal", "length": 100},
        ],
    }


def sample_spatial_words():
    return [
        {"text": "SCALE", "bbox": [0, 90, 20, 100], "orientation": "horizontal", "confidence": "pdf_text_layer"},
        {"text": "1:100", "bbox": [22, 90, 44, 100], "orientation": "horizontal", "confidence": "pdf_text_layer"},
        {"text": "4328", "bbox": [40, 20, 65, 30], "orientation": "horizontal", "confidence": "pdf_text_layer"},
        {"text": "A2.10", "bbox": [80, 90, 110, 100], "orientation": "horizontal", "confidence": "pdf_text_layer"},
    ]


class FakeRunResult:
    def __init__(self, stdout):
        self.stdout = stdout


def check_blank_pdf_pages_are_preserved():
    import pdf_pipeline.extractors as extractors

    original_run = extractors.subprocess.run
    extractors.subprocess.run = lambda *args, **kwargs: FakeRunResult("\f\fHVAC FLOOR PLAN\n\f")
    try:
        pages = extract_text_pages("fake.pdf")
    finally:
        extractors.subprocess.run = original_run

    check_value("blank visual PDF pages are preserved", len(pages), 3)
    check_value("text page keeps real page position", pages[2].strip(), "HVAC FLOOR PLAN")


def check_visual_feature_scores():
    with tempfile.TemporaryDirectory() as temp_dir:
        plan = Path(temp_dir) / "plan.png"
        side = Path(temp_dir) / "side.png"
        draw_plan_image(plan)
        draw_side_image(side)

        plan_features = image_features(plan)
        side_features = image_features(side)

    check_value("visual plan classified as top down", plan_features["likely_view"], "top_down_plan")
    check_value("visual side is not top down", side_features["likely_view"] != "top_down_plan", True)


def main():
    check_blank_pdf_pages_are_preserved()
    check_visual_feature_scores()
    check(
        "architect floor plan",
        "General Arrangement Plan lease line office service counter dimensions",
        "floor_plan",
    )
    check(
        "architect rcp",
        "Reflected Ceiling Plan air condition register supply slot diffuser grille access panel",
        "reflected_ceiling_plan",
    )
    check_page_with_document_title(
        "dimensioned sheet inherits rcp document title",
        """
        1 2 3 4
        3' - 0" 9' - 5" 2' - 7"
        A
        LF-01 LF-01 LF-02 SM-02
        BATH 104
        B
        LF-04 LF-01 SM-03
        C
        LF-06 LF-05 SM-04
        D
        11' - 4" 14' - 4" 7' - 10"
        """,
        "Sinatra Living: Reflected Ceiling Plan",
        "reflected_ceiling_plan",
    )
    visual_plan = {"likely_view": "top_down_plan", "top_down_score": 0.82, "plan_confidence": 0.82, "side_view_score": 0.1}
    check_value(
        "visual top-down page survives section callout",
        classify_page("Floor layout with section marker 1/A3.01 and elevation callout", 1, "", visual_plan)["type"],
        "floor_plan",
    )
    check_value(
        "visual only top-down role",
        classify_page("A few labels only", 1, "", visual_plan)["plan_role"],
        "uncertain_top_down_context",
    )
    visual_side = {"likely_view": "side_or_detail", "top_down_score": 0.2, "side_view_score": 0.7}
    check_value(
        "visual side page is not primary",
        classify_page("12' - 0\" 8' - 0\" ceiling line bulkhead detail", 1, "", visual_side),
        None,
    )
    check_value(
        "component detail with plan view is not building plan",
        classify_page("Front Elevation Side Elevation Plan View 3D Reference scale nts indicative only", 1, "", visual_plan),
        None,
    )
    check_value(
        "equipment schedule top view is not building floor plan",
        classify_page("Wall legend equipment schedule stainless steel schedule equipment list E01 E02 1200mmW x 700mmD", 1, "", visual_plan),
        None,
    )
    check_value(
        "visual site/photo page is not primary",
        classify_page("SITE PHOTO REFERENCE SITE PLAN locality not for construction", 1, "", visual_plan),
        None,
    )
    visual_render = {
        "likely_view": "render_or_photo",
        "top_down_score": 0.9,
        "plan_confidence": 0.2,
        "photo_like_score": 0.7,
        "side_view_score": 0.0,
    }
    check_value(
        "render with straight edges is not primary",
        classify_page("General Notes rendered view indicative only drawing render image dimensions by scaling drawings", 1, "", visual_render),
        None,
    )
    check_value(
        "notes page mentioning ceiling plan is not primary",
        classify_page(
            "\n".join(["General Notes", "This is an indicative reflected ceiling plan only."] + [f"{i}. Contractor note" for i in range(1, 90)]),
            1,
            "",
            visual_plan,
        ),
        None,
    )
    check(
        "existing hvac plan",
        "Base Building Mechanical Services Plan VAV supply air ductwork return air",
        "existing_hvac_or_services_plan",
    )
    check(
        "mechanical floor plan",
        "Drawing Title Mechanical Main Level Floor Plan Drawing Number M2.10 VAV supply air ductwork",
        "existing_hvac_or_services_plan",
    )
    check(
        "electrical floor plan should be ignored",
        "Drawing Title Electrical Power Main Level Floor Plan Drawing Number EP2.10 room office ceiling",
        None,
    )
    check(
        "electrical multiline floor plan should be ignored",
        "Drawing Title\nELECTRICAL\nPOWER MAIN\nLEVEL FLOOR\nPLAN\nDrawing Number\nEP2.10",
        None,
    )
    check(
        "plumbing floor plan should be ignored",
        "Drawing Title Plumbing Main Level Floor Plan Drawing Number P2.10 room office pipe",
        None,
    )
    check(
        "architect construction annotation plan",
        "Drawing Title\nMAIN LEVEL CONSTRUCTION ANNOTATION PLAN\nDrawing Number\nA2.10",
        "floor_plan",
    )
    check(
        "loose furniture plan",
        "Loose Furniture Plan office service counter dimensions joinery equipment legend",
        "floor_plan",
    )
    check_value(
        "loose furniture plan role",
        classify_page("Loose Furniture Plan office service counter dimensions joinery equipment legend", 1)["plan_role"],
        "furniture_plan",
    )
    check_value(
        "general arrangement plan role",
        classify_page("General Arrangement Plan ground floor office room area dimensions lease line", 1)["plan_role"],
        "main_floor_plan",
    )
    check_value(
        "small scale plan role",
        classify_page("Floor Plan joinery detail scale 1:10 fixture signage dimensions room", 1)["plan_role"],
        "enlarged_plan",
    )
    check(
        "bulkhead detail is not a top view plan",
        "Bulkhead Detail ceiling line ceiling void clearance",
        None,
    )
    check(
        "electrical legend should be ignored",
        "Electrical Abbreviations Electrical Symbols Lighting Symbols Fire Alarm Legend "
        "Electrical General Notes coordinate with mechanical contractor ceiling mounted detector",
        None,
    )
    check(
        "random notes should be ignored",
        "office room area contractor notes refer to schedule",
        None,
    )
    check_reference(
        "mechanical legend should be reference",
        "Mechanical Notes and Symbols ductwork legend fan coil vav diffuser",
        "hvac_or_rcp_legend",
    )
    check_reference(
        "mechanical symbols title should not be blocked",
        "Mechanical Symbols diffuser grille supply air return air exhaust fan damper register",
        "hvac_or_rcp_legend",
    )
    check_reference(
        "rcp legend should be reference",
        "RCP Legend air condition register supply slot diffuser access panel grille",
        "hvac_or_rcp_legend",
    )
    check_reference(
        "diffuser schedule should be reference",
        "Diffuser Schedule supply air return air grille register neck size",
        "hvac_or_rcp_legend",
    )
    check_reference(
        "equipment schedule should be reference",
        "Kitchen Equipment exhaust hood fridge dishwasher heat",
        "equipment_or_fixture_schedule",
    )
    check_reference("generic general notes should not auto-reference", "General Notes all drawings to be verified on site do not scale", None)
    check_reference("ceiling elevation should not be reference", "Elevation 2 ceiling line 4200 ceiling height clearances", None)
    check_value(
        "electrical legend discard",
        is_primary_discard("Electrical Abbreviations Electrical Symbols Fire Alarm Legend")["type"],
        "electrical_or_fire",
    )
    check_value(
        "repository cover discard",
        is_primary_discard(
            "Repository Citation Special Collections and Archives accepted for inclusion "
            "Sinatra Living Reflected Ceiling Plan"
        )["type"],
        "document_repository_cover",
    )
    check_value(
        "door schedule discard",
        is_primary_discard("Schedule of Doors frame height width material")["type"],
        "architectural_detail_noise",
    )
    check_value(
        "signage detail discard",
        is_primary_discard("Signage 2 Detail front lit acrylic side trim")["type"],
        "architectural_detail_noise",
    )
    check_value(
        "elevation retained as thermal surface evidence",
        analyze_pages_texts(["Elevation 2 ceiling line 4200 ceiling height clearances"])["kept_pages"][0]["sheet_classification"],
        "elevation",
    )
    check_value("floor plan is top view", has_top_view_signal("Ground Floor Plan section marker 1/A3.01"), True)
    check_value("side view is not top view", has_top_view_signal("Elevation 2 ceiling line 4200"), False)
    check_value("metric scale", extract_scale("Scale @ A3 1:50"), "1:50")
    check_value("spaced metric scale", extract_scale("Loose Furniture Plan 1 : 50"), "1:50")
    check_value("imperial scale", extract_scale("K12 1/8\" = 1'-0\""), "1/8\" = 1'-0\"")
    check_value("ground floor label", extract_level_name("Drawing Title Ground Floor Plan"), "Ground Floor")
    check_value("main level label", extract_level_name("Drawing Title Main Level Construction Annotation Plan"), "Main Level")
    check_value("level one label", extract_level_name("Drawing Title Level 1 General Arrangement Plan"), "Level 1")
    check_value("basement label", extract_level_name("Basement Level Floor Plan"), "Basement Level")
    check_value("roof label", extract_level_name("Roof Plan mechanical coordination"), "Roof")
    regions = title_block_regions(200, 100)
    check_value("spatial ocr title block regions", [region[0] for region in regions], ["bottom_band", "bottom_right", "right_band"])
    title_block = region_summary("bottom_band", [0, 80, 200, 100], sample_spatial_words())
    check_value("spatial ocr title block text", "1:100" in title_block["text_excerpt"], True)
    check_value("spatial ocr scale candidate", scale_candidates(sample_spatial_words(), [title_block])[0]["text"], "1:100")
    check_value("spatial ocr dimension candidate", spatial_dimension_candidates(sample_spatial_words())[0]["value_mm"], 4328)
    check_value(
        "dimension detector ignores drawing numbers",
        [item["value_mm"] for item in sample_geometry()["dimension_texts"]],
        [4328],
    )

    rooms = extract_rooms("Office\n01.02\n9 m²\nService Counter\n01.01\n13 m²")
    check_value("room count", len(rooms), 2)
    check_value("first room area", rooms[0]["area"], "9 m²")

    dimensions = extract_written_dimensions("Lease line dimensions 1386 4328 2576 and 4.2 m ceiling 2024")
    check_value("dimension count", len(dimensions), 4)
    check_value("first dimension", dimensions[0], {"value": 1386, "unit": "mm"})
    check_value("thumbnail page number", page_number_from_path("page-14.png"), 14)
    check_value("review folder cleanup", safe_folder_name("A/B:C.pdf"), "A_B_C.pdf")

    packet = sample_packet()
    ai_packet = build_ai_packet(packet)
    check_value("ai packet floor plans", len(ai_packet["confirmed_pages"]["floor_plans"]), 1)
    check_value("ai packet rcp plans", len(ai_packet["confirmed_pages"]["reflected_ceiling_plans"]), 1)
    check_value("ai packet needs review", ai_packet["review_status"]["human_reviewed"], False)
    check_value("ai packet scale status", ai_packet["design_inputs"]["scale_status"], "single_scale_found")
    check_value("ai packet source pdf", ai_packet["source_files"]["pdf"], "/tmp/sample.pdf")
    check_value("ai packet page image count", len(ai_packet["source_files"]["page_images"]), 3)
    check_value("ai packet floor plan level", ai_packet["confirmed_pages"]["floor_plans"][0]["level_name"], "Ground Floor")
    check_value("ai packet keeps old page evidence", "confirmed_pages" in ai_packet and "design_inputs" in ai_packet, True)
    check_value("ai packet level count", len(ai_packet["design_inputs"]["levels"]), 1)
    check_value("ai packet level name", ai_packet["design_inputs"]["levels"][0]["level_name"], "Ground Floor")
    check_value("ai packet level label", ai_packet["design_inputs"]["levels"][0]["level_label"], "Ground Floor")
    check_value("ai packet level status", ai_packet["design_inputs"]["levels"][0]["level_status"], "detected")
    building = ai_packet["building_model"]
    check_value("building model exists", building["project"]["model_status"], "partial_from_reviewed_pdf_evidence")
    check_value("building model floor created", building["floors"][0]["label"], "Ground Floor")
    check_value("building model floor count", len(building["floors"]), 1)
    check_value("building model room nested", building["floors"][0]["rooms"][0]["name"], "Office")
    check_value("building model rcp attaches as support", building["floors"][0]["supporting_pages"][0]["plan_role"], "reflected_ceiling_plan")
    check_value("building model room aggregate", building["rooms"][0]["floor_id"], "floor_001")
    check_value("building model keeps walls empty", building["floors"][0]["rooms"][0]["walls"], [])
    check_value("building model keeps openings empty", building["floors"][0]["rooms"][0]["openings"], [])
    check_value(
        "building model dimensions stay unassigned",
        building["floors"][0]["dimensions"][0]["assigned_to"],
        "unassigned_floor_dimension",
    )
    check_value(
        "ai packet includes structured markdown",
        ai_packet["confirmed_pages"]["floor_plans"][0]["structured_content"]["markdown"],
        "General Arrangement Plan",
    )
    support_only_packet = sample_packet()
    support_only_packet["primary_pages"] = [
        {
            "page": 9,
            "type": "floor_plan",
            "importance": "essential",
            "plan_role": "uncertain_top_down_context",
            "title": "Dimensioned Top View Plan",
            "confidence": 0.82,
            "thumbnail_path": "thumbnails/page_009.png",
            "extracted": {
                "scale": "1:10",
                "level_name": "",
                "written_dimensions": [{"value": 1200, "unit": "mm"}],
                "rooms": [],
                "ceiling_constraints": [],
                "hvac_terms": [],
                "drawing_number": "",
                "notes_for_ai": [],
            },
        }
    ]
    support_only_packet["kept_pages"] = support_only_packet["primary_pages"]
    support_only_ai_packet = build_ai_packet(support_only_packet)
    check_value("uncertain top-down creates no floors", support_only_ai_packet["building_model"]["floors"], [])
    check_value(
        "uncertain top-down is ungrouped context",
        support_only_ai_packet["building_model"]["ungrouped_plan_context"][0]["plan_role"],
        "uncertain_top_down_context",
    )
    no_scale_packet = sample_packet()
    no_scale_packet["primary_pages"][0]["extracted"]["scale"] = None
    no_scale_packet["primary_pages"][1]["extracted"]["scale"] = None
    no_scale_ai_packet = build_ai_packet(no_scale_packet)
    check_value(
        "direct dimensions make scale optional",
        no_scale_ai_packet["design_inputs"]["scale_status"],
        "direct_dimensions_present_scale_optional",
    )
    check_value(
        "scale question skipped when dimensions exist",
        "What scale should be used, or are written dimensions enough for this drawing set?"
        in no_scale_ai_packet["questions_for_user"],
        False,
    )

    reference_only_packet = sample_packet()
    reference_only_packet["reference_pages"] = [
        {
            "page": 4,
            "type": "bca_or_ventilation_notes",
            "importance": "reference",
            "title": "General Notes",
            "confidence": 0.7,
            "thumbnail_path": "thumbnails/page_004.png",
            "extracted": {
                "scale": None,
                "level_name": "",
                "written_dimensions": [],
                "rooms": [],
                "ceiling_constraints": [],
                "hvac_terms": [],
                "drawing_number": "",
                "notes_for_ai": [],
            },
        }
    ]
    auto_reference_packet = build_ai_packet(reference_only_packet)
    check_value("reference is not auto-confirmed before review", auto_reference_packet["confirmed_pages"]["reference_pages"], [])

    context_packet = sample_packet()
    context_packet["kept_pages"] += [
        {
            "page": 4,
            "type": "unclassified_context",
            "importance": "possible_context",
            "title": "High Confidence Plan Context",
            "confidence": 0.84,
            "thumbnail_path": "thumbnails/page_004.png",
            "review_bucket": "unclassified",
            "visual_features": {
                "likely_view": "top_down_plan",
                "plan_confidence": 0.84,
                "top_down_score": 0.86,
                "side_view_score": 0.1,
            },
            "extracted": {"scale": "1:50", "level_name": "Level 2"},
        },
        {
            "page": 5,
            "type": "unclassified_context",
            "importance": "possible_context",
            "title": "Below Threshold Context",
            "confidence": 0.82,
            "thumbnail_path": "thumbnails/page_005.png",
            "review_bucket": "unclassified",
            "visual_features": {"likely_view": "top_down_plan", "plan_confidence": 0.82},
            "extracted": {},
        },
    ]
    context_ai_packet = build_ai_packet(context_packet)
    context_pages = context_ai_packet["high_confidence_context"]["pages"]
    check_value(
        "ai context threshold",
        context_ai_packet["high_confidence_context"]["threshold"],
        AI_CONTEXT_CONFIDENCE_THRESHOLD,
    )
    check_value("high confidence context page count", len(context_pages), 1)
    check_value("high confidence context page", context_pages[0]["page"], 4)
    check_value("confirmed pages not duplicated as context", 1 in [page["page"] for page in context_pages], False)

    decisions = {
        "pages": [
            {"page": 1, "decision": "Keep as reference", "scale_confirmed": True, "note": "Use only as context."},
            {"page": 2, "decision": "Confirm as RCP", "scale_confirmed": True, "note": ""},
        ]
    }
    reviewed_ai_packet = build_ai_packet(packet, decisions)
    check_value("reviewed packet status", reviewed_ai_packet["review_status"]["human_reviewed"], True)
    check_value("human decision overrides floor plan", len(reviewed_ai_packet["confirmed_pages"]["floor_plans"]), 0)
    check_value("human decision keeps reference", len(reviewed_ai_packet["confirmed_pages"]["reference_pages"]), 1)
    check_value("reference override removes level", reviewed_ai_packet["design_inputs"]["levels"], [])
    check_value("decisions are not mutated", "source" in decisions, False)

    legend_packet = sample_packet()
    legend_page = {
        "page": 4,
        "type": "hvac_or_rcp_legend",
        "importance": "reference",
        "packet_role": "symbol_key_context",
        "title": "Mechanical Symbols",
        "confidence": 0.82,
        "thumbnail_path": "thumbnails/page_004.png",
        "review_bucket": "reference",
        "matched_title_words": ["mechanical symbols"],
        "matched_support_words": ["diffuser", "grille"],
        "extracted": {
            "scale": None,
            "level_name": "",
            "written_dimensions": [],
            "rooms": [],
            "ceiling_constraints": ["access panel"],
            "hvac_terms": ["diffuser", "grille"],
            "drawing_number": "M0.01",
            "notes_for_ai": ["Use this page only to decode HVAC/RCP symbols."],
        },
    }
    legend_packet["reference_pages"] = [legend_page]
    legend_packet["kept_pages"].append(legend_page)
    legend_decisions = {"pages": [{"page": 4, "decision": "Keep as reference", "scale_confirmed": False, "note": ""}]}
    legend_ai_packet = build_ai_packet(legend_packet, legend_decisions)
    legend_inputs = legend_ai_packet["design_inputs"]["legend_key_pages"]
    check_value("legend key page in ai packet", legend_inputs[0]["packet_role"], "symbol_key_context")
    check_value("legend kept as reference page", legend_ai_packet["confirmed_pages"]["reference_pages"][0]["page"], 4)
    legend_sheet = sheet_summary(legend_page, Path(__file__).resolve().parents[1] / "output" / "test_review")
    check_value("legend selected by default in website", legend_sheet["selected_by_default"], True)
    check_value("legend not counted as primary geometry", legend_sheet["relevant"], False)

    measurement_review = {
        "source": "/tmp/measurement_review.json",
        "pages": [
            {
                "page": 1,
                "level_label": "Ground Floor",
                "overlay": "page_001_measurement_overlay.svg",
                "matches": [
                    {"dimension_text": "4328", "value_mm": 4328, "orientation": "horizontal", "confidence": 0.82, "decision": "accepted"},
                    {"dimension_text": "1200", "value_mm": 1200, "orientation": "horizontal", "confidence": 0.5, "decision": "needs_review"},
                ],
            }
        ],
    }
    measured_ai_packet = build_ai_packet(packet, measurements=measurement_review)
    check_value(
        "accepted measurement reaches ai packet",
        measured_ai_packet["measurement_review"]["confirmed_wall_measurements"][0]["value_mm"],
        4328,
    )
    check_value(
        "unaccepted measurement stays proposed",
        measured_ai_packet["measurement_review"]["proposed_wall_measurements"][0]["decision"],
        "needs_review",
    )

    vision_review = {
        "source": "/tmp/vision_measurements.json",
        "provider": "chatgpt_manual",
        "model": "manual_upload",
        "result": {
            "pages": [
                {
                    "page": 1,
                    "level_label": "Ground Floor",
                    "wall_dimensions": [
                        {
                            "value_mm": 4328,
                            "text_seen": "4328",
                            "applies_to": "north wall of Office",
                            "nearby_room": "Office",
                            "orientation": "horizontal",
                            "confidence": "medium",
                            "evidence": "dimension line appears aligned with wall",
                        }
                    ],
                    "unassigned_dimensions": [{"text_seen": "1200", "reason": "unclear target"}],
                    "uncertainties": ["small text is hard to read"],
                }
            ],
            "overall_uncertainties": ["scale should be confirmed"],
        },
    }
    vision_ai_packet = build_ai_packet(packet, vision=vision_review)
    check_value("vision wall dimension reaches packet", vision_ai_packet["vision_review"]["wall_dimensions"][0]["value_mm"], 4328)
    check_value(
        "vision dimension reaches building model evidence",
        vision_ai_packet["building_model"]["dimensions"][1]["assigned_to"],
        "vision_wall_dimension",
    )
    check_value("vision dimensions do not create walls", vision_ai_packet["building_model"]["walls"], [])
    check_value("vision uncertainty reaches packet", vision_ai_packet["vision_review"]["uncertainties"][0], "scale should be confirmed")

    spatial_ocr = {
        "source": "/tmp/spatial_ocr.json",
        "pages": [
            {
                "page": 1,
                "detected_type": "floor_plan",
                "quality": {"has_text_layer": True},
                "title_blocks": [title_block],
                "scale_candidates": [{"text": "1:100", "source": "bottom_band", "source_bbox": [0, 80, 200, 100]}],
                "drawing_number_candidates": [],
                "dimension_candidates": [{"text": "4328", "value_mm": 4328, "bbox": [40, 20, 65, 30]}],
                "room_label_candidates": [],
                "rotated_text": [],
            }
        ],
    }
    spatial_ai_packet = build_ai_packet(packet, spatial_ocr=spatial_ocr)
    check_value("spatial ocr reaches ai packet", spatial_ai_packet["spatial_ocr"]["pages"][0]["scale_candidates"][0]["text"], "1:100")
    check_value(
        "spatial ocr respects confirmed unclassified page",
        [page["page"] for page in spatial_useful_pages(packet, {"pages": [{"page": 3, "decision": "Keep as reference"}]})],
        [3],
    )

    check_value("table markdown", table_markdown([["A", "B"], ["1", "2"]]), "| A | B |\n| --- | --- |\n| 1 | 2 |")
    structure = {"pages": [{"page": 2, "word_count": 1, "table_count": 0, "needs_ocr": False, "markdown": "x", "elements": [], "width": 10, "height": 20}]}
    check_value("useful page structure", useful_page_structure(structure, [{"page": 2}])[0]["markdown"], "x")

    analysis = analyze_pages_texts(
        [
            "General Arrangement Plan room area",
            "Some detail page with dimensions and ceiling context",
            "Electrical Abbreviations Electrical Symbols Fire Alarm Legend",
        ]
    )
    check_value("all drawing-set pages retained", [page["page"] for page in analysis["kept_pages"]], [1, 2, 3])
    check_value("discard-first discarded pages", [page["page"] for page in analysis["discarded_pages"]], [3])
    check_value("retained administrative page has non-calculation role", analysis["kept_pages"][2]["thermal_role"], "not_calculation_evidence")

    lighting_report = analyze_pages_texts(
        ["Non-residential Lighting Calculator satisfies part j7d3 lighting system power"]
    )
    check_value("lighting report discarded", [page["page"] for page in lighting_report["discarded_pages"]], [1])

    render_report = analyze_pages_texts(["Rendered View NTS indicative only drawing render image general notes"])
    check_value("render page discarded", [page["page"] for page in render_report["discarded_pages"]], [1])

    floor_analysis = analyze_pages_texts(
        [
            "Drawing Title Level 1 Floor Plan room area dimensions",
            "Drawing Title Ground Floor Plan room area dimensions",
            "Drawing Title Roof Plan room area dimensions",
            "Drawing Title Basement Floor Plan room area dimensions",
        ]
    )
    floor_packet = build_ai_packet(
        {
            "pdf": "/tmp/floors.pdf",
            "primary_pages": floor_analysis["primary_pages"],
            "reference_pages": [],
            "kept_pages": floor_analysis["kept_pages"],
            "discarded_pages": [],
        }
    )
    check_value(
        "one plan page per floor labels",
        [level["level_name"] for level in floor_packet["design_inputs"]["levels"]],
        ["Basement", "Ground Floor", "Level 1", "Roof"],
    )
    check_value(
        "primary pages sorted by floor",
        [page["extracted"]["level_name"] for page in floor_analysis["primary_pages"]],
        ["Basement", "Ground Floor", "Level 1", "Roof"],
    )
    unlabelled_analysis = analyze_pages_texts(["Drawing Title Layout Plan room area dimensions"])
    unlabelled_packet = build_ai_packet(
        {
            "pdf": "/tmp/unlabelled.pdf",
            "primary_pages": unlabelled_analysis["primary_pages"],
            "reference_pages": [],
            "kept_pages": unlabelled_analysis["kept_pages"],
            "discarded_pages": [],
        }
    )
    check_value(
        "unlabelled floor plan fallback",
        unlabelled_packet["design_inputs"]["levels"][0]["level_label"],
        "Unlabelled Floor Plan 1",
    )
    check_value(
        "unlabelled floor plan needs confirmation",
        unlabelled_packet["design_inputs"]["levels"][0]["level_status"],
        "needs_confirmation",
    )

    multi_evidence_analysis = analyze_pages_texts(
        [
            "Drawing Title Dimension Plan shop T31b room area dimensions 8075 overall c.o.s 8205 overall c.o.s 3530 overall c.o.s 6705 950 830 scale 1:40",
            "Drawing Title Floor Finish Plan shop T31b room area dimensions 8075 overall c.o.s 8205 overall c.o.s floor finish schedule scale 1:40",
            "Drawing Title Reflected Ceiling Plan Service diffuser grille air condition register access panel sprinkler scale 1:40",
        ]
    )
    multi_packet = build_ai_packet(
        {
            "pdf": "/tmp/multi-evidence.pdf",
            "primary_pages": multi_evidence_analysis["primary_pages"],
            "reference_pages": [],
            "kept_pages": multi_evidence_analysis["kept_pages"],
            "discarded_pages": [],
        }
    )
    check_value(
        "dimension and finish plans are both geometry evidence",
        [page["page"] for page in multi_packet["design_inputs"]["geometry_evidence_pages"]],
        [1, 2],
    )
    check_value(
        "dimension evidence includes dimension plan",
        [page["page"] for page in multi_packet["design_inputs"]["dimension_evidence_pages"]],
        [1],
    )
    check_value(
        "multiple geometry pages create one floor",
        len(multi_packet["building_model"]["floors"]),
        1,
    )
    floor = multi_packet["building_model"]["floors"][0]
    check_value("multi evidence floor source pages", floor["source_pages"], [1, 2, 3])
    check_value("dimension page attached to floor", [page["page"] for page in floor["dimension_pages"]], [1])
    check_value("finish page attached to floor", [page["page"] for page in floor["supporting_geometry_pages"]], [2])
    check_value("rcp page attached as ceiling context", [page["page"] for page in floor["ceiling_context_pages"]], [3])

    embedded_legend_packet = sample_packet()
    embedded_legend_packet["primary_pages"][1]["extracted"]["ceiling_constraints"] = [
        "access panel",
        "sprinkler",
        "smoke detector",
    ]
    embedded_legend_packet["primary_pages"][1]["extracted"]["hvac_terms"] = [
        "air condition register",
        "supply slot diffuser",
        "diffuser",
        "grille",
    ]
    embedded_legend_packet["primary_pages"][1]["extracted"]["notes_for_ai"] = [
        "Legend includes light switch, lighting circuit, access panel, air condition register, and supply slot diffuser."
    ]
    embedded_legend_ai = build_ai_packet(embedded_legend_packet)
    check_value(
        "embedded rcp legend reaches legend context",
        embedded_legend_ai["design_inputs"]["legend_key_pages"][0]["packet_role"],
        "embedded_symbol_key_context",
    )
    check_value(
        "embedded rcp stays rcp context",
        [page["page"] for page in embedded_legend_ai["design_inputs"]["rcp_service_context_pages"]],
        [2],
    )
    check_value(
        "embedded legend does not become geometry",
        2 in [page["page"] for page in embedded_legend_ai["design_inputs"]["geometry_evidence_pages"]],
        False,
    )

    detail_context_packet = sample_packet()
    detail_page = {
        "page": 13,
        "type": "floor_plan",
        "importance": "essential",
        "plan_role": "enlarged_plan",
        "title": "Dimensioned Top View Plan",
        "confidence": 0.8,
        "thumbnail_path": "thumbnails/page_013.png",
        "extracted": {
            "scale": "1:10",
            "level_name": "Level 1",
            "written_dimensions": [{"value": 800, "unit": "mm"}],
            "rooms": [],
            "ceiling_constraints": [],
            "hvac_terms": [],
            "drawing_number": "03.01",
            "notes_for_ai": ["Door battens and cabinet details."],
        },
    }
    detail_context_packet["primary_pages"].append(detail_page)
    detail_context_packet["kept_pages"].append(detail_page)
    detail_context_ai = build_ai_packet(detail_context_packet)
    detail_floor = detail_context_ai["building_model"]["floors"][0]
    check_value("detail page not attached to main floor source pages", 13 in detail_floor["source_pages"], False)
    check_value("detail page is ungrouped context", detail_context_ai["building_model"]["ungrouped_plan_context"][0]["page"], 13)
    check_value(
        "detail page reason is explicit",
        "Detail/enlarged context" in detail_context_ai["building_model"]["ungrouped_plan_context"][0]["reason"],
        True,
    )

    promoted_detail_decisions = {"pages": [{"page": 13, "decision": "Confirm as floor plan", "scale_confirmed": False, "note": ""}]}
    promoted_detail_ai = build_ai_packet(detail_context_packet, promoted_detail_decisions)
    check_value(
        "explicitly confirmed detail stays confirmed",
        13 in [page["page"] for page in promoted_detail_ai["confirmed_pages"]["floor_plans"]],
        True,
    )
    check_value(
        "explicitly confirmed detail still does not create fake floor",
        any(13 in floor["main_plan_pages"] for floor in promoted_detail_ai["building_model"]["floors"]),
        False,
    )
    check_value(
        "explicitly confirmed detail remains ungrouped for review",
        13 in [page["page"] for page in promoted_detail_ai["building_model"]["ungrouped_plan_context"]],
        True,
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        image_path = tmp_path / "page.png"
        tiny_png(image_path)
        ai_input_path = tmp_path / "ai_input.json"
        chatgpt_input = sample_ai_input("page.png")
        chatgpt_input["spatial_ocr"] = spatial_ai_packet["spatial_ocr"]
        chatgpt_input["source_files"]["page_images"].append(
            {
                "page": 4,
                "type": "hvac_or_rcp_legend",
                "title": "Mechanical Symbols",
                "packet_role": "symbol_key_context",
                "path": "page.png",
            }
        )
        chatgpt_input["confirmed_pages"]["reference_pages"].append(
            {"page": 4, "title": "Mechanical Symbols", "detected_type": "hvac_or_rcp_legend"}
        )
        chatgpt_input["design_inputs"]["legend_key_pages"] = [{"page": 4, "title": "Mechanical Symbols"}]
        ai_input_path.write_text(json.dumps(chatgpt_input), encoding="utf-8")

        images = selected_images(chatgpt_input)
        check_value("chatgpt selected screenshots", [image["page"] for image in images], [1, 4])
        prompt = build_prompt(
            chatgpt_input,
            [
                {"page": 1, "type": "floor_plan", "packet_filename": "screenshots/page_001_floor_plan.png"},
                {
                    "page": 4,
                    "type": "hvac_or_rcp_legend",
                    "packet_filename": "screenshots/page_004_hvac_or_rcp_legend.png",
                },
            ],
        )
        check_value("chatgpt prompt has geometry scope", "Geometry Vision Review" in prompt, True)
        check_value("chatgpt prompt asks for one geometry response", '"geometry_review"' in prompt, True)
        check_value("chatgpt prompt references legend context", "legend_and_rcp_context" in prompt, True)
        check_value("chatgpt prompt asks for pixel geometry", "image_px" in prompt, True)
        check_value("chatgpt prompt asks for vision wall id", "wall_id" in prompt and "VWALL" in prompt, True)
        check_value("chatgpt prompt avoids nearest wall matching", "nearest geometry" in prompt, True)
        check_value("chatgpt prompt asks for witness lines", "witness_lines_px" in prompt, True)
        check_value("chatgpt prompt asks for measured span", "measured_span_start_px" in prompt, True)
        check_value("chatgpt prompt has no duplicate coordinate review", '"coordinate_review"' in prompt, False)
        check_value("chatgpt prompt drops contractor questions", '"questions_for_contractor"' in prompt, False)
        check_value("chatgpt prompt mentions direct dimensions", "All dimensions are millimetres" in prompt, True)
        check_value("chatgpt prompt says do not guess", "Do not invent IDs" in prompt, True)
        check_value("chatgpt prompt asks for strict json", "Return valid JSON only" in prompt, True)

        packet_result = create_chatgpt_packet(ai_input_path, dpi=DEFAULT_DPI)
        check_value("chatgpt packet folder exists", Path(packet_result["folder"]).exists(), True)
        check_value("chatgpt packet prompt exists", Path(packet_result["prompt"]).exists(), True)
        check_value("chatgpt packet context exists", Path(packet_result["context"]).exists(), True)
        check_value("chatgpt packet manifest exists", Path(packet_result["manifest"]).exists(), True)
        check_value("chatgpt packet zip exists", Path(packet_result["zip"]).exists(), True)
        check_value("chatgpt packet copied screenshot", Path(packet_result["screenshots"][0]).exists(), True)
        manifest = json.loads(Path(packet_result["manifest"]).read_text())
        screenshot = manifest["screenshots_kept_for_reasoning"][0]
        check_value("chatgpt packet records dpi", screenshot["render_dpi"], DEFAULT_DPI)
        check_value("chatgpt packet falls back without pdf", screenshot["quality"], "thumbnail_fallback")
        check_value("chatgpt packet records fallback status", screenshot["render_status"], "thumbnail_fallback")
        check_value("chatgpt manifest references compact context", manifest["context"].endswith("vision_context.json"), True)

    valid_vision = {
        "provider": "chatgpt_manual",
        "model": "manual_upload",
        "result": {
            "pages": [
                {
                    "page": 1,
                    "image": "screenshots/page_001_floor_plan.png",
                    "coordinate_system": {"image_width": 200, "image_height": 100},
                    "plan_viewport_bbox_px": [10, 20, 190, 90],
                    "plan_viewport_confidence": "high",
                    "plan_viewport_uncertainties": [],
                    "wall_dimensions": [
                        {
                            "measurement_id": "P1-DIM-001",
                            "value_mm": 4328,
                            "dimension_text_bbox": [40, 20, 65, 30],
                            "dimension_line_start": [30, 35],
                            "dimension_line_end": [140, 35],
                            "target_wall_candidate_id": "P1-WALL-001",
                            "target_wall_start": [30, 55],
                            "target_wall_end": [140, 55],
                            "confidence": "high",
                            "should_use_for_calculation": True,
                        }
                    ],
                    "wall_candidates": [
                        {
                            "candidate_id": "P1-WALL-001",
                            "line_start_px": [30, 55],
                            "line_end_px": [140, 55],
                            "source": "vision_model",
                            "confidence": "high",
                        }
                    ],
                }
            ]
        },
    }
    check_value("vision validator accepts good output", validate_vision(valid_vision)["issue_count"], 0)

    coordinate_vision = {
        "provider": "chatgpt_manual",
        "model": "manual_upload",
        "result": {
            "pages": [
                {
                    "page": 1,
                    "image": "screenshots/page_001_floor_plan.png",
                    "coordinate_system": {"image_width": 200, "image_height": 100, "units": "image_px"},
                    "plan_viewport_bbox_px": [10, 20, 190, 90],
                    "plan_viewport_confidence": "high",
                    "plan_viewport_uncertainties": [],
                    "wall_candidates": [
                        {
                            "candidate_id": "P1-WALL-001",
                            "line_start_px": [30, 55],
                            "line_end_px": [140, 55],
                            "source": "vision_model",
                            "confidence": "high",
                        }
                    ],
                    "dimension_candidates": [
                        {
                            "candidate_id": "P1-DIMTXT-001",
                            "text_seen": "4328",
                            "value_mm": 4328,
                            "bbox_px": [40, 20, 65, 30],
                            "source": "vision_model",
                            "confidence": "high",
                        }
                    ],
                    "room_label_candidates": [
                        {
                            "candidate_id": "P1-ROOM-001",
                            "text_seen": "Office",
                            "bbox_px": [70, 50, 95, 65],
                            "source": "vision_model",
                            "confidence": "medium",
                        }
                    ],
                    "opening_candidates": [
                        {
                            "candidate_id": "P1-OPEN-001",
                            "line_start_px": [100, 55],
                            "line_end_px": [120, 55],
                            "source": "vision_model",
                            "confidence": "medium",
                        }
                    ],
                    "wall_dimensions": [
                        {
                            "measurement_id": "P1-WD-001",
                            "value_mm": 4328,
                            "dimension_text_bbox_px": [40, 20, 65, 30],
                            "dimension_line_start_px": [30, 35],
                            "dimension_line_end_px": [140, 35],
                            "target_wall_candidate_id": "P1-WALL-001",
                            "target_wall_start_px": [30, 55],
                            "target_wall_end_px": [140, 55],
                            "source": "vision_model",
                            "confidence": "high",
                            "should_use_for_calculation": True,
                        }
                    ],
                }
            ]
        },
    }
    check_value("image point converts to plan px", image_point_to_plan_px([20, 100], [10, 20, 110, 120]), [10, 20])
    check_value("coordinate candidates validate", validate_vision(coordinate_vision)["issue_count"], 0)

    with tempfile.TemporaryDirectory() as tmp:
        vision_path = Path(tmp) / "vision.json"
        coordinate_vision["result"]["pages"][0]["wall_dimensions"][0]["site_confirm_required"] = True
        vision_path.write_text(json.dumps(coordinate_vision), encoding="utf-8")
        coordinate_path = create_coordinate_review(vision_path)
        coordinate_data = json.loads(Path(coordinate_path).read_text())
        check_value("coordinate review created", Path(coordinate_path).name, "coordinate_review.json")
        check_value("coordinate review image origin", coordinate_data["coordinate_systems"][0]["image_px"]["origin"], "top_left_full_screenshot")
        check_value("coordinate review plan origin", coordinate_data["coordinate_systems"][0]["plan_px"]["origin"], "bottom_left_plan_viewport")
        check_value("coordinate review wall candidate", coordinate_data["wall_candidates"][0]["candidate_id"], "P1-WALL-001")
        check_value("coordinate review wall plan start", coordinate_data["wall_candidates"][0]["line_start_plan_px"], [20, 35])
        check_value("coordinate review dimension bbox plan", coordinate_data["dimension_candidates"][0]["bbox_plan_px"], [30, 60, 55, 70])
        coordinate_ai_packet = build_ai_packet(sample_packet(), coordinate_review=coordinate_data)
        check_value("coordinate review reaches ai packet", coordinate_ai_packet["coordinate_review"]["wall_candidates"][0]["candidate_id"], "P1-WALL-001")
        check_value("coordinate wall reaches building model", coordinate_ai_packet["building_model"]["floors"][0]["walls"][0]["candidate_id"], "P1-WALL-001")
        check_value(
            "coordinate dimension reaches building model",
            coordinate_ai_packet["building_model"]["floors"][0]["dimensions"][-1]["measurement_id"],
            "P1-WD-001",
        )
        check_value("coordinate review status", coordinate_data["proposed_wall_dimension_links"][0]["approval_status"], "validator_passed")
        check_value("coordinate review preserves explicit site confirmation", coordinate_data["proposed_wall_dimension_links"][0]["site_confirm_required"], True)
        check_value("coordinate review scale conversion source", coordinate_data["scale_conversions"][0]["source_measurement_id"], "P1-WD-001")
        check_value("coordinate review provisional cad geometry", coordinate_data["provisional_cad_geometry"][0]["candidate_id"], "P1-WALL-001")
        check_value("coordinate review provisional cad source", coordinate_data["provisional_cad_geometry"][0]["source_units"], "plan_px")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        screenshots_dir = root / "screenshots"
        overlays_dir = root / "overlays"
        screenshots_dir.mkdir()
        tiny_png(screenshots_dir / "page_001_floor_plan.png")
        vision_path = root / "vision.json"
        vision_path.write_text(json.dumps(coordinate_vision), encoding="utf-8")
        overlay_coordinate_path = create_coordinate_review(vision_path, screenshots_dir=screenshots_dir, overlays_dir=overlays_dir)
        overlay_data = json.loads(Path(overlay_coordinate_path).read_text())
        check_value("coordinate review overlay created", overlay_data["overlays"][0]["status"], "created")
        check_value("coordinate review overlay file exists", Path(overlay_data["overlays"][0]["path"]).exists(), True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ai_input_path = root / "ai_input.json"
        coordinate_path = root / "coordinate_review.json"
        chatgpt_dir = root / "chatgpt_packet"
        screenshots_dir = chatgpt_dir / "screenshots"
        overlays_dir = root / "overlays"
        screenshots_dir.mkdir(parents=True)
        overlays_dir.mkdir()
        tiny_png(screenshots_dir / "page_001_floor_plan.png")
        tiny_png(overlays_dir / "page_001_coordinate_overlay.png")
        vector_overlay_dir = root / "vector_source_overlays"
        vector_overlay_dir.mkdir()
        tiny_png(vector_overlay_dir / "page_005_vector_overlay.png")
        coordinate_with_overlay = dict(coordinate_data)
        coordinate_with_overlay["overlays"] = [{"page": 1, "status": "created", "path": str(overlays_dir / "page_001_coordinate_overlay.png")}]
        coordinate_path.write_text(json.dumps(coordinate_with_overlay), encoding="utf-8")
        vector_path = root / "vector_geometry.json"
        vector_path.write_text(
            json.dumps(
                {
                    "geometry_key_points": {
                        "pages": [
                            {
                                "page": 5,
                                "title": "General Arrangement Plan",
                                "wall_candidates": [
                                    {
                                        "wall_id": "P5-WALL-LINE-001",
                                        "geometry_type": "line",
                                        "points_plan_px": [[0, 0], [200, 0]],
                                        "source": "pdf_vector",
                                    },
                                    {
                                        "wall_id": "P5-WALL-CURVE-001",
                                        "geometry_type": "curve_polyline",
                                        "points_plan_px": [[200, 0], [240, 20], [260, 60]],
                                        "source": "pdf_vector",
                                    },
                                ],
                                "line_candidates": [{"candidate_id": "P5-VLINE-0001"}],
                                "curve_candidates": [{"candidate_id": "P5-VCURVE-0001"}],
                                "dimension_candidates": [{"candidate_id": "P5-VDIMTXT-0001"}],
                            }
                        ]
                    },
                    "overlays": [{"page": 5, "status": "created", "path": str(vector_overlay_dir / "page_005_vector_overlay.png")}],
                }
            ),
            encoding="utf-8",
        )
        reasoning_input = sample_ai_input("thumbnails/page_001.png")
        reasoning_input["design_inputs"]["geometry_evidence_pages"] = [{"page": 5, "title": "General Arrangement Plan"}]
        reasoning_input["design_inputs"]["rcp_service_context_pages"] = [{"page": 7, "title": "Reflected Ceiling Plan"}]
        reasoning_input["design_inputs"]["legend_key_pages"] = [{"page": 7, "title": "RCP Legend"}]
        reasoning_input["confirmed_pages"]["floor_plans"][0]["scale"] = "1:40"
        reasoning_input["confirmed_pages"]["floor_plans"][0]["written_dimensions"] = [{"value": 4328, "unit": "mm"}]
        ai_input_path.write_text(json.dumps(reasoning_input), encoding="utf-8")
        reasoning = create_reasoning_packet(ai_input_path, coordinate_path, chatgpt_dir, vector_geometry_path=vector_path)
        check_value("reasoning packet folder exists", Path(reasoning["folder"]).exists(), True)
        check_value("reasoning packet has coordinate review", Path(reasoning["coordinate_review"]).exists(), True)
        check_value("reasoning packet has vector geometry", Path(reasoning["vector_geometry"]).exists(), True)
        check_value("reasoning packet has screenshot", len(reasoning["screenshots"]), 1)
        check_value("reasoning packet has overlay", len(reasoning["overlays"]), 1)
        check_value("reasoning packet has vector overlay", len(reasoning["vector_overlays"]), 1)
        prompt = Path(reasoning["prompt"]).read_text()
        manifest = json.loads(Path(reasoning["manifest"]).read_text())
        check_value("reasoning prompt requires overlays", "overlay images" in prompt, True)
        check_value("reasoning prompt includes vector geometry", "vector_geometry.json" in prompt, True)
        check_value("reasoning prompt prioritizes vision-created walls", "vision-created outer walls" in prompt, True)
        check_value("reasoning prompt keeps raw vectors as evidence", "Raw vectors support" in prompt, True)
        check_value("reasoning prompt gates automation", "confidence-gated" in prompt, True)
        check_value("reasoning prompt mentions curved walls", "For curved walls" in prompt, True)
        check_value("reasoning prompt includes all evidence instruction", "Use all relevant geometry evidence pages together" in prompt, True)
        check_value("reasoning prompt includes legend instruction", "Use legend/key and schedule pages" in prompt, True)
        check_value("reasoning prompt includes image px rule", "`image_px` coordinates" in prompt, True)
        check_value("reasoning prompt includes plan px rule", "`plan_px` coordinates" in prompt, True)
        check_value("reasoning prompt asks source pages", "Cite source page numbers" in prompt, True)
        check_value("reasoning prompt blocks invented loads", "Do not invent HVAC loads" in prompt, True)
        check_value("reasoning manifest records geometry evidence", manifest["evidence_summary"]["geometry_evidence_pages"][0]["page"], 5)
        check_value("reasoning manifest records rcp context", manifest["evidence_summary"]["rcp_service_context_pages"][0]["page"], 7)
        check_value("reasoning manifest records legend context", manifest["evidence_summary"]["legend_key_context_pages"][0]["page"], 7)
        check_value("reasoning manifest records scale per page", manifest["evidence_summary"]["scales_by_page"][0]["scale"], "1:40")
        check_value("reasoning manifest records coordinate rules", "plan_px" in manifest["coordinate_rules"]["coordinate_system"], True)
        check_value("reasoning manifest records design requirements", "occupancy" in manifest["design_requirements"]["required_before_full_hvac_design"], True)
        check_value("reasoning manifest records vector evidence", manifest["evidence_summary"]["vector_geometry_evidence"]["curve_candidate_count"], 1)
        check_value("reasoning manifest records vector overlay", manifest["evidence_summary"]["vector_geometry_evidence"]["overlay_count"], 1)
        check_value("reasoning manifest blocks unverified raw geometry", manifest["geometry_verification_status"], "geometry_not_vision_verified")

    bad_coordinate = json.loads(json.dumps(coordinate_vision))
    bad_coordinate["result"]["pages"][0]["wall_candidates"][0]["line_end_px"] = [999, 55]
    check_value("coordinate validator catches out of bounds", validate_vision(bad_coordinate)["issue_count"] > 0, True)

    bad_viewport = json.loads(json.dumps(coordinate_vision))
    bad_viewport["result"]["pages"][0]["plan_viewport_bbox_px"] = [10, 20, 250, 90]
    check_value("coordinate validator catches bad viewport", validate_vision(bad_viewport)["issue_count"] > 0, True)

    low_confidence_calc = json.loads(json.dumps(coordinate_vision))
    low_confidence_calc["result"]["pages"][0]["wall_dimensions"][0]["confidence"] = "medium"
    check_value("coordinate validator blocks low confidence calculation", validate_vision(low_confidence_calc)["issue_count"] > 0, True)

    prose_only_calc = json.loads(json.dumps(coordinate_vision))
    del prose_only_calc["result"]["pages"][0]["wall_dimensions"][0]["target_wall_candidate_id"]
    prose_only_calc["result"]["pages"][0]["wall_dimensions"][0]["applies_to"] = "right angled street-facing boundary depth/offset"
    check_value("coordinate validator blocks prose-only calculation", validate_vision(prose_only_calc)["issue_count"] > 0, True)

    missing_wall_id = json.loads(json.dumps(coordinate_vision))
    missing_wall_id["result"]["pages"][0]["wall_dimensions"][0]["target_wall_candidate_id"] = "P1-WALL-MISSING"
    check_value("coordinate validator blocks missing wall id", validate_vision(missing_wall_id)["issue_count"] > 0, True)

    weak_vision = {
        "provider": "chatgpt_manual",
        "model": "manual_upload",
        "result": {
            "pages": [
                {
                    "page": 1,
                    "image": "screenshots/page_001_floor_plan.png",
                    "coordinate_system": {"image_width": 200, "image_height": 100},
                    "plan_viewport_bbox_px": [10, 20, 190, 90],
                    "wall_candidates": [
                        {
                            "candidate_id": "P1-WALL-002",
                            "line_start_px": [60, 55],
                            "line_end_px": [60, 90],
                            "source": "vision_model",
                            "confidence": "medium",
                        }
                    ],
                    "wall_dimensions": [
                        {
                            "measurement_id": "P1-DIM-002",
                            "value_mm": 1200,
                            "dimension_text_bbox": [40, 20, 65, 30],
                            "dimension_line_start": [30, 35],
                            "dimension_line_end": [140, 35],
                            "target_wall_candidate_id": "P1-WALL-002",
                            "target_wall_start": [60, 55],
                            "target_wall_end": [60, 90],
                            "confidence": "medium",
                            "should_use_for_calculation": True,
                        }
                    ],
                }
            ]
        },
    }
    check_value("vision validator catches unsafe output", validate_vision(weak_vision)["issue_count"] > 0, True)


if __name__ == "__main__":
    main()
