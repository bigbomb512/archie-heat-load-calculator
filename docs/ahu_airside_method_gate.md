# AHU and air-side cooling method gate

Stage 9 adds a separate, explicit air-side calculation path. It consumes a
current room-level hourly cooling report and never changes the room formulas or
historical room reports.

The first release supports single-zone constant-volume and multi-zone VAV
systems. AHU topology, zone ownership, number-off, air paths, airflow,
return/outside-air states, fan heat, duct effects, leakage, heat recovery,
preconditioning, and coil leaving states must be reviewed and cited. Airflow
is not derived from room load, screenshots, room names, or system type.

Outside-air conditioning is centrally owned by the AHU adapter for AHU-served
zones. The adapter removes the room report's outside-air component only from
its AHU reconciliation view; the historical room report is not rewritten.
Infiltration remains room-level and separate.

The air-side method gate is a project-local approval record. A placeholder gate
allows development/draft results, while an approved gate is required for a
`review_ready` AHU report. Approval requires a named engineer, credential,
date, method citation, scope, and supporting citations. The gate is not a
credential verifier and passing synthetic tests is not engineering approval.

The hourly report retains room-load reconciliation, mixed-air and coil state
points, sensible/latent/total coil duty, fan and duct effects, flow balance,
blocked AHUs, source citations, and dependency fingerprints. Invalid states,
missing return-air conditions, missing airflow, path imbalance, and incomplete
reviewed inputs fail closed. Plant, hydraulic, annual, and benchmark-validating
calculations remain outside this method.
