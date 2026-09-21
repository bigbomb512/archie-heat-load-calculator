"""All-page, evidence-only window discovery and cross-view matching.

Provider output is intentionally never an approval of geometry, exposure or
glazing properties.  The normalized register is a review queue.
"""

from copy import deepcopy
import hashlib
import json
import math

POLICY_VERSION = "all-page-windows-v1"
BATCH_SIZE = 8
VIEW_TYPES = {"plan", "elevation", "section", "schedule", "render", "photo", "detail", "other"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def page_batches(ai_input):
    """Include every physical page, irrespective of ranking or page role."""
    pages = ai_input.get("drawing_set", {}).get("pages", [])
    numbers = [row.get("page") for row in pages]
    if not numbers or any(type(number) is not int or number < 1 for number in numbers) or len(set(numbers)) != len(numbers):
        raise ValueError("The complete PDF page register must contain unique positive physical page numbers.")
    ordered = sorted(pages, key=lambda row: row["page"])
    return [{"batch_id": f"windows-{index // BATCH_SIZE + 1:03d}",
             "pages": ordered[index:index + BATCH_SIZE]}
            for index in range(0, len(ordered), BATCH_SIZE)]


def _bbox(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 4 or any(
        type(value) not in (int, float) or not math.isfinite(value) for value in raw
    ) or raw[2] <= raw[0] or raw[3] <= raw[1]:
        raise ValueError("A window sighting needs a finite, ordered [left, top, right, bottom] crop box.")
    return [round(float(value), 3) for value in raw]


def normalize_sightings(raw, pages, source_fingerprint, provider, model, prompt_fingerprint):
    """Validate a single batch; IDs do not depend on provider array order."""
    if not isinstance(raw, dict) or not isinstance(raw.get("sightings"), list):
        raise ValueError("Window scan must return a sightings array.")
    known = {row["page"]: row for row in pages}
    seen = set()
    output = []
    for item in raw["sightings"]:
        if not isinstance(item, dict) or item.get("page") not in known:
            raise ValueError("A window sighting references a page outside its immutable batch.")
        page = item["page"]
        bbox = _bbox(item.get("bbox"))
        view = str(item.get("view_type", "other")).strip().lower()
        if view not in VIEW_TYPES:
            raise ValueError("Window sighting has an unsupported view type.")
        appearance = str(item.get("appearance_status", "unknown")).strip().lower()
        if appearance not in {"proposed_design", "existing_condition", "unknown"}:
            raise ValueError("Window sighting must distinguish proposed design from existing condition or mark it unknown.")
        tag = str(item.get("tag", "") or "").strip().upper()
        level = str(item.get("level_name", "") or "").strip()
        sighting_id = "sighting_" + fingerprint([source_fingerprint, page, bbox, view, tag, level])[:18]
        if sighting_id in seen:
            raise ValueError("Duplicate window sighting in one batch.")
        seen.add(sighting_id)
        output.append({
            "sighting_id": sighting_id, "page": page,
            "drawing_number": str(known[page].get("drawing_number", "") or ""),
            "bbox": bbox, "view_type": view, "appearance_status": appearance,
            "tag": tag, "level_name": level,
            "landmarks": [str(value)[:160] for value in item.get("landmarks", []) if isinstance(value, str)][:12],
            "geometry_clues": str(item.get("geometry_clues", "") or "")[:500],
            "facade_clue": str(item.get("facade_clue", "") or "")[:160],
            "room_clue": str(item.get("room_clue", "") or "")[:160],
            "host_wall_clue": str(item.get("host_wall_clue", "") or "")[:160],
            "revision": str(item.get("revision", "") or "")[:80],
            "source_excerpt": str(item.get("source_excerpt", "") or "")[:500],
            "confidence": item.get("confidence") if type(item.get("confidence")) in (int, float) and math.isfinite(item["confidence"]) and 0 <= item["confidence"] <= 1 else None,
            "provider": provider, "model": model, "prompt_fingerprint": prompt_fingerprint,
            "source_fingerprint": source_fingerprint,
        })
    return sorted(output, key=lambda row: row["sighting_id"])


def resolve_clusters(sightings, proposed_clusters, source_fingerprint):
    """Reject unsafe associations; retain all sightings, including unmatched ones."""
    by_id = {row["sighting_id"]: row for row in sightings}
    if len(by_id) != len(sightings) or not isinstance(proposed_clusters, list):
        raise ValueError("Window matching needs unique sightings and a clusters array.")
    claimed = set()
    openings = []
    for raw in proposed_clusters:
        if not isinstance(raw, dict) or not isinstance(raw.get("member_sighting_ids"), list):
            raise ValueError("Window cluster must list its sighting IDs.")
        ids = raw["member_sighting_ids"]
        if not ids or len(ids) != len(set(ids)) or any(value not in by_id for value in ids):
            raise ValueError("Window cluster references missing or repeated sightings.")
        if claimed.intersection(ids):
            raise ValueError("A window sighting cannot belong to two opening clusters.")
        claimed.update(ids)
        members = [by_id[value] for value in sorted(ids)]
        issues = []
        levels = {row["level_name"].casefold() for row in members if row["level_name"]}
        tags = {row["tag"] for row in members if row["tag"]}
        revisions = {row["revision"].casefold() for row in members if row["revision"]}
        appearances = {row["appearance_status"] for row in members}
        if len(levels) > 1: issues.append("conflicting levels")
        if len(tags) > 1: issues.append("conflicting opening tags")
        if len(revisions) > 1: issues.append("competing drawing revisions")
        if "proposed_design" in appearances and "existing_condition" in appearances:
            issues.append("proposed design and existing-condition image need review")
        if raw.get("mirrored_view") or raw.get("competing_matches"):
            issues.append("mirrored or competing view match needs review")
        if len(members) < 2: issues.append("no independent cross-view sighting")
        rationale = str(raw.get("match_reason", "") or "").strip()
        if len(members) > 1 and not rationale:
            issues.append("cross-view match reason is missing")
        opening_id = "opening_" + fingerprint([source_fingerprint, sorted(ids)])[:16]
        openings.append({
            "opening_id": opening_id, "source": "all_page_window_scan", "sighting_ids": sorted(ids),
            "sightings": deepcopy(members), "tag": next(iter(tags)) if len(tags) == 1 else "",
            "level_name": next(iter(levels)) if len(levels) == 1 else "",
            "page": min(row["page"] for row in members), "match_reason": rationale,
            "competing_matches": deepcopy(raw.get("competing_matches", [])),
            "owner_room_id": "", "owner_zone_id": "", "host_wall_id": "", "facade": "",
            "unresolved_fields": issues + ["confirm exact room, host wall, geometry and exterior exposure", "cited U-value and solar property required"],
            "status": "conflict" if issues else "proposed", "properties_status": "missing_cited_window_properties",
        })
    for row in sightings:
        if row["sighting_id"] in claimed:
            continue
        opening_id = "opening_" + fingerprint([source_fingerprint, row["sighting_id"]])[:16]
        openings.append({"opening_id": opening_id, "source": "all_page_window_scan", "sighting_ids": [row["sighting_id"]],
                         "sightings": [deepcopy(row)], "tag": row["tag"], "level_name": row["level_name"],
                         "page": row["page"], "owner_room_id": "", "owner_zone_id": "", "host_wall_id": "", "facade": "",
                         "unresolved_fields": ["unmatched sighting", "confirm exact room, host wall, geometry and exterior exposure", "cited U-value and solar property required"],
                         "status": "proposed", "properties_status": "missing_cited_window_properties"})
    # A repeated tag is never enough to assert one canonical opening.
    by_tag = {}
    for row in openings:
        if row["tag"]:
            by_tag.setdefault((row["tag"], row["level_name"].casefold()), []).append(row)
    for group in by_tag.values():
        if len(group) > 1:
            for row in group:
                row["status"] = "conflict"
                row["unresolved_fields"].append("repeated tag on this level requires an exact plan/elevation match")
                row["competing_opening_ids"] = sorted(other["opening_id"] for other in group if other is not row)
    openings.sort(key=lambda row: row["opening_id"])
    register = {"schema_version": 1, "policy_version": POLICY_VERSION,
                "source_fingerprint": source_fingerprint, "pages_scanned": sorted({row["page"] for row in sightings}),
                "sightings": sorted(deepcopy(sightings), key=lambda row: row["sighting_id"]), "openings": openings}
    register["fingerprint"] = fingerprint(register)
    return register
