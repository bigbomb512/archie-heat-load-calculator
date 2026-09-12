# AI-first evidence-to-load pipeline

The evidence pipeline now has a deterministic boundary between extraction and
calculation:

```text
architect PDF + saved vision response
  -> architect_evidence_fusion.json (pages, entities, normalized facts)
  -> validation and safe fact activation
  -> calculator draft / review queue
  -> existing hourly cooling engine
```

`architect_evidence_fusion.json` is schema 2. It retains the original page and
entity graph and adds a normalized `facts` registry. Every fact carries a
stable ID, source page or reference, extraction confidence, validation status,
activation status, dependencies, and a candidate fingerprint.

Only exact, conflict-free metadata and directly explicit low-risk records may
be activated automatically. Geometry, room areas, thermal properties,
occupancy, schedules, and load bases remain proposals unless their evidence
meets the current validation rules. The pipeline never converts a detected
piece of equipment into a heat load.

The evidence API supports these actions:

- `build` or `build_ai_extraction`: rebuild the fusion artifact from current
  local evidence artifacts;
- `save_extraction`: persist validated structured facts from the saved vision
  response;
- `validate_facts`: return validation results without changing artifacts;
- `apply_safe_facts`: activate only safe facts in the fusion registry;
- `save_review`: persist review decisions with optimistic revision checking.

External facts are represented by `research_cache.json`. It is a project-local
cache of cited, reviewed records; it contains no network client and calculations
never perform live web research. Expired, rejected, out-of-scope, or merely
proposed records are not eligible inputs.

The calculator input assembler now produces immutable, content-addressed
cooling snapshots in `calculator_input_sets/`. Each snapshot resolves one
field at a time using cited project overrides, explicit PDF/fusion evidence,
supported derivations, then approved Australia-first source-pack defaults.
It preserves every source, citation, formula and exclusion and does not modify
the editable project artifacts. The existing hourly engine remains the only
load calculator; it materializes a selected snapshot in memory.

Assembly is an explicit step before calculation. Reassembling unchanged inputs
reuses the same fingerprint; changed evidence, schedules, scenarios, context,
overrides, research or gate inputs produce a new snapshot. The snapshot also
contains room coverage (`included`, `excluded`, `blocked`, `draft-only`) and a
complete-scope flag so a partial result cannot be presented as a project duty.

Defaults are limited to weather, indoor conditions, safety allowance,
occupancy/people assumptions, lighting assumptions, schedules and outside-air
basis. Geometry, boundaries, U-values, glazing, construction assemblies and
equipment heat remain non-defaultable. Unsupported airflow and moisture
components are never inferred as zero: confirmed absence is explicit, while
known or unassessed loads keep the relevant result at `draft`.

A complete AI-assembled/default-backed supported cooling scope can be
`review_ready`, but it remains visibly labelled as such and is never
`validated`. Validation is reserved for the authorised benchmark gate.

Drawing 6 remains private. The reviewed-case tool writes derived evidence to a
local output directory and never copies the source PDF into the repository.
