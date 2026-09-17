# Controlled geometric shading method gate

This V1 method can replace a reviewed glazing surface's manual external
shading factor only after a named HVAC engineer approves the project-local
`shading_method_gate.json`. It uses a cited hourly incident-solar basis and
cited hourly sun vectors; it does not source weather radiation or solar
positions itself.

Each reviewed record links to one confirmed glazing opening and supplies a
cardinal façade orientation, opening width and height, at least one confirmed
overhang/fin/reveal dimension or obstruction altitude, and 24 cited sun
positions. The result is a bounded external shading factor from 0 to 1.
When geometric shading is eligible, it **replaces** the manual external factor;
otherwise the reviewed manual factor remains the fallback. The two are never
multiplied in V1.

The method deliberately excludes diffuse-sky separation, automatic solar
position, 3D-derived dimensions, movable/dynamic shading, annual analysis,
and any uncited geometry. It is not a validation or CAMEL+ equivalence claim.
