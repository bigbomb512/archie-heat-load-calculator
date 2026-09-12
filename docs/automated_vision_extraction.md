# Automated AI drawing extraction

Archie can send selected architect-page images and the associated local drawing metadata to an opt-in OpenAI Responses API job. It extracts topology and geometry evidence only: floors, rooms, areas, ceiling links, openings, surfaces, orientation candidates, and conflicts.

## Server configuration

Set these values only in the server environment:

```text
OPENAI_API_KEY=...
ARCHIE_VISION_MODEL=gpt-5
ARCHIE_VISION_ESTIMATED_COST_PER_GROUP_AUD=...
```

The browser never receives the API key. Every request sets `store: false`; no live web research occurs. The configured estimate is a local guardrail, not a provider invoice. A project owner must opt in, select evidence groups, and set a maximum budget that covers the estimate before a job can begin.

## Job lifecycle

`GET /api/vision-extraction?project_id=...` returns settings, available evidence groups, estimate, latest job, and artifact links. `POST /api/vision-extraction` accepts `estimate`, `start`, `cancel`, and `retry` with a project id and settings.

Each run stores a request manifest, selected rendered-page hashes, raw provider outputs, normalized output, validation outcome, and redacted failure message in the local project review directory. A server restart changes an in-flight job to `interrupted`; it never resumes or repeats a provider request automatically.

## Evidence safety

Extraction cannot calculate a cooling duty or activate an envelope. A geometry record may be marked `ai_verified` only when it has two independent cited architect witnesses and no validation conflict. Everything else remains a proposal or an explicit exception for the existing review/readiness workflow.

The old paste-JSON control remains under **Advanced recovery** and is not the primary path.
