#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path

import pdfplumber

from ai.ai_packet import load_json
from pdf_pipeline.numeric_annotations import classify_numeric_annotation


def create_spatial_ocr(packet_path, output_path=None, decisions=None):
    packet_path = Path(packet_path)
    packet = load_json(packet_path)
    pages = useful_pages(packet, decisions or {})
    source_pdf = resolve_source_path(packet.get("pdf", ""), packet_path.parent)

    with pdfplumber.open(source_pdf) as pdf:
        results = [analyse_page(pdf.pages[page["page"] - 1], page) for page in pages]

    output = {
        "source_pdf": packet.get("pdf", ""),
        "source_packet": str(packet_path),
        "pages": results,
        "note": "Spatial OCR is coordinate evidence for AI/vision. It is not confirmed design truth.",
    }
    output_path = Path(output_path or packet_path.with_name("spatial_ocr.json"))
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output_path


def useful_pages(packet, decisions):
    decision_by_page = {item["page"]: item for item in decisions.get("pages", [])}
    if decision_by_page:
        pages = []
        for page in packet.get("kept_pages", packet.get("primary_pages", []) + packet.get("reference_pages", [])):
            decision = decision_by_page.get(page["page"])
            if decision and decision.get("decision") != "Discard":
                pages.append(page)
        return sorted(pages, key=lambda page: page["page"])

    pages = []
    for page in packet.get("primary_pages", []) + packet.get("reference_pages", []):
        pages.append(page)
    return sorted(pages, key=lambda page: page["page"])


def resolve_source_path(path, base_dir):
    source = Path(path)
    if source.is_absolute() and source.exists():
        return source
    if source.exists():
        return source
    candidate = base_dir / source
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Source PDF not found: {path}")


def analyse_page(pdf_page, packet_page):
    words = normalised_words(pdf_page.extract_words() or [])
    lines = pdf_page.lines or []
    rects = pdf_page.rects or []
    marker_bboxes = [vector_bbox(item) for item in (pdf_page.curves or []) + rects]
    title_regions = title_block_regions(pdf_page.width, pdf_page.height)
    title_blocks = [region_summary(name, bbox, words) for name, bbox in title_regions]

    return {
        "page": packet_page["page"],
        "detected_type": packet_page.get("type", ""),
        "title": packet_page.get("title", ""),
        "width": round(pdf_page.width, 2),
        "height": round(pdf_page.height, 2),
        "quality": {
            "word_count": len(words),
            "line_count": len(lines),
            "rect_count": len(rects),
            "has_text_layer": len(words) > 0,
            "has_vector_data": len(lines) + len(rects) > 50,
            "needs_ocr": len(words) == 0,
        },
        "title_blocks": title_blocks,
        "scale_candidates": scale_candidates(words, title_blocks),
        "drawing_number_candidates": drawing_number_candidates(words, title_blocks),
        "dimension_candidates": dimension_candidates(words, marker_bboxes),
        "room_label_candidates": room_label_candidates(words),
        "rotated_text": [word for word in words if word["orientation"] != "horizontal"][:80],
        "word_samples": words[:120],
    }


def normalised_words(words):
    clean = []
    for word in words:
        text = re.sub(r"\s+", " ", word.get("text", "")).strip()
        if not text:
            continue
        clean.append(
            {
                "text": text,
                "bbox": bbox(word),
                "orientation": "horizontal" if word.get("upright", True) else "rotated",
                "confidence": "pdf_text_layer",
            }
        )
    return clean


def bbox(word):
    return [
        round(word["x0"], 2),
        round(word["top"], 2),
        round(word["x1"], 2),
        round(word["bottom"], 2),
    ]


def title_block_regions(width, height):
    return [
        ("bottom_band", [0, round(height * 0.82, 2), round(width, 2), round(height, 2)]),
        ("bottom_right", [round(width * 0.55, 2), round(height * 0.65, 2), round(width, 2), round(height, 2)]),
        ("right_band", [round(width * 0.78, 2), 0, round(width, 2), round(height, 2)]),
    ]


def region_summary(name, region_bbox, words):
    region_words = [word for word in words if bbox_intersects(word["bbox"], region_bbox)]
    text = " ".join(word["text"] for word in region_words)
    return {
        "region": name,
        "bbox": region_bbox,
        "word_count": len(region_words),
        "text_excerpt": text[:2000],
    }


def bbox_intersects(a, b):
    return max(a[0], b[0]) <= min(a[2], b[2]) and max(a[1], b[1]) <= min(a[3], b[3])


def scale_candidates(words, title_blocks):
    candidates = []
    for source, text, source_bbox in searchable_text(words, title_blocks):
        for match in re.finditer(r"(?i)scale\s*[:@]?\s*(?:at\s+)?(?:a\d\s+)?(\d+\s*:\s*\d+)", text):
            candidates.append(candidate(source, match.group(1).replace(" ", ""), source_bbox))
        for match in re.finditer(r"\b(1\s*:\s*(?:5|10|20|25|50|75|100|125|150|200|250|500))\b", text):
            candidates.append(candidate(source, re.sub(r"\s*:\s*", ":", match.group(1)), source_bbox))
        for match in re.finditer(r"\b(\d+/\d+\"\s*=\s*\d+'-?\d*\")", text):
            candidates.append(candidate(source, match.group(1), source_bbox))
    return unique_candidates(candidates)[:20]


def drawing_number_candidates(words, title_blocks):
    candidates = []
    patterns = [r"\b[A-Z]{1,4}\d{1,4}(?:\.\d+)?\b", r"\bTA-\d+\b", r"\bM\d+\.\d+\b", r"\bA\d+\.\d+\b"]
    for source, text, source_bbox in searchable_text(words, title_blocks):
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                candidates.append(candidate(source, match.group(0), source_bbox))
    return unique_candidates(candidates)[:30]


def searchable_text(words, title_blocks):
    for block in title_blocks:
        yield block["region"], block["text_excerpt"], block["bbox"]
    yield "full_page", " ".join(word["text"] for word in words), []


def dimension_candidates(words, marker_bboxes=()):
    candidates = []
    for word in words:
        value = dimension_value(word["text"])
        if value is None:
            continue
        item = {
            "text": word["text"],
            "value_mm": value,
            "bbox": word["bbox"],
            "orientation": word["orientation"],
            "status": "unassigned_spatial_ocr_candidate",
        }
        item.update(classify_numeric_annotation(word["text"], word["bbox"], words, marker_bboxes) or {})
        candidates.append(item)
    return candidates[:120]


def dimension_value(text):
    clean = text.replace(",", "")
    if not re.fullmatch(r"\d{3,5}", clean):
        return None
    value = int(clean)
    if 400 <= value <= 50000 and not 1900 <= value <= 2099:
        return value
    return None


def vector_bbox(item):
    values = [item.get(key) for key in ["x0", "top", "x1", "bottom"]]
    if not all(isinstance(value, (int, float)) for value in values):
        return []
    return [round(value, 2) for value in values]


def room_label_candidates(words):
    labels = []
    for index, word in enumerate(words):
        text = word["text"]
        if not looks_like_label(text):
            continue
        nearby = words[index : index + 3]
        joined = " ".join(item["text"] for item in nearby if looks_like_label(item["text"]))
        if joined:
            labels.append({"text": joined[:80], "bbox": word["bbox"], "status": "possible_room_or_area_label"})
    return unique_label_candidates(labels)[:80]


def looks_like_label(text):
    if len(text) < 3 or len(text) > 30:
        return False
    if re.search(r"\d", text):
        return False
    if text.isupper() and len(text) <= 4:
        return False
    bad = ["scale", "date", "drawing", "revision", "project", "north"]
    return not any(word in text.lower() for word in bad)


def candidate(source, text, source_bbox):
    confidence = "higher" if source in ["bottom_band", "bottom_right", "right_band"] else "lower"
    return {"text": text, "source": source, "source_bbox": source_bbox, "source_confidence": confidence}


def unique_candidates(candidates):
    seen = set()
    clean = []
    for item in candidates:
        key = (item["text"], item["source"])
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
    return clean


def unique_label_candidates(labels):
    seen = set()
    clean = []
    for item in labels:
        key = item["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
    return clean


def main():
    parser = argparse.ArgumentParser(description="Extract spatial OCR evidence from useful architect PDF pages.")
    parser.add_argument("packet", help="Path to packet.json")
    parser.add_argument("--decisions", help="Optional reviewed_decisions.json")
    parser.add_argument("--output", help="Output path; defaults to spatial_ocr.json beside packet.json")
    args = parser.parse_args()

    decisions = load_json(args.decisions) if args.decisions else None
    output = create_spatial_ocr(args.packet, args.output, decisions)
    print(f"Spatial OCR created: {output}")


if __name__ == "__main__":
    main()
