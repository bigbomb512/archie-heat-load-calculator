"""Self-checks on a saved cooling result, so a PDF-only result is never labelled "Design" while it is plainly short.

Build queue task 9 (2026-11-28). The house scoreboard (docs/HOUSE_SCOREBOARD_2026-11-28.md) found every PDF-only
house 22-52 % low while nothing on the result said so. Each check returns a plain reason. Rooms not counted and windows
not placed are concrete gaps, so they keep the result "Preliminary"; the load per m² is only compared with three
checked houses, so it is shown as a warning and doesn't change the label (2026-11-28 review). No load value is changed.

The limits come from the three checked houses and are provisional (DECISIONS.md, 2026-11-28):
- windows: the checked set 17 placed 24 of 28 scheduled windows (86 %); PDF-only runs placed 54 %;
- load per cooled m²: the checked houses are 49, 83 and 90 W/m²; the band is 20 % beyond the lowest and highest.
"""

WINDOWS_PLACED_SHARE = 0.75
HOUSE_W_PER_M2 = (39.0, 108.0)


def _room_peaks(full_report):
    """Peak kW of each room over the design days (sum of its hourly components)."""
    peaks = {}
    for scenario in (full_report or {}).get("scenario_results", []) or []:
        for room in scenario.get("rooms", []) or []:
            best = max((sum(component.get("total_kw") or 0 for component in (hour.get("components") or {}).values()
                            if isinstance(component, dict))
                        for hour in room.get("hours") or []), default=0.0)
            peaks[room.get("room_id")] = max(peaks.get(room.get("room_id"), 0.0), best)
    return peaks


def cooled_area_m2(full_report, model):
    """Floor area of the rooms that carry a load (a driveway left on the room list with no load isn't cooled floor)."""
    peaks = _room_peaks(full_report)
    rooms = ((model or {}).get("topology") or {}).get("rooms", []) or []
    return sum(float(room.get("area_m2") or 0) for room in rooms if peaks.get(room.get("room_id"), 0.0) > 0)


def checks(base_report, full_report, model, dwelling):
    """Every reason, blocking or not, as text; [] when the checks pass."""
    return [row["text"] for row in findings(base_report, full_report, model, dwelling)]


def findings(base_report, full_report, model, dwelling):
    """[{"text", "blocks"}]: "blocks" is True when the reason keeps the result from being "Design"."""
    reasons = []
    coverage = (base_report or {}).get("phase1_coverage") or {}
    left_out = [row.get("label", "") for row in coverage.get("rooms_left_out", []) or [] if isinstance(row, dict)]
    if left_out:
        shown = ", ".join(left_out[:5]) + (f" and {len(left_out) - 5} more" if len(left_out) > 5 else "")
        reasons.append(_blocks(f"{len(left_out)} room{'' if len(left_out) == 1 else 's'} on the room list "
                       f"{'is' if len(left_out) == 1 else 'are'}n't counted ({shown}). "
                       + ("Give it an area or take it off the list." if len(left_out) == 1
                          else "Give each an area or take it off the list.")))
    if not dwelling:
        return reasons
    glazing = (base_report or {}).get("glazing_coverage") or {}
    scheduled, placed = int(glazing.get("scheduled") or 0), int(glazing.get("placed") or 0)
    if scheduled and placed < WINDOWS_PLACED_SHARE * scheduled:
        reasons.append(_blocks(f"Only {placed} of the {scheduled} windows in the window schedule are on a room's wall, "
                               "so glass and its sun may be missing."))
    peak = (full_report or {}).get("included_scope_peak") or {}
    total_kw = peak.get("final_design_total_kw", peak.get("design_total_kw"))
    area = cooled_area_m2(full_report, model)
    if total_kw is not None and area > 0:
        per_m2 = float(total_kw) * 1000 / area
        low, high = HOUSE_W_PER_M2
        if not low <= per_m2 <= high:
            reasons.append({"text": f"{per_m2:.0f} W per m² of cooled floor is outside the {low:.0f}–{high:.0f} W/m² of "
                                    "the checked houses; check that no room, wall or glass is missing or counted twice.",
                            "blocks": False})
    return reasons


def _blocks(text):
    return {"text": text, "blocks": True}
