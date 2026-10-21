# Subskill: address_confirmation

- In ordinary PDF review, present ranked address candidates and citations, then
  wait for a person to confirm or correct the exact project address.
- In autonomous Phase 1 only, select an address when one distinct project-site
  candidate is directly supported by cited drawing evidence. Record the method
  as an AI inference. Never invent a person, confirmation time, or consent ref.
- If candidates materially conflict, return `needs_review` and leave the
  address unset. This task does not research the site or select weather; the
  drawing site task and the separate weather picker own those inputs.
