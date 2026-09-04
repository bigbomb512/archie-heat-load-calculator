# DA09/CAMEL+ benchmark cases

Store authorised benchmark cases under `output/benchmark_cases/<case-id>/`; `output/` is ignored by Git.

Required layout:

- `benchmark_case.json` — normalised case, input reconciliation, source references and reference results.
- `source/` — drawings, authorised DA09 references/excerpts and assumptions register.
- `archie/` — reviewed evidence, thermal model and Archie results.
- `reference/` — authorised CAMEL+ input/export and result reports.
- `reports/` — generated `parity_report.json` and readable report.

Do not commit licensed DA09/CAMEL+ materials. Mark every mapped input `matched`, `archie_only`, `camel_only` or `unresolved`. A parity report never permits a final-parity claim until authorised material is present, the input reconciliation is complete, and tolerances have been set from the reconciled baseline.
