# Archie Product Plan

## End goal

The long-term aspiration is a fully autonomous HVAC design assistant. Archie should take varied project evidence—drawings, schedules, specifications, site conditions and project decisions—and turn it into traceable load calculations and broader HVAC design outputs, automating each scope only after it has demonstrated reliable performance across representative work.

For every output, Archie should show where its inputs came from, what assumptions and methods it used, what remains uncertain, and what is outside its supported scope. It should calculate only within methods that have been implemented and appropriately checked. It should guide users to resolve gaps instead of silently filling them. Earn autonomy through performance evidence across varied real projects, confidence assessment, and contractor and engineering feedback; keep required engineering approvals explicit rather than simulating them.

The goal is defined by the product's capability across project types, not by completing one PDF or reaching a particular drawing-specific result.

## Product capabilities to build

1. **Understand project evidence.** Ingest multiple drawing sets and supporting documents; identify pages, spaces, systems, dimensions and schedules; link extracted facts back to page-level evidence; and expose ambiguity for review.
2. **Build a coherent project model.** Connect sites, buildings, floors, zones, rooms, envelope elements, air paths and HVAC systems while preserving provenance, ownership and unresolved conflicts.
3. **Calculate supported design quantities.** Provide room cooling and heating first, then validated air-side and plant calculations. Keep methods, boundaries and approval states explicit; do not present unsupported calculations as final design values.
4. **Validate across varied work.** Use independent analytical cases and a representative, permissioned set of project cases. Track component errors, missing evidence, failure modes and changes in coverage. A single drawing is one regression case, not proof of general capability.
5. **Make the workflow usable and auditable.** Help users move from evidence review to a complete calculation package, explain what needs attention, retain decisions and versions, and make draft, blocked and review-ready states unmistakable.
6. **Expand automation responsibly.** Automate repetitive evidence handling and well-tested decisions first. Increase the scope of automated conclusions only when representative evaluations support it and users can inspect and correct the result.

## Delivery approach

- Work from capability gaps and user impact, not from a single project's checklist.
- Keep a portfolio of diverse test projects: different building uses, drawing conventions, scales, document quality, room layouts and system types. Include the Butcher Buffet Melrose Park / Drawing 6 material only where it provides a useful, permitted regression case.
- Keep analytical benchmarks separate from project examples and from engineer approval. Record source, version, units, assumptions, exclusions and tolerances for every reference case.
- Prefer complete vertical slices: evidence through model, calculation, report and verification. Do not mistake a new form, schema, field or method gate for a completed capability.
- Keep unsupported or unverified scope visible. No synthetic fixture, passing software test or AI confidence score constitutes engineering validation by itself.
- Update the roadmap and TODO when evidence changes priorities. A case-specific task may be next, but it does not become the product's definition of done.

## Definition of product progress

Progress is demonstrated when Archie can handle a broader and more varied set of real project situations with fewer unsupported assumptions, more complete evidence traceability, more dependable calculation results, clearer unresolved items and a more usable review workflow. Report these measures by scope and test set; do not collapse them into a claim of universal accuracy or autonomy.

The detailed heat-load calculation scope and its current implementation status are tracked in [`cool_heat_load_roadmap.md`](cool_heat_load_roadmap.md). Immediate actionable work is tracked in [`TODO.md`](../TODO.md), and the repeatable accuracy process is described in [`ACCURACY_WORK_LOOP.md`](ACCURACY_WORK_LOOP.md).
