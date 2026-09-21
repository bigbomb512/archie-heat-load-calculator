"""Normalize AI-proposed openings into a traceable, non-authoritative register."""

from copy import deepcopy
import hashlib
import json
from math import isfinite


FACADES = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def resolve_openings(vision_response, source_fingerprint, known_pages, window_scan=None, window_reviews=None):
    """AI supplies relationships; this validator never creates window properties."""
    review = (vision_response or {}).get("result", {}).get("geometry_review", {})
    if not review and isinstance((vision_response or {}).get("geometry_review"), dict):
        review = vision_response["geometry_review"]
    pages = set(known_pages)
    records = []
    for page in review.get("pages", []) if isinstance(review, dict) else []:
        physical_page = page.get("page")
        for raw in page.get("opening_candidates", []) or []:
            if not isinstance(raw, dict):
                continue
            tag = str(raw.get("tag", "")).strip().upper()
            level = str(raw.get("level_name", "")).strip().casefold()
            facade = str(raw.get("facade", "")).strip().upper()
            bbox = raw.get("opening_bbox_px")
            identity = [source_fingerprint, physical_page, level, tag, bbox, raw.get("host_wall_id", "")]
            opening_id = "opening_" + _hash(identity)[:16]
            issues = []
            if physical_page not in pages:
                issues.append("source page is missing")
            if not tag:
                issues.append("opening tag is missing")
            if not level:
                issues.append("level identity is missing")
            if not raw.get("owner_room_id") or not raw.get("owner_zone_id"):
                issues.append("unique room and zone owner are missing")
            if not raw.get("host_wall_id"):
                issues.append("host wall is missing")
            if facade not in FACADES:
                issues.append("reviewed façade orientation is missing")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) for value in bbox
            ):
                issues.append("opening position is missing")
            for key in ("width_m", "height_m", "quantity"):
                value = raw.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                    issues.append(f"positive {key} is missing")
            if raw.get("competing_matches"):
                issues.append("competing opening matches require review")
            refs = []
            for key in ("elevation_refs", "section_refs", "schedule_refs"):
                for ref in raw.get(key, []) or []:
                    if isinstance(ref, dict):
                        refs.append({"kind": key, **deepcopy(ref)})
                        if ref.get("page") not in pages:
                            issues.append(f"{key} references an unknown page")
                        for field in ("tag", "level_name", "facade", "owner_room_id"):
                            if ref.get(field) and raw.get(field) and str(ref[field]).strip().casefold() != str(raw[field]).strip().casefold():
                                issues.append(f"{key} {field} conflicts with the plan opening")
                        for field in ("width_m", "height_m"):
                            if isinstance(ref.get(field), (int, float)) and isinstance(raw.get(field), (int, float)):
                                if abs(ref[field] - raw[field]) > 0.005:
                                    issues.append(f"{key} {field} conflicts with the plan opening")
            if not refs:
                issues.append("independent opening drawing or schedule link is missing")
            if not raw.get("source_crop") and not raw.get("citations"):
                issues.append("source crop or citation is missing")
            proposed_properties = raw.get("window_properties") if isinstance(raw.get("window_properties"), dict) else {}
            property_status = "cited_proposal" if proposed_properties.get("source") and proposed_properties.get("citations") else "not_cited"
            records.append({
                "opening_id": opening_id, "ai_opening_id": raw.get("opening_id", ""), "tag": tag,
                "page": physical_page, "drawing_number": raw.get("drawing_number", page.get("drawing_number", "")),
                "level_name": raw.get("level_name", ""), "owner_room_id": raw.get("owner_room_id", ""),
                "owner_zone_id": raw.get("owner_zone_id", ""), "host_wall_id": raw.get("host_wall_id", ""),
                "facade": facade, "opening_bbox_px": deepcopy(bbox),
                "width_m": raw.get("width_m"), "height_m": raw.get("height_m"), "quantity": raw.get("quantity"),
                "source_crop": raw.get("source_crop", ""), "source_excerpt": raw.get("source_excerpt", ""),
                "citations": deepcopy(raw.get("citations", [])),
                "evidence_refs": refs, "confidence": raw.get("confidence", "low"),
                "competing_matches": deepcopy(raw.get("competing_matches", [])), "unresolved_fields": sorted(set(issues)),
                "status": "blocked" if issues else "ai_estimated",
                "proposed_window_properties": deepcopy(proposed_properties) if property_status == "cited_proposal" else {},
                "properties_status": "evidence_only_until_reviewed_window_record" if property_status == "cited_proposal" else "missing_cited_window_properties",
            })
    by_tag_level = {}
    for row in records:
        by_tag_level.setdefault((row["tag"], row["level_name"].casefold()), []).append(row)
    for group in by_tag_level.values():
        if len(group) > 1:
            for row in group:
                row["status"] = "conflict"
                row["unresolved_fields"].append("repeated tag on level requires an exact plan/elevation/schedule match")
                row["competing_opening_ids"] = sorted(other["opening_id"] for other in group if other is not row)
    scan = window_scan or {}
    if scan.get("source_fingerprint") and scan.get("pages_inspected"):
        known = set(known_pages)
        if set(scan["pages_inspected"]) == known:
            # Scan matches are a review queue, not confirmed room-owned opening
            # evidence. Never promote them to the glazing eligibility path.
            reviews = window_reviews or {}
            for proposed in scan.get("openings", []):
                row = deepcopy(proposed)
                review = (reviews.get("openings") or {}).get(row.get("opening_id"), {})
                if (reviews.get("source_fingerprint") == scan["source_fingerprint"]
                        and review.get("scan_fingerprint") == scan.get("fingerprint")):
                    for key in ("owner_room_id", "owner_zone_id", "host_wall_id", "level_name", "facade",
                                "width_m", "height_m", "quantity", "source", "citations",
                                "review_type", "system_name", "member_sighting_ids", "includes_glazed_doors",
                                "external_exposure", "opening_coverage", "explicit_glass_area_m2",
                                "glass_area_basis", "applicability_note"):
                        row[key] = deepcopy(review.get(key))
                    row["review_mode"] = review.get("review_mode", "preliminary_ai_estimate")
                    row["reviewed_at"] = review.get("reviewed_at", "")
                    # A reviewed system can establish room ownership before its
                    # true-north orientation is available.  Solar eligibility
                    # remains separately gated by site_orientation.json.
                    row["status"] = "reviewed" if (row["review_mode"] == "engineering_reviewed"
                                                       and (row.get("facade") or row.get("review_type") == "glazing_system")) else "ai_estimated"
                    row["unresolved_fields"] = ["cited U-value and solar property required"]
                    if not row.get("facade"):
                        row["unresolved_fields"].append("façade orientation remains unresolved")
                    if row.get("external_exposure") not in {"external", "internal"}:
                        row["unresolved_fields"].append("external versus internal exposure requires review")
                    coverage = row.get("opening_coverage") or {}
                    if coverage.get("status") != "complete":
                        row["unresolved_fields"].append("complete host-wall opening coverage is required before gross-wall subtraction")
                    row["review_fingerprint"] = reviews.get("fingerprint", "")
                records.append(row)
    records.sort(key=lambda row: row["opening_id"])
    result = {"schema_version": 1, "source_fingerprint": source_fingerprint,
              "window_scan_fingerprint": scan.get("fingerprint", ""),
              "window_reviews_fingerprint": (window_reviews or {}).get("fingerprint", ""), "openings": records}
    result["fingerprint"] = _hash(result)
    return result
