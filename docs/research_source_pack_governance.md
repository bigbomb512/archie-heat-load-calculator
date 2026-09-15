# Research Source-Pack Governance

External research is stored separately from project evidence. Storing a cited
record does not authorise Archie to use it in a calculation.

## Candidate versus released default

Candidate records are reviewed research inputs. They remain visible in the
project cache but are inert. A candidate becomes an automatic default only when
all of the following are true:

1. Its source-pack version exists and its URL is allowlisted.
2. The record is cited, approved, and unexpired.
3. A current entry in `research_source_pack_releases.json` names the same pack,
   record ID, and exact content hash.
4. That entry is marked `released`, names a qualified HVAC engineer and
   credential, and matches the project scope.

The manifest is the release authority. A cache record's legacy `released` flag
cannot by itself authorise a calculation input.

## Scope and safety

Automatic candidates are curated through `validate_candidate_record` and are
limited to low-risk cooling defaults: weather, indoor conditions, safety
allowance, people/lighting assumptions, outside-air basis, and complete
day-type profiles. The calculator-input resolver independently prevents
defaults from supplying geometry, thermal boundaries, constructions, U-values,
glazing, solar/shading, or actual equipment heat-to-space values.

If a project PDF and a released default disagree, the explicit project evidence
wins. Equal-authority project facts that disagree remain blocked for resolution.
Every applied default records the pack version, release, citation, scope, and
content hash in the immutable input snapshot.

## Curation workflow

Use `tools/seed_au_default_pack.py --check` to validate a candidate pack before
copying it into a private project cache. It rejects uncited, unallowlisted,
expired, incomplete, or high-risk candidates and never marks a record approved
or released. A qualified HVAC engineer must create the separate release-manifest
entry for real calculation use.

Passing a unit test or adding a candidate record is development evidence only;
neither is engineering approval.
