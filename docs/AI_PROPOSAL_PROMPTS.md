# Card P — AI proposals for reviewer declarations (design, 2026-10-04)

Goal: the AI proposes the values a reviewer now declares by hand: the site,
north, wall boundaries and storefront glazing. Each proposal pre-fills the
existing declaration screen; **a person confirms or edits it before it is
saved**. Nothing the AI returns enters a calculation directly.

Ground rules (user decisions):
- Small, targeted prompts, one task per prompt, with a hard size budget. If the
  evidence would not fit, block with a reason instead of trimming silently
  (decision 2026-10-02).
- Transport for now: the manual ChatGPT route (copy prompt plus attached crops
  into ChatGPT on the user's computer, paste the JSON reply back). Later a
  hosted model replaces only the transport; prompts, validation and review stay
  the same. Contractors never need their own ChatGPT.
- Block, don't guess: every task has an explicit "can't tell" answer, and that
  answer is a valid, expected outcome.

Each task uses the existing subskill format (`config/archie_subskills_v1.json`:
`task`, `inputs`, `proposal_fields`, `constraints`, `handoff`, `failure`).

---

## P1 `site_identification` (text only)

**Fills:** "Confirm project location" panel (site address/name field and the
site-name clue list). It does not set coordinates; position still comes from a
cited map or G-NAF.

**Inputs (budget ≤ 4,000 characters, no images):** the title-block and notes
excerpts that contain address-like or tenancy-like text, each tagged with page
number. Use the excerpts `ai/site_location_resolution.infer_pdf_context`
already finds: address candidates, excluded addresses and site-name candidates.
Never send the whole text layer.

**Prompt:**
> Here are short excerpts from the title blocks and notes of one drawing set,
> each with its page number. Decide which excerpt names the **project site**
> (a street address, or a tenancy in a named centre or building), and which
> addresses belong to **consultants** (architect, engineer, builder offices).
> Use only the excerpts given. Do not add a suburb, postcode or state that is
> not printed. If no excerpt names the site, return `"site": null`.
> Reply with JSON only:
> `{"site": {"text": string, "page": int, "kind": "street_address"|"tenancy_in_centre"} | null,
>   "consultant_addresses": [{"text": string, "page": int, "why": string}],
>   "notes": string}`

**Validation:** `site.text` must be a substring of a supplied excerpt on that
page. Reject an unprinted suburb, postcode or state. Kinds are restricted to
the enum.

**Review:** the proposal appears as the top site clue with a "Use this" button
that fills the confirmed-site field. The reviewer still saves it.

**Answer key (Butcher Buffet):** site = "TENANCY MZ01,M38, MELROSE CENTRAL"
(tenancy_in_centre, p. 20 or any sheet); consultant = "211-223 Pacific Hwy …
North Sydney NSW 2060". The rule-based reader already gets this right, so P1
must not do worse.

---

## P2 `north_arrow` (one image crop per plan page)

**Fills:** "North for this plan page" (Card K `declare_north`), as a proposed
plan-up bearing plus the two arrow points.

**Inputs (budget: ≤ 3 crops per page, each ≤ 1,024 px on the long side):**
crops of the plan page's title block and corners, where north arrows usually
are, at a known scale back to the full-resolution page image. Also the page's
text items near the crop that read "N", "NORTH" or "TN".

**Prompt:**
> These images are crops of one architectural plan page. Find the **north
> arrow** (a symbol that shows which way north is on the drawing; it may be an
> arrow, a needle in a circle, or a letter N with a pointer). Give the pixel
> position of the arrow's **tail** and **tip** in the crop where you see it.
> If there is no north arrow, or you cannot tell which end is the tip, return
> `"found": false`. Do not use the sheet orientation, text direction or
> building shape to guess north.
> Reply with JSON only:
> `{"found": bool, "crop_index": int|null, "tail_px": [x, y]|null, "tip_px": [x, y]|null,
>   "labelled_north": bool, "description": string}`

**Validation:** points inside the crop and distinct. The server maps the crop
pixels back to page image pixels, then calls
`reviewer_room_geometry.page_up_bearing_from_north_arrow`. An unlabelled symbol
(`labelled_north: false`) is shown with a warning.

**Review:** north panel shows "Proposed: plan-up bearing N° from the arrow on
the title block (unlabelled symbol)", with the arrow drawn on the page. The
reviewer clicks Save page north or marks it themselves.

**Answer key (Butcher Buffet, p. 20):** circle with a single needle pointing up
the sheet, in the title block, no "N" label → bearing 0°, `labelled_north: false`.

---

## P3 `boundary_classification` (one traced room per prompt)

**Fills:** "Walls and roof" panel edge classes (Card J `classify_envelope`), as
proposed values. **Roof exposure is never proposed from a plan.**

**Inputs (budget: 1 crop ≤ 1,536 px, ≤ 1,500 characters):** a crop of the plan
around the traced room, with the trace drawn and each edge **numbered** at its
midpoint. Also the edge list (number, length in m), the room name, and the
other traced rooms' names and their shared edges if known.

**Prompt:**
> The image shows one room on an architectural floor plan. Its traced outline
> is drawn with numbered edges. For each edge, say what is on the **other side**
> of that wall as drawn: `external` (outside the building: street, mall,
> laneway or open air), `adjacent_tenancy` (another shop or tenancy),
> `internal` (another room of this same tenancy), or `unknown`. Base each
> answer on what is drawn or written near that edge (for example "NEIGHBOURING
> TENANCY", a shopfront line, a boundary or fire-rated wall note). If nothing
> on the drawing tells you, answer `unknown`. Do not guess from the room's
> position on the sheet.
> Reply with JSON only:
> `{"edges": [{"index": int, "boundary": "external"|"adjacent_tenancy"|"internal"|"unknown",
>   "evidence": string}], "notes": string}`

**Validation:** exactly the traced edge indices; enum values; `evidence` must
be non-empty for any non-unknown answer.

**Review:** edges show the proposed class with its evidence text and a "from
AI" marker. The reviewer accepts all, edits, or clears. Accepted values save
through `classify_envelope` with the reviewer's name, never the AI's.

**Answer key (Butcher Buffet, Shop on p. 20):** edge 11 (11.97 m, bottom) is
the storefront and plausibly `external` *or* facing an indoor mall. The plan
alone may not settle that, so `unknown` with "storefront line" is an
acceptable answer, while `external` with no evidence is not. Expected
behaviour: few confident answers, many `unknown`.

**Why roof is excluded:** whether a roof is exposed needs a section, the
building's other levels, or the client. Melrose Central (Butcher Buffet's
centre) is apartments above a retail precinct; the user confirmed on
2026-10-04 that Shop G38's roof is not exposed. A plan crop could not have told
that.

---

## P4 `storefront_openings` (one elevation crop per prompt)

**Fills:** "Shopfront openings" on a chosen external edge (Card K openings), as
proposed width, sill and head. The reviewer still chooses the edge.

**Inputs (budget: 1 crop ≤ 2,048 px, ≤ 2,000 characters):** the elevation
view crop (e.g. "STOREFRONT ELEVATION", p. 26), the dimension and level strings
the text layer finds inside that crop (with positions), the target edge length
in m, and the room's ceiling height.

**Prompt:**
> The image is an architectural elevation of a shopfront. List each **glazed
> panel** (glass you can see through; not signage boards, solid panels, menu
> boards or open doorways). For each, give its **width** and its **sill** and
> **head** heights above finished floor, **only from dimensions printed on the
> drawing**, quoting the printed text you used. If a value is not printed,
> return `null` for it; do not scale from the image. Also give the total
> printed width of the elevation.
> Reply with JSON only:
> `{"total_width_mm": number|null, "total_width_text": string|null,
>   "panels": [{"label": string, "width_mm": number|null, "sill_mm": number|null, "head_mm": number|null,
>               "printed_text": [string], "is_glazing": true}],
>   "excluded": [{"label": string, "why": string}]}`

**Validation:**
- every number must appear in `printed_text`, and each printed string must exist
  in the crop's text layer;
- head > sill;
- the sum of widths ≤ the total width;
- the total width within 2% of the traced edge length, otherwise flag "this
  elevation may not belong to this edge";
- head above ceiling height → propose `min(head, ceiling)` with a note
  (Card K now refuses a head above the wall height).

**Review:** a proposed row per panel in the openings form, with "from AI ·
p. 26 · printed: 2025", for the reviewer to accept or edit.

**Answer key (Butcher Buffet, p. 26):**
- total 11,900 mm (traced edge 11.97 m, within 1%);
- glazed panel 2,025 mm wide, sill about 1,100 mm (printed "1100" from the
  bottom panel), head above the 2,700 mm ceiling, so capped at 2,700;
- the 3,250 mm roller-shutter opening and the "LET'S MEAT" sign panel are
  **excluded** (not glazing);
- if the sill or head is not printed as a dimension, `null` is the correct
  answer.

---

## Plumbing (Codex, after this design is approved)

1. **Task registry:** add P1–P4 to `config/archie_subskills_v1.json` with the
   fields above and a `budget` block (characters, crop count, crop size).
2. **Packet builder per task:** builds the crop(s) from the existing
   full-resolution page renders, overlays the numbered trace for P3, and
   enforces the budget (block with a reason when exceeded). It writes
   `ai_proposals/<task>/<target>/packet.json` plus images.
3. **Manual route UI:** a "Propose with AI" button in each panel (location,
   north, walls and roof, openings). It opens the prompt with "Copy prompt",
   lists the images to attach, and has a box to paste the reply.
4. **Validator per task:** the rules above; store
   `ai_proposals/<task>/<target>/proposal.json` with status
   `proposed | accepted | edited | rejected`, the reviewer, and the reply hash.
5. **Pre-fill only:** proposals fill the form fields with a visible "from AI"
   marker. Saving still goes through the existing declaration actions
   (`set_cited_location`, `declare_north`, `classify_envelope`), recorded as
   the reviewer's declaration with `ai_proposal_id` for audit.
6. **Evaluation:** run each task on Butcher Buffet against the answer keys
   above, and on the Global Exchange sets. Report per task: correct, wrong,
   or abstained. A wrong confident answer counts worse than an abstention.

Not in scope here: equipment schedules and kitchen exhaust (Card Q, needs
data); roof exposure (reviewer or client only); the hosted-model transport
(later, by user decision).
