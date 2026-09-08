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

The calculator input assembler produces a deterministic readiness record. It
does not calculate loads; the existing hourly engine remains the only load
calculator. A complete project duty is still withheld when active rooms have
missing topology, schedules, supported gains, or current envelope inputs.

Drawing 6 remains private. The reviewed-case tool writes derived evidence to a
local output directory and never copies the source PDF into the repository.
