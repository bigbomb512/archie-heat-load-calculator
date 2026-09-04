# Site Design Conditions API

This backend-only resource stores an engineer-entered, cited site and design
condition record. It does not look up weather data, infer values, or update
`design_requirements.json`. A frontend can use this contract independently.

## Endpoints

`GET /api/site-design-conditions?project_id=<project-id>` returns the saved
packet or the empty version-1 template, its readiness summary, and the artifact
URL when saved.

`POST /api/site-design-conditions` saves a packet. The request body is:

```json
{
  "project_id": "project-123",
  "site_design_conditions": {
    "site": {
      "project_name": {
        "value": "Example retail tenancy",
        "status": "confirmed",
        "source": "Client brief",
        "citations": [{"reference": "Client brief", "page": 1, "excerpt": "Project name"}]
      }
    },
    "design_basis": {
      "name": "Engineer design conditions",
      "reference_version_or_date": "2026-09-04",
      "status": "confirmed",
      "source": "Mechanical engineer design brief",
      "citations": []
    }
  }
}
```

All omitted fields are returned as empty fields with `status: "missing"`.
The full version-1 packet contains these sections:

- `site`: `project_name`, `address`, `location_description`,
  `weather_station_reference`, `elevation_m`, and `north_orientation_note`.
- `design_basis`: `name`, `reference_version_or_date`, status, source, and
  citations.
- `summer`: outdoor DB/WB, indoor DB/RH, and atmospheric pressure.
- `winter`: outdoor DB/RH and indoor DB/RH.

Every site/summer/winter field uses this shape:

```json
{
  "value": 35,
  "status": "confirmed",
  "source": "Engineer design-condition schedule",
  "citations": [{"reference": "Engineer schedule", "page": 2, "excerpt": "Summer DB"}]
}
```

Allowed statuses are `missing`, `provisional`, `confirmed`, and
`not_applicable`. Confirmed or provisional fields require both a value and a
non-empty source. Citations are optional structured references, not a weather
data service lookup.

## Readiness

The response includes `readiness` with per-section missing/provisional fields.
`readiness.status` stays `review_required` while any item is missing or
provisional; `readiness.completion_status` distinguishes a partially missing
packet from one ready for engineer confirmation.

- `review_required`: required values or sources are missing.
- `ready_for_engineer_confirmation`: returned as `completion_status` when all
  required values and sources are present, but one or more remain provisional.
- `confirmed`: all required site, design-basis, summer, and winter values are
  confirmed.

The artifact is saved at
`<project review directory>/site_design_conditions.json`. The project analysis
response exposes `site_design_conditions_url` and
`site_design_conditions_status` so a future frontend can discover it.

## Deliberate v1 boundary

Saving this resource does not update the existing preliminary cooling or
ventilation inputs, invalidate reports, select a climate station, or perform a
cooling/heating calculation. A future, separately approved integration must
make that linkage explicit.
