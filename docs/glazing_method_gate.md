# Standalone reviewed glazing method gate

This document defines the first glazing calculation slice. It is calculation-capable for isolated tests only and is **inactive in hourly reports** until the reviewed-envelope integration gate is approved. It does not claim CAMEL+ equivalence or engineering validation.

## Required reviewed inputs

Each opening must have a unique plan/elevation-to-surface relationship, owning room and zone, positive width/height/quantity or an explicit glass area, a supported boundary method and reviewed boundary temperature basis. The linked window record must have a cited U-value, exactly one cited SHGC or solar-transmission factor, a frame fraction when glass area is derived, a glass-area correction factor, and an internal-shading factor. The manual solar record must include a cited incident solar value in W/m² and a cited external shading factor.

No geometry, U-value, glazing property, boundary temperature, or shading value is defaulted. Ambiguous mappings, 3D-only dimensions, missing citations, incomplete solar inputs, and stored-only records return a blocked result.

## Supported equations

```text
opening_area = width × height × quantity
glass_area = explicit glass area
             or opening_area × (1 − frame fraction)
corrected_glass_area = glass_area × glass-area correction factor
conduction_kW = U-value × opening area
                × (boundary temperature − indoor temperature) ÷ 1000
solar_kW = incident solar W/m² × corrected glass area ×
           solar-transmission factor × external shading factor ×
           internal shading factor ÷ 1000
```

Conduction is retained as a signed diagnostic. Solar transmission is a positive gain. The result includes the resolved areas, operands, formulas, source citations, and unresolved requirements.

## Deliberate exclusions

This slice does not calculate solar position, orientation-based radiation, overhangs, fins, reveals, adjacent obstructions, dynamic shading, annual glazing behavior, or hourly cooling reports. Existing `envelope.py` normalization continues to report glazing as `stored_not_calculated`; the standalone module is not imported by `hourly_loads.py` or `heat_loads.py`.

## Integration gate

Before activation, the project must approve the reviewed envelope adapter, reconcile opening ownership and boundaries, provide cited properties and manual solar inputs, add independent benchmark cases, and verify that historical reports remain unchanged. Only then may a separate integration change connect this method to hourly cooling.
