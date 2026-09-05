#!/usr/bin/env python3

"""Classify drawing numbers before downstream code treats them as dimensions."""

import re


REFERENCE_WORDS = {"detail", "section", "elevation", "drawing", "sheet", "scale", "callout"}


def classify_numeric_annotation(text, bbox, words, marker_bboxes=()):
    """Return a conservative semantic classification for a numeric drawing annotation."""
    value = numeric_value(text)
    if value is None:
        return None

    marker = enclosing_marker(bbox, marker_bboxes)
    nearby = nearby_words(bbox, words)
    peer_numbers = [word for word in nearby if numeric_token(word.get("text", "")) and word.get("text") != text]
    reference_words = [word.get("text", "") for word in nearby if word.get("text", "").lower() in REFERENCE_WORDS]
    reasons = []
    if marker:
        reasons.append("inside or adjacent to a compact closed marker")
    if peer_numbers:
        reasons.append("paired with another number in the same callout area")
    if reference_words:
        reasons.append(f"near reference text: {', '.join(reference_words[:2])}")

    if marker and (peer_numbers or reference_words):
        return annotation("detail_or_sheet_reference", "ineligible", reasons)
    return annotation("unknown_numeric", "vision_review", reasons or ["no positive dimension evidence in text/vector extraction"])


def annotation(kind, eligibility, reasons):
    return {
        "annotation_kind": kind,
        "dimension_eligibility": eligibility,
        "annotation_reasons": reasons,
    }


def numeric_value(text):
    clean = str(text or "").replace(",", "").strip()
    if not re.fullmatch(r"\d{3,5}", clean):
        return None
    value = int(clean)
    return value if 400 <= value <= 50000 and not 1900 <= value <= 2099 else None


def numeric_token(text):
    return bool(re.fullmatch(r"\d{1,5}", str(text or "").replace(",", "").strip()))


def enclosing_marker(text_bbox, marker_bboxes):
    if not valid_bbox(text_bbox):
        return None
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    center = [(text_bbox[0] + text_bbox[2]) / 2, (text_bbox[1] + text_bbox[3]) / 2]
    for marker in marker_bboxes:
        if not valid_bbox(marker):
            continue
        width = marker[2] - marker[0]
        height = marker[3] - marker[1]
        if min(width, height) < max(text_height * 1.25, 6):
            continue
        if max(width, height) > max(text_width, text_height) * 14:
            continue
        ratio = width / height if height else 0
        if not 0.5 <= ratio <= 2:
            continue
        pad = max(text_height * 0.8, 3)
        if marker[0] - pad <= center[0] <= marker[2] + pad and marker[1] - pad <= center[1] <= marker[3] + pad:
            return marker
    return None


def nearby_words(text_bbox, words):
    if not valid_bbox(text_bbox):
        return []
    radius = max(text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1], 12) * 4
    center = [(text_bbox[0] + text_bbox[2]) / 2, (text_bbox[1] + text_bbox[3]) / 2]
    nearby = []
    for word in words:
        bbox = word.get("bbox") or word.get("bbox_px")
        if not valid_bbox(bbox):
            continue
        other = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
        if abs(other[0] - center[0]) <= radius and abs(other[1] - center[1]) <= radius:
            nearby.append(word)
    return nearby


def valid_bbox(bbox):
    return isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(value, (int, float)) for value in bbox) and bbox[2] > bbox[0] and bbox[3] > bbox[1]
