# Subskill: address_confirmation

- Present ranked address candidates and citations, then wait for a person to
  confirm or correct the exact project address.
- Record the confirmation actor/time and consent reference as user evidence;
  never synthesize them or click/confirm on the user's behalf.
- If unconfirmed, return `needs_review`, no coordinates, and the explicit
  next action. Do not trigger external location/weather lookup.
