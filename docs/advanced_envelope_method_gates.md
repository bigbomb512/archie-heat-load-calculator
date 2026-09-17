# Advanced Envelope Method Gates

The Stage 6 envelope methods are stored and fingerprinted independently from
the legacy steady-state envelope calculation.

## First-order RC thermal mass

`dynamic_thermal_mass_rc_v1` uses one hourly state node with explicit exterior
and interior resistances and areal thermal capacitance:

```text
T_next = T_state + dt/C × ((T_boundary − T_state)/R_ext + α × I)
Q_room = (T_next − T_indoor)/R_int × A
```

The surface must provide its area, both resistances, capacitance,
solar-absorptance, source and citations. The result retains the previous and
next node temperature, signed room heat flow, operands, formula, method ID and
gate version. It is not used by the hourly report until the project-local
dynamic method gate is approved and the surface is explicitly marked eligible.

## Cited surface irradiance

`cited_solar_radiation_v1` accepts a project-local, cited 24-hour surface-plane
irradiance artifact. The artifact includes the location, timezone, date,
surface orientation, calculation method, citation, and one non-negative value
for every hour 0 through 23. It does not perform a web lookup, infer a solar
position, or transform a 3D render into radiation.

The source is independently fingerprinted and included in calculator-input
and report freshness checks. It remains separate from the existing manual
solar basis until a reviewed surface explicitly selects it and the method gate
is approved.

Both gates require a named engineer, credential, approval date, method
citation, scope and supporting citations. Placeholder records are safe to
create during project bootstrap; they cannot contribute to a calculation.
