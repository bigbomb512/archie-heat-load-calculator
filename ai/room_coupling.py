"""Approval-gated dynamic room-to-room partition coupling.

The solver is intentionally small and deterministic: each reviewed partition
has one state node on each room side, and the transfer reported to the owning
room is exactly the opposite of the transfer reported to the adjacent room.
No record is eligible without explicit geometry, construction, ownership and
citation evidence plus the project-local method gate.
"""

from copy import deepcopy
import hashlib
import json
import math

from ai.site_design_conditions import validate_citations


METHOD_ID = "room_to_room_dynamic_v1"
APPROVAL_STATES = {"placeholder", "approved"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def room_coupling_gate_fingerprint(gate):
    """Fingerprint gate content independently of any stored fingerprint field."""
    canonical = deepcopy(gate or {})
    canonical.pop("fingerprint", None)
    return fingerprint(canonical)


def empty_room_coupling_method_gate():
    return {
        "schema_version": 1,
        "updated_at": "",
        "method_id": METHOD_ID,
        "approval_status": "placeholder",
        "engineer_name": "",
        "engineer_credential": "",
        "approved_at": "",
        "method_citation": "",
        "scope": "Two-node hourly dynamic heat transfer across explicitly reviewed room-to-room partitions.",
        "policy": {
            "solver": "bounded_gauss_seidel",
            "timestep_hours": 1,
            "transfer_basis": "equal_and_opposite_partition_flow",
            "unsupported": ["annual_state_carryover", "inferred_adjacent_room", "reciprocal_duplicate_surface"],
        },
        "citations": [],
    }


def validate_room_coupling_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Room-coupling method gate must be a JSON object.")
    result = deepcopy(empty_room_coupling_method_gate())
    result.update({key: raw.get(key, result[key]) for key in result})
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Room-coupling method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in APPROVAL_STATES:
        raise ValueError("Room-coupling approval status must be placeholder or approved.")
    for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[field] = str(result.get(field, "") or "").strip()
    result["citations"] = validate_citations(raw.get("citations", []), "Room-coupling method gate")
    if result.get("policy") != empty_room_coupling_method_gate()["policy"]:
        raise ValueError("Room-coupling method policy is fixed for V1.")
    if result["approval_status"] == "approved":
        missing = [field.replace("_", " ") for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope") if not result[field]]
        if missing or not result["citations"]:
            raise ValueError("Approved room-coupling method gate requires " + ", ".join(missing + ([] if result["citations"] else ["citation"])) + ".")
    result["fingerprint"] = room_coupling_gate_fingerprint(result)
    return result


def room_coupling_gate_is_approved(gate):
    return bool(gate and gate.get("approval_status") == "approved" and gate.get("method_id") == METHOD_ID)


def validate_coupling_record(raw):
    if not isinstance(raw, dict):
        raise ValueError("Room-coupling record must be an object.")
    required = (
        "coupling_id", "surface_id", "owner_room_id", "adjacent_room_id",
        "owner_zone_id", "adjacent_zone_id", "area_m2", "r_owner_m2k_w",
        "r_adjacent_m2k_w", "r_partition_m2k_w", "capacitance_owner_kj_m2k",
        "capacitance_adjacent_kj_m2k", "initial_owner_state_temperature_c",
        "initial_adjacent_state_temperature_c", "source", "citations",
    )
    missing = [key for key in required if raw.get(key) in (None, "")]
    if missing:
        raise ValueError("Room-coupling record is missing: " + ", ".join(missing) + ".")
    if raw["owner_room_id"] == raw["adjacent_room_id"]:
        raise ValueError("Room-coupling record cannot connect a room to itself.")
    positive = ("area_m2", "r_owner_m2k_w", "r_adjacent_m2k_w", "r_partition_m2k_w", "capacitance_owner_kj_m2k", "capacitance_adjacent_kj_m2k")
    for key in positive:
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Room-coupling {key} must be positive and finite.")
    temperatures = ("initial_owner_state_temperature_c", "initial_adjacent_state_temperature_c")
    for key in temperatures:
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Room-coupling {key} must be finite numeric data.")
    citations = validate_citations(raw.get("citations", []), "Room-coupling record")
    review_status = raw.get("review_status", "missing")
    if review_status not in {"missing", "provisional", "confirmed"}:
        raise ValueError("Room-coupling review status is invalid.")
    if review_status == "confirmed" and not citations:
        raise ValueError("Confirmed room-coupling record requires citations.")
    result = deepcopy(raw)
    result["citations"] = citations
    result["coupling_id"] = str(raw["coupling_id"])
    result["method_id"] = METHOD_ID
    return result


def solve_dynamic_partition(record, owner_air_temperature_c, adjacent_air_temperature_c,
                            previous_owner_state_temperature_c=None,
                            previous_adjacent_state_temperature_c=None,
                            *, timestep_hours=1.0, tolerance_c=1e-6,
                            max_iterations=500, gate_version=""):
    """Solve one coupled partition hour with bounded Gauss-Seidel iteration."""
    record = validate_coupling_record(record)
    values = (owner_air_temperature_c, adjacent_air_temperature_c, timestep_hours, tolerance_c, max_iterations)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise ValueError("Room-coupling temperatures and solver settings must be finite numeric values.")
    if timestep_hours <= 0 or tolerance_c <= 0 or max_iterations < 1:
        raise ValueError("Room-coupling timestep, tolerance and iteration count must be positive.")
    owner_state = record["initial_owner_state_temperature_c"] if previous_owner_state_temperature_c is None else previous_owner_state_temperature_c
    adjacent_state = record["initial_adjacent_state_temperature_c"] if previous_adjacent_state_temperature_c is None else previous_adjacent_state_temperature_c
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in (owner_state, adjacent_state)):
        raise ValueError("Room-coupling previous state temperatures must be finite numeric values.")
    dt = timestep_hours * 3600.0
    owner_next, adjacent_next = owner_state, adjacent_state
    converged = False
    last_delta = float("inf")
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        owner_flux = (owner_air_temperature_c - owner_next) / record["r_owner_m2k_w"] + (adjacent_next - owner_next) / record["r_partition_m2k_w"]
        owner_candidate = owner_next + dt * owner_flux / (record["capacitance_owner_kj_m2k"] * 1000.0)
        adjacent_flux = (adjacent_air_temperature_c - adjacent_next) / record["r_adjacent_m2k_w"] + (owner_candidate - adjacent_next) / record["r_partition_m2k_w"]
        adjacent_candidate = adjacent_next + dt * adjacent_flux / (record["capacitance_adjacent_kj_m2k"] * 1000.0)
        delta = max(abs(owner_candidate - owner_next), abs(adjacent_candidate - adjacent_next))
        last_delta = delta
        owner_next, adjacent_next = owner_candidate, adjacent_candidate
        if delta <= tolerance_c:
            converged = True
            break
    transfer_kw = (adjacent_next - owner_next) / record["r_partition_m2k_w"] * record["area_m2"] / 1000.0
    # Residual is the remaining thermal-state change expressed as a heat-flow
    # equivalent. Equal-and-opposite transfer is reported separately below.
    residual_kw = last_delta * max(
        record["capacitance_owner_kj_m2k"], record["capacitance_adjacent_kj_m2k"]
    ) * record["area_m2"] / (max(timestep_hours, 1e-12) * 3600.0)
    status = "calculated" if converged else "blocked"
    return {
        "status": status,
        "coupling_id": record["coupling_id"],
        "surface_id": record["surface_id"],
        "owner_room_id": record["owner_room_id"],
        "adjacent_room_id": record["adjacent_room_id"],
        "owner_state_temperature_c": round(owner_next, 6),
        "adjacent_state_temperature_c": round(adjacent_next, 6),
        "owner_sensible_kw": round(transfer_kw, 6),
        "adjacent_sensible_kw": round(-transfer_kw, 6),
        "raw_signed_owner_sensible_kw": round(transfer_kw, 6),
        "raw_signed_adjacent_sensible_kw": round(-transfer_kw, 6),
        "iterations": iterations,
        "residual_kw": round(residual_kw, 9),
        "state_delta_c": round(last_delta, 9),
        "tolerance_c": tolerance_c,
        "max_iterations": max_iterations,
        "timestep_hours": timestep_hours,
        "formula": "two-node bounded Gauss-Seidel; Q_owner = (T_adjacent_state − T_owner_state) / R_partition × area; Q_adjacent = −Q_owner",
        "method_id": METHOD_ID,
        "gate_version": gate_version,
        "source": record["source"],
        "citations": deepcopy(record["citations"]),
        "operands": {
            "owner_air_temperature_c": owner_air_temperature_c,
            "adjacent_air_temperature_c": adjacent_air_temperature_c,
            "previous_owner_state_temperature_c": owner_state,
            "previous_adjacent_state_temperature_c": adjacent_state,
            "area_m2": record["area_m2"],
            "r_owner_m2k_w": record["r_owner_m2k_w"],
            "r_adjacent_m2k_w": record["r_adjacent_m2k_w"],
            "r_partition_m2k_w": record["r_partition_m2k_w"],
            "capacitance_owner_kj_m2k": record["capacitance_owner_kj_m2k"],
            "capacitance_adjacent_kj_m2k": record["capacitance_adjacent_kj_m2k"],
        },
    }


def coupling_record_fingerprint(record):
    return fingerprint(validate_coupling_record(record))
