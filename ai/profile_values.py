"""A room's default values: its profile, with the house profile for a house room and the room-use values for room
uses that share a profile but have their own row in the cited sources (a kitchen and a dining room are both
"hospitality"; NCC 2022 Table J7D3a and AS 1668.2 Table A1 list them separately). See the pack's "value_sources".
"""
from copy import deepcopy

from ai import house_profiles


def room_profile(pack, profile_id, room_use=""):
    profiles = (pack or {}).get("profiles", {}) if isinstance(pack, dict) else {}
    house = house_profiles.profile(profiles, profile_id)
    if house is not None:
        return house
    result = deepcopy(profiles.get(profile_id) or profiles.get("generic_conditioned_room", {}))
    by_use = ((pack or {}).get("room_use_values") or {}).get("by_room_use", {}) if isinstance(pack, dict) else {}
    result.update(deepcopy(by_use.get(str(room_use or ""), {})))
    return result
