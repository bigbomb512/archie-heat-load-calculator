"""Draft-only, evidence-preserving room-use classification for AI preliminary models."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import math
from statistics import median


ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "config" / "au_room_use_taxonomy_v1.json"
PACK_ID = "au-room-use-v1"
STATUSES = {"resolved", "needs_review", "excluded", "stale"}
SCOPES = {"comfort_hvac", "comfort_hvac_with_process_exception", "refrigeration_process", "unresolved_scope", "not_a_room", "not_cooled"}
# Scopes that never enter comfort-HVAC totals. "not_a_room" is a reviewer
# decision that a detected label is not a real space at all.
EXCLUDED_SCOPES = {"refrigeration_process", "unresolved_scope", "not_a_room", "not_cooled"}
PROFILES = {"retail", "office", "hospitality", "storage", "residential", "generic_conditioned_room", "house_bedroom", "house_living", "house_wet", "house_service", "garage", "outdoor", "lift_shaft", "circulation"}
HOUSE_USES = {"house_bedroom", "house_living", "house_wet", "house_service", "garage", "outdoor", "lift_shaft", "circulation"}


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    def normalise(item):
        if isinstance(item, dict):
            return {key: normalise(value) for key, value in sorted(item.items()) if key not in {"updated_at", "created_at"}}
        if isinstance(item, list):
            return [normalise(value) for value in item]
        return item
    return hashlib.sha256(json.dumps(normalise(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _text(value):
    return str(value or "").strip()


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", _text(value).casefold()).strip("-") or "unassigned"


UNKNOWN_LEVELS = {"", "unassigned", "unassigned-level", "unknown", "unknown-level", "none", "n-a"}


def room_identity(label, level):
    # One unknown level: "" (a level-less text reading) and "Unassigned level" are the same floor, or one room becomes
    # two (house set 15, 2026-11-03: Dining, Kitchen and Living twice, the copies with no area).
    level_slug = _slug(level)
    return f"room-use:{'unassigned-level' if level_slug in UNKNOWN_LEVELS else level_slug}:{_slug(label)}"


def _confidence(value, fallback=0.4):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return {"high": 0.85, "medium": 0.65, "low": 0.4}.get(_text(value).casefold(), fallback)


def confidence_band(value):
    return "high" if value >= 0.8 else "medium" if value >= 0.5 else "low"


def load_taxonomy():
    try:
        raw = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The Australia-first room-use taxonomy pack is unavailable.") from error
    if raw.get("schema_version") != 1 or raw.get("pack_id") != PACK_ID or raw.get("country") != "AU" or raw.get("status") != "released":
        raise ValueError("The Australia-first room-use taxonomy pack is invalid.")
    categories = raw.get("categories")
    if not isinstance(categories, dict) or "generic_conditioned" not in categories:
        raise ValueError("The Australia-first room-use taxonomy pack is incomplete.")
    for category_id, row in categories.items():
        if not isinstance(row, dict) or not _text(row.get("label")) or not isinstance(row.get("aliases"), list):
            raise ValueError(f"Room-use taxonomy category '{category_id}' is invalid.")
        if row.get("space_scope") not in SCOPES or row.get("profile_id", "") not in PROFILES | {""}:
            raise ValueError(f"Room-use taxonomy category '{category_id}' has an invalid profile or scope.")
    return raw


def empty_room_use_resolution():
    result = {"schema_version": 1, "taxonomy_pack_id": PACK_ID, "taxonomy_fingerprint": "", "records": [],
              "source_fingerprints": {}, "status": "needs_review", "updated_at": ""}
    result["fingerprint"] = fingerprint(result)
    return result


def _taxonomy_fingerprint(taxonomy, dwelling):
    if dwelling:
        return fingerprint(taxonomy)
    legacy = deepcopy(taxonomy)
    legacy["categories"] = {key: value for key, value in legacy["categories"].items() if key not in HOUSE_USES}
    return fingerprint(legacy)


def _evidence(row):
    evidence = row.get("evidence", []) if isinstance(row, dict) else []
    result = [deepcopy(item) for item in evidence if isinstance(item, dict) and item.get("page")]
    if not result and isinstance(row, dict) and row.get("page"):
        result.append({"page": row.get("page"), "drawing_number": _text(row.get("drawing_number")), "excerpt": _text(row.get("excerpt", row.get("label")))})
    return result


def _room_rows(building, vision, proposal):
    vision_entities = (((vision or {}).get("result", {}) or {}).get("auto_extraction", {}) or {}).get("entities", []) if isinstance(vision, dict) else []
    ai_rooms = [row for row in list(vision_entities) + list((proposal or {}).get("rooms", []))
                if isinstance(row, dict) and row.get("kind") == "room"]
    grouped = {}
    for row in (building or {}).get("spaces", []) if isinstance(building, dict) else []:
        if not isinstance(row, dict) or not _text(row.get("name")):
            continue
        label, level = _text(row.get("name")), _text(row.get("level_name")) or "Unassigned level"
        grouped.setdefault(room_identity(label, level), {"label": label, "level_name": level, "building": row, "ai": []})["ai"].extend(
            item for item in ai_rooms if _text(item.get("label")).casefold() == label.casefold() and
            (not _text(item.get("level_name")) or _text(item.get("level_name")).casefold() == level.casefold())
        )
    for row in ai_rooms:
        label, level = _text(row.get("label")), _text(row.get("level_name")) or "Unassigned level"
        if label:
            grouped.setdefault(room_identity(label, level), {"label": label, "level_name": level, "building": {}, "ai": []})["ai"].append(row)
    return [grouped[key] for key in sorted(grouped)]


def _label_matches(label, taxonomy):
    text = _text(label).casefold()
    matches = []
    for category_id, category in taxonomy["categories"].items():
        if category_id in HOUSE_USES:
            continue                    # a dwelling's rooms are matched by _house_label_matches, never a shop's
        # Use word boundaries so a short alias such as "bar" does not classify
        # unrelated labels such as "barrier".  The original PDF label remains
        # the evidence; this is only controlled-taxonomy matching.
        if any(re.search(r"(?<![a-z0-9])" + re.escape(alias.casefold()) + r"(?![a-z0-9])", text)
               for alias in category.get("aliases", []) if alias):
            matches.append(category_id)
    # Specific retail/front-of-house phrases take precedence over the broad
    # "service" alias used for unresolved plant/service rooms.
    if re.search(r"(?<![a-z0-9])(?:service\s+counter|counter|kiosk)(?![a-z0-9])", text):
        if "kitchen" in matches:
            return ["kitchen"]
        return ["retail"] if "retail" in taxonomy["categories"] else sorted(set(matches))
    return sorted(set(matches))


def _ai_candidate(rows, taxonomy):
    candidates = []
    reverse_profiles = {}
    for category_id, category in taxonomy["categories"].items():
        if category.get("profile_id"):
            reverse_profiles.setdefault(category["profile_id"], []).append(category_id)
    for row in rows:
        category_id = _text(row.get("room_use_category"))
        if not category_id:
            profile = _text(row.get("preliminary_profile_id"))
            mapped = reverse_profiles.get(profile, [])
            category_id = mapped[0] if len(mapped) == 1 else ""
        if category_id in taxonomy["categories"]:
            candidates.append({"category_id": category_id, "confidence": _confidence(row.get("confidence")), "row": row,
                               "rationale": _text(row.get("room_use_rationale", row.get("rationale"))) or "AI-selected controlled room-use category.",
                               "alternatives": [value for value in row.get("room_use_alternatives", []) if value in taxonomy["categories"]]})
    candidates.sort(key=lambda item: (-item["confidence"], item["category_id"]))
    return candidates[0] if candidates else None, candidates




def plan_text_translation(source_words, target_words):
    """Conservative same-scale translation, checked by four unique dimension anchors in two axes (PDF points)."""
    def unique(words):
        grouped = {}
        for word in words:
            text = word.get("text", "")
            if text.isdigit() and 100 <= int(text) <= 20000:
                grouped.setdefault(text, []).append(word)
        return {key: rows[0] for key, rows in grouped.items() if len(rows) == 1}
    source, target = unique(source_words), unique(target_words)
    pairs = []
    for key in sorted(source.keys() & target.keys()):
        a, b = source[key]["bbox"], target[key]["bbox"]
        ax, ay, bx, by = (a[0]+a[2])/2, (a[1]+a[3])/2, (b[0]+b[2])/2, (b[1]+b[3])/2
        pairs.append({"text": key, "dx": bx-ax, "dy": by-ay, "x": ax, "y": ay})
    clusters = [[other for other in pairs if math.hypot(other["dx"]-row["dx"], other["dy"]-row["dy"]) <= 2] for row in pairs]
    ranked = sorted(clusters, key=lambda rows: (-len(rows), json.dumps(rows, sort_keys=True)))
    if not ranked or len(ranked[0]) < 4:
        return None
    best = ranked[0]
    if max(row["x"] for row in best)-min(row["x"] for row in best) < 100 or max(row["y"] for row in best)-min(row["y"] for row in best) < 20:
        return None
    dx, dy = median(row["dx"] for row in best), median(row["dy"] for row in best)
    if any(len(cluster) >= len(best) - 1 and len(cluster) >= 4
           and math.hypot(median(row["dx"] for row in cluster)-dx, median(row["dy"] for row in cluster)-dy) > 2
           for cluster in ranked[1:]):
        return None
    return {"dx_pt": dx, "dy_pt": dy, "anchors": [row["text"] for row in best],
            "max_residual_pt": max(math.hypot(row["dx"]-dx, row["dy"]-dy) for row in best)}

def score_use_evidence(label, selected, context, dwelling=False):
    """Rank use clues, never equipment heat. Family caps prevent repeated schedule rows winning by volume."""
    from ai.equipment_heat import equipment_type, normalise
    scores, factors = {}, []
    def add(category, weight, kind, reason, evidence=None):
        scores[category] = scores.get(category, 0) + weight
        factors.append({"category": category, "weight": weight, "factor": kind, "reason": reason,
                        "evidence": deepcopy(evidence or [])})
    add(selected, 3, "label", f'Existing label/use selection for "{label}": {selected}.')
    if dwelling:
        factors.append({"factor": "tenancy", "weight": 0, "reason": "Residential drawing context; dwelling room-use categories apply.",
                        "evidence": deepcopy(context.get("dwelling_evidence", []))})
        if selected in HOUSE_USES:
            return {"taxonomy_id": selected, "confidence": .9, "rationale": f"House room label retained as {selected.replace('_', ' ')} in the evidenced dwelling context.",
                    "factors": factors, "scores": {selected: 3}, "label_disagrees": False,
                    "alternatives": [], "decisive": True}
    if context.get("tenancy"):
        factors.append({"factor": "tenancy", "weight": 0, "reason": f'Tenancy context: {context["tenancy"]}.',
                        "evidence": context.get("tenancy_evidence", [])})
    front = "dining" if context.get("tenancy") == "hospitality" else "retail" if context.get("tenancy") == "retail" else None
    groups = {}
    for item in context.get("contents", []):
        name = normalise(item.get("name"))
        if item.get("source") == "equipment_location_label":
            factors.append({"factor": "placement_uncertain", "weight": 0,
                "reason": f'{item.get("name")}: placement follows a room name, not a verified outline.',
                "evidence": item.get("evidence", [])})
            continue
        kind = equipment_type(name)
        group = ("front" if re.search(r"\b(pos|eftpos|menu|display|buffet|hot food|food station|seating|seat|table|chair)\b", name)
                 and not re.search(r"\b(prep|preparation|pizza prep|sandwich prep)\b", name) else
                 "bar" if re.search(r"\b(beer|drink|bottle|backbar|ice maker|ice machine|glasswasher|glass washer)\b", name) else
                 "cooking" if kind == "cooking" or name in {"hot line", "cooking line", "pizza"} else "washing" if kind == "dishwashing" or name == "dishwash" else
                 "prep" if name == "prep" or kind == "refrigeration" and re.search(r"\b(prep|underbench|freezer)\b", name) else
                 "hood" if kind == "exhaust_hood" else "counter" if "counter" in name else
                 "sanitary" if re.search(r"\b(wc|toilet|urinal)\b", name) else
                 "basin" if re.search(r"\b(basin|handwash|hand wash)\b", name) else
                 "office" if re.search(r"\b(desk|workstation|office computer)\b", name) else "")
        if group:
            groups.setdefault(group, {})[name] = item
    for group, category, weight in (("cooking", "kitchen", 8), ("washing", "kitchen", 5),
            ("prep", "kitchen", 4), ("hood", "kitchen", 4), ("sanitary", "ancillary_conditioned", 12),
            ("basin", "kitchen" if groups.get("cooking") else "ancillary_conditioned", 4), ("office", "office", 8)):
        items = groups.get(group, {})
        if items:
            # Cooking plus another independent content family can confidently outweigh a wrong label.
            add(category, weight, "contents", ", ".join(sorted(items)),
                [ref for item in [items[name] for name in sorted(items)] for ref in item.get("evidence", [])])
    if groups.get("front"):
        if front:
            add(front, 9, "contents", ", ".join(sorted(groups["front"])),
                [ref for item in [groups["front"][name] for name in sorted(groups["front"])] for ref in item.get("evidence", [])])
        else:
            factors.append({"factor": "tenancy", "weight": 0, "reason": "Front-of-house contents found; tenancy type is not established."})
    if groups.get("bar") and (groups.get("counter") or context.get("counter") or any("backbar" in name for name in groups["bar"])):
        add("dining", 10, "contents", "Drink refrigeration / ice equipment with a counter supports a bar.")
    if context.get("storefront") and front:
        add(front, 5, "position", "Glazing or a confirmed shopfront wall faces outside or the mall.", context.get("position_evidence"))
    if context.get("largest_front_room") and front:
        add(front, 3, "size_shape", "Largest open traced space at the shopfront.", context.get("position_evidence"))
    if context.get("access_through_room") and not context.get("outside_wall"):
        add("kitchen" if context.get("tenancy") == "hospitality" else "storage", 2, "position", "Internal room accessed through another room.")
    area = context.get("area_m2")
    if isinstance(area, (int, float)) and 0 < area < 6 and context.get("enclosed"):
        add("ancillary_conditioned", 2, "size_shape", "Small enclosed room under 6 m²; dining is unlikely.")
    ranked = sorted(scores, key=lambda key: (-scores[key], key))
    winner = ranked[0]
    margin = scores[winner] - max((score for category, score in scores.items() if category != winner), default=0)
    decisive = scores[winner] >= 8 and margin >= 4
    if winner == "kitchen" and winner != selected:
        kitchen_groups = [group for group in ("cooking", "washing", "prep", "hood", "basin") if groups.get(group)]
        decisive = decisive and (len(kitchen_groups) >= 2 or len(groups.get("cooking", {})) >= 2)
    chosen = winner if decisive else selected
    confidence = .85 if decisive else .6 if any(factor.get("weight", 0) > 0 and factor["factor"] != "label" for factor in factors) else .4
    reasons = [item["reason"] for item in factors if item.get("category") == chosen and item["factor"] != "label"]
    shown_use = chosen.replace("_", " ")
    rationale = (f'Named {label} on the drawing, but {"; ".join(reasons)}: used as {shown_use}.' if chosen != selected else
                 f'Use kept as {shown_use}. ' + ("; ".join(reasons) if decisive else
                 "Available factors do not clearly establish a different use; the label is retained at reduced confidence."))
    return {"taxonomy_id": chosen, "confidence": confidence, "rationale": rationale,
            "factors": factors, "scores": dict(sorted(scores.items())), "label_disagrees": chosen != selected,
            "alternatives": [key for key in ranked if key != chosen], "decisive": decisive}

def _house_label_matches(label):
    text = re.sub(r"\s+", " ", _text(label).casefold()).strip()
    # Combined open-plan labels are a single house living area.
    if re.search(r"\b(living\s*/\s*dining|kitchen\s*/\s*dining|living\s+dining)\b", text):
        return ["house_living"]
    # This scan's compound label denotes a bar/dining zone; "powder" is part
    # of the name rather than a sanitary-room instruction.
    if re.search(r"\bpowder\s+bar\b", text):
        return ["house_living"]
    if re.search(r"\b(master bedroom|main bedroom|bedroom)\b", text):
        return ["house_bedroom"]
    if re.match(r"^(?:dining|kitchen)\b.*\bbed(?:\s*\d+)?\b", text):
        return ["house_living"]
    patterns = {
        "house_bedroom": r"\b(master bedroom|main bedroom|mbr|bed(?:room)?(?:\s*\d+)?|br\s*\d*)\b",
        "house_wet": r"\b(bath(?:room)?|ens(?:uite)?|powder|pdr|wc|toilet)\b",
        "house_service": r"\b(laundry|l['’]?dry|ldry|w\.?i\.?r\.?|walk.?in robe|robe|linen)\b",
        "garage": r"\b(garage|carport)\b",
        "outdoor": r"\b(balcony|alfresco|deck|terrace|patio|pool|porch|verandah?|courtyard|roof)\b",
        "lift_shaft": r"\b(lift(?: shaft)?)\b",
        "circulation": r"\b(entry|hall(?:way)?|stairs?|staircase|lobby|corridor|passage)\b",
        "house_living": r"\b(living|lounge|family|rumpus|media|cinema|theatre|games|study|office|gym|entertainment|kitchen|kitch|dining|meals|pantry|wip|walk.?in pantry|scullery|bar)\b",
    }
    priority = ("house_wet", "house_service", "garage", "outdoor", "lift_shaft", "house_bedroom", "house_living", "circulation")
    leading = {
        "house_wet": r"^(bath(?:room)?|ens(?:uite)?|powder|pdr|wc|toilet)\b",
        "house_service": r"^(laundry|l['’]?dry|ldry|w\.?i\.?r\.?|walk.?in robe|robe|linen)\b",
        "garage": r"^(garage|carport)\b", "outdoor": r"^(balcony|alfresco|deck|terrace|patio|pool|porch|verandah?|courtyard)\b",
        "lift_shaft": r"^lift\b", "house_bedroom": r"^(mbr|bed(?:room)?(?:\s*\d+)?|br\s*\d*)\b",
        "house_living": r"^(living|lounge|family|rumpus|media|cinema|theatre|games|study|office|gym|entertainment|kitchen|kitch|dining|meals|pantry|wip|walk.?in pantry|scullery|bar)\b",
        "circulation": r"^(entry|hall(?:way)?|stairs?|staircase|lobby|corridor|passage)\b",
    }
    for category in priority:
        if re.search(leading[category], text):
            return [category]
    return [category for category in priority if re.search(patterns[category], text)][:1]


def _record(room, taxonomy, source_fingerprints, existing=None, use_evidence=None, dwelling=False):
    label, level = room["label"], room["level_name"]
    room_id = room_identity(label, level)
    skill_context = next((row for row in reversed(room.get("ai", [])) if isinstance(row, dict) and (
        "direct_label_verified" in row or "calculation_scope_override" in row or "non_counting_functional_subarea" in row
    )), {})
    # Runtime room-identity skills may establish that a supplied legacy label
    # is not actually printed in the drawing (for example, a source-system
    # placeholder). In that case it is context, not direct label evidence, and
    # must not outrank the cited visual interpretation.
    direct = [] if skill_context.get("direct_label_verified") is False else (_house_label_matches(label) if dwelling else _label_matches(label, taxonomy))
    if not dwelling:
        direct = [item for item in direct if item not in HOUSE_USES]
    ai, ai_candidates = _ai_candidate(room["ai"], taxonomy)
    override = deepcopy((existing or {}).get("override")) if isinstance(existing, dict) else None
    alternatives, conflicts = [], []
    if override and override.get("taxonomy_id") not in taxonomy["categories"]:
        override = None
    if override:
        selected, origin, score, rationale = override["taxonomy_id"], "contractor_override", 1.0, _text(override.get("note")) or "Contractor room-use override."
    elif len(direct) == 1:
        selected, origin, score, rationale = direct[0], "direct_project_evidence", 0.9, "Explicit room label matches the controlled taxonomy."
    elif len(direct) > 1:
        selected, origin, score, rationale = "generic_conditioned", "conflict", 0.35, "The room label matches competing controlled room-use categories."
        alternatives = direct
        conflicts.append("Competing explicit room-label classifications.")
    elif ai:
        selected, origin, score, rationale = ai["category_id"], "ai_interpretation", ai["confidence"], ai["rationale"]
        alternatives = ai["alternatives"]
    else:
        selected, origin, score, rationale = "generic_conditioned", "generic_fallback", 0.3, "No specific room-use evidence was available; a controlled generic conditioned-room profile is used for draft coverage."
    assessment = None
    reviewed = next((row for row in reversed(room.get("ai", [])) if row.get("room_use_reviewed") is True), None)
    protected = taxonomy["categories"][selected]["space_scope"] in EXCLUDED_SCOPES or skill_context.get("calculation_scope_override") in EXCLUDED_SCOPES
    if not override and not protected and (existing or {}).get("space_scope") != "refrigeration_process" and reviewed and reviewed.get("room_use_category") in taxonomy["categories"]:
        selected, origin, score = reviewed["room_use_category"], "reviewer_decision", 1.0
        rationale = reviewed.get("room_use_rationale") or "Reviewer-selected room use."
    elif not override and not protected and (existing or {}).get("space_scope") == "refrigeration_process":
        selected, origin, score = (existing or {})["taxonomy_id"], "preserved_refrigeration_scope", .9
        rationale = "Existing refrigeration scope is preserved; contents scoring cannot change it."
    elif not override and not protected and use_evidence is not None:
        assessment = score_use_evidence(label, selected, use_evidence, dwelling=dwelling)
        selected, score, rationale = assessment["taxonomy_id"], assessment["confidence"], assessment["rationale"]
        origin = "multi_factor_evidence"
        alternatives = assessment["alternatives"]
    category = taxonomy["categories"][selected]
    scope, profile = category["space_scope"], category.get("profile_id", "")
    ac_evidence = (use_evidence or {}).get("air_conditioning_evidence") or []
    cooled_by_drawings = dwelling and scope == "not_cooled" and selected != "outdoor" and bool(ac_evidence)
    if cooled_by_drawings:
        # Not cooled in a house unless the drawings show air-conditioning in it (user decision 2026-10-30).
        scope = "comfort_hvac"
        rationale = ("Normally not air-conditioned in a house, but the drawings show air-conditioning in it ("
                     + "; ".join(f"p{row.get('page')}: {row.get('excerpt')}" for row in ac_evidence) + "): cooled, with no occupants.")
        category = deepcopy(category)
        category["space_scope"] = scope
    if dwelling and re.search(r"\bvoid\b", label, re.I):
        scope = "not_cooled"
        rationale = "Void could not be associated with the room it opens into from current evidence; excluded as not cooled pending a geometry decision."
        category = deepcopy(category)
        category["space_scope"] = scope
    scope_override = skill_context.get("calculation_scope_override")
    if scope_override in SCOPES and not override:
        scope = scope_override
        category = deepcopy(category)
        category["space_scope"] = scope
    special = scope in EXCLUDED_SCOPES
    status = "excluded" if special else "needs_review" if origin in {"generic_fallback", "conflict"} or selected == "generic_conditioned" or (score < 0.5 and origin != "multi_factor_evidence") else "resolved"
    evidence = []
    for item in [room["building"], *(room["ai"] or [])]:
        for reference in _evidence(item):
            if reference not in evidence:
                evidence.append(reference)
    if assessment:
        for factor in assessment["factors"]:
            for reference in factor.get("evidence", []):
                if reference not in evidence:
                    evidence.append(reference)
    if cooled_by_drawings:
        evidence.extend({"page": row.get("page"), "excerpt": row.get("excerpt"), "method": "air_conditioning_text"}
                        for row in ac_evidence)
    geometry_reference = {"room_identity": room_id, "source_pages": sorted({item.get("page") for item in evidence if item.get("page")})}
    result = {
        "room_id": room_id, "component_id": room_id, "original_label": label, "level_name": level,
        "geometry_reference": geometry_reference, "taxonomy_id": selected, "taxonomy_label": category["label"],
        "preliminary_profile_id": profile, "space_scope": scope, "confidence_score": round(score, 4),
        "confidence_band": confidence_band(score), "classification_origin": origin, "rationale": rationale,
        "evidence": evidence, "direct_label_evidence": direct, "alternatives": sorted(set(alternatives)),
        "conflicts": conflicts, "ai_candidates": [{"taxonomy_id": item["category_id"], "confidence_score": item["confidence"],
                                                        "rationale": item["rationale"]} for item in ai_candidates],
        "override": override, "status": status, "remediation": (
            "Confirm the room use before relying on this generic preliminary profile." if status == "needs_review" else
            "Provide a refrigeration/process load method; this space is excluded from comfort-HVAC totals." if scope == "refrigeration_process" else
            "Confirm whether this plant, service, or unconditioned space belongs in comfort-HVAC scope." if scope == "unresolved_scope" else
            "A reviewer marked this detection as not a room; it is excluded from tracing, the calculator draft and preliminary totals." if scope == "not_a_room" else ""
        ),
        "source_fingerprints": deepcopy(source_fingerprints or {}),
    }
    if assessment:
        result["use_assessment"] = assessment
        result["review_recommended"] = not assessment["decisive"]
    if skill_context.get("non_counting_functional_subarea"):
        result["non_counting_functional_subarea"] = True
    result["record_fingerprint"] = fingerprint(result)
    return result


def resolve(building, vision=None, proposal=None, source_fingerprints=None, existing=None, use_evidence=None, dwelling_context=None):
    taxonomy = load_taxonomy()
    previous = {row.get("room_id"): row for row in (existing or {}).get("records", []) if isinstance(row, dict)}
    dwelling_context = deepcopy(dwelling_context or (existing or {}).get("dwelling_context") or {"is_dwelling": False, "evidence": []})
    evidence_by_room = deepcopy(use_evidence or {})
    if dwelling_context.get("is_dwelling") is True:
        for room_context in evidence_by_room.values():
            room_context["dwelling_evidence"] = deepcopy(dwelling_context.get("evidence", []))
    records = [_record(room, taxonomy, source_fingerprints or {}, previous.get(room_identity(room["label"], room["level_name"])),
                       evidence_by_room.get(room_identity(room["label"], room["level_name"])), dwelling_context.get("is_dwelling") is True)
               for room in _room_rows(building, vision or {}, proposal or {})]
    result = {"schema_version": 1, "taxonomy_pack_id": PACK_ID, "taxonomy_fingerprint": _taxonomy_fingerprint(taxonomy, dwelling_context.get("is_dwelling") is True),
              "records": records, "source_fingerprints": deepcopy(source_fingerprints or {}),
              "status": "needs_review" if any(row["status"] == "needs_review" for row in records) else "draft_ready" if records else "needs_review",
              "updated_at": now()}
    if dwelling_context.get("is_dwelling") is True:
        result["dwelling_context"] = dwelling_context
        for row in records:
            row["dwelling"] = True
            row["record_fingerprint"] = fingerprint({key: value for key, value in row.items() if key != "record_fingerprint"})
    result["fingerprint"] = fingerprint(result)
    return result


def validate(raw):
    raw = raw or empty_room_use_resolution()
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or raw.get("taxonomy_pack_id") != PACK_ID:
        raise ValueError("Unsupported room-use resolution artifact.")
    taxonomy = load_taxonomy()
    records = raw.get("records", [])
    if not isinstance(records, list):
        raise ValueError("Room-use resolution records must be a list.")
    ids = []
    is_dwelling = raw.get("dwelling_context", {}).get("is_dwelling") is True
    for row in records:
        if not isinstance(row, dict) or not _text(row.get("room_id")) or row.get("taxonomy_id") not in taxonomy["categories"]:
            raise ValueError("Room-use resolution record is invalid.")
        if row.get("space_scope") not in SCOPES or row.get("preliminary_profile_id", "") not in PROFILES | {""} or row.get("status") not in STATUSES - {"stale"}:
            raise ValueError("Room-use resolution record has an invalid profile, scope, or status.")
        if row.get("taxonomy_id") in HOUSE_USES and not is_dwelling:
            raise ValueError("House room-use categories require a persisted dwelling flag.")
        ids.append(row["room_id"])
    if len(ids) != len(set(ids)):
        raise ValueError("Room-use resolution room IDs must be unique.")
    result = deepcopy(raw)
    result["taxonomy_fingerprint"] = _taxonomy_fingerprint(taxonomy, result.get("dwelling_context", {}).get("is_dwelling") is True)
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def apply_override(artifact, room_id, taxonomy_id, reviewer, note):
    taxonomy = load_taxonomy()
    if taxonomy_id not in taxonomy["categories"] or not _text(reviewer):
        raise ValueError("Room-use override needs a valid taxonomy category and reviewer.")
    artifact = deepcopy(validate(artifact))
    for row in artifact["records"]:
        if row["room_id"] == room_id:
            row["override"] = {"taxonomy_id": taxonomy_id, "reviewer": _text(reviewer), "note": _text(note), "updated_at": now()}
            return resolve_from_existing(artifact)
    raise ValueError("Room-use override references an unknown room.")


def clear_override(artifact, room_id):
    artifact = deepcopy(validate(artifact))
    for row in artifact["records"]:
        if row["room_id"] == room_id:
            row["override"] = None
            return resolve_from_existing(artifact)
    raise ValueError("Room-use override references an unknown room.")


def resolve_from_existing(artifact):
    """Reapply an override without needing to re-run PDF/AI interpretation."""
    taxonomy = load_taxonomy()
    rows = []
    for prior in artifact["records"]:
        category_id = (prior.get("override") or {}).get("taxonomy_id", prior["taxonomy_id"])
        category = taxonomy["categories"][category_id]
        row = deepcopy(prior)
        if prior.get("override"):
            row.update({"taxonomy_id": category_id, "taxonomy_label": category["label"], "preliminary_profile_id": category.get("profile_id", ""),
                        "space_scope": category["space_scope"], "classification_origin": "contractor_override", "confidence_score": 1.0,
                        "confidence_band": "high", "rationale": _text(prior["override"].get("note")) or "Contractor room-use override."})
        row["status"] = "excluded" if row["space_scope"] in EXCLUDED_SCOPES else "resolved"
        row["record_fingerprint"] = fingerprint({key: value for key, value in row.items() if key != "record_fingerprint"})
        rows.append(row)
    result = {**artifact, "records": rows, "status": "needs_review" if any(row["status"] == "needs_review" for row in rows) else "draft_ready", "updated_at": now()}
    result["fingerprint"] = fingerprint({key: value for key, value in result.items() if key != "fingerprint"})
    return result


def is_current(artifact, source_fingerprints):
    return bool(artifact) and artifact.get("source_fingerprints") == (source_fingerprints or {}) and artifact.get("taxonomy_fingerprint") == _taxonomy_fingerprint(load_taxonomy(), artifact.get("dwelling_context", {}).get("is_dwelling") is True)


def effective_proposal(proposal, artifact):
    """Overlay controlled selections onto draft proposal rooms, never reviewed data."""
    output = deepcopy(proposal or {})
    output.setdefault("rooms", [])
    index = {row["room_id"]: row for row in artifact.get("records", []) if isinstance(row, dict)}
    for room in output["rooms"]:
        if not isinstance(room, dict):
            continue
        record = index.get(room_identity(room.get("label"), room.get("level_name", "Unassigned level")))
        if record:
            room["preliminary_profile_id"] = record.get("preliminary_profile_id") or "generic_conditioned_room"
            room["space_scope"] = record.get("space_scope", "unresolved_scope")
            room["room_use_category"] = record.get("taxonomy_id", "")
            room["room_use_rationale"] = record.get("rationale", "")
    return output
