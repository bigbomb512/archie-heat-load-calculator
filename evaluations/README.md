# Vision Lab Evaluations

Evaluation cases are compact, anonymised answer sheets for local packets. They
measure progress; they do not block normal tests or appear in the contractor UI.

## Create A Case

1. Copy `cases/small_vector_plan.json` and choose a generic `case_id`.
2. Add only 5-10 facts that are visually certain: plan pages, floor labels,
   major dimensions, and obvious regions that must never become walls.
3. Do not use generated candidate IDs. They are implementation details and may
   change between runs.
4. Keep real PDFs, screenshots, and packet folders under ignored `output/`.

## Run A Scorecard

```bash
PYTHONPATH=. python3 tools/evaluate_packet.py \
  evaluations/cases/small_vector_plan.json \
  /absolute/path/to/project-review-folder
```

The command writes JSON and Markdown reports under `output/evaluations/`.

## Starter Cases

The repository includes three report-only baselines for local test PDFs:

- `compact_tenancy_vector.json`: checks page roles and the clearly visible overall tenancy dimensions.
- `crowded_rcp_context.json`: checks a crowded layout plan and its RCP stay in their correct roles.
- `multi_floor_residential.json`: checks Ground and Lower Ground remain separate floor groups.

The cases intentionally do not assert every wall or small fixture dimension. Add a fact only after it is visibly certain.
Missing manual vision or geometry-confirmation files are shown as
`not_evaluated`, not passed or failed.
