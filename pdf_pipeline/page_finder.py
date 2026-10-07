#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path

from pdf_pipeline.extractors import (
    clean_text,
    extract_drawing_title,
    extract_pdf_title,
    extract_page_info,
    extract_text_pages,
    found_words,
    guess_title,
    title_area,
)
from pdf_pipeline.renderer import render_thumbnails
from pdf_pipeline.rules import DISCARD_RULES, IGNORE_TITLE_WORDS, NON_HVAC_DISCIPLINES, PAGE_RULES, REFERENCE_RULES, TOP_VIEW_WORDS
from pdf_pipeline.structured_pdf import extract_structured_pdf, useful_page_structure
from pdf_pipeline.visual_features import extract_pdf_visual_features


def classify_page(raw_text, page_number, document_title="", visual=None):
    title_text = clean_text(extract_drawing_title(raw_text))

    # Explicit sheet titles outrank visual heuristics.  Schedule tables and
    # architect service plans can have strong horizontal/vertical line scores
    # and were previously promoted to false floor plans.
    non_geometry_titles = {
        "material schedule",
        "finish schedule",
        "schedule of finishes",
        "service plan - lighting",
        "service plan - electrical",
        "service plan - power",
    }
    if title_text in non_geometry_titles:
        return None

    title_block_text = title_area(raw_text)
    explicit_ceiling_plan = (
        title_text in {"reflective ceiling plan", "ceiling plan"}
        and "drawing" in title_block_text
        and "dwg no" in title_block_text
    )

    if found_words(title_area(raw_text), IGNORE_TITLE_WORDS) and not is_visual_top_down(visual) and not explicit_ceiling_plan:
        return None
    if should_ignore_title(title_text) and not is_visual_top_down(visual) and not explicit_ceiling_plan:
        return None

    match = best_rule_match(raw_text, page_number, PAGE_RULES)
    if match:
        if match["type"] in {"elevation", "section"} and is_visual_top_down(visual):
            return inferred_plan_match(raw_text, page_number, document_title, visual)
        attach_visual_features(match, visual)
        if match["type"] == "reflected_ceiling_plan" and not explicit_ceiling_plan and non_plan_context(raw_text, visual):
            return None
        if not explicit_ceiling_plan and not has_primary_plan_evidence(raw_text, match, visual):
            return None
        return match
    return inferred_plan_match(raw_text, page_number, document_title, visual)


def classify_reference_page(raw_text, page_number):
    title_text = clean_text(extract_drawing_title(raw_text))

    if is_primary_discard(raw_text):
        return None
    if should_ignore_reference_title(title_text):
        return None

    # Preserve explicit architectural elevations/sections as surface evidence
    # even when their body text also contains material or fixture terms.
    titled = best_rule_match(raw_text, page_number, PAGE_RULES)
    has_drawing_footer = "drawing" in title_area(raw_text) and "dwg no" in title_area(raw_text)
    if titled and (
        (titled.get("type") in {"elevation", "section"} and (title_text or has_drawing_footer))
        or (titled.get("type") == "reflected_ceiling_plan" and title_text in {"reflective ceiling plan", "ceiling plan"})
    ):
        return titled

    return best_rule_match(raw_text, page_number, REFERENCE_RULES, allow_support_only=True)


def best_rule_match(raw_text, page_number, rules, allow_support_only=False):
    page_text = clean_text(raw_text)
    title_text = clean_text(extract_drawing_title(raw_text))
    best = None

    for rule in rules:
        title_hits = found_words(title_text or page_text, rule["title_words"])
        support_hits = found_words(page_text, rule["support_words"])
        if not title_hits and not (allow_support_only and len(support_hits) >= 2):
            continue

        score = len(title_hits) * 100 + len(support_hits) * 15
        if rule["type"] == "existing_hvac_or_services_plan":
            score += 20

        extracted = extract_page_info(raw_text, rule["type"])
        match = {
            "page": page_number,
            "type": rule["type"],
            "importance": rule["importance"],
            "packet_role": rule.get("packet_role", ""),
            "plan_role": plan_role_for_match(raw_text, rule["type"], title_hits, support_hits, extracted),
            "title": guess_title(raw_text),
            "confidence": min(0.99, round(score / (score + 35), 2)),
            "score": score,
            "matched_title_words": title_hits,
            "matched_support_words": support_hits,
            "extracted": extracted,
        }
        if best is None or match["score"] > best["score"]:
            best = match

    return best


def should_ignore_reference_title(title):
    if not title:
        return False
    if any(word in title for word in NON_HVAC_DISCIPLINES):
        return "mechanical" not in title and "hvac" not in title
    if "demolition" in title and "mechanical" not in title and "hvac" not in title:
        return True
    return False


def should_ignore_title(title):
    if not title:
        return False
    if any(word in title for word in ["abbreviations", "symbols", "notes", "demolition"]):
        return True
    if any(word in title for word in NON_HVAC_DISCIPLINES):
        return "mechanical" not in title and "hvac" not in title
    return False


def is_primary_discard(raw_text):
    area = title_area(raw_text)
    for rule in DISCARD_RULES:
        hits = found_words(area, rule["words"])
        if hits:
            if rule["type"] == "architectural_detail_noise" and has_top_view_signal(raw_text):
                continue
            return {"type": rule["type"], "matched_words": hits}
    return None


def has_top_view_signal(raw_text):
    text = clean_text(raw_text)
    return bool(found_words(text, TOP_VIEW_WORDS))


def inferred_plan_match(raw_text, page_number, document_title="", visual=None):
    has_text_plan_shape = looks_like_dimensioned_top_view_plan(raw_text)
    has_visual_plan_shape = (
        is_visual_top_down(visual)
        and not visual_promotion_blocked(raw_text)
        and visual_can_promote_to_building_plan(raw_text)
    )
    if not has_text_plan_shape and not has_visual_plan_shape:
        return None

    page_type, title = inferred_plan_type(document_title)
    confidence = 0.58
    score = 65
    support = []
    if has_text_plan_shape:
        support.append("dimensioned plan sheet")
    if has_visual_plan_shape:
        support.append("visual top-down plan")
        confidence = max(confidence, min(0.82, 0.45 + visual.get("top_down_score", 0) * 0.45))
        score += int(visual.get("top_down_score", 0) * 50)

    extracted = extract_page_info(raw_text, page_type)
    match = {
        "page": page_number,
        "type": page_type,
        "importance": "essential",
        "plan_role": inferred_plan_role(raw_text, page_type, has_text_plan_shape, has_visual_plan_shape, extracted),
        "title": title,
        "confidence": round(confidence, 2),
        "score": score,
        "matched_title_words": [],
        "matched_support_words": support,
        "extracted": extracted,
    }
    attach_visual_features(match, visual)
    return match


def attach_visual_features(match, visual):
    if visual:
        match["visual_features"] = visual


def plan_role_for_match(raw_text, page_type, title_hits, support_hits, extracted=None):
    text = clean_text(raw_text)
    if page_type == "reflected_ceiling_plan":
        return "reflected_ceiling_plan"
    if page_type == "existing_hvac_or_services_plan":
        return "existing_hvac_plan"
    if page_type == "roof_plan":
        return "main_floor_plan"
    if page_type in {"hvac_or_rcp_legend", "equipment_or_fixture_schedule", "bca_or_ventilation_notes"}:
        return "reference_context"
    if page_type != "floor_plan":
        return "reference_context"
    role = support_plan_role(text, (extracted or {}).get("scale"))
    if role != "main_floor_plan":
        return role
    if has_main_floor_signal(text, title_hits, support_hits):
        return "main_floor_plan"
    return "uncertain_top_down_context"


def inferred_plan_role(raw_text, page_type, has_text_plan_shape, has_visual_plan_shape, extracted=None):
    text = clean_text(raw_text)
    if page_type == "reflected_ceiling_plan":
        return "reflected_ceiling_plan"
    role = support_plan_role(text, (extracted or {}).get("scale"))
    if role != "main_floor_plan":
        return role
    if has_text_plan_shape and has_main_floor_signal(text, [], []):
        return "main_floor_plan"
    if has_visual_plan_shape:
        return "uncertain_top_down_context"
    return "uncertain_top_down_context"


def support_plan_role(text, scale=None):
    if "site plan" in text or "locality" in text:
        return "site_plan"
    if "loose furniture" in text or "furniture plan" in text:
        return "furniture_plan"
    if "floor finish plan" in text or "finish plan" in text or "fitout plan" in text or "fit-out plan" in text:
        return "supporting_geometry_plan"
    if scale in {"1:10", "1:20"}:
        return "enlarged_plan"
    if any(word in text for word in ["section", "elevation"]):
        return "detail_plan"
    if any(word in text for word in ["layout plan", "general arrangement plan", "dimension plan", "floor plan"]):
        return "main_floor_plan"
    if any(word in text for word in ["enlarged", "detail plan", "detail", "fixture", "joinery", "signage", "equipment schedule", "equipment list", "counter detail"]):
        return "detail_plan"
    return "main_floor_plan"


def has_main_floor_signal(text, title_hits, support_hits):
    strong_titles = ["floor plan", "general arrangement plan", "layout plan", "main level", "ground floor", "level 1"]
    if any(word in text for word in strong_titles):
        return True
    if found_words(text, ["room", "office", "lease line", "area", "dimensions"]) and len(support_hits) >= 2:
        return True
    return bool(title_hits and not any(word in text for word in ["detail", "section", "elevation"]))


def visual_rejects_title_only_match(match, visual):
    if not visual:
        return False
    title_only = bool(match.get("matched_title_words")) and not match.get("matched_support_words")
    return title_only and visual.get("likely_view") == "cover_or_text"


def has_primary_plan_evidence(raw_text, match, visual):
    if non_plan_context(raw_text, visual):
        return False
    if is_strong_title_block_match(raw_text, match):
        return True
    if match.get("matched_title_words") and len(match.get("matched_support_words", [])) >= 2:
        return True
    if looks_like_dimensioned_top_view_plan(raw_text):
        return True
    return is_visual_top_down(visual)


def is_strong_title_block_match(raw_text, match):
    title_text = clean_text(extract_drawing_title(raw_text))
    if not title_text:
        return False
    return bool(found_words(title_text, match.get("matched_title_words", [])))


def visual_overrides_discard(discard, visual):
    if not is_visual_top_down(visual):
        return False
    return discard["type"] == "architectural_detail_noise"


def is_visual_top_down(visual):
    return bool(visual and visual.get("likely_view") == "top_down_plan" and visual.get("plan_confidence", 0) >= 0.72)


def side_or_detail_support(raw_text, page_number, visual):
    if not visual or visual.get("likely_view") != "side_or_detail":
        return None
    text = clean_text(raw_text)
    if not any(word in text for word in ["ceiling", "bulkhead", "duct", "diffuser", "grille", "mechanical"]):
        return None

    return {
        "page": page_number,
        "type": "ceiling_or_hvac_side_view",
        "importance": "reference",
        "title": guess_title(raw_text),
        "confidence": 0.52,
        "score": 45,
        "matched_title_words": [],
        "matched_support_words": ["visual side/detail with HVAC or ceiling context"],
        "extracted": extract_page_info(raw_text, "ceiling_or_hvac_side_view"),
        "visual_features": visual,
    }


def inferred_plan_type(document_title):
    title = clean_text(document_title)
    if "reflected ceiling plan" in title or "rcp" in title:
        return "reflected_ceiling_plan", "Reflected Ceiling Plan"
    if "ceiling plan" in title:
        return "reflected_ceiling_plan", "Ceiling Plan"
    return "floor_plan", "Dimensioned Top View Plan"


def looks_like_dimensioned_top_view_plan(raw_text):
    if visual_promotion_blocked(raw_text):
        return False
    return looks_like_dimensioned_top_view_plan_text_only(raw_text)


def looks_like_dimensioned_top_view_plan_text_only(raw_text):
    dimension_count = len(re.findall(r"\b\d+'\s*-\s*\d+", raw_text))
    dimension_count += len(re.findall(r"\b\d{3,5}\b", raw_text))
    grid_signal = len(re.findall(r"(?m)^\s*[A-D]\s*$", raw_text)) >= 2
    tag_signal = len(re.findall(r"\b[A-Z]{1,3}-\d{2}\b", raw_text)) >= 3
    room_signal = re.search(r"\b[A-Z][A-Z ]{2,}\s+\d{2,4}\b", raw_text) is not None

    return dimension_count >= 6 and sum([grid_signal, tag_signal, room_signal]) >= 2


def visual_promotion_blocked(raw_text):
    return non_plan_context(raw_text, None)


def non_plan_context(raw_text, visual=None):
    text = clean_text(raw_text)
    if visual and visual.get("likely_view") in {"render_or_photo", "cover_or_text"}:
        return True
    if notes_page_score(raw_text) >= 0.68:
        return True
    if render_or_photo_text_score(raw_text) >= 0.45:
        return True
    if schedule_or_component_score(raw_text) >= 0.55:
        return True
    if side_detail_text_score(raw_text) >= 0.62:
        return not (has_top_view_signal(raw_text) or "floor layout" in text)
    return False


def visual_can_promote_to_building_plan(raw_text):
    if sparse_or_unreadable_text(raw_text):
        return True
    return has_building_plan_context(raw_text)


def sparse_or_unreadable_text(raw_text):
    lines = [line for line in raw_text.splitlines() if line.strip()]
    letters = re.findall(r"[A-Za-z]", raw_text)
    return len(lines) <= 8 or len(letters) <= 80


def has_building_plan_context(raw_text):
    text = clean_text(raw_text)
    words = [
        "floor plan",
        "reflected ceiling plan",
        "ceiling plan",
        "general arrangement plan",
        "layout plan",
        "lease line",
        "room",
        "office",
        "bath",
        "kitchen",
        "level ",
        "ground floor",
        "main level",
    ]
    return any(word in text for word in words)


def notes_page_score(raw_text):
    lines = [line for line in raw_text.splitlines() if line.strip()]
    text = clean_text(raw_text)
    score = 0
    if len(lines) >= 70:
        score += 0.38
    if len(lines) >= 110:
        score += 0.22
    if "general notes" in text or "construction notes" in text:
        score += 0.28
    if "all drawings to be verified" in text or "do not scale" in text:
        score += 0.18
    if len(re.findall(r"\b\d+\.", raw_text)) >= 6:
        score += 0.18
    if looks_like_dimensioned_top_view_plan_text_only(raw_text):
        score -= 0.35
    if has_strong_plan_text_evidence(raw_text):
        score -= 0.45
    return max(0, min(1, score))


def has_strong_plan_text_evidence(raw_text):
    text = clean_text(raw_text)
    dimension_count = len(re.findall(r"\b\d{3,5}\b", raw_text))
    plan_words = ["reflected ceiling plan", "floor plan", "general arrangement plan", "layout plan"]
    services_words = ["diffuser", "grille", "supply air", "return air", "sprinkler", "access panel", "vav"]
    room_words = ["office", "room", "bath", "kitchen", "service counter"]
    return (
        dimension_count >= 10
        and any(word in text for word in plan_words)
        and (any(word in text for word in services_words) or any(word in text for word in room_words))
    )


def render_or_photo_text_score(raw_text):
    text = clean_text(raw_text)
    words = [
        "rendered view",
        "render image",
        "indicative only",
        "3d image",
        "3d view",
        "perspective",
        "photo reference",
        "site photo",
        "site plan",
        "locality",
        "proposed project",
        "nts",
    ]
    return min(1, sum(0.24 for word in words if word in text))


def schedule_or_component_score(raw_text):
    text = clean_text(raw_text)
    words = [
        "equipment schedule",
        "stainless steel schedule",
        "equipment list",
        "sample image",
        "3d reference",
        "counter detail",
        "screen detail",
    ]
    return min(1, sum(0.28 for word in words if word in text))


def side_detail_text_score(raw_text):
    text = clean_text(raw_text)
    strong_words = ["front elevation", "side elevation", "internal elevation", "shopfront elevation", "section", "bulkhead detail"]
    weak_words = ["elevation", "detail", "ceiling line"]
    score = sum(0.38 for word in strong_words if word in text)
    score += sum(0.22 for word in weak_words if word in text)
    if "plan view" in text and score:
        score += 0.18
    return min(1, score)


# ---------------------------------------------------------------- view titles
# The printed view title ("1  DIMENSION PLAN  SCALE 1:100", "PP  PROPOSED PLAN") says what a
# sheet is more reliably than words found anywhere on it (legends, notes and schedules mention
# "furniture", "section" or "schedule" on plan sheets). Kinds are checked in this order on each
# line, because "existing floor plan" also contains "floor plan".
VIEW_TITLE_KINDS = [
    ("rcp", r"(?:reflected|reflective)\s+ceiling(?:\s+set\s*-?\s*out)?\s+plan|ceiling\s+(?:set\s*-?\s*out\s+)?plan|rcp"),
    ("existing_plan", r"(?:existing|demolition)(?:\s*(?:&|and)\s*demolition)?\s+(?:floor\s+|layout\s+)?plan"),
    ("context_plan", r"(?:outdoor|site|location|locality|key|context|lease|core\s+hole\s+location)\s+(?:floor\s+)?plan"),
    ("support_plan", r"(?:loose\s+)?furniture(?:\s+layout)?\s+plan|(?:wall\s*(?:&|and)\s*)?floor\s+finish(?:es)?\s+plan|"
                     r"wall\s+finish(?:es)?\s+plan|finish(?:es)?\s+plan|partition(?:\s*(?:&|and)\s*finish(?:es)?)?\s+plan|"
                     r"equipment(?:\s+layout)?\s+plan|elevation\s+(?:index|guide|key)\s+plan"),
    # A plan named after one room or counter is an enlarged part plan, not the tenancy plan.
    ("part_plan", r"(?:bar|kitchen|toilets?|wc|amenit\w*|(?:front\s+|side\s+|bar\s+|training\s+)?counter|servery|"
                  r"f\s*&\s*b|reception|office|cool\s*room|freezer|store\s*room)(?:\s+(?:layout|floor|setout|set\s*-?\s*out))?\s+plan"),
    ("services_plan", r"(?:electrical|power(?:\s*(?:&|and)\s*data)?|power\s+points?\s+service\s+layout|lighting|hydraulics?|"
                      r"switch|data|signage|services?|fire|plumbing|drainage)\s+(?:layout\s+)?plan"),
    ("main_plan", r"(?:proposed\s+)?(?:floor|layout|dimension(?:ed)?(?:\s+floor)?|general\s+arrangement|ga|set\s*-?\s*out|"
                  r"fit\s*-?\s*out|tenancy|ground\s+floor|mezzanine(?:\s+floor)?)\s+plan|proposed\s+plan"),
    ("shopfront", r"(?:shop\s*front|store\s*front|external|street|facade)\s+elevations?"),
    ("elevation", r"(?:internal\s+|interior\s+|front\s+|side\s+|rear\s+)?elevations?(?:\s+[a-z0-9]{1,3})?"),
    ("section", r"sections?(?:\s+[a-z0-9]{1,3})?"),
    ("schedule", r"[a-z][a-z &/]{1,40}?\s+schedules?(?:\s*-?\s*\d{1,2})?"),
]
VIEW_TITLE_PRIORITY = {"rcp": 0, "main_plan": 1, "shopfront": 2, "existing_plan": 3, "support_plan": 4, "part_plan": 4,
                       "services_plan": 5, "context_plan": 6, "elevation": 7, "section": 8, "schedule": 9}
TITLE_BLOCK_LABEL = re.compile(r"(?:drawing\s+)?title\s*:\s*([a-z0-9 &/\-()]{3,70}?\b(?:plan|elevations?|sections?|schedules?))\b", re.I)
TITLE_NOTE_WORDS = re.compile(r"\b(?:refer|conjunction|see|shown|to\s+be|shall|must|notify|read|including|accordance|"
                              r"verify|check|confirm|discrepanc\w*|contractor|prior)\b", re.I)
# Drawing-view codes before a title: "1", "1.", "P01", "A-101", or up to three letters followed by a
# column gap ("EP   EXISTING FLOOR PLAN"). A word like "BAR" followed by one space is part of the title.
TITLE_CODE_PREFIX = re.compile(r"^(?:\d{1,3}(?:\.\d{1,2})?[\s.:)\-]+|[a-z]{1,3}-?\d{1,3}(?:\.\d{1,2})?[\s.:)\-]+|[a-z]{1,3}\s{2,})?", re.I)
DRAWING_LIST_ROW = re.compile(r"^\s*(?:[a-z]{1,3}[-\s.]?\d{2,4}(?:\.\d)?|\d{2}\.\d{2})\s{2,}[a-z]", re.I)
DRAWING_WORD = re.compile(r"\b(?:plan|elevations?|sections?|notes|page|details?|schedules?|perspectives?|images?|rcp)\b", re.I)


def view_title_candidates(raw_text):
    """(kind, phrase, line) for every line that reads as a printed view title.

    When no line does, the title block is used instead: on some sheets the view title is only
    recoverable from there. Not on notes, render or schedule pages, whose title area often names
    other drawings.
    """
    found = _title_candidates_from(raw_text.splitlines())
    if not found and not non_plan_context(raw_text):
        # A labelled title ("TITLE : BAR LAYOUT PLAN") is more specific than the extracted drawing
        # title, which may keep only the generic end ("Layout Plan").
        labelled = [match.group(1) for match in TITLE_BLOCK_LABEL.finditer(title_area(raw_text))]
        sheet_titles = labelled or [clean_text(extract_drawing_title(raw_text, title_block_only=True))]
        plain_lines = [re.sub(r"\s+", " ", line).lower() for line in raw_text.splitlines()
                       if not TITLE_NOTE_WORDS.search(line) and len(SHEET_NUMBER.findall(line)) < 2]
        for kind, phrase, line in _title_candidates_from(sheet_titles):
            # The title must appear somewhere other than inside a note ("refer to reflected ceiling plan")
            # or a drawing-list row ("01.01 Layout Plan  03.18 Detail").
            if labelled or any(" ".join(phrase.split()) in plain for plain in plain_lines):
                found.append((kind, phrase, line))
    return found


ROOM_AFTER_TITLE = re.compile(r"\s*[-–:,]?\s*(?:bathroom|toilets?|wc|kitchen|bar|amenit\w*|counter|servery|cool\s*room|"
                              r"freezer|store\s*room|reception|office)\b", re.I)
SHEET_NUMBER = re.compile(r"\b(?:\d{1,3}\.\d{2}|[a-z]{1,2}-?\d{2,4}(?:\.\d)?)\b", re.I)


def _title_candidates_from(lines):
    found = []
    for raw_line in lines:
        line = re.sub(r"[ \t]{2,}", "  ", raw_line.strip())
        if not line or len(line) > 160 or TITLE_NOTE_WORDS.search(line):
            continue
        if len(SHEET_NUMBER.findall(line)) >= 2:  # a drawing-list row ("01.01 Layout Plan  03.18 Detail")
            continue
        lowered = line.lower()
        lowered = re.sub(r"^drawing\s+title\s*:?\s*", "", lowered)
        body = TITLE_CODE_PREFIX.sub("", lowered, count=1).strip()
        body = re.sub(r"^(?:proposed|new)\s+(?!plan\b)", "", body)  # "proposed reflected ceiling plan"
        for kind, pattern in VIEW_TITLE_KINDS:
            match = re.match(r"(?:" + pattern + r")\b", body)
            if not match:
                continue
            rest = body[match.end():]
            if kind == "main_plan" and ROOM_AFTER_TITLE.match(rest):  # "layout plan bathroom": a part plan
                found.append(("part_plan", match.group(0), line))
                break
            # The title ends the line, or is followed by a column gap, a level name or the scale.
            if rest.strip() and not re.match(r"\s{2,}|\s*[-–:]?\s*(?:level\b|ground\b|lower\b|upper\b|mezzanine\b|scale\b|1\s*:\s*\d|\(|option\b)", rest):
                continue
            found.append((kind, match.group(0), line))
            break
    return found


def view_title(raw_text):
    """The page's strongest printed view title: (kind, phrase), ("drawing_list", "") or None."""
    # Five or more "A-131    PROPOSED RCP (GF)"-style rows naming drawings: a drawing list (cover or
    # index sheet). Legend rows on plans ("FL 02    FLOOR TILES") don't name a drawing type.
    if sum(1 for line in raw_text.splitlines() if DRAWING_LIST_ROW.match(line) and DRAWING_WORD.search(line)) >= 5:
        return ("drawing_list", "")
    candidates = view_title_candidates(raw_text)
    if not candidates:
        return None
    # A drawing list names many sheets of different kinds (plans, ceiling plans, elevations, schedules);
    # a sheet of elevations A-D is one kind.
    coded_rows = [line for kind, phrase, line in candidates if DRAWING_LIST_ROW.match(line)]
    phrases, kinds = {phrase for kind, phrase, line in candidates}, {kind for kind, phrase, line in candidates}
    if len(phrases) >= 4 and (len(coded_rows) >= 4 or len(kinds) >= 3):
        return ("drawing_list", "")
    kind, phrase, _line = min(candidates, key=lambda row: VIEW_TITLE_PRIORITY[row[0]])
    return (kind, phrase)


def strongly_top_down(visual):
    return bool(visual and visual.get("likely_view") == "top_down_plan" and visual.get("top_down_score", 0) >= 0.8)


def title_first_match(raw_text, page_number, visual):
    """Classify a page by its printed view title, before the word-anywhere rules. None = no decision."""
    titled = view_title(raw_text)
    if titled is None:
        return None
    kind, phrase = titled
    title = " ".join(phrase.split()).title() if phrase else ""
    base = {"page": page_number, "title": title, "confidence": 0.9, "score": 150,
            "matched_title_words": [phrase] if phrase else [], "matched_support_words": ["printed view title"]}
    if kind == "drawing_list":
        return {"bucket": "discarded", "discard": {"type": "cover_or_drawing_list", "matched_words": ["drawing list"]}}
    if kind == "rcp":
        match = {**base, "type": "reflected_ceiling_plan", "importance": "essential", "plan_role": "reflected_ceiling_plan"}
    elif kind == "main_plan":
        match = {**base, "type": "floor_plan", "importance": "essential", "plan_role": "main_floor_plan"}
    elif kind in {"support_plan", "part_plan"}:
        role = ("furniture_plan" if "furniture" in phrase else "enlarged_plan" if kind == "part_plan"
                else "supporting_geometry_plan")
        match = {**base, "type": "floor_plan", "importance": "essential", "plan_role": role}
    elif kind == "context_plan":
        match = {**base, "type": "site_plan", "importance": "useful", "plan_role": "site_plan"}
    elif kind == "schedule" and not strongly_top_down(visual):
        # A page titled only as a schedule (lighting, finishes, equipment…) is a table, not a plan.
        schedule_type = ("material_or_finish_schedule" if re.search(r"finish|material", phrase)
                         else "equipment_or_fixture_schedule")
        match = {**base, "type": schedule_type, "importance": "useful", "plan_role": "reference_context"}
        match["extracted"] = extract_page_info(raw_text, schedule_type)
        attach_visual_features(match, visual)
        return {"bucket": "reference", "match": match}
    elif kind == "shopfront":
        match = {**base, "type": "elevation", "importance": "useful", "plan_role": "reference_context"}
        match["extracted"] = extract_page_info(raw_text, "elevation")
        attach_visual_features(match, visual)
        return {"bucket": "reference", "match": match}
    else:  # existing and services plans keep their earlier handling
        return None
    match["extracted"] = extract_page_info(raw_text, match["type"])
    attach_visual_features(match, visual)
    return {"bucket": "primary", "match": match}


SERVICES_TITLE = re.compile(r"\b(?:mechanical|services|pipework|ductwork|electrical|power|lighting|hydraulics?|plumbing|"
                            r"drainage|fire|sprinkler|data|communications)\b", re.I)


def fallback_main_plan(primary_pages, kept_pages, texts):
    """When no sheet reads as a main plan, use the most plan-like page (visual top-down score).

    Never a ceiling plan, elevation, section, schedule or a page whose printed title says it is
    something else; only pages the visual check sees as top-down plans with a score of 0.75 or more.
    """
    if any(row.get("plan_role") == "main_floor_plan" for row in primary_pages):
        return None
    excluded_types = {"reflected_ceiling_plan", "elevation", "section", "site_plan", "equipment_or_fixture_schedule",
                      "material_or_finish_schedule", "schedule", "notes", "cover_or_drawing_list", "render_or_photo"}
    candidates = []
    for row in [*primary_pages, *kept_pages]:
        visual = row.get("visual_features") or {}
        text = texts[row["page"] - 1] if 0 < row["page"] <= len(texts) else ""
        titled = view_title(text)
        if row.get("type") in excluded_types or row.get("review_bucket") == "non_thermal":
            continue
        if titled and titled[0] != "main_plan":  # titled as something else (section, schedule, services…)
            continue
        if SERVICES_TITLE.search(str(row.get("title", ""))):  # a services consultant's plan, not the tenancy plan
            continue
        if visual.get("likely_view") not in {"top_down_plan", "uncertain"} or visual.get("top_down_score", 0) < 0.75:
            continue
        if schedule_or_component_score(text) >= 0.55 or notes_page_score(text) >= 0.68:
            continue
        candidates.append((visual.get("top_down_score", 0), -row["page"], row))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def find_pages(pdf_path):
    matches = []
    document_title = extract_pdf_title(pdf_path)
    visual_features = safe_visual_features(pdf_path)
    for page_number, text in enumerate(extract_text_pages(pdf_path), start=1):
        match = classify_page(text, page_number, document_title, visual_features.get(page_number))
        if match:
            matches.append(match)
    return sorted(matches, key=sort_key)


def analyze_pages(pdf_path, visual_features=None):
    primary_pages = []
    reference_pages = []
    kept_pages = []
    discarded_pages = []
    document_title = extract_pdf_title(pdf_path)
    visual_features = visual_features or safe_visual_features(pdf_path)

    texts = list(extract_text_pages(pdf_path))
    for page_number, text in enumerate(texts, start=1):
        visual = visual_features.get(page_number)
        titled = title_first_match(text, page_number, visual)
        if titled and titled["bucket"] == "discarded":
            discarded = discarded_page(text, page_number, titled["discard"], visual)
            discarded_pages.append(discarded)
            kept_pages.append(retained_non_thermal_page(discarded, text))
            continue
        if titled and titled["bucket"] == "primary":
            primary_pages.append(titled["match"])
            kept_pages.append(kept_page_from_match(titled["match"], "primary"))
            continue
        if titled and titled["bucket"] == "reference":
            reference_pages.append(titled["match"])
            kept_pages.append(kept_page_from_match(titled["match"], "reference"))
            continue
        visual_discard = visual_non_design_discard(text, visual)
        if visual_discard:
            discarded = discarded_page(text, page_number, visual_discard, visual)
            discarded_pages.append(discarded)
            kept_pages.append(retained_non_thermal_page(discarded, text))
            continue

        discard = is_primary_discard(text)
        if discard:
            if visual_overrides_discard(discard, visual):
                primary = inferred_plan_match(text, page_number, document_title, visual)
                if primary:
                    primary_pages.append(primary)
                    kept_pages.append(kept_page_from_match(primary, "primary"))
                    continue
            discarded = discarded_page(text, page_number, discard, visual)
            discarded_pages.append(discarded)
            kept_pages.append(retained_non_thermal_page(discarded, text))
            continue

        primary = classify_page(text, page_number, document_title, visual)
        if primary:
            primary_pages.append(primary)
            kept_pages.append(kept_page_from_match(primary, "primary"))
            continue

        side_reference = side_or_detail_support(text, page_number, visual)
        if side_reference:
            reference_pages.append(side_reference)
            kept_pages.append(kept_page_from_match(side_reference, "reference"))
            continue

        reference = classify_reference_page(text, page_number)
        if reference:
            attach_visual_features(reference, visual)
            reference_pages.append(reference)
            kept_pages.append(kept_page_from_match(reference, "reference"))
            continue

        kept_pages.append(unclassified_page(text, page_number, visual))

    fallback = fallback_main_plan(primary_pages, kept_pages, texts)
    if fallback is not None:
        promoted = {**fallback, "type": "floor_plan", "plan_role": "main_floor_plan", "importance": "essential",
                    "plan_role_reason": "No sheet is titled as a main plan; this is the most plan-like page."}
        primary_pages = [row for row in primary_pages if row["page"] != promoted["page"]] + [promoted]
        kept_pages = [row for row in kept_pages if row["page"] != promoted["page"]] + [kept_page_from_match(promoted, "primary")]

    return {
        "primary_pages": sorted(primary_pages, key=sort_key),
        "reference_pages": sorted(reference_pages, key=sort_key),
        "kept_pages": sorted(kept_pages, key=kept_sort_key),
        "discarded_pages": discarded_pages,
    }


def build_design_packet(pdf_path, render_pages=False, thumbnail_dir=None, include_structure=False):
    visual_features = safe_visual_features(pdf_path)
    analysis = analyze_pages(pdf_path, visual_features)
    packet = {
        "pdf": str(pdf_path),
        "primary_pages": analysis["primary_pages"],
        "reference_pages": analysis["reference_pages"],
        "kept_pages": analysis["kept_pages"],
        "discarded_pages": analysis["discarded_pages"],
        "visual_features": visual_features,
    }
    if include_structure:
        pages = classified_pages(packet)
        page_numbers = [page["page"] for page in pages]
        packet["structured_pages"] = useful_page_structure(extract_structured_pdf(pdf_path, page_numbers), pages)
    if render_pages:
        thumbnails = render_thumbnails(pdf_path, thumbnail_dir, page_numbers=kept_page_numbers(packet))
        packet["thumbnails"] = thumbnails
        attach_thumbnail_paths(packet["primary_pages"], thumbnails)
        attach_thumbnail_paths(packet["reference_pages"], thumbnails)
        attach_thumbnail_paths(packet["kept_pages"], thumbnails)
    return packet


def safe_visual_features(pdf_path):
    try:
        return extract_pdf_visual_features(pdf_path)
    except Exception:
        return {}


def visual_non_design_discard(raw_text, visual):
    if render_or_photo_text_score(raw_text) >= 0.45:
        return {"type": "render_or_photo", "matched_words": ["render/photo presentation evidence"]}
    if visual and visual.get("likely_view") == "render_or_photo" and not has_strong_plan_text_evidence(raw_text):
        return {"type": "render_or_photo", "matched_words": ["photo-like page image"]}
    return None


def kept_pages(packet):
    return packet.get("kept_pages", packet["primary_pages"] + packet["reference_pages"])


def classified_pages(packet):
    return kept_pages(packet)


def kept_page_numbers(packet):
    return [page["page"] for page in kept_pages(packet)]


def attach_thumbnail_paths(pages, thumbnails):
    by_page = {thumbnail["page"]: thumbnail["path"] for thumbnail in thumbnails}
    for page in pages:
        page["thumbnail_path"] = by_page.get(page["page"])


def kept_page_from_match(match, bucket):
    kept = dict(match)
    kept["review_bucket"] = bucket
    kept["sheet_classification"] = kept.get("type", "other")
    kept["thermal_role"] = thermal_role(kept["sheet_classification"])
    kept["classification_evidence"] = classification_evidence(kept)
    return kept


def discarded_page(text, page_number, discard, visual):
    return {
        "page": page_number,
        "type": discard["type"],
        "title": guess_title(text),
        "matched_words": discard["matched_words"],
        "visual_features": visual,
    }


def retained_non_thermal_page(discarded, text):
    page = dict(discarded)
    page.update({
        "importance": "indexed",
        "confidence": 0.95,
        "score": 0,
        "matched_title_words": discarded.get("matched_words", []),
        "matched_support_words": [],
        "extracted": extract_page_info(text, discarded["type"]),
        "review_bucket": "non_thermal",
        "sheet_classification": discarded["type"],
        "thermal_role": "not_calculation_evidence",
        "classification_evidence": "Explicit non-calculation title or visual classification.",
    })
    return page


def unclassified_page(text, page_number, visual):
    classification = context_sheet_classification(text, visual)
    page = {
        "page": page_number,
        "type": classification,
        "importance": "possible_context",
        "title": guess_title(text),
        "confidence": 0.0,
        "score": 0,
        "matched_title_words": [],
        "matched_support_words": [],
        "extracted": extract_page_info(text, classification),
        "visual_features": visual,
        "review_bucket": "unclassified",
        "sheet_classification": classification,
        "thermal_role": thermal_role(classification),
        "classification_evidence": "Retained because its thermal role could not be ruled out automatically.",
    }
    return page


def context_sheet_classification(text, visual):
    clean = clean_text(text)
    if "elevation" in clean:
        return "elevation"
    if "section" in clean:
        return "section"
    if "roof plan" in clean:
        return "roof_plan"
    if "site plan" in clean or "locality plan" in clean:
        return "site_plan"
    if any(word in clean for word in ["perspective", "3d view", "3d image", "artist impression", "rendered view"]):
        return "perspective_or_3d"
    if any(word in clean for word in ["construction detail", "wall detail", "glazing detail", "door detail", "detail"]):
        return "detail"
    if any(word in clean for word in ["schedule", "equipment list"]):
        return "schedule"
    if any(word in clean for word in ["general notes", "specification", "notes"]):
        return "notes"
    if visual and visual.get("likely_view") == "render_or_photo":
        return "perspective_or_3d"
    return "other"


def thermal_role(sheet_classification):
    if sheet_classification in {"floor_plan", "roof_plan"}:
        return "primary_geometry"
    if sheet_classification in {"elevation", "section", "reflected_ceiling_plan"}:
        return "surface_confirmation"
    if sheet_classification == "site_plan":
        return "site_orientation_or_shading"
    if sheet_classification in {"detail", "door_schedule"}:
        return "construction_or_opening_detail"
    if sheet_classification in {"existing_hvac_or_services_plan", "equipment_or_fixture_schedule", "material_or_finish_schedule", "architect_lighting_plan", "architect_electrical_plan", "bca_or_ventilation_notes"}:
        return "services_or_internal_load"
    if sheet_classification == "perspective_or_3d":
        return "visual_context"
    return "not_calculation_evidence"


def classification_evidence(page):
    words = page.get("matched_title_words") or page.get("matched_support_words") or []
    if words:
        return "Matched drawing evidence: " + ", ".join(words)
    visual = page.get("visual_features") or {}
    if visual.get("likely_view"):
        return "Visual classification: " + visual["likely_view"].replace("_", " ")
    return "Retained drawing-set page."


def kept_sort_key(match):
    bucket_rank = {"primary": 0, "reference": 1, "unclassified": 2, "non_thermal": 3}
    return (
        bucket_rank.get(match.get("review_bucket"), 9),
        level_sort_value(match),
        match["page"],
    )


def sort_key(match):
    importance_rank = {"essential": 0, "useful": 1, "supporting": 2}
    return (
        importance_rank.get(match["importance"], 9),
        level_sort_value(match),
        -match["confidence"],
        -match["score"],
        match["page"],
    )


def level_sort_value(match):
    level = clean_text(match.get("extracted", {}).get("level_name", ""))
    title = clean_text(match.get("title", ""))
    text = f"{level} {title}"

    if "basement" in text:
        return -200
    if "lower ground" in text:
        return -100
    if "ground floor" in text or "ground level" in text or "main level" in text or "main floor" in text:
        return 0
    if "roof" in text:
        return 900

    ordinal = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
    }
    for word, value in ordinal.items():
        if f"{word} floor" in text:
            return value

    match_level = re.search(r"\b(?:level|floor)\s+(\d+)", text)
    if match_level:
        return int(match_level.group(1))

    return 500


def print_results(packet):
    if not packet["primary_pages"] and not packet["reference_pages"]:
        print("No useful architect pages found for HVAC design.")
        return

    print_page_table("Primary pages for HVAC design:", packet["primary_pages"])

    if packet["reference_pages"]:
        print_page_table("Reference pages kept for AI/user review:", packet["reference_pages"])

    if packet["discarded_pages"]:
        print()
        print(f"Discarded obvious non-HVAC pages: {len(packet['discarded_pages'])}")


def print_page_table(title, pages):
    print()
    print(title)
    print()
    print(f"{'Page':<6} {'Use':<11} {'Confidence':<11} {'Type':<34} Title")
    print("-" * 95)
    for match in pages:
        print(
            f"{match['page']:<6} "
            f"{match['importance']:<11} "
            f"{match['confidence']:<11.2f} "
            f"{match['type']:<34} "
            f"{match['title']}"
        )
        print_extracted(match["extracted"])


def print_extracted(data):
    print(f"       scale: {data['scale'] or 'manual confirmation needed'}")
    if data["written_dimensions"]:
        dims = ", ".join(
            f"{dimension['value']} {dimension['unit']}" for dimension in data["written_dimensions"][:10]
        )
        print(f"       written dimensions: {dims}")
    if data["rooms"]:
        rooms = ", ".join(f"{room['name']} ({room['area']})" for room in data["rooms"][:5])
        print(f"       rooms: {rooms}")
    if data["ceiling_constraints"]:
        print("       constraints: " + ", ".join(data["ceiling_constraints"][:8]))
    if data["hvac_terms"]:
        print("       hvac terms: " + ", ".join(data["hvac_terms"][:8]))


def main():
    parser = argparse.ArgumentParser(description="Find useful architect PDF pages for HVAC design.")
    parser.add_argument("pdf", help="Path to the architect PDF")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--render-thumbnails",
        action="store_true",
        help="Render useful PDF pages to PNG screenshots",
    )
    parser.add_argument(
        "--thumbnail-dir",
        help="Directory for rendered screenshots; defaults to output/thumbnails/<pdf name>",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Create packet.json, relevant page screenshots, and review.html for human checking",
    )
    parser.add_argument(
        "--review-dir",
        help="Directory for the review packet; defaults to output/review/<pdf name>",
    )
    parser.add_argument(
        "--structured",
        action="store_true",
        help="Include Markdown, bounding boxes, tables, and OCR-needed flags for kept pages",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    if args.review:
        from pdf_pipeline.review import create_review_packet

        result = create_review_packet(pdf_path, args.review_dir, args.structured)
        print("Review packet created:")
        print(f"  HTML: {result['html']}")
        print(f"  JSON: {result['packet']}")
        print(f"  Thumbnails: {result['thumbnail_count']}")
        print(f"  Primary pages: {result['primary_count']}")
        print(f"  Reference pages: {result['reference_count']}")
        print(f"  Kept pages: {result['kept_count']}")
        print(f"  Discarded pages: {result['discarded_count']}")
        print(f"  Structured pages: {result['structured_count']}")
        return

    packet = build_design_packet(pdf_path, args.render_thumbnails, args.thumbnail_dir, args.structured)
    if args.json:
        print(json.dumps(packet, indent=2))
    else:
        print_results(packet)


if __name__ == "__main__":
    main()
