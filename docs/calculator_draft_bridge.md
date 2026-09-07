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
room inputs, schedules and envelope candidates. Each row shows the proposed
value, evidence excerpt, confidence and target artifact. The engineer uses
**Save review**, **Preview changes**, and **Apply reviewed changes**, then
continues detailed edits in the hourly/envelope editors. The result panel lists
created records, populated fields, already-present records, conflicts, missing
dependencies, unresolved evidence and any report marked stale.

No new cooling physics is introduced by this bridge. Detailed glazing and
shading, infiltration and transfer-air calculations, heating, AHU, plant,
annual analysis and authorised CAMEL+/DA09 validation remain deferred.
