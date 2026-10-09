# Subskill: air_path_reconciliation

- Trace supply, return, outdoor, exhaust, relief, make-up, transfer, and
  leakage paths through schematic/plan, keeping direction and ownership.
- Normalize units only when exact unit and source are available; compare
  central AHU flow with room-level airflow to detect duplication.
- Do not infer missing flow from duct width or fan size; keep unbalanced
  paths as explicit issues for deterministic balance checks.
