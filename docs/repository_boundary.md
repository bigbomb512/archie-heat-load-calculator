# Repository Boundary

`archie-heat-load-calculator` is the dedicated calculation workstream. It owns:

- PDF extraction, page review, OCR, and building-evidence artifacts needed to establish cited load inputs.
- AI handoff packets for visual review and externally researched, cited proposed facts.
- Engineer-reviewed load inputs, cooling/heating calculation engines, schedules, scenarios, reports, readiness, and validation.

It deliberately does not own HVAC design layouts, equipment placement, duct/pipe routing, clash resolution, CAD actions, DWG/DXF generation, or issued construction drawings. Those capabilities remain in the broader Archie project.

Some retained geometry modules use names such as `cad_geometry`. In this repository they are evidence-review utilities only: no CAD drawing is generated or issued from them. A later integration should pass reviewed building evidence into this project and consume immutable calculator result artifacts from it.

## External research boundary

The research packet is intentionally manual and provider-neutral. It packages evidence and asks a web-enabled AI or human researcher to return cited proposed facts. This repository makes no internet calls, stores no provider credentials, and never imports external research directly into a calculation. An engineer must review any proposed fact first.
