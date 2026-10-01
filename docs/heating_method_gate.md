# Heating method gate — hourly room heating V1

This document describes the separate, approval-gated heating calculation path. It is not a claim of benchmark validation or CAMEL+ equivalence.

Heating V1 reuses the reviewed room topology, envelope, glazing, schedule, outside-air, and infiltration inputs already used by the cooling workflow. A project-local `heating_method_gate.json` is required. A placeholder gate permits development/draft results only; an approved gate requires a named HVAC engineer, credential, approval date, method citation, scope, and supporting citations.

Supported components are positive sensible heating demand from opaque-envelope conduction, reviewed glazing conduction, outside air, approved infiltration, and explicitly cited sensible internal-gain credits. Credits are capped at the room's gross sensible heat loss and are never allowed to make demand negative. In legacy mode, a separately cited room heating safety factor is applied once after credits at room-hour level. With a cited engineer-approved project policy, room factors are retained as evidence, neutralized in the room calculations, and the policy is applied once to the coincident project peak. A factor above 1.0 blocks only if it survives into calculated room hours, where it would actually compound with the project policy. Solar gains, latent/humidification credits, AHU, plant, annual analysis, and benchmark validation remain outside this slice.

Heating room checks require a sourced outside-air input with a non-missing review status. Provisional room, outside-air, or calculated infiltration evidence keeps the result draft-only and suppresses a complete project peak. Heating uses the shared room-level infiltration eligibility checks: a single declared path, assessed and calculated input, approved gate eligibility, a dedicated schedule distinct from outside air, and reviewed room volume inputs when the unit is ACH. Missing volume or other per-hour calculation errors block only the affected room; other rooms remain reportable.

The current implementation uses `infiltration_method_gate.json` (whose documented scope is cooling infiltration) as an eligibility check for heating infiltration. This records current software behavior; it does not decide that cooling approval covers heating. Whether heating needs a separate infiltration approval scope remains an open owner/engineering decision. No separate heating infiltration gate has been created.

The AI-preliminary workflow uses a separate provisional 1.10 fallback once and remains draft-only. It is not an approved project policy. The preliminary workflow currently persists this artifact; whether a later strict report should ignore that fallback and use legacy room factors or remain blocked is unresolved and must not be inferred from this document.

Winter scenarios require 24 cited outdoor dry-bulb and wet-bulb values plus cited atmospheric pressure. Room heating applicability, setpoint, source, and schedule assignment remain room-specific; no value is copied from another room and no heating default is used. Missing or stale inputs fail closed and remain visible as blockers or included-scope draft exclusions.

`tests/fixtures/analytical_heating_benchmarks_v1.json` and
`tests/test_analytical_heating_benchmarks.py` pin simple steady-state opaque
transmission, sensible outside-air heating, scheduled ACH infiltration, and
the scheduled sensible internal-gain credit/cap arithmetic, including
flow-direction and zero-flow checks. The cases use the stated equations and
explicitly declared flow references; they test arithmetic and software
behavior only. The internal-gain case verifies Archie V1's documented policy,
not whether that policy is suitable for a specific design.
They do not validate the heating method, weather selection, project airflow
reference, safety factor, or an Australian design method, and they do not
change the approval gate.
