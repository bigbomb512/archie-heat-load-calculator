"""Project-local, draft-only Australian site-location resolution.

PDF evidence can propose a site address and north clues.  This module keeps
those proposals separate from confirmed geocoding, weather selection and the
existing reviewed façade-orientation workflow.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


SCHEMA_VERSION = 1
COUNTRY = "AU"
STATUSES = {
    "inferred", "awaiting_address_confirmation", "location_resolved",
    "orientation_proposed", "needs_review", "blocked",
}
STATE_TIMEZONES = {
    "ACT": "Australia/Sydney", "NSW": "Australia/Sydney", "NT": "Australia/Darwin",
    "QLD": "Australia/Brisbane", "SA": "Australia/Adelaide", "TAS": "Australia/Hobart",
    "VIC": "Australia/Melbourne", "WA": "Australia/Perth",
}
STATE_PATTERN = r"(?:NSW|VIC|QLD|SA|WA|TAS|ACT|NT)"
# The street type must be a whole word and the state an upper-case
# abbreviation after a separator; otherwise "PLAN ... current" reads as a
# "PL" street in the "NT".
STREET_PATTERN = re.compile(
    rf"\b(?:[A-Z]{{1,2}}\d{{1,4}}[A-Z]?/)?\d{{1,5}}(?:[A-Z]?[-/]\d{{1,5}})?\s+[A-Z0-9][A-Z0-9 .'-]{{1,70}}?\s"
    rf"(?:STREET|ST|ROAD|RD|AVENUE|AVE|AV|DRIVE|DR|PLACE|PL|PARADE|PDE|HIGHWAY|HWY|LANE|LN|COURT|CT|"
    rf"BOULEVARD|BLVD|CRESCENT|CRES|TERRACE|TCE|CIRCUIT|CCT|WAY)\b"
    rf"(?:[\s,]+[A-Z][A-Z .'-]{{1,45}}?)?[\s,]+(?-i:{STATE_PATTERN})(?:\s+\d{{4}})?\b",
    re.I,
)
# A tenancy line names the centre or development the tenancy is in, e.g.
# "TENANCY MZ01,M38, MELROSE CENTRAL".
TENANCY_PATTERN = re.compile(
    r"\b(?:TENANCY|SHOP|SUITE)\s+([A-Z0-9][A-Z0-9 ,/&.-]{0,30}?),\s*"
    r"([A-Z][A-Z'&. -]{1,60}?\b(?:CENTRAL|CENTRE|CENTER|PLAZA|MALL|SQUARE|VILLAGE|MARKETPLACE|QUARTER|EXCHANGE|ARCADE))\b",
    re.I,
)
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\(0\d\)\s?\d{4}\s?\d{4}|0\d{3}\s?\d{3}\s?\d{3}|0\d\s?\d{4}\s?\d{4}|1[38]00\s?\d{3}\s?\d{3})(?!\d)")
# Standard sheet notes that mention "site" or "construction" without saying
# anything about where the project is.
BOILERPLATE_PATTERN = re.compile(
    r"(?:checked|verified|confirmed)\s+on\s+site|for\s+construction|construction\s+issue|"
    r"do\s+not\s+scale|site\s*-\s*do\s+not\s+scale|prior\s+to\s+construction|construction\s+and\s+fit-?out",
    re.I,
)
SITE_NAME_REJECT = re.compile(r"^\W*\d|do not scale|drawing|dwg|scale|rev(?:ision)?\s+(?:no|date)|sheet|description|\babn\b|requirements", re.I)
COORDINATE_PATTERN = re.compile(r"(?<!\d)(-?\d{1,2}\.\d{3,})\s*[,/]\s*(-?\d{2,3}\.\d{3,})(?!\d)")
SURVEY_PATTERN = re.compile(r"\b(?:TRUE\s+NORTH|GRID\s+NORTH|NORTH\s+ARROW|SURVEY\s+BEARING|BEARING)\b", re.I)
DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)?\d{2}\b")
BAD_ADDRESS_CONTEXT = ("architect", "consultant", "office", "copyright", "email", "abn", "phone", "tel")
GOOD_ADDRESS_CONTEXT = ("project", "site", "development", "construction", "location", "address")


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _clean_for_fingerprint(value):
    if isinstance(value, dict):
        # Keys added later are left out while empty so older artifacts keep
        # their fingerprint (design weather is bound to it).
        return {key: _clean_for_fingerprint(item) for key, item in value.items()
                if key not in {"updated_at", "retrieved_at"}
                and not (key in {"cited_location", "excluded_address_candidates"} and not item)}
    if isinstance(value, list):
        return [_clean_for_fingerprint(item) for item in value]
    return value


def _text(value):
    return str(value or "").strip()


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def empty_site_location_resolution():
    result = {
        "schema_version": SCHEMA_VERSION,
        "country": COUNTRY,
        "status": "inferred",
        "pdf_context": {
            "source_fingerprint": "", "address_candidates": [], "site_name_candidates": [],
            "coordinate_candidates": [], "orientation_clues": [], "excluded_address_candidates": [],
        },
        "confirmed_address": "",
        "external_lookup_consent": False,
        "geocode": {"provider": "G-NAF", "status": "not_requested", "candidates": [], "selected_candidate_id": ""},
        "location": {"latitude_deg": None, "longitude_deg": None, "state": "", "locality": "", "timezone": "", "elevation": {}},
        "weather_candidates": [],
        "selected_weather_source": {},
        "map_or_survey_evidence": {},
        "cited_location": {},
        "conflicts": [],
        "updated_at": "",
    }
    result["fingerprint"] = fingerprint(_clean_for_fingerprint(result))
    return result


def _page_rows(ai_input, spatial_ocr):
    ocr = {}
    for page in (spatial_ocr or {}).get("pages", []):
        texts = [item.get("text_excerpt", "") for item in page.get("title_blocks", [])]
        texts.extend(item.get("text", "") for item in page.get("word_samples", []) if item.get("text"))
        ocr[page.get("page")] = "\n".join(texts)
    drawing_pages = (ai_input or {}).get("drawing_set", {}).get("pages", [])
    if not drawing_pages:
        confirmed = (ai_input or {}).get("confirmed_pages", {})
        drawing_pages = confirmed.get("floor_plans", []) + confirmed.get("reference_pages", [])
    for page in drawing_pages:
        number = page.get("page")
        text = _text(page.get("structured_content", {}).get("markdown")) + "\n" + ocr.get(number, "")
        yield {
            "page": number, "drawing_number": _text(page.get("drawing_number")),
            "title": _text(page.get("title")), "text": text,
        }


def _source(page, excerpt):
    return {"page": page.get("page"), "drawing_number": page.get("drawing_number", ""), "excerpt": excerpt[:500]}


def _address_context(text, start, end, title):
    raw = text[max(0, start - 140):min(len(text), end + 140)] + " " + title
    return BOILERPLATE_PATTERN.sub(" ", raw).casefold(), raw


def _consultant_signals(raw):
    return bool(EMAIL_PATTERN.search(raw)) or bool(PHONE_PATTERN.search(raw))


def _address_score(text, start, end, title):
    context, raw = _address_context(text, start, end, title)
    score = 0.55 + 0.12 * sum(term in context for term in GOOD_ADDRESS_CONTEXT)
    score -= 0.20 * sum(term in context for term in BAD_ADDRESS_CONTEXT)
    score -= 0.20 if _consultant_signals(raw) else 0.0
    return max(0.0, min(1.0, round(score, 3)))


def _unrelated_address_context(text, start, end, title):
    context, raw = _address_context(text, start, end, title)
    bad = any(term in context for term in BAD_ADDRESS_CONTEXT) or _consultant_signals(raw)
    return bad and not any(term in context for term in GOOD_ADDRESS_CONTEXT)


def _candidate_id(kind, value, source):
    return "site-" + fingerprint({"kind": kind, "value": value.casefold(), "source": source})[:20]


def infer_pdf_context(ai_input, spatial_ocr=None, building_evidence=None):
    """Extract bounded, source-linked site clues without accepting them."""
    addresses, names, coordinates, clues, excluded = [], [], [], [], []
    for page in _page_rows(ai_input, spatial_ocr or {}):
        text = page["text"]
        for match in STREET_PATTERN.finditer(text):
            value = re.sub(r"\s+", " ", match.group(0)).strip(" ,.;")
            if DATE_PATTERN.search(value):
                continue
            if _unrelated_address_context(text, match.start(), match.end(), page["title"]):
                excluded.append({"address": value, "source": _source(page, value),
                                 "reason": "Next to consultant contact details (phone, email or office); likely a consultant's address, not the site."})
                continue
            source = _source(page, value)
            addresses.append({
                "candidate_id": _candidate_id("address", value, source), "address": value,
                "confidence": _address_score(text, match.start(), match.end(), page["title"]), "source": source,
                "status": "proposed",
            })
        for match in COORDINATE_PATTERN.finditer(text):
            latitude, longitude = _number(match.group(1)), _number(match.group(2))
            if latitude is None or longitude is None or not (-44 <= latitude <= -9 and 110 <= longitude <= 155):
                continue
            excerpt = match.group(0)
            source = _source(page, excerpt)
            coordinates.append({
                "candidate_id": _candidate_id("coordinate", excerpt, source), "latitude_deg": latitude,
                "longitude_deg": longitude, "confidence": 0.6, "source": source, "status": "proposed",
            })
        for match in SURVEY_PATTERN.finditer(text):
            excerpt = text[max(0, match.start() - 80):match.end() + 140].strip()
            source = _source(page, excerpt)
            clues.append({"candidate_id": _candidate_id("orientation", excerpt, source), "kind": match.group(0).casefold(),
                          "source": source, "status": "proposed"})
        for match in re.finditer(r"\b(?:PROJECT|SITE|DEVELOPMENT)\s*(?:NAME|TITLE)?(?:\s*:\s*|\s+[-\u2013]\s+)([^\n\r]{3,90})", text, re.I):
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .:-")
            if value and not DATE_PATTERN.search(value) and not SITE_NAME_REJECT.search(value):
                source = _source(page, match.group(0))
                names.append({"candidate_id": _candidate_id("site-name", value, source), "site_name": value,
                              "confidence": 0.65, "source": source, "status": "proposed"})
        for match in TENANCY_PATTERN.finditer(text):
            value = re.sub(r"\s+", " ", match.group(2)).strip(" .,-").title()
            tenancy = re.sub(r"\s+", " ", match.group(1)).strip(" ,")
            source = _source(page, re.sub(r"\s+", " ", match.group(0)))
            names.append({"candidate_id": _candidate_id("site-name", value, source), "site_name": value,
                          "tenancy": tenancy, "confidence": 0.75, "source": source, "status": "proposed",
                          "basis": "tenancy_line"})
    def dedupe(rows, key):
        selected = {}
        for row in rows:
            current = selected.get(row[key].casefold() if isinstance(row[key], str) else str(row[key]))
            if current is None or row.get("confidence", 0) > current.get("confidence", 0):
                selected[row[key].casefold() if isinstance(row[key], str) else str(row[key])] = row
        return sorted(selected.values(), key=lambda row: (-row.get("confidence", 0), row["candidate_id"]))
    return {
        "source_fingerprint": fingerprint({"ai_input": ai_input or {}, "spatial_ocr": spatial_ocr or {}, "building": building_evidence or {}}),
        "address_candidates": dedupe(addresses, "address"), "site_name_candidates": dedupe(names, "site_name"),
        "coordinate_candidates": dedupe(coordinates, "candidate_id"), "orientation_clues": sorted(clues, key=lambda row: row["candidate_id"]),
        "excluded_address_candidates": [row for key, row in {row["address"].casefold(): row for row in excluded}.items()
                                        if key not in {item["address"].casefold() for item in addresses}],
    }


def _validate_candidate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Geocode candidate must be an object.")
    latitude, longitude = _number(raw.get("latitude_deg")), _number(raw.get("longitude_deg"))
    if latitude is None or longitude is None or not (-44 <= latitude <= -9 and 110 <= longitude <= 155):
        raise ValueError("Geocode candidate needs plausible Australian latitude and longitude.")
    state = _text(raw.get("state")).upper()
    if state not in STATE_TIMEZONES:
        raise ValueError("Geocode candidate needs a supported Australian state or territory.")
    formatted = _text(raw.get("formatted_address"))
    if not formatted:
        raise ValueError("Geocode candidate needs a formatted address.")
    return {
        "candidate_id": _text(raw.get("candidate_id")) or "gnaf-" + fingerprint({"address": formatted, "latitude": latitude, "longitude": longitude})[:18],
        "formatted_address": formatted, "latitude_deg": latitude, "longitude_deg": longitude,
        "state": state, "locality": _text(raw.get("locality")), "confidence": max(0.0, min(1.0, _number(raw.get("confidence")) or 0.5)),
        "provider_record_id": _text(raw.get("provider_record_id")), "release_version": _text(raw.get("release_version")),
        "source_url": _text(raw.get("source_url")), "retrieved_at": _text(raw.get("retrieved_at")) or now(),
        "content_hash": _text(raw.get("content_hash")),
    }


def _validate_elevation(raw):
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Elevation must be an object.")
    elevation = _number(raw.get("elevation_m"))
    if elevation is None or not -1000 <= elevation <= 10000:
        raise ValueError("Elevation must be a plausible finite metres value.")
    if not _text(raw.get("source_url")) or not _text(raw.get("content_hash")):
        raise ValueError("Elevation requires a cited source URL and content hash.")
    return {"elevation_m": elevation, "source_url": _text(raw["source_url"]), "dataset_version": _text(raw.get("dataset_version")),
            "retrieved_at": _text(raw.get("retrieved_at")) or now(), "content_hash": _text(raw["content_hash"]), "status": "provisional"}


def _status(result):
    if result.get("conflicts"):
        return "needs_review"
    if not result.get("confirmed_address"):
        return "awaiting_address_confirmation" if result.get("pdf_context", {}).get("address_candidates") else "blocked"
    selected = result.get("geocode", {}).get("selected_candidate_id") or result.get("cited_location")
    if selected and result.get("location", {}).get("timezone"):
        return "orientation_proposed" if result.get("map_or_survey_evidence") else "location_resolved"
    return "needs_review" if result.get("geocode", {}).get("candidates") else "inferred"


def validate_site_location_resolution(raw):
    source = deepcopy(raw or empty_site_location_resolution())
    if not isinstance(source, dict) or source.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("Unsupported site-location-resolution schema.")
    result = empty_site_location_resolution()
    result.update({key: deepcopy(value) for key, value in source.items() if key in result})
    if _text(result.get("country", COUNTRY)).upper() != COUNTRY:
        raise ValueError("Only Australian site locations are supported in this release.")
    context = result.get("pdf_context", {})
    if not isinstance(context, dict):
        raise ValueError("PDF context must be an object.")
    for key in ("address_candidates", "site_name_candidates", "coordinate_candidates", "orientation_clues", "excluded_address_candidates"):
        if not isinstance(context.get(key, []), list):
            raise ValueError(f"PDF context {key} must be a list.")
    confirmed = _text(result.get("confirmed_address"))
    if confirmed and not 5 <= len(confirmed) <= 200:
        raise ValueError("Confirmed address must contain 5 to 200 characters.")
    geocode = result.get("geocode", {})
    if not isinstance(geocode, dict):
        raise ValueError("Geocode must be an object.")
    candidates = [_validate_candidate(item) for item in geocode.get("candidates", [])]
    if len({item["candidate_id"] for item in candidates}) != len(candidates):
        raise ValueError("Geocode candidate IDs must be unique.")
    selected_id = _text(geocode.get("selected_candidate_id"))
    selected = next((item for item in candidates if item["candidate_id"] == selected_id), None)
    if selected_id and selected is None:
        raise ValueError("Selected geocode candidate is not in the current response.")
    location = result.get("location", {})
    if not isinstance(location, dict):
        raise ValueError("Location must be an object.")
    cited = _validate_cited_location(result.get("cited_location")) if result.get("cited_location") else {}
    if selected:
        location = {"latitude_deg": selected["latitude_deg"], "longitude_deg": selected["longitude_deg"], "state": selected["state"],
                    "locality": selected["locality"], "timezone": STATE_TIMEZONES[selected["state"]], "elevation": _validate_elevation(location.get("elevation"))}
    elif cited:
        location = {"latitude_deg": cited["latitude_deg"], "longitude_deg": cited["longitude_deg"], "state": cited["state"],
                    "locality": cited["locality"], "timezone": STATE_TIMEZONES[cited["state"]], "elevation": {},
                    "basis": "reviewer_cited_map"}
    else:
        location = {"latitude_deg": None, "longitude_deg": None, "state": "", "locality": "", "timezone": "", "elevation": {}}
    weather = result.get("weather_candidates", [])
    if not isinstance(weather, list):
        raise ValueError("Weather candidates must be a list.")
    selected_weather = deepcopy(result.get("selected_weather_source", {}))
    if selected_weather:
        weather_ids = {_text(item.get("source_id")) for item in weather if isinstance(item, dict)}
        if _text(selected_weather.get("source_id")) not in weather_ids:
            raise ValueError("Selected weather source is not a current eligible candidate.")
    evidence = result.get("map_or_survey_evidence", {})
    if evidence and (not isinstance(evidence, dict) or not _text(evidence.get("source")) or not _text(evidence.get("citation"))):
        raise ValueError("Map or survey evidence needs a source and citation.")
    result["pdf_context"] = context
    result["confirmed_address"] = confirmed
    result["external_lookup_consent"] = bool(result.get("external_lookup_consent", False))
    result["geocode"] = {"provider": "G-NAF", "status": _text(geocode.get("status")) or "not_requested", "candidates": candidates,
                         "selected_candidate_id": selected_id, "source_url": _text(geocode.get("source_url")),
                         "retrieved_at": _text(geocode.get("retrieved_at")), "content_hash": _text(geocode.get("content_hash"))}
    result["location"] = location
    result["weather_candidates"] = [deepcopy(item) for item in weather if isinstance(item, dict)]
    result["selected_weather_source"] = selected_weather
    result["map_or_survey_evidence"] = evidence
    result["cited_location"] = cited
    result["conflicts"] = [str(item)[:500] for item in result.get("conflicts", []) if _text(item)]
    result["status"] = _status(result)
    result["updated_at"] = _text(result.get("updated_at")) or now()
    result["fingerprint"] = fingerprint(_clean_for_fingerprint(result))
    return result


def with_pdf_context(current, context):
    result = deepcopy(current or empty_site_location_resolution())
    result["pdf_context"] = deepcopy(context)
    result["updated_at"] = now()
    return validate_site_location_resolution(result)


def confirm_address(current, address, consent):
    if not consent:
        raise ValueError("Confirm the exact project address and approve the server-side location lookup first.")
    result = deepcopy(current or empty_site_location_resolution())
    result.update({"confirmed_address": _text(address), "external_lookup_consent": True})
    result["geocode"] = {"provider": "G-NAF", "status": "awaiting_lookup", "candidates": [], "selected_candidate_id": ""}
    result["location"] = empty_site_location_resolution()["location"]
    result["cited_location"] = {}
    result["weather_candidates"], result["selected_weather_source"] = [], {}
    result["updated_at"] = now()
    return validate_site_location_resolution(result)


def _confirmed_place_parts(address):
    state_match = re.search(r"\b(NSW|VIC|QLD|SA|WA|TAS|ACT|NT)\b", _text(address), re.I)
    state = state_match.group(1).upper() if state_match else ""
    before_state = _text(address[:state_match.start()]) if state_match else ""
    locality = before_state.rsplit(",", 1)[-1].strip(" ,.-") if "," in before_state else ""
    return state, locality.casefold()


def apply_geocode_candidates(current, candidates, source_url, content_hash, selected_candidate_id="", elevation=None, weather_candidates=None):
    result = deepcopy(current)
    if not result.get("confirmed_address") or not result.get("external_lookup_consent"):
        raise ValueError("Confirm the project address and consent before resolving its location.")
    checked = [_validate_candidate({**item, "source_url": item.get("source_url", source_url)}) for item in candidates]
    expected_state, expected_locality = _confirmed_place_parts(result["confirmed_address"])
    if expected_state:
        checked = [item for item in checked if item["state"] == expected_state]
    if expected_locality:
        matched_locality = [item for item in checked if item["locality"].casefold() == expected_locality]
        if matched_locality:
            checked = matched_locality
    if not checked:
        raise ValueError("No G-NAF result matches the confirmed address state/locality. Check the address or use cited map/survey evidence.")
    selected = _text(selected_candidate_id)
    if not selected and len(checked) == 1:
        selected = checked[0]["candidate_id"]
    result["geocode"] = {"provider": "G-NAF", "status": "resolved" if selected else "candidate_selection_required", "candidates": checked,
                         "selected_candidate_id": selected, "source_url": _text(source_url), "retrieved_at": now(), "content_hash": _text(content_hash)}
    if selected:
        row = next(item for item in checked if item["candidate_id"] == selected)
        result["location"] = {"latitude_deg": row["latitude_deg"], "longitude_deg": row["longitude_deg"], "state": row["state"],
                              "locality": row["locality"], "timezone": STATE_TIMEZONES[row["state"]], "elevation": _validate_elevation(elevation)}
        result["weather_candidates"] = deepcopy(weather_candidates or [])
    result["updated_at"] = now()
    return validate_site_location_resolution(result)


def select_weather_source(current, source_id):
    result = deepcopy(current)
    selected = next((item for item in result.get("weather_candidates", []) if _text(item.get("source_id")) == _text(source_id)), None)
    if not selected:
        raise ValueError("Select a current compatible BOM/released-weather candidate.")
    result["selected_weather_source"] = deepcopy(selected)
    result["updated_at"] = now()
    return validate_site_location_resolution(result)


def _validate_cited_location(raw):
    """A reviewer-cited site position, used when no geocoder is configured.

    The reviewer reads the position from a map or survey they can cite; the
    tool never looks it up, so no external lookup consent is involved.
    """
    if not isinstance(raw, dict):
        raise ValueError("Cited location must be an object.")
    latitude, longitude = _number(raw.get("latitude_deg")), _number(raw.get("longitude_deg"))
    if latitude is None or longitude is None or not (-44 <= latitude <= -9 and 110 <= longitude <= 155):
        raise ValueError("Enter the site latitude and longitude in decimal degrees (Australia: latitude -44 to -9, longitude 110 to 155).")
    state = _text(raw.get("state")).upper()
    if state not in STATE_TIMEZONES:
        raise ValueError("Choose the site's Australian state or territory.")
    locality = _text(raw.get("locality"))
    if not locality:
        raise ValueError("Enter the site suburb or locality.")
    source, citation, reviewer = _text(raw.get("source")), _text(raw.get("citation")), _text(raw.get("reviewer"))
    if not source or not citation:
        raise ValueError("Cite where the position was read from (map or survey, and a link or reference).")
    if not reviewer:
        raise ValueError("Enter a reviewer name for the cited site location.")
    return {"latitude_deg": round(latitude, 6), "longitude_deg": round(longitude, 6), "state": state, "locality": locality,
            "source": source[:200], "citation": citation[:500], "reviewer": reviewer[:120],
            "declared_at": _text(raw.get("declared_at")) or now(), "status": "provisional"}


def set_cited_location(current, address, cited, weather_candidates=None):
    """Confirm the site from a reviewer-cited map position instead of G-NAF."""
    if not 5 <= len(_text(address)) <= 200:
        raise ValueError("Enter the project site (name, suburb and state) you are confirming, 5 to 200 characters.")
    checked = _validate_cited_location(cited)
    result = deepcopy(current or empty_site_location_resolution())
    result.update({"confirmed_address": _text(address), "external_lookup_consent": False, "cited_location": checked})
    result["geocode"] = {"provider": "G-NAF", "status": "not_requested", "candidates": [], "selected_candidate_id": ""}
    result["weather_candidates"], result["selected_weather_source"] = deepcopy(weather_candidates or []), {}
    result["updated_at"] = now()
    return validate_site_location_resolution(result)


def accept_map_or_survey(current, evidence):
    result = deepcopy(current)
    if not isinstance(evidence, dict) or not _text(evidence.get("source")) or not _text(evidence.get("citation")):
        raise ValueError("Map or survey evidence needs a source and citation.")
    result["map_or_survey_evidence"] = {
        "source": _text(evidence["source"]), "citation": _text(evidence["citation"]),
        "reference": _text(evidence.get("reference")), "true_north_basis": _text(evidence.get("true_north_basis")),
    }
    result["updated_at"] = now()
    return validate_site_location_resolution(result)
