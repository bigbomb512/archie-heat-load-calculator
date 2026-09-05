# Hourly Schedules and Peak Timing API

This is the backend contract for the authoritative hourly cooling design-day workflow. The older flat cooling report is read-only legacy history. All endpoints require `project_id`; all saved artifacts live in that project's review folder.

## Evidence and readiness

Every input status is one of `missing`, `provisional`, `confirmed`, or `not_applicable`. Values are never defaulted, interpolated, or copied from site conditions. A response returns the artifact, `readiness`, an artifact URL, and an artifact status. A report is `blocked`, `draft`, or `review_ready`; `validated` is reserved for the later authorised benchmark gate.

`confirmed` schedules and scenarios require sources. A review-ready calculation requires confirmed complete-scope inputs and no unresolved drawing-coverage exceptions. Saving any artifact does not update `design_requirements.json`, legacy cooling reports, or ventilation reports.

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

- `{"project_id":"...","action":"build"}` seeds one **inferred**, provisional room per existing design zone. It also creates one provisional `unassigned` floor and maps each migrated zone to it; it never guesses a real level.
- `{"project_id":"...","action":"save","hourly_load_model":{...}}` saves the engineer-reviewed model.

Schema-v3 stores a topology before the existing room load inputs: each floor has a stable ID, name, optional elevation, review status, source and citations; each zone has a stable ID, name and valid `floor_id`; each room has a stable ID, name and valid `zone_id`. A room therefore belongs to a floor only through its zone. Schema-v1 and schema-v2 models are normalised in memory on `GET`; only an explicit save writes schema v3.

Rooms retain source labels and mapping evidence, their own area/occupancy/setpoint/static cooling inputs/conditions, heat sources with stable source IDs, surfaces with existing surface IDs, and `schedule_assignments`:

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

Each room also has `unapproved_components`. The calculator creates one record for every currently unsupported input family: infiltration, minimum supply, extract, spill, transfer and make-up air; vapour, steam and process latent loads. A record has a stable `component_id`, `component_type`, raw `value` and `unit`, evidence `source` and `citations`, `verification_status`, and one `calculation_status`:

- `not_present_confirmed`: a cited, engineer-confirmed declaration that the component is absent. It has no value or unit.
- `stored_not_calculated`: a positive, cited raw value in an accepted capture unit. It is deliberately excluded from the cooling total until its method is approved.
- `not_assessed`: neither absence nor a source-backed value is known yet.

Transfer-air records may reference an existing `source_room_id`; no other component type may do so. Stored input units are captured but never converted in this release: airflow accepts `L/s`, `m3/s`, or `m3/h` (with `ACH` for infiltration); moisture/process input accepts `kg/h`, `g/h`, or `W`.

## Hourly report

`GET /api/hourly-load-report?project_id=...` returns the saved report and whether all input artifacts are current.

`POST /api/hourly-load-report` runs it:

```json
{
  "project_id": "example",
  "selected_scenario_ids": ["summer_design_day"]
}
```

For every hour, the engine schedules people, lights, heat sources, solar and outside air; calculates envelope conduction at hourly outdoor DB; calculates psychrometric outside-air sensible/latent load at hourly DB/WB/pressure; then applies the existing explicit safety factor. It retains pre-safety and post-safety totals, components, tied peaks, and the earliest tied hour for display. Floors aggregate zones and zones aggregate rooms at the **same hour**, never independent room peaks.

The report exposes `known_exclusions` for stored uncalculated room inputs and `unresolved_room_inputs` for unassessed categories, separately from the calculated hourly components. A known excluded or unassessed room component makes the result `draft` and removes the project peak, while retaining an included-scope subtotal for engineering review.

V1 excludes partitions, infiltration, dynamic thermal mass, detailed glazing physics, AHU coil and fan/duct effects, heat recovery, and plant loads. The analysis response exposes each artifact URL/status for frontend discovery. A draft may show only an included-scope subtotal; a project peak is available only for review-ready complete scope. The parity adapter remains disabled until an authorised CAMEL+/DA09 reconciliation is completed.
