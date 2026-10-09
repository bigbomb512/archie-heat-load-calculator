# Subskill: coil_duty

- Assemble references to validated coincident room/zone load, airflow,
  system topology, and coil state points; check that each required operand
  exists and belongs to the same scenario/hour.
- Never add independent room peaks or calculate new coil physics. Existing
  Stage 9 owns the equations and produces the report.
- Return eligibility and missing inputs only; do not write reviewed AHU
  systems or air-side artifacts.
