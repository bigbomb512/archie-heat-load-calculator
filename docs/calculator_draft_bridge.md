# Evidence-to-calculator draft bridge

The bridge is the controlled handoff between cited PDF/thermal evidence and the
engineer-owned cooling artifacts. It reduces repetitive model entry without
turning extraction confidence into engineering approval.

```text
source evidence → build draft → inspect citations
→ accept/edit/reject/needs evidence → preview changes
→ apply additive records → readiness → recalculate
```

## Artifact and provenance

Each project review folder may contain `calculator_draft.json` schema 2. It has
a monotonic revision, source-content fingerprints, candidate fingerprints,
dependencies, persisted decisions, review history, and application receipts.
Every candidate retains its source document, page/excerpt, evidence IDs and
extraction confidence. Engineer review attribution and citations are stored
separately from that extraction confidence.

Candidate IDs are derived from the source document, level/room identity and
evidence location, not array order. Reordering evidence therefore does not move
an approval to another room. Changed values or dependencies invalidate the old
approval and return the candidate to review.

The draft supplements `building_evidence.spaces` with the current room registry
from room-use resolution and the local inference proposal. It matches records
only by exact room ID or exact label and level; ambiguous or duplicate matches
remain explicit conflicts. Each accepted room gets a one-room zone. A floor is
linked only when its level name matches; an unassigned level must be mapped in
the review.

Current reviewer traces with a calibrated area create linked room and area
candidates. The trace remains proposal evidence until an engineer accepts the
room candidate. That named decision records the proof ID, trace calibration and
source fingerprints in the applied room's bridge provenance and sets its
geometry status to confirmed there. `geometry_status` and
`geometry_reference` cannot be edited in the draft. A stale trace makes the
draft stale through its PDF, vector, registry and trace fingerprints; rebuild
and re-review are required. Uncalibrated traces create no area candidate.
When multiple current traces for a room agree within the geometry resolver's
2% relative comparison tolerance, one area proposal uses their mean and cites
every trace. The comparison is the largest-to-smallest area spread divided by
the mean of those extremes. Disagreeing traces create a blocking room review
item and do not propose an area or trace-based geometry confirmation. A trace
is compared with a printed area using 2% of the traced area plus half of the
last displayed digit (for example, ±0.05 m² for a value printed to 0.1 m²).
Matching traced and cited areas share one area candidate rather than creating
duplicates. Conflicting existing room areas are reported and preserved.

These tolerances are workflow consistency thresholds, not estimates of actual
measurement uncertainty, an engineering acceptance criterion, or proof that a
traced area is accurate. A reviewer still evaluates the underlying plans and
calibration evidence.

After room inference updates the room registry, an existing calculator draft
is rebuilt so it does not remain stale from changes made inside that workflow.
If that refresh fails, the room-inference job still completes and the draft
reports that it must be rebuilt. Existing schema-2 drafts created before room
and trace freshness tracking are marked stale once after this upgrade. Rebuild
retains decisions only when their candidate fingerprints still match; changed
or newly introduced candidates return to review, and no decision is applied by
the rebuild itself.

## API actions

`GET /api/calculator-draft?project_id=...` reads the draft. `POST` supports:

* `build`: refreshes proposals only; it never changes calculator artifacts.
* `save_review`: persists all four decisions, edited values, reviewer/source
  attribution and supporting citations.
* `preview_apply`: validates the proposed output and returns creates, empty
  fields, already-present records, conflicts, missing dependencies and unresolved
  evidence. The response contains a fingerprinted preview token.
* `apply`: requires the expected revision and preview token. It uses staged JSON
  writes and a recovery journal so an interrupted batch cannot leave a partial
  model. A stale revision or changed source/target returns `409`.

When source fingerprints are stale, the response includes remediation. Review,
preview and apply stay disabled in the interface until the draft is rebuilt.

## Application rules

Application is additive and ordered: floors, zones, rooms, room metadata/inputs,
schedules, then envelope records. Existing populated authored values are never
replaced. Identical records are reported as already present; differing values
are explicit conflicts. Missing or rejected parent proposals block only their
dependent group. Envelope proposals can add inactive library/model records but
cannot activate or edit an active calculation envelope.

Accepted evidence is not automatically a complete design input. Occupancy,
operating conditions, airflow declarations, schedules, construction properties,
orientation, boundary methods and other required engineering fields remain
visible in the hourly readiness result until separately reviewed in their editor.

## Frontend sequence

The existing frontend panel presents groups for floors/coverage, zones/rooms,
room inputs, schedules and envelope candidates. Room cards show the room
source, current linked trace and calibrated area; only rooms with a current
calibrated trace offer **Accept traced geometry**. Each row shows the proposed
value, evidence excerpt, confidence and target artifact. The engineer uses
**Save review**, **Preview changes**, and **Apply reviewed changes**, then
continues detailed edits in the hourly/envelope editors. The result panel lists
created records, populated fields, already-present records, conflicts, missing
dependencies, unresolved evidence and any report marked stale.

No new cooling physics is introduced by this bridge. Detailed glazing and
shading, infiltration and transfer-air calculations, heating, AHU, plant,
annual analysis and authorised CAMEL+/DA09 validation remain deferred.
