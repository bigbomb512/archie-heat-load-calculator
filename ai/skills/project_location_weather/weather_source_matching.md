# Subskill: weather_source_matching

- After confirmation, compare only released design-condition records against
  country/state/locality or climate scope, design basis, cooling/heating
  scenario, validity, pressure, and required hourly-profile completeness.
- Return ranked source IDs, scope match, expiry, citation, and conflicts;
  separate cooling and heating. Point-only values cannot be promoted to an
  hourly profile without an approved transformation.
- Reject live forecasts, stale/out-of-scope records, and station observations
  presented as HVAC design conditions; report missing pack requirements.
