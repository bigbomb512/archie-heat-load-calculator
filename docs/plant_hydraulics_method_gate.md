# Plant and hydraulic calculation method gate

Stage 10 aggregates a current hourly AHU report into explicit cooling plant
duty. It supports reviewed chiller records and chilled-water circuits first;
boilers, heating-water circuits, refrigerant circuits, and package/unitary
records are stored and reported as deferred or excluded from the central
cooling total.

Plant ownership is explicit:

```text
AHU → chilled-water circuit → chiller
```

The report uses coincident AHU coil duty at the same scenario hour. It applies
one explicit plant diversity factor, then adds cited pump power and signed pipe
effects. Equipment number-off multiplies only records explicitly marked
`representative_per_unit`; combined equipment is not multiplied implicitly.

Pump power is entered as reviewed kW. Pipe gains/losses are entered as reviewed
signed kW. No head/efficiency pump model, pipe heat-transfer model, annual
hydraulics, automatic equipment selection, or plant sizing recommendation is
included in V1.

The project-local plant gate requires a named engineer, credential, approval
date, method citation, scope, and supporting citations. A placeholder gate
allows draft calculations only. Missing mappings, stale AHU reports, invalid
circuits, incomplete pump/pipe inputs, and unapproved methods fail closed or
produce an included-scope subtotal. Passing synthetic tests is not engineering
approval or benchmark validation.
