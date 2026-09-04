# Archie Heat Load Calculator

Dedicated evidence-first cooling and heating load-calculation project for Archie.

This repository owns load inputs, schedules, design-day scenarios, room/zone load models, calculation reports, and calculation readiness. It also retains the PDF intake and evidence-to-AI handoff pipeline needed to collect cited project facts.

It does **not** own HVAC layout, equipment placement, routing, CAD actions, or drawing-file generation. Those remain in the broader Archie design project and may consume reviewed calculator outputs through a later API/artifact contract.

## Current capability

- PDF page triage, rendering, review packets, spatial OCR, and cited building evidence.
- Manual AI packets for visual/evidence review.
- External-research handoff packets for a web-enabled AI or researcher; every external fact must have a direct citation and remains review-required.
- Evidence-aware preliminary and hourly cooling calculations for entered/reviewed inputs.
- Site conditions, schedules, design-day scenarios, room-within-zone load overlays, provisional/final readiness, and parity-report scaffolding.

The current cooling method is limited to its declared inputs. It is not CAMEL+/DA09 parity, equipment selection, heating design, AHU/plant analysis, annual analysis, or geometric shading.

## Run locally

```bash
python3 -m backend.web_app --port 8000
```

Open `http://127.0.0.1:8000`.

Dependencies for PDF processing are listed in `requirements.txt`; Poppler is required for rendered page previews.

## Evidence-to-research flow

```text
PDF drawings
→ reviewed PDF/evidence packet
→ manual AI visual review and/or web-enabled research handoff
→ cited proposed facts
→ engineer review
→ calculator input artifacts
→ provisional or confirmed load report
```

Run the research handoff after a reviewed `ai_input.json` exists:

```bash
PYTHONPATH=. python3 ai/research_packet.py output/review/<project>/ai_input.json
```

See [the research handoff guide](docs/ai_research_handoff.md) and [the cooling roadmap](docs/cool_heat_load_roadmap.md).
