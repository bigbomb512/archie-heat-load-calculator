"""A confirmed scope choice for fixtures that calculate an air-conditioned kitchen."""

from ai import room_use_resolution


def cooled_kitchen(room, scope="comfort_hvac_with_process_exception"):
    if room_use_resolution.is_kitchen_only(room.get("label")):
        return {**room, "calculation_scope_override": scope}
    return room
