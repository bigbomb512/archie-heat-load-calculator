# Research Source-Pack Governance

`ai/research_cache.py` is the only path by which an external engineering fact can
become an automatic calculator input. This note states the gates it enforces.

Nothing here approves anything. The Australia-first pack `au-cooling-v1` is still
`"release_status": "candidate"`, and its two NCC 2022 Class 6 profile records remain
`review_status: proposed`, `released: false`. They are stored, visible and inert.

## The two ladders

A record is **stored** if it passes `validate_record`. A record is **usable as an
automatic default** only if it additionally passes `released_source_record` *and*
its binding target is permitted by the pack.

### Storage gates (`validate_record`, all records)

| Dimension | Rule |
|---|---|
| Identity | `record_id`, `category`, `unit`, `value` present; IDs unique per cache |
| Publisher | non-blank after strip |
| URL | parseable, scheme `https`, hostname present |
| Citation | non-blank after strip |
| Content hash | non-blank after strip |
| Retrieval date | ISO 8601 timestamp |
| Expiry | ISO 8601 timestamp, strictly **after** `retrieved_at` |
| Scope | must be an object; bindings' scopes must be objects |
| Review status | one of `proposed`, `approved`, `expired`, `rejected`; `approved` requires `reviewed_by` |

### Release gates (additional, only when `released: true`)

- `review_status` must be `approved` — a proposed record **cannot** be released.
  This is the "never silently promote" rule, enforced at validation, not at read time.
- `reviewed_by` must name a reviewer.
- `source_pack_version` must be set, must match the cache's pack, and that pack
  must exist in `config/approved_research_source_packs.json`.
- `scope.country` must be declared (geographic scope).
- Bindings on `room.*` and `schedule.*` targets must carry a building/room use
  scope (`room_use` or `building_use`), on the record or on the binding.
- `retrieved_at` may not be in the future.
- The URL host must be inside the pack's `allowed_domains`.
- The binding target must not be prohibited (see below).

### Read gates (`eligible_bindings`)

Applied on every lookup: pack exists → target permitted → record released →
not expired *at the requested instant* → scope compatible. Records broader than the
project are allowed; records that contradict it are not.

Conflicts are **not** resolved here. When two released records bind the same target
in scope, both are returned, in `record_id` order, so
`ai/calculator_inputs.py::_resolve_field` blocks the field rather than picking one.

## Permitted and prohibited targets

`released_binding_targets` in the pack is a **policy ceiling, not an approval**. Only
low-risk quantities appear there: setpoints and design wet bulb, per-person sensible
and latent gains, lighting density, diversity and safety factors, occupancy density,
outside-air rates, hourly schedule profiles, and the design-day weather profile.

`blocked_binding_targets`, unioned with the module-level `PROHIBITED_BINDING_TARGETS`,
can never be defaulted regardless of what any pack declares:

    room.area_m2, room.volume_m3, room.height_m, room.occupancy, room.use,
    room.envelope_surfaces, room.u_value_w_m2k, room.shgc,
    room.glazing_area_m2, room.orientation

These are project facts. Geometry comes from the drawings, envelope construction and
room use come from confirmed project evidence. `room.occupancy` is deliberately on the
deny list: occupancy may only be **derived** from an approved
`room.occupancy_density_per_m2` multiplied by a resolved room area, and that derivation
is recorded with its formula and operands.

The denylist wins over the allowlist. A target absent from both is not permitted.

## Adding a record

1. Add it to a pack manifest with `review_status: proposed`, `released: false`, a real
   citation, publisher, retrieval date and content hash.
2. Seed it into a private project cache with `tools/seed_au_default_pack.py`.
   It stays ineligible.
3. A named engineering release review sets `review_status: approved`, `reviewed_by`
   and `released: true`. Only then can the resolver reach it, and only for a target on
   the allowlist.

A record's presence in this repository is never evidence of engineering approval.
Only the artifact's own `review_status`, `reviewed_by` and `released` fields carry
that claim.

## Compatibility note

The storage gates apply to every record in a cache, including pre-existing ones. A
cache holding an `http://` URL, a blank publisher/citation/content hash, or an expiry
at or before its retrieval date will now fail `validate_cache` on load and must be
corrected rather than silently accepted.
