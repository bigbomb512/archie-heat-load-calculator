# Australian ventilation rules: location-based matching

## Current implementation

The contractor enters a confirmed project address in the existing site-location
flow, then records the building-approval application date and NCC building
class in the **Australian ventilation rules** section. The selected G-NAF
jurisdiction, approval date, class, project use and saved zone uses form the
matching context. The room-use taxonomy currently limits automatic matches to
hospitality/food-service room categories.

When design requirements are saved, Archie evaluates the context against
`config/au_ventilation_rules_v1.json`. It only uses one released exact match for
jurisdiction, date, NCC edition, building class and room-use category. The
reviewed rate and citation populate blank ventilation inputs. When occupancy
and area permit it, Archie calculates the required outside-air flow and copies
it into the room cooling-load input and any matching hourly room whose flow is
blank or already rule-derived. Process exhaust and make-up-air values remain
separate.

Existing user-entered rates are preserved. If they conflict with the matching
rule, the result reports a conflict and does not replace them. Unknown location,
approval date, class or room use; a project-specific ventilation basis; no
applicable rule; or multiple matches prevents automatic application. A change
to project context, location or saved zone use causes the result to be
re-evaluated when viewed or when design inputs are saved.

## Ruleset data and release requirements

The versioned ruleset declares all eight Australian jurisdictions but is
currently a candidate pack with empty adoption histories and no ventilation
rules. Consequently, the product reports the missing reviewed coverage and
applies **no numerical defaults**. Test fixtures exercise the matching engine
using values labelled synthetic; those values are never loaded as production
rules.

Each released jurisdiction timeline needs its source, reviewer and review date.
Each released ventilation rule needs effective dates, NCC edition and clause,
AS 1668.2 edition and clause, building class, controlled room-use category,
ventilation method and rates, source/access basis, and named reviewer
credential/date. Released rules are rejected if these fields or rate
consistency checks are missing. The project currently has no authorized
AS 1668.2 numerical data or Australian HVAC engineer review to populate these
records.

Project-specific requirements currently block automatic rules for the project
until they are reconciled. There is no general precedence engine for combining
multiple independent requirements; conflicting cases remain for designer
review.

## Verification

`tests/test_au_ventilation_rules.py` exercises each jurisdiction with
test-only adoption/rule records, date boundaries, missing and ambiguous cases,
project-specific conflicts, manual-value preservation, and outside-air flow
propagation. `tests/test_au_ventilation_rules_service.py` checks local context
persistence and the current unsupported-coverage response. The Playwright
test verifies the contractor context form saves jurisdictional rule inputs and
clearly reports that no reviewed rates are currently released.
