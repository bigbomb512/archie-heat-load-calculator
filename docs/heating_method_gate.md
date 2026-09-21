# Heating method gate — hourly room heating V1

This document describes the separate, approval-gated heating calculation path. It is not a claim of benchmark validation or CAMEL+ equivalence.

Heating V1 reuses the reviewed room topology, envelope, glazing, schedule, outside-air, and infiltration inputs already used by the cooling workflow. A project-local `heating_method_gate.json` is required. A placeholder gate permits development/draft results only; an approved gate requires a named HVAC engineer, credential, approval date, method citation, scope, and supporting citations.

Supported components are positive sensible heating demand from opaque-envelope conduction, reviewed glazing conduction, outside air, approved infiltration, and explicitly cited sensible internal-gain credits. Credits are capped at the room's gross sensible heat loss and are never allowed to make demand negative. A separately cited room heating safety factor is applied once, after credits, at room-hour level. Solar gains, latent/humidification credits, AHU, plant, annual analysis, and benchmark validation remain outside this slice.

Winter scenarios require 24 cited outdoor dry-bulb and wet-bulb values plus cited atmospheric pressure. Room heating applicability, setpoint, source, and schedule assignment remain room-specific; no value is copied from another room and no heating default is used. Missing or stale inputs fail closed and remain visible as blockers or included-scope draft exclusions.
