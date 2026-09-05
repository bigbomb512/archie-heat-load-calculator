# Archie Heat Load Calculator

Dedicated evidence-first cooling and heating load-calculation project for Archie.

This repository owns load inputs, schedules, design-day scenarios, room/zone load models, calculation reports, and calculation readiness. It also retains the PDF intake and evidence-to-AI handoff pipeline needed to collect cited project facts.

It does **not** own HVAC layout, equipment placement, routing, CAD actions, or drawing-file generation. Those remain in the broader Archie design project and may consume reviewed calculator outputs through a later API/artifact contract.

## Current capability

- PDF page triage, rendering, review packets, spatial OCR, and cited building evidence.
- Manual AI packets for visual/evidence review.
- External-research handoff packets for a web-enabled AI or researcher; every external fact must have a direct citation and remains review-required.
- Evidence-aware hourly cooling reports with explicit blocked, draft, and review-ready states.
- Reviewed per-project opaque-envelope libraries and boundary models, including
  exterior and fixed-adjacent-temperature steady-state cooling conduction.
- Site conditions, schedules, design-day scenarios, reviewed floor/zone/room overlays, readiness, and parity-report scaffolding.
- Room-owned evidence records for unsupported airflow and moisture/process inputs. They are captured as confirmed absent, stored-not-calculated, or unassessed; they never silently change cooling totals.

The current cooling method is limited to its declared inputs. Stored infiltration, transfer/extract/make-up air, vapour/steam, and process latent inputs remain explicit exclusions until an approved method exists. It is not CAMEL+/DA09 parity, equipment selection, heating design, AHU/plant analysis, annual analysis, or geometric shading.

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

See [the research handoff guide](docs/ai_research_handoff.md), [the reviewed
envelope guide](docs/reviewed_envelope_slice.md), and [the cooling roadmap](docs/cool_heat_load_roadmap.md).
