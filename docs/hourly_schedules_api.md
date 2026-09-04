# Hourly Schedules and Peak Timing API

This is the backend contract for the engineer-reviewed hourly cooling design-day feature. It is deliberately independent of the existing preliminary cooling-load inputs and its frontend. All endpoints require `project_id`; all saved artifacts live in that project's review folder.

## Evidence and readiness

Every status is one of `missing`, `provisional`, `confirmed`, or `not_applicable`. Values are never defaulted, interpolated, or copied from site conditions. A response returns the artifact, `readiness`, an artifact URL, and an artifact status. A report is `blocked`, `calculated_provisional`, or `calculated`.

`confirmed` schedules and scenarios require sources. A final calculation requires confirmed inputs and no unresolved drawing-coverage exceptions. Saving any artifact does not update `design_requirements.json`, preliminary cooling reports, or ventilation reports.

## Schedules

`GET /api/schedules?project_id=...` retrieves `schedule_library.json`.

`POST /api/schedules` saves a library. A schedule has a stable lowercase `schedule_id`, a title, evidence, and all three explicit day profiles: `weekday`, `saturday`, and `sunday_holiday`. Each supplied profile has exactly 24 values indexed in order from hour 0 through 23, constrained to `0.0`–`1.0`. There is no fallback between day types.

```json
{
  "project_id": "example",
  "schedule_library": {
    "schedules": [{
      "schedule_id": "retail_people",
      "title": "Retail occupancy",
      "status": "confirmed",
      "source": "Engineer design brief, rev C",
      "citations": [{"reference": "Design brief", "location": "p. 3"}],
      "day_profiles": {
        "weekday": {"status": "confirmed", "source": "Design brief, p. 3", "citations": [], "values": [0,0,0,0,0,0,0,0,0.5,1,1,1,1,1,1,1,1,1,0.5,0,0,0,0,0]},
        "saturday": {"status": "missing", "source": "", "citations": [], "values": []},
        "sunday_holiday": {"status": "missing", "source": "", "citations": [], "values": []}
      }
    }]
  }
}
```

The schedule semantic is a generic load fraction. Assign it explicitly to people, lighting, each heat source, outside air, and each solar-bearing surface as applicable.

## Design-day scenarios

`GET`/`POST /api/design-day-scenarios` manages `design_day_scenarios.json`. Each scenario has a stable ID, title, mode (`cooling` or `heating`), representative month, day type, evidence status, source, citations, and pressure field. Cooling scenarios have 24 distinct rows `0`–`23`; every row carries cited outdoor DB and WB fields, with `WB <= DB`. Heating scenarios can be stored now, but the v1 runner reports that hourly heating is not implemented.

## Hourly room model

`GET /api/hourly-load-model?project_id=...` retrieves `hourly_load_model.json`.

`POST /api/hourly-load-model` supports two actions:

- `{"project_id":"...","action":"build"}` seeds one **inferred**, provisional room per existing design zone. It copies source values only.
- `{"project_id":"...","action":"save","hourly_load_model":{...}}` saves the engineer-reviewed model.

Rooms have stable IDs, an explicit `zone_id`, source labels and mapping evidence, their own area/occupancy/setpoint/static cooling inputs/conditions, heat sources with stable source IDs, surfaces with existing surface IDs, and `schedule_assignments`:

```json
{
  "people": "retail_people",
  "lighting": "retail_lights",
  "outside_air": "retail_ventilation",
  "equipment": {"room-a-source-1": "refrigeration"},
  "solar": {"surface-north": "north_solar"}
}
```

Non-zero timed drivers need a valid profile for the scenario day type. A zero or explicitly not-applicable driver needs none. The model records the saved `design_requirements.updated_at`; it is stale when that revision changes.

## Hourly report

`GET /api/hourly-load-report?project_id=...` returns the saved report and whether all input artifacts are current.

`POST /api/hourly-load-report` runs it:

```json
{
  "project_id": "example",
  "selected_scenario_ids": ["jan_weekday"],
  "calculation_stage": "preliminary"
}
```

For every hour, the engine schedules people, lights, heat sources, solar and outside air; calculates envelope conduction at hourly outdoor DB; calculates psychrometric outside-air sensible/latent load at hourly DB/WB/pressure; then applies the existing explicit safety factor. It retains pre-safety and post-safety totals, components, tied peaks, and the earliest tied hour for display. Zones sum rooms and the project sums zones at the **same hour**, never independent room peaks.

V1 excludes partitions, infiltration, dynamic thermal mass, detailed glazing physics, AHU coil and fan/duct effects, heat recovery, and plant loads. The analysis response exposes each artifact URL/status for frontend discovery. The parity adapter can expose a current hourly project peak and components, but `final_parity_allowed` remains false until an authorised CAMEL+/DA09 reconciliation is completed.
