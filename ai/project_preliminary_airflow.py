"""Project-scoped arithmetic for a provisional outside-air assessment.

This helper deliberately stays separate from the shared airflow resolver,
whose default rule is ``max(people rate, area rate)``.  It supports the
explicit additive fallback chosen for the Butcher Buffet preliminary review
without changing other projects or implying code compliance.
"""

import math


def _nonnegative_number(value, name):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite non-negative number or None")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number or None") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be a finite non-negative number or None")
    return result


def calculate_additive_fallback_outside_air(
    occupancy_count,
    area_m2,
    people_rate_lps_person=7.5,
    area_rate_lps_m2=0.3,
):
    """Calculate supported components, returning a total only when complete.

    ``None`` means the operand is unresolved; zero is a known zero.  The
    partial component sum is evidence only and must not be used as a final
    ventilation flow when either operand is missing.
    """
    occupancy = _nonnegative_number(occupancy_count, "occupancy_count")
    area = _nonnegative_number(area_m2, "area_m2")
    people_rate = _nonnegative_number(people_rate_lps_person, "people_rate_lps_person")
    area_rate = _nonnegative_number(area_rate_lps_m2, "area_rate_lps_m2")
    if people_rate is None or area_rate is None:
        raise ValueError("fallback rates must be provided")

    people_component = occupancy * people_rate if occupancy is not None else None
    area_component = area * area_rate if area is not None else None
    components = [value for value in (people_component, area_component) if value is not None]
    complete = occupancy is not None and area is not None
    return {
        "formula": "occupancy × people_rate + area × area_rate",
        "operands": {
            "occupancy_count": occupancy,
            "area_m2": area,
            "people_rate_lps_person": people_rate,
            "area_rate_lps_m2": area_rate,
        },
        "components_lps": {
            "people": people_component,
            "area": area_component,
        },
        "known_component_sum_lps": sum(components) if components else None,
        "total_lps": sum(components) if complete else None,
        "status": "provisional" if complete else "incomplete",
        "unresolved_fields": [
            field for field, value in (("occupancy_count", occupancy), ("area_m2", area))
            if value is None
        ],
    }
