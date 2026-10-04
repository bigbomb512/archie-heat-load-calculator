"""Server-side Australian site-location lookups and project-local persistence.

The configured G-NAF and elevation adapters are intentionally narrow: callers
can supply an address or coordinates, never an arbitrary endpoint or URL.
"""

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode, urlparse
import urllib.request

from ai.research_cache import eligible_bindings, empty_research_cache, source_pack_release_manifest, validate_cache
from ai.site_location_resolution import (
    accept_map_or_survey, apply_geocode_candidates, confirm_address, empty_site_location_resolution,
    fingerprint, infer_pdf_context, select_weather_source, set_cited_location, validate_site_location_resolution, with_pdf_context,
)
from ai.vision_extraction import timestamp
from backend.vision_extraction_service import _atomic_json


GNAF_ENDPOINT_ENV = "ARCHIE_GNAF_GEOCODER_URL"
GNAF_TOKEN_ENV = "ARCHIE_GNAF_GEOCODER_TOKEN"
ELEVATION_ENDPOINT_ENV = "ARCHIE_GA_ELEVATION_URL"
MAX_RESPONSE_BYTES = 2_000_000


def _path(project):
    return Path(project["review_dir"]) / "site_location_resolution.json"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else empty_site_location_resolution()


def _read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _configured_url(name, allowed_suffix):
    value = os.environ.get(name, "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or not (parsed.hostname == allowed_suffix or parsed.hostname.endswith("." + allowed_suffix)):
        raise ValueError(f"{name} must use an HTTPS {allowed_suffix} endpoint.")
    return value


def _fetch_json(url, params, token=""):
    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(url + separator + urlencode(params), headers={"User-Agent": "ArchieSiteLocation/1.0"})
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=15) as response:
        contents = response.read(MAX_RESPONSE_BYTES + 1)
    if len(contents) > MAX_RESPONSE_BYTES:
        raise ValueError("Location service response exceeds the permitted size.")
    try:
        return json.loads(contents.decode("utf-8")), hashlib.sha256(contents).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Location service returned invalid JSON.") from error


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _distance_km(latitude, longitude, scope):
    station_latitude = _number((scope or {}).get("latitude_deg", (scope or {}).get("station_latitude_deg")))
    station_longitude = _number((scope or {}).get("longitude_deg", (scope or {}).get("station_longitude_deg")))
    if station_latitude is None or station_longitude is None:
        return None
    radius_km = 6371.0088
    lat1, lon1, lat2, lon2 = map(math.radians, (latitude, longitude, station_latitude, station_longitude))
    return round(radius_km * 2 * math.asin(math.sqrt(math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)), 3)


def _candidate_from_row(row, source_url, release_version, content_hash):
    attributes = row.get("attributes", row) if isinstance(row, dict) else {}
    geometry = row.get("geometry", {}) if isinstance(row, dict) else {}
    latitude = attributes.get("latitude_deg", attributes.get("latitude", geometry.get("y")))
    longitude = attributes.get("longitude_deg", attributes.get("longitude", geometry.get("x")))
    return {
        "candidate_id": str(attributes.get("candidate_id", attributes.get("id", attributes.get("address_id", attributes.get("objectid", ""))))),
        "formatted_address": str(attributes.get("formatted_address", attributes.get("formattedaddress", attributes.get("address", "")))).strip(),
        "latitude_deg": _number(latitude), "longitude_deg": _number(longitude),
        "state": str(attributes.get("state", attributes.get("stateterritory", ""))).upper().strip(),
        "locality": str(attributes.get("locality", attributes.get("suburb", ""))).strip(),
        "confidence": _number(attributes.get("confidence", attributes.get("match_confidence", 0.5))) or 0.5,
        "provider_record_id": str(attributes.get("provider_record_id", attributes.get("address_id", attributes.get("objectid", "")))).strip(),
        "release_version": str(attributes.get("release_version", release_version)).strip(),
        "source_url": source_url, "retrieved_at": timestamp(), "content_hash": content_hash,
    }


def gnaf_candidates(address):
    """Call a configured G-NAF broker; no endpoint leaves server configuration."""
    endpoint = _configured_url(GNAF_ENDPOINT_ENV, "geoscape.com.au")
    if not endpoint:
        raise ValueError("G-NAF geocoding is not configured. Upload cited map/survey evidence or configure the server-side G-NAF adapter.")
    data, digest = _fetch_json(endpoint, {"address": address, "country": "AU"}, os.environ.get(GNAF_TOKEN_ENV, ""))
    rows = data.get("candidates", data.get("features", data.get("results", []))) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        raise ValueError("Configured G-NAF adapter did not return address candidates.")
    release = str(data.get("release_version", data.get("release", ""))) if isinstance(data, dict) else ""
    candidates = []
    for row in rows[:10]:
        candidate = _candidate_from_row(row, endpoint, release, digest)
        if candidate["formatted_address"] and candidate["latitude_deg"] is not None and candidate["longitude_deg"] is not None:
            candidates.append(candidate)
    return candidates, endpoint, digest


def geoscience_elevation(longitude, latitude):
    """Resolve elevation only from an explicitly configured GA adapter."""
    endpoint = _configured_url(ELEVATION_ENDPOINT_ENV, "ga.gov.au")
    if not endpoint:
        return {}, ""
    data, digest = _fetch_json(endpoint, {"longitude": longitude, "latitude": latitude})
    payload = data.get("result", data) if isinstance(data, dict) else {}
    elevation = _number(payload.get("elevation_m", payload.get("elevation"))) if isinstance(payload, dict) else None
    if elevation is None:
        return {}, endpoint
    return {
        "elevation_m": elevation, "source_url": endpoint,
        "dataset_version": str(payload.get("dataset_version", payload.get("version", ""))),
        "retrieved_at": timestamp(), "content_hash": digest,
    }, endpoint


def _weather_candidates(root, location):
    cache = validate_cache(_read_json(root / "research_cache.json", empty_research_cache()))
    scope = {"country": "AU", "scenario": "summer"}
    if location.get("state"): scope["state"] = location["state"]
    if location.get("locality"): scope["locality"] = location["locality"]
    rows = eligible_bindings(cache, "scenario.weather_profile", scope, release_manifest=source_pack_release_manifest())
    result = []
    for row in rows:
        distance = _distance_km(location["latitude_deg"], location["longitude_deg"], row.get("scope", {}))
        result.append({
            "source_id": row["record_id"], "record_id": row["record_id"], "target": "scenario.weather_profile",
            "value": deepcopy(row.get("value")), "unit": row.get("unit", "profile"), "scope": deepcopy(row.get("scope", {})),
            "publisher": row.get("publisher", "Bureau of Meteorology"), "citation": row.get("citation", ""),
            "source_reference": row.get("source_reference", row.get("url", "")), "content_hash": row.get("content_hash", ""),
            "expiry": row.get("expiry", ""), "source_pack_release": deepcopy(row.get("source_pack_release", {})),
            "distance_km": distance,
        })
    return sorted(result, key=lambda row: (row["distance_km"] is None, row["distance_km"] if row["distance_km"] is not None else float("inf"), row["source_id"]))


def _response(web, project):
    path = _path(project)
    artifact = validate_site_location_resolution(_read(path)) if path.exists() else empty_site_location_resolution()
    return {"id": project["id"], "site_location_resolution": artifact, "status": artifact["status"],
            "artifact_url": web.safe_link(path) if path.exists() else ""}


def get(web, project):
    return _response(web, project)


def _save(web, project, artifact, action):
    from backend import productization
    path = _path(project)
    before = _read(path)
    checked = validate_site_location_resolution(artifact)
    _atomic_json(path, checked)
    productization.record_change_if_fingerprint_changed(
        path.parent, action=action, target=path.name, previous_fingerprint=fingerprint(before),
        new_fingerprint=checked["fingerprint"], affected_ids=[project["id"]],
    )
    project["site_location_resolution"] = str(path)
    project["updated_at"] = timestamp()
    if (path.parent / "design_requirements.json").exists():
        from backend.au_ventilation_rules_service import refresh_requirements_for_context
        refresh_requirements_for_context(web, project)
    web.update_project(project)
    return _response(web, project)


def post(web, project, data):
    path = _path(project)
    current = _read(path)
    action = data.get("action", "")
    root = Path(project["review_dir"])
    if action == "infer_from_pdf":
        artifact = with_pdf_context(current, infer_pdf_context(
            _read_json(root / "ai_input.json", {}), _read_json(root / "spatial_ocr.json", {}),
            _read_json(root / "building_evidence.json", {}),
        ))
    elif action == "confirm_address":
        artifact = confirm_address(current, data.get("confirmed_address", ""), bool(data.get("confirm_address")))
    elif action == "resolve_location":
        if not current.get("confirmed_address") or not current.get("external_lookup_consent"):
            raise ValueError("Confirm the exact address and location-lookup consent before resolving it.")
        candidates, source_url, digest = gnaf_candidates(current["confirmed_address"])
        selected = str(data.get("geocode_candidate_id", ""))
        if selected:
            chosen = next((row for row in candidates if row.get("candidate_id") == selected), None)
            if not chosen:
                raise ValueError("Choose an address candidate returned by the current G-NAF lookup.")
            elevation, _unused = geoscience_elevation(chosen["longitude_deg"], chosen["latitude_deg"])
            provisional = apply_geocode_candidates(current, candidates, source_url, digest, selected, elevation)
            weather = _weather_candidates(root, provisional["location"])
            artifact = apply_geocode_candidates(current, candidates, source_url, digest, selected, elevation, weather)
        else:
            artifact = apply_geocode_candidates(current, candidates, source_url, digest)
            if len(candidates) == 1:
                elevation, _unused = geoscience_elevation(candidates[0]["longitude_deg"], candidates[0]["latitude_deg"])
                weather = _weather_candidates(root, artifact["location"])
                artifact = apply_geocode_candidates(current, candidates, source_url, digest, candidates[0]["candidate_id"], elevation, weather)
    elif action == "select_weather_source":
        artifact = select_weather_source(current, data.get("source_id", ""))
    elif action == "accept_map_or_survey":
        artifact = accept_map_or_survey(current, data.get("evidence", {}))
    elif action == "set_cited_location":
        cited = {key: data.get(key) for key in ("latitude_deg", "longitude_deg", "state", "locality", "source", "citation", "reviewer")}
        provisional = set_cited_location(current, data.get("confirmed_address", ""), cited)
        artifact = set_cited_location(current, data.get("confirmed_address", ""), cited,
                                      _weather_candidates(root, provisional["location"]))
    else:
        raise ValueError("Site-location action must be infer_from_pdf, confirm_address, resolve_location, select_weather_source, accept_map_or_survey, or set_cited_location.")
    return _save(web, project, artifact, "site_location_" + action)
