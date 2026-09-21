# Product records and reproducible packages

Stage 12 productization consumes persisted calculator artifacts; it does not
recalculate loads or reinterpret PDF evidence. The current project view exposes
health, audit history, report packages, and project export/import.

## Report packages

`POST /api/report-package` with `project_id` and `report_type` (`cooling` or
`heating`) creates a write-once package under `report_packages/<fingerprint>/`.
The package contains a self-contained `report.html`, a dependency-free print
PDF companion, a manifest, and copies of the cited JSON artifacts under
`artifacts/`. A current package requires a current report and an immutable
calculator-input snapshot. Stale reports may only be packaged explicitly as
historical records.

`GET /api/report-package` lists historical and current packages. Package
fingerprints include the report, input snapshot, and source artifact hashes, so
changed inputs create a new package without rewriting the old one.

## Audit history

Mutating artifact writes and product actions append to `audit_log.jsonl`.
Events contain fingerprints and a previous-event hash, while
`audit_log_head.json` makes a truncated tail detectable. The log contains
references and metadata only; it does not copy private PDFs, API keys, or
large page images.

## Export and import

`POST /api/project-export` creates a ZIP with `project_manifest.json`, authored
JSON/JSONL/Markdown/HTML artifacts, historical snapshots/reports/packages, and
audit history. Private PDFs, rendered images, uploads, and vision thumbnails
are excluded by default; their recorded source references remain visible.

`POST /api/project-import` accepts a base64 ZIP and always creates a new project
ID. It validates the archive version, rejects undeclared or unsafe paths, checks
every declared SHA-256 hash, and never overwrites an existing project.

`GET /api/project-health` reports missing artifacts, unavailable private
sources, interrupted draft transactions, invalid snapshots, stale reports,
and recovery actions. Product endpoints use a common actionable error shape
with `code`, `affected_artifact`, `remediation`, and `retryable` fields.

Calculator-input exceptions use deterministic IDs derived from the source
fingerprint, scope, entity, category, and field. The UI presents them in
severity order, with source page/excerpt, competing values, and remediation;
large lists are loaded in bounded pages. Stage 7 overlays will attach through
these normalized exception records after its branch is integrated.
