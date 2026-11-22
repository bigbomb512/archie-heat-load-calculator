# AC design input v1

`GET /api/ac-design-input?project_id=<id>` returns the saved load handoff. The backend route calls `backend.ac_design_input_service.api(web, project)`. This document describes schema `toki.ac-design-input`, version `1`.

All loads are cooling loads in **kW**. Temperatures are **°C**, area is **m²**, `indoor_rh` is a fraction (0.56 = 56%), and `hour` is the design day's hour (0–23). `null` means the value was not saved; it does not mean zero. `generated_at` is an ISO 8601 UTC timestamp for the handoff, not the calculation time.

| Field | Meaning |
| --- | --- |
| `project` | Project ID, display name, confirmed address (otherwise found site or empty string), and dwelling flag. |
| `status` | `state` is `current`, `stale`, `building_only`, or `none`; `ready` is true only for `current`. `reason` explains the state. |
| `design_conditions` | Saved weather location, outdoor and indoor dry/wet bulb temperatures, indoor relative humidity, and design day source. The indoor values come from the result's design setpoint (the baseline of its saved setpoint comparison); they are null until that comparison has been worked out, which happens the first time Load and report is opened after a new result. Missing saved values are null. |
| `totals` | Coincident whole-house peak, its sensible (temperature) and latent (moisture) portions, governing month/hour, saved safety factor, suggested ducted size, and saved household count when present. The suggestion is an indicative label, not an equipment selection. |
| `rooms[]` | Cooled rooms first, ordered by level and display name. `id` is the room ID or `join:<id>`; joined rooms replace their `member_ids`. `use_id` and `use_label` identify the resolved room use; `people` is saved occupancy. |
| `rooms[].peak` | The room's **own** maximum over the calculated design days, with its month/hour and any saved sensible and latent portions. Size a room outlet or zone against this load, subject to the design method and airflow checks. |
| `rooms[].at_house_peak_kw` | The room's load at the whole-house governing hour, read from its 24-hour profile. This is a coincidence reference, not its outlet size. |
| `rooms[].hourly_kw` | 24 hourly loads on the governing design day, or null when a complete saved profile is unavailable. `components_at_peak` gives saved load-group kW values when available. |
| `not_cooled[]` | Excluded rooms with ID, name, level, area, and reason. |
| `not_counted[]` | Rooms meant to be cooled that aren't in the saved load yet (no area, walls not worked out), with ID, name, level, area and reason. Design for them only after the load is updated; they are not "not cooled". |
| `assumptions[]` | Deduplicated assumed, typical, and placeholder notes from result status. |
| `open_items` | Count and first five titles of blocking or load-changing todos. |

Use `totals.peak_kw` for the central unit's load basis. Room own peaks generally occur at different hours or months; summing them overstates the simultaneous whole-house load. The design step should allow inspection of `stale`, `building_only`, and `none` handoffs but hold equipment sizing and final design until `status.ready` is true. Stale numbers remain visible for comparison and must be refreshed before use.

Additive fields within this schema keep version 1. Renaming or removing a field, or changing its units or meaning, requires a new version and a corresponding consumer update.

## Example

Trimmed from the saved Vaucluse job `architectural-plan---132-old-south-head-road_-vaucluse---1h-20251124-1795317033` (the status and values shown were read on 22 November 2026):

```json
{
  "schema": "toki.ac-design-input",
  "version": 1,
  "project": {"id": "architectural-plan---132-old-south-head-road_-vaucluse---1h-20251124-1795317033", "name": "Architectural Plan - 132 Old South Head Road, Vaucluse - 1H 20251124", "address": "132 Old South Head Road, Vaucluse", "dwelling": true},
  "status": {"state": "stale", "reason": "Out of date: the saved results have changed inputs; update the load.", "ready": false},
  "design_conditions": {"location": "Sydney", "outdoor_db_c": 31.1, "outdoor_wb_c": 19.8, "indoor_db_c": 24.0, "indoor_wb_c": 18.0, "indoor_rh": 0.56, "source": "AIRAH Technical Handbook, 4th edition (2007), Design temperature data, p. 37 (from AIRAH DA9, 1998)"},
  "totals": {"peak_kw": 21.6568, "sensible_kw": 21.3045, "latent_kw": 0.3523, "month": "January", "hour": 17, "safety_factor": 1.0, "suggested_system": "24 kW ducted", "household_people": null},
  "rooms": [
    {"id": "room-use:first-floor:bedroom-2", "name": "Bedroom 2", "level": "First Floor", "area_m2": 15.196210485753994, "peak": {"kw": 1.9608, "sensible_kw": null, "latent_kw": null, "month": "February", "hour": 17}, "at_house_peak_kw": 1.8327}
  ],
  "not_cooled": [{"id": "…", "name": "…", "level": "…", "area_m2": null, "reason": "…"}],
  "not_counted": [{"id": "…", "name": "…", "level": "…", "area_m2": 3.6, "reason": "…"}],
  "assumptions": ["…"],
  "open_items": {"count": 23, "titles": ["The load is out of date", "Living (Lower Ground Floor) isn't in the total yet", "Family room isn't in the total yet", "Dining (Lower Ground Floor) isn't in the total yet", "Kitchen isn't in the total yet"]}
}
```

The room example omits `use_id`, `use_label`, `cooled`, `people`, `member_ids`, `hourly_kw`, and possible `components_at_peak` for brevity; the endpoint includes them.
