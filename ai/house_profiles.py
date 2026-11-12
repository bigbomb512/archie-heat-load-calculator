"""House room profiles (config/house_profiles.json) on the residential base of the assumption pack.

Every step that needs a house room's default values reads them here, so the value resolver, airflow and the draft
can't disagree. Before 12 November 2026 the value resolver didn't know the house profiles and gave every house room
the generic conditioned-room values (10 W/m² lighting, 75/55 W per person, 7.5 L/s per person), which then replaced
the house values in the draft.
"""
import json
from copy import deepcopy
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "config" / "house_profiles.json"
IDS = {"house_bedroom", "house_living", "house_wet", "house_service", "garage", "outdoor", "lift_shaft", "circulation"}


def load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def profile(pack_profiles, profile_id):
    """The residential pack profile with the house profile's values over it (with "schedule_hours" and
    "basis_label"), or None when profile_id isn't a house profile."""
    if profile_id not in IDS:
        return None
    house = load()
    result = deepcopy((pack_profiles or {}).get("residential", {}))
    result.update(deepcopy(house["profiles"][profile_id]))
    result["basis_label"] = house["basis_label"]
    return result
