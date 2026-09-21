# DA09/CAMEL+ benchmark cases

Store authorised benchmark cases under `output/benchmark_cases/<case-id>/`; `output/` is ignored by Git.

Required layout:

- `benchmark_case.json` — normalised case, input reconciliation, source references and reference results.
- `source/` — drawings, authorised DA09 references/excerpts and assumptions register.
- `archie/` — reviewed evidence, thermal model and Archie results.
- `reference/` — authorised CAMEL+ input/export and result reports.
- `reports/` — generated `parity_report.json` and readable report.

Do not commit licensed DA09/CAMEL+ materials. Mark every mapped input `matched`, `archie_only`, `camel_only` or `unresolved`. A parity report never permits a final-parity claim until authorised material is present, the input reconciliation is complete, and tolerances have been set from the reconciled baseline.

## Benchmark-result reporting (roadmap 7.4)

The existing `POST /api/parity-report` action now writes four files under the
case's `reports/` directory:

| File | Purpose |
| --- | --- |
| `parity_report.json` | Existing machine-readable comparison results |
| `parity_report.md` | Readable Markdown report |
| `parity_report.html` | Standalone, printable browser report with no external assets |
| `parity_report.csv` | Totals/component table for spreadsheet analysis |

Both `GET /api/parity-report?project_id=...` and the POST response provide
`report_url`, `markdown_url`, `html_url`, and `csv_url`. Existing cases gain the
new formats when their comparison is rebuilt. A GET does not generate files;
missing format links remain empty. The existing API `status` indicates report
currency separately from the comparison result's status. Regenerate stale
reports before using their exports.

The human-readable reports include:

- Comparison status and the existing final-parity flag.
- Missing reference material, unresolved inputs, and unmapped input families.
- Comparison counts and reference/Archie peak month and hour.
- The supplied tolerance policy, without interpreting it as approval.
- Source-file references, authorisation declarations, and input reconciliation.
- Available sensible, latent, subtotal, and design totals plus component rows.
- Both missing-side entities and components that cannot be compared.

Source declarations are reported as supplied; rendering does not authenticate
licensed material or create engineering approval. The existing comparator
currently supplies room and zone comparisons. The presentation layer can show
floor/project comparison records when supplied, but does not generate them or
sum independent peaks. Completing those comparisons belongs to roadmap 7.2.

Difference means Archie minus reference. Missing values are displayed as
`Not provided` in the readable formats and empty cells in CSV. Zero is retained
as zero; a zero reference has no percentage difference. Presentation follows
the existing difference precision (four decimals in kW, three in percent).
It does not introduce tolerance or rounding-policy configuration (roadmap 7.3).

CSV is the numerical table; JSON and the readable reports retain the full
comparison context. CSV text fields are neutralized against spreadsheet-formula
execution. HTML/Markdown source text is escaped.

### Export a previously saved comparison

From the repository root:

```sh
PYTHONPATH=. python3 tools/export_benchmark_report.py \
  --report /private/case/reports/parity_report.json \
  --output-dir /private/case/reports
```

This writes the three presentation formats without running a calculation or
changing the source JSON, project inputs, tolerance policy, acceptance state,
or validation status. It replaces prior presentation exports at that explicit
destination. A printable HTML report is not an immutable issued PDF package;
that separate product capability remains roadmap 12.3.

Tests use synthetic comparisons only:

```sh
python3 -m unittest tests.test_benchmark_reporting
PYTHONPATH=. python3 tests/test_parity_harness.py
```

## Explicit benchmark acceptance (roadmap 7.5)

The benchmark report can now reach `status: "validated"` only after an explicit
acceptance action. No real reference fixture or engineer approval is shipped.
Tests contain synthetic reviewer declarations, which authorize nothing outside
the tests.

### Scope

Acceptance covers **the exact room/zone peak comparison** identified by the
case, results, material hashes, hourly-report fingerprint, and code fingerprint.
It is not general CAMEL+/DA09 certification, floor/project numerical validation,
all-scenario/hourly parity, or approval of another project. The authoritative
hourly report retains its existing readiness status; the accepted benchmark
report carries `validated` and its explicit `validation.scope`. Expanding
comparison coverage is still roadmap 7.2.

### Prerequisites

- A current `review_ready` hourly report with a complete project peak, no blocked
  reasons, and no explicit partial-scope flag. Recalculate legacy reports first:
  new hourly reports record `calculation_engine_fingerprint`.
- Actual comparison results equal the adapter output of that current report.
  Client-supplied results cannot substitute for it at acceptance.
- All required input mappings are present and `matched`; governing month/hour
  agree; complete uniquely identified room and zone sets match.
- Required reference files exist inside the benchmark directory. The gate hashes
  `camel_input_or_export`, `camel_results`, `da09_basis`, and `assumption_register`.
  Relative traversal, absolute paths outside that directory, and symlinks to
  outside files are rejected. Existing authorization declarations must be set.
- The supplied `comparison_policy` has `tolerance_status: "approved"`, finite
  non-negative `component_tolerance_percent`, `total_tolerance_percent`, and
  `absolute_tolerance_kw`, plus a nonempty `rounding_policy` rationale.
- Sensible, latent, total, and design totals are supplied on each side. All
  supported components are supplied, or explicitly listed in
  `excluded_components` with no nonzero contribution on either side.

No tolerance defaults are created. The gate checks supplied numbers before
presentation rounding, using:

```text
abs(actual − reference) <= max(absolute_tolerance_kw,
                              abs(reference) × tolerance_percent / 100)
```

The absolute tolerance handles a zero reference. The rationale describes the
reviewed input/export precision; this gate does not implement a configurable
rounding engine. These prerequisites consume an engineer-supplied policy; they
do not complete roadmap 7.3's broader policy configuration workflow.

### API workflow

1. Build using the existing `POST /api/parity-report`, optionally specifying
   `"action": "build"`. Inspect `report.validation.issues`,
   `eligible_for_acceptance`, and `binding_fingerprint`.
2. After review, repeat the POST with the same case and:

```json
{
  "action": "accept",
  "expected_binding_fingerprint": "copy the fingerprint reviewed in step 1",
  "reviewer": {
    "engineer_name": "actual reviewing engineer",
    "credential": "actual credential",
    "rationale": "document the actual benchmark review and accepted scope"
  }
}
```

Include the existing `project_id` and `benchmark_case` fields. The server rejects
missing/stale bindings and unmet prerequisites before writing an acceptance.
It records its own acceptance timestamp. This is a named-reviewer declaration
under the application's existing local trust model, not identity or credential
verification; users must not enter fabricated reviewer details.

3. To revoke, use `"action": "revoke"` with the current binding fingerprint and
   reviewer fields, placing the reason in `rationale`. Revocation does not require
   the benchmark still to be eligible. A new acceptance always needs a fresh
   eligible review.

The current decision is stored in `benchmark_acceptance.json`; content-addressed
records in `acceptance_history/` retain acceptance/revocation history. Project
input artifacts, physics, reference files, and the hourly report are not altered
by accepting or revoking a benchmark.

### Staleness and reports

Every benchmark GET re-evaluates its saved comparison against current reference
files, case/policy, calculation code, current hourly report, and acceptance.
Changed evidence cannot retain live `validated` status. Revoked/stale acceptance
suppresses download links until the report is rebuilt; GET itself writes nothing.
Previously exported documents remain historical snapshots and must be checked
against the API's current status. Rebuilding regenerates exports with current
validation metadata. The 7.4 HTML/Markdown exports now include the acceptance
scope, binding, reviewer information, and blockers.

No calculation test, `provided` source flag, `released` flag, or user-supplied
`validated` status bypasses this action. With no acceptance record, an eligible
comparison remains `baseline_compared` and `final_parity_allowed` stays false.

```sh
python3 -m unittest tests.test_benchmark_acceptance tests.test_benchmark_reporting
```
