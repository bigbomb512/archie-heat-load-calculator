# Subskill: information_needs

- Purpose: tell the operators what they must research or ask the client, and
  nothing else. They will check every item, so a short, exact list is the goal.
- Work through the inputs a cooling heat load needs: rooms with areas and
  heights; occupancy or seats; lighting; each piece of equipment with its rated
  power and whether it is under a hood; opening hours; walls and roof
  constructions and what is beyond each boundary (outside, neighbour, roof,
  plant room); windows with sizes, glass type and orientation; outside air,
  kitchen exhaust and make-up air; the site location.
- For each, look in the case file and the prerequisite proposals first. If a
  value is printed, it is not a need. If it can reasonably be worked out from
  the drawings (a ceiling height from a section, seats counted on the furniture
  plan, an area from printed dimensions, orientation from the north point),
  put it under `inferred` with the method and pages, not under `needs`.
- What remains goes under `needs`, one concrete item each: the target (room,
  equipment code, window, wall), the field, why the calculation needs it, its
  likely impact (a rough share of the load, e.g. "kitchen equipment, likely
  several kW"), and where it is usually found (equipment spec sheet or
  supplier quote, the client, the mechanical drawings, a site visit, the
  landlord's base-building information).
- One need per equipment item that needs a rating, with its code as the
  target: an answer is recorded against one item, so a need naming several
  items would be marked answered when only one of them is. Other needs may
  cover several things of one kind (e.g. all unrated light fittings).
- Keep each need short: `why` in one sentence of at most 20 words, `impact`
  in at most 8 words, `where_to_look` in at most 12 words.
- Do not list generic design values the calculation supplies from its own
  labelled defaults (design weather from the AIRAH tables, people heat gains
  per person); list them only if the drawings contradict the default.
- `answer_kind` says what the answer will be, so it reaches the right input:
  `room_area`, `ceiling_height`, `occupancy` (people), `lighting_load` (W),
  `equipment_rating` (an item's rated power), `roof_above` (what is above the
  tenancy), `opening_hours`, `glazing` (glass U-value and SHGC), `exhaust` (a
  kitchen's hood exhaust rate and how its air is replaced), `boundary` (what is beyond a
  wall, floor or ceiling), or `other`. `room` is the room's name as it appears
  in the room proposals, or null when the need is not about one room.
- `pages` is a comma-separated list of the pages looked at, or "".
