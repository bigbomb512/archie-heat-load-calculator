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

## Portfolio pipeline scorecard

To measure persisted coverage across projects without creating or changing
project artifacts, pass one or more review folders to the portfolio command.
By default, it assigns pseudonymous IDs by hashing each folder's final name,
so argument order does not affect project matching. These hashes are not
anonymous: someone who knows a folder or client name can test guesses against
them. New uploads usually have timestamped folder names, so their hash changes
between runs. Use an explicit neutral label to compare uploads of the same
project under one stable ID:

    PYTHONPATH=. python3 tools/evaluate_portfolio.py \
      /absolute/path/to/project-review-folder \
      --label /absolute/path/to/project-review-folder=caseA

Reuse `caseA` for later folders from that same project. Labels must be unique
within one run and can themselves disclose identity; use neutral labels such
as `caseA`, never client names. Reports omit folder names and absolute paths.
The command writes aggregate JSON and Markdown reports to the ignored
output/evaluations/ directory.

    PYTHONPATH=. python3 tools/evaluate_portfolio.py \
      /absolute/path/to/project-review-folder-1 \
      /absolute/path/to/project-review-folder-2

An optional verified case may be assigned to a project ID:

    PYTHONPATH=. python3 tools/evaluate_portfolio.py \
      /absolute/path/to/project-review-folder \
      --case project-a1b2c3d4e5f6=evaluations/cases/small_vector_plan.json

Compare a new report with a saved report using --compare
/path/to/previous-portfolio.json. The comparison reports stages and numeric
counts that improved, worsened or changed; comparing an identical scorecard
produces no changes.

Pipeline stage state means:

- present: a readable artifact exists and has a supported schema.
- absent: the artifact does not exist and is not waiting on a human gate.
- stale: a recorded upstream fingerprint differs from the available upstream
  artifact.
- gated: the artifact is absent because its human prerequisite, currently
  page review, has not been completed.
- error: an artifact is malformed or declares an unsupported schema.

Each stage also reports fingerprint freshness. The scorecard compares only
producer-compatible fingerprints; when it cannot compute the same canonical
fingerprint as the producer, freshness is unknown. It never treats an
artifact's upstream source fingerprint as that artifact's own identity. A
calculator-input pointer is followed to its named snapshot and checked for
matching fingerprint, schema and source fingerprints; without a pointer, the
scorecard does not guess which historical snapshot is current. It reads saved
statuses, input coverage and blocker reasons.
It does not run pipeline producers, calculate loads, or complete human review.
The scorecard measures artifact coverage and workflow progress only. It is not
an accuracy score, benchmark result, engineering approval or design validation.

Comparison labels count changes as improved or worsened only for metrics with
an explicit direction (for example, fewer blockers is better); other count
changes are neutral. A changed project-ID set is listed in the report and
printed as a warning.

The portfolio report uses aggregate counts and pseudonymous IDs; do not
add client names, drawing content or file paths to committed evaluation
records. Do not create answer-sheet facts until a person verifies them.
The drawing-coverage stage also reports saved level-candidate counts by kind
and page level-status counts. These describe what the classifier recorded;
they do not measure whether its classifications are correct.

## Page-role scorecard (architect sets)

`evaluations/page_roles/caseP01-14.json` hold a few page facts per permitted architect set:
the main plan page(s), ceiling plans (RCPs), pages that must not be discarded, and pages that must
never be chosen as the main plan. Sheets marked `"confirmed": false` are drafts awaiting the
user's check. The PDFs stay outside the repository; map each case to its PDF in the ignored
`output/evaluations/page_role_sources.json` (`{"caseP03": "/absolute/path.pdf", ...}`).

    PYTHONPATH=. python3 tools/evaluate_page_roles.py            # full analysis per set (minutes)
    PYTHONPATH=. python3 tools/evaluate_page_roles.py --fast     # page finder only, on cached text (seconds)
    PYTHONPATH=. python3 tools/evaluate_page_roles.py --fast --compare output/evaluations/page-roles-<time>.json

`--fast` caches each PDF's page text and visual features under `output/evaluations/page_role_cache/`
and gives the same page groups as the full analysis. `--compare` lists every fact that was fixed or
regressed since an earlier report. Baseline on 2026-10-07: 135/157 facts (main plan right in 6 of 14
sets, RCPs 9 of 16); after the printed-view-title rules: 157/157.

## Pass 1: AI reads every page (`tools/evaluate_page_inventory.py`)

Runs pass 1 of the PDF review on page-role sets and scores it beside the code's page finder, against the same
answer sheets. Every page is sent on its own to the AI through the Codex CLI signed in with ChatGPT (no API key).
Page images and replies are cached under the ignored `output/evaluations/page_inventory/<case>/`, so a re-run only
calls the AI for pages not yet read.

    python3 tools/evaluate_page_inventory.py --case caseP04 --case caseP06

Answer sheets can also contain an optional `information` object. Its string page-number keys cover every
page in the set; each value is a list of kinds from `ai.page_inventory.INFORMATION_KINDS`. An empty list
means a person checked that page and found no heat-load information. The `information_status` records the
provenance:

- `not_started`: no person-confirmed information key yet.
- `drafted_from_ai`: the AI's list was copied in, but the person has not checked every page.
- `confirmed_blind`: the person labelled every page without filling from the AI first. This is the honest
  measure of pass 1 accuracy.
- `confirmed_from_draft`: the person checked every page after seeing the AI's list. These labels are
  useful for coverage, but they are not independent; scoring them alongside the AI draft likely overstates
  accuracy. Reports show these sets separately from blind confirmations.

The page-role check page includes a “What's on each page” section with the 11 information kinds. A page is
complete when at least one kind is selected or “Nothing for heat load” is checked. “Fill in from the AI's
reading” is optional and off by default. `python3 tools/page_role_check.py --apply` writes confirmed page
contents to the same answer sheets. Page-role facts are unchanged.

The page-inventory report scores information-list recall and precision per kind, per set, and overall, and
lists pages with missed or added kinds. AI pages that could not be read count as misses. Unconfirmed
information keys are not scored.
