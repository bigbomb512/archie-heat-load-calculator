# Subskill: schedule_evidence

- Extract explicit opening/operating hours and weekday/Saturday/Sunday/
  holiday distinctions from project brief, tenancy notes, or schedules.
- Map only to a supplied controlled 24-hour profile. Verify 24 finite factors
  in `[0,1]` and retain the profile/source fingerprint.
- Do not infer hours from a business type, apply outside-air schedules to
  infiltration, or assume all-day operation. Missing day types remain
  unresolved or use an explicitly supplied provisional fallback.
