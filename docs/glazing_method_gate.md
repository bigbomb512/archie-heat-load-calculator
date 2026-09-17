# Standalone reviewed glazing method gate

This document defines the first reviewed glazing calculation slice. It enters
hourly cooling only when the project-local `glazing_method_gate.json` is
approved by a named HVAC engineer. It does not claim CAMEL+ equivalence or
engineering validation.

## Required reviewed inputs

Each opening must have a unique plan/elevation-to-surface relationship, owning
room and zone, positive width/height/quantity or an explicit **opening area**
for conduction, and a supported boundary method. Explicit glass area remains a
separate solar input; it never silently becomes opening area. The linked
window record must have a cited **overall-window** U-value, exactly one cited
SHGC or solar-transmission factor, a frame fraction when glass area is
derived, a glass-area correction factor, and an internal-shading factor. The
manual solar record must include a cited incident solar value in W/m² before
window/shading factors, a cited external shading factor, and a complete
assigned 24-hour profile when non-zero.

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

This slice does not calculate solar position, orientation-based radiation,
overhangs, fins, reveals, adjacent obstructions, dynamic shading, annual
glazing behavior, heating, AHU, or plant effects. Incomplete glazing remains
explicitly excluded and can only produce an included-scope draft subtotal.

Opaque surfaces are reviewed as net opaque area, or as gross area minus every
linked confirmed opening when the evidence confirms coverage is complete. This
prevents wall/window double counting.

## Integration gate

Before a project reaches a complete result, it must approve the method gate,
reconcile opening ownership and boundaries, and provide cited properties and
manual solar inputs. Historical reports retain their original snapshots.
