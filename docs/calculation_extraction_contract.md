# PDF calculation-input extraction contract

`ai.calculation_extraction.extract_calculation_input_evidence` is a pure, local
transformation of supplied PDF text, normalized records, OCR observations,
vector witnesses, and optional existing vision responses. It performs no file
I/O, network access, provider calls, model assembly, schedule generation, report
updates, or load calculation. Its extractor version is `calculation-input-v2`.
No page number, drawing identifier, or project-specific room name controls it.

## Supported evidence

| Page capability | Retained facts |
| --- | --- |
| Dimensioned plans | Explicit room labels and areas, dimension pairs and ordered dimension chains, wall/partition witnesses, room-to-surface endpoints |
| Ceiling/service plans | Directly room-linked ceiling heights and types, fixture tags/counts, explicit lighting quantity × watts, room-to-service endpoints |
| Opening schedules/elevations/plans | Opening tags, width/height, sill/head levels, cited cross-page tag references |
| Equipment schedules | Name, quantity, model, location, nameplate power; separately stated heat-to-space with its explicit basis |
| Notes/schedules | Occupancy counts, operating intervals, setpoints, outside-air rates, construction labels, explicit U-values and glazing references |
| Sections/elevations | Floor-to-floor dimensions, directly room-linked ceilings, vertical boundary endpoints |
| 3D/reference renders | Cross-check evidence only; primary values are suppressed even when an upstream response requests activation |

Text readers recognize explicit statements. They do not interpret arbitrary
unlabelled table layouts or reconstruct room polygons. Rich table and vector
facts use the normalized record interface below. Raw untyped table cells remain
cited observations; a numeric cell alone does not establish its field or units.
A construction label never supplies a U-value. A room name never supplies an
occupancy, schedule, or neighbouring room's ceiling height.

## Normalized record interface

Place records in each page's `structured_content.records`. OCR `table_cells`
with an explicit `field` use the same interface. Existing untyped table cells
remain available through `binding.observations`.

```json
{
  "entity": "room",
  "label": "Studio A",
  "field": "ceiling_height",
  "value": 3.2,
  "unit": "m",
  "excerpt": "Studio A — ceiling 3.2 m",
  "coordinates": [12, 20, 80, 30],
  "table_id": "ceiling-schedule",
  "row": "Studio A",
  "column": "height",
  "extraction_method": "table_ocr",
  "confidence": "high"
}
```

Fields:

- Numeric: `area`, `dimension`, `dimension_chain`, `ceiling_height`,
  `floor_to_floor_height`, `width`, `height`, `sill`, `head`, `occupancy`,
  `setpoint`, `outside_air`, `u_value`, `quantity`, `nameplate_power`,
  `heat_to_space`.
- Descriptive: `identity`, `ceiling_type`, `fixture_tag`, `name`, `model`,
  `location`, `construction`, `glazing_reference`.
- `operating_hours`: a map from `weekday`, `saturday`, `sunday`, or `holiday`
  to two explicit 24-hour times, such as `{"weekday": ["08:00", "17:00"]}`.
  These intervals are evidence, not an assumed hourly profile.
- Relationships: `wall`, `partition`, `room_surface`, `room_service`, and
  `vertical_boundary` have explicit `{"from": "...", "to": "..."}` endpoints.
  Endpoints record the stated relationship, not computed geometry.

Use `entity: "equipment"`, `"opening"`, `"level"`, or `"surface"` where
appropriate. Room entities use their explicit label as allocation; other
entities can carry a separately supplied `room_id`. Unallocated facts stay
unresolved. Equipment `location` text is retained without guessing a room match.
Equipment heat records require a nonempty `heat_basis` describing the direct
heat-to-space statement; upstream readers must not substitute electrical power.
All equipment records remain evidence-only, including explicit heat statements.

Coordinates/bounding boxes, named table cells, `vector_id`, and `witness_ids`
retain their originating evidence. Missing vector references are flagged.
For plain text, citation coordinates identify character ranges in `page_text`;
these are explicitly textual locations, **not drawing coordinates or scale**.
Normalized records missing a location or excerpt/table/vector citation carry
unresolved fields. The extractor cannot independently authenticate a supplied
record against PDF bytes; supplying accurate units and citations is the reader's
responsibility.

An explicit text equipment row may also use this form:

```text
Equipment EQ7: Process unit; quantity 2; model MX-7; location Studio A; nameplate power 2 kW; heat-to-space 0.8 kW
```

Unstructured equipment mentions preserve presence and any isolated power token
as evidence requiring allocation, without inventing a quantity or heat load.

## Units and unresolved values

Supported explicit conversions include metres to millimetres, kW to W,
m³/h to L/s, square-metre spellings, Celsius, and W/m²K. ACH is retained as ACH;
no room volume or airflow is derived. Mixed opening dimensions such as
`1.2 m x 2100 mm` normalize each side independently. A trailing pair unit
(`1200 x 2100 mm`) applies to both values; a unitless pair supplies no dimensions.

Missing/unsupported units, invalid numeric values, nonintegral counts, invalid
operating times, unsupported fields, missing allocations, missing witnesses,
and incomplete relationships are retained as unresolved candidates. Raw values
remain available when normalized `value` is null. Proposed or blocked records
are not calculator approvals. Conflicting values retain distinct IDs and
competing-candidate references; equipment conflicts remain evidence-only and
still block the evidence package.

Raw vision dimensions require explicit dimension-witness review. 3D values are
never primary dimensions, areas, scale, or thermal inputs, and 3D disagreement
cannot displace an explicit plan fact.

## Identity and relationships

Candidate IDs combine source fingerprint, page, drawing number, entity label,
evidence location, target field, and the value/unit witness. Distinct conflicting
readings of the same location remain separate. No generated identity uses an
array position. Supplied vector witness IDs remain external reference IDs.

Evidence collections are canonicalized before extraction; geometric point order
and dimension-chain order are preserved. Fingerprints exclude the output
creation timestamp. Reordering page, record, OCR, table, or vision collections
does not change IDs or fingerprints.

Existing unique plan/elevation bindings remain available. Normalized opening
records additionally provide `opening_tag_cross_reference` relationships with
both citations. These links establish tag co-reference only: repeated tags do
not prove a unique physical opening and never activate a dimension or fill a
missing field. Ambiguous existing opening bindings remain review blockers.

## Verification and scope

Synthetic coverage is in `tests/test_calculation_extraction_contract.py`.
Run from the repository root:

```sh
python3 -m unittest tests.test_calculation_extraction_contract tests.test_calculation_extraction tests.test_geometry_resolution
PYTHONPATH=. python3 tests/test_building_evidence.py
python3 -m unittest discover -s tests -t .
git diff --check
```

The building-evidence file uses its own `main()` checks rather than unittest
test cases, so it must also run directly. No test fixture needs private PDFs or
Drawing 6 output. The extraction task changes only this module, its focused
contract test, and this document. Infiltration, the input assembler, hourly
physics, frontend, source-pack policy, schedules, envelope/report artifacts,
and private outputs are outside the extraction change.
