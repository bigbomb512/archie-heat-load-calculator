"""Explicit NSW address lookup and cited site-orientation persistence."""

import base64
import binascii
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlencode
import urllib.request

from ai.site_orientation import empty_site_orientation, fingerprint, validate_site_orientation
from ai.vision_extraction import file_hash, timestamp
from backend.vision_extraction_service import _atomic_json

ADDRESS_LAYER = "https://portal.spatial.nsw.gov.au/server/rest/services/Hosted/NSW_Address_Point_Formatted/FeatureServer/1/query"
IMAGERY_SERVICE = "https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Imagery_Dates/MapServer/0"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _path(project):
    return Path(project["review_dir"]) / "site_orientation.json"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else empty_site_orientation()


def nsw_address_candidates(address):
    """Query only the fixed NSW government endpoint after explicit consent."""
    if not isinstance(address, str) or not 5 <= len(address.strip()) <= 200:
        raise ValueError("Confirm a complete NSW site address before lookup.")
    if any(character in address for character in ("%", "_", "\\")):
        raise ValueError("Address lookup does not accept wildcard characters.")
    escaped = address.strip().replace("'", "''")
    query = urlencode({"f": "json", "where": f"formattedaddress LIKE '%{escaped}%' AND stateterritory='NSW'",
                       "outFields": "formattedaddress,complexunitidentifier,complexlevelnumber,objectid,portal_last_update",
                       "outSR": "4326", "returnGeometry": "true", "resultRecordCount": "10"})
    request = urllib.request.Request(ADDRESS_LAYER + "?" + query, headers={"User-Agent": "ArchieSiteOrientation/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read(2_000_000).decode("utf-8"))
    if data.get("error"):
        raise ValueError("NSW address service rejected the lookup; upload a cited site map or survey instead.")
    result = []
    for feature in data.get("features", [])[:10]:
        attributes, geometry = feature.get("attributes", {}), feature.get("geometry", {})
        longitude, latitude = geometry.get("x"), geometry.get("y")
        if type(longitude) not in (int, float) or type(latitude) not in (int, float):
            continue
        result.append({"formatted_address": attributes.get("formattedaddress", ""),
                       "unit": attributes.get("complexunitidentifier", ""), "level": attributes.get("complexlevelnumber", ""),
                       "object_id": attributes.get("objectid"), "longitude_deg": longitude, "latitude_deg": latitude,
                       "dataset_updated_at": attributes.get("portal_last_update")})
    return {"queried_address": address.strip(), "source_url": ADDRESS_LAYER,
            "imagery_service_url": IMAGERY_SERVICE, "retrieved_at": timestamp(),
            "candidates": result, "status": "candidate_only_no_tenancy_or_facade_alignment",
            "fingerprint": fingerprint(result)}


def nsw_imagery_candidates(longitude, latitude):
    """Find dated aerial-image footprints at one explicitly selected address point."""
    if (type(longitude) not in (int, float) or type(latitude) not in (int, float)
            or not math.isfinite(longitude) or not math.isfinite(latitude)
            or not 140 <= longitude <= 155 or not -38 <= latitude <= -28):
        raise ValueError("Selected address point must have plausible NSW coordinates.")
    query = urlencode({"f": "json", "geometry": f"{longitude},{latitude}",
                       "geometryType": "esriGeometryPoint", "inSR": "4326",
                       "spatialRel": "esriSpatialRelIntersects", "outFields": "OBJECTID,BlockName,BlockType,BlockStartDate,Resolution_cm",
                       "returnGeometry": "false", "resultRecordCount": "10"})
    request = urllib.request.Request(IMAGERY_SERVICE + "/query?" + query,
                                     headers={"User-Agent": "ArchieSiteOrientation/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read(2_000_000).decode("utf-8"))
    if data.get("error"):
        raise ValueError("NSW imagery-date service rejected this point; upload a cited map or survey instead.")
    results = []
    for feature in data.get("features", [])[:10]:
        attributes = feature.get("attributes", {})
        date_value = attributes.get("BlockStartDate")
        image_date = ""
        if type(date_value) in (int, float) and math.isfinite(date_value):
            image_date = datetime.fromtimestamp(date_value / 1000, timezone.utc).date().isoformat()
        results.append({"object_id": attributes.get("OBJECTID"), "block_name": attributes.get("BlockName", ""),
                        "block_type": attributes.get("BlockType", ""), "imagery_date": image_date,
                        "resolution_cm": attributes.get("Resolution_cm")})
    return {"source_url": IMAGERY_SERVICE, "retrieved_at": timestamp(), "address_point": [longitude, latitude],
            "candidates": results, "status": "date_candidate_only_not_tenancy_or_facade_evidence",
            "fingerprint": fingerprint(results)}


def get(web, project):
    path = _path(project)
    artifact = validate_site_orientation(_read(path)) if path.exists() else empty_site_orientation()
    evidence = artifact.get("map_evidence") or {}
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}.get(evidence.get("mime_type"))
    source_file = path.parent / "site_orientation_sources" / (str(evidence.get("file_sha256", "")) + (suffix or ""))
    return {"id": project["id"], "site_orientation": artifact,
            "artifact_url": web.safe_link(path) if path.exists() else "",
            "map_evidence_url": web.safe_link(source_file) if suffix and source_file.is_file() else "",
            "status": "reviewed" if any(row.get("status") == "reviewed" for row in artifact.get("facades", [])) else artifact.get("status", "placeholder")}


def post(web, project, data):
    path = _path(project)
    action = data.get("action", "save")
    current = _read(path)
    if action == "lookup":
        if not data.get("confirm_address") or str(data.get("state", "")).upper() != "NSW":
            raise ValueError("Confirm the exact NSW address and state before sending it to Spatial Services.")
        lookup = nsw_address_candidates(data.get("site_address", ""))
        updated = {**current, "site_address": data["site_address"].strip(), "address_confirmed": True,
                   "state": "NSW", "address_lookup": lookup, "status": "proposed"}
    elif action == "lookup_imagery":
        if not data.get("confirm_imagery") or current.get("state") != "NSW" or not current.get("address_confirmed"):
            raise ValueError("Confirm the selected NSW address before querying its aerial-imagery date.")
        candidates = current.get("address_lookup", {}).get("candidates", [])
        selected = next((row for row in candidates if row.get("object_id") == data.get("address_object_id")), None)
        if not selected:
            raise ValueError("Select a candidate from the current NSW address lookup.")
        lookup = nsw_imagery_candidates(selected["longitude_deg"], selected["latitude_deg"])
        updated = {**current, "selected_address_object_id": selected["object_id"],
                   "imagery_lookup": lookup, "status": "proposed"}
    elif action == "upload_map":
        mime = data.get("mime_type")
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}.get(mime)
        if not suffix:
            raise ValueError("Site evidence upload must be PNG, JPEG or PDF.")
        if not data.get("source") or not data.get("citation"):
            raise ValueError("Map or survey upload requires a source and citation.")
        try:
            contents = base64.b64decode(data.get("base64", ""), validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Site evidence upload is not valid base64.") from error
        if not contents or len(contents) > MAX_UPLOAD_BYTES or not (
            contents.startswith(b"%PDF-") if suffix == ".pdf" else
            contents.startswith(b"\x89PNG\r\n\x1a\n") if suffix == ".png" else
            contents.startswith(b"\xff\xd8\xff")
        ):
            raise ValueError("Site evidence has invalid contents or exceeds 20 MB.")
        digest = hashlib.sha256(contents).hexdigest()
        source_path = path.parent / "site_orientation_sources" / (digest + suffix)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        if not source_path.exists(): source_path.write_bytes(contents)
        evidence = {"file_sha256": file_hash(source_path), "mime_type": mime, "source": data["source"],
                    "citation": data["citation"], "uploaded_at": timestamp(), "imagery_date": data.get("imagery_date", "")}
        updated = {**current, "map_evidence": evidence, "status": "proposed"}
    elif action == "save":
        updated = deepcopy(data.get("site_orientation", {}))
        if current.get("map_evidence", {}).get("file_sha256") and not updated.get("map_evidence"):
            updated["map_evidence"] = current["map_evidence"]
    else:
        raise ValueError("Site orientation action must be lookup, lookup_imagery, upload_map or save.")
    validated = validate_site_orientation(updated)
    _atomic_json(path, validated)
    return get(web, project)
