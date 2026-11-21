"""The indoor setpoint comparison (backend/setpoint_comparison.py) for the job's saved cooling result, worked out once
per saved result and kept beside it (setpoint_comparison.json), so the Results page and the contractor's report read
the same numbers without recalculating them on every view.

The variants are calculated from the input set the cooling result was calculated from (its recorded fingerprint),
never from a newer input set: comparing a newer set against an older baseline would show a change the setpoint did
not cause."""

import hashlib
import json
import threading
from pathlib import Path

from backend import ai_preliminary_service, setpoint_comparison

# Bump when the comparison method or the compact shape changes: a saved comparison of another version is redone.
VERSION = 1
CACHE_NAME = "setpoint_comparison.json"
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock(root):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(root), threading.Lock())


def _report_key(report):
    """What identifies a saved cooling result for this comparison: its input set, safety policy and baseline peak."""
    if not isinstance(report, dict) or not report:
        return {}
    peak = report.get("included_scope_peak") or {}
    identity = {"input_set": report.get("preliminary_input_set_fingerprint") or "",
                "safety_policy": report.get("safety_policy_fingerprint") or "",
                "equipment": report.get("equipment_source_hash") or "",
                "peak": {key: peak.get(key) for key in ("scenario_id", "hour", "final_design_total_kw", "design_total_kw",
                                                        "sensible_kw", "latent_kw")}}
    return {"input_set": identity["input_set"],
            "fingerprint": hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()}


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _room_peaks(scenario_results):
    """Each room's own largest design total over the variant's design days, with when it happens."""
    best = {}
    for scenario in scenario_results or []:
        for room in scenario.get("rooms") or [] if isinstance(scenario, dict) else []:
            peak = room.get("peak") if isinstance(room, dict) else None
            value = _number((peak or {}).get("design_total_kw"))
            room_id = str(room.get("room_id") or "") if isinstance(room, dict) else ""
            if not room_id or value is None:
                continue
            if room_id not in best or value > best[room_id]["kw"]:
                best[room_id] = {"kw": round(value, 4), "hour": peak.get("display_hour", peak.get("hour")),
                                 "month": scenario.get("representative_month") or scenario.get("title") or "",
                                 "name": str(room.get("name") or room_id)}
    return best


def compact(comparison, report):
    """The comparison as the Results page shows it: one row per setpoint with the change from the baseline, and each
    room's own peak at each setpoint. Nothing is estimated: a value the calculation didn't produce stays None."""
    if comparison.get("status") != "calculated":
        return {"status": "not_calculated",
                "reason": comparison.get("reason") or "The comparison could not be calculated."}
    variants = comparison.get("variants") or []
    baseline_peak = (variants[0].get("peak") or {}) if variants else {}
    baseline = _number(baseline_peak.get("final_design_total_kw", baseline_peak.get("design_total_kw")))
    rows, rooms = [], {}
    identities = report.get("room_identities") or {}
    for index, variant in enumerate(variants):
        setpoint = variant.get("setpoint_c")
        peak = variant.get("peak") or {}
        total = _number(peak.get("final_design_total_kw", peak.get("design_total_kw")))
        row = {"setpoint_c": setpoint, "baseline": index == 0, "status": variant.get("status"),
               "wet_bulb_c": _number(variant.get("wet_bulb_c")),
               "relative_humidity": _number(variant.get("relative_humidity"))}
        if variant.get("status") == "not_calculated" or total is None:
            row.update({"status": "not_calculated",
                        "reason": variant.get("reason") or "No peak was recorded for this setpoint."})
            rows.append(row)
            continue
        delta = None if baseline is None else total - baseline
        row.update({"total_kw": round(total, 4), "sensible_kw": _number(peak.get("sensible_kw")),
                    "latent_kw": _number(peak.get("latent_kw")), "month": peak.get("month") or "",
                    "hour": peak.get("display_hour", peak.get("hour")), "scenario_id": peak.get("scenario_id") or "",
                    "delta_kw": None if delta is None else round(delta, 4),
                    "delta_pct": None if delta is None or not baseline else round(delta / baseline * 100, 2)})
        rows.append(row)
        for room_id, value in _room_peaks(variant.get("scenario_results")).items():
            entry = rooms.setdefault(room_id, {"room_id": room_id, "name": value["name"], "peaks": {}})
            if identities.get(room_id):
                entry["source_room_id"] = identities[room_id]
            entry["peaks"][str(setpoint)] = {key: value[key] for key in ("kw", "hour", "month")}
    return {"status": "calculated", "baseline_setpoint_c": rows[0]["setpoint_c"] if rows else None,
            "pressure_kpa": _number(comparison.get("pressure_kpa")),
            "relative_humidity": _number(comparison.get("relative_humidity")),
            "variants": rows, "rooms": sorted(rooms.values(), key=lambda row: row["name"].casefold())}


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _inputs(paths, key):
    """(report, input set the report was calculated from, reason when either is missing)."""
    report = ai_preliminary_service._read(paths["report"], {})
    if not report or not report.get("included_scope_peak"):
        return None, None, "No final cooling load has been calculated yet."
    fingerprint = key.get("input_set")
    if not fingerprint:
        return report, None, "The cooling result doesn't record which inputs it was calculated from."
    snapshot = Path(paths["sets"]) / f"{fingerprint}.json"
    input_set = ai_preliminary_service._read(snapshot, {})
    if not input_set:
        current = ai_preliminary_service._read(paths["current_set"], {})
        input_set = current if current.get("input_fingerprint") == fingerprint else {}
    if not input_set or input_set.get("input_fingerprint") != fingerprint:
        return report, None, ("The inputs the cooling result was calculated from are no longer saved; "
                              "update the final load to compare setpoints.")
    return report, input_set, ""


def get(project, *, full=False):
    """The comparison for the job's saved cooling result. full=True returns the uncompacted comparison (with each
    variant's scenario results) for the contractor's report; it is not cached, only the compact view is."""
    if not project.get("review_dir"):
        return {"status": "not_calculated", "reason": "The drawings haven't been read yet."}
    paths = ai_preliminary_service._paths(project)
    # Cheap: worked out once per saved version of the report file.
    key = ai_preliminary_service._file_derived(paths["report"], {}, "setpoint_comparison_key", _report_key)
    if not key:
        return {"status": "not_calculated", "reason": "No final cooling load has been calculated yet."}
    cache_path = Path(paths["root"]) / CACHE_NAME
    cached = _read(cache_path)
    if not full and cached.get("version") == VERSION and cached.get("key") == key["fingerprint"]:
        return cached.get("result") or {}
    with _lock(paths["root"]):
        cached = _read(cache_path)
        if not full and cached.get("version") == VERSION and cached.get("key") == key["fingerprint"]:
            return cached.get("result") or {}
        report, input_set, reason = _inputs(paths, key)
        if reason:
            raw = {"status": "not_calculated", "reason": reason}
        else:
            raw = setpoint_comparison.compare(input_set, report)
        result = compact(raw, report or {})
        ai_preliminary_service._write(cache_path, {"version": VERSION, "key": key["fingerprint"], "result": result})
    return raw if full else result
