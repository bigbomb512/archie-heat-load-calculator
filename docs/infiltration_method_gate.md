# Infiltration Method Gate

Status: **not approved — calculation implementation is blocked**

This record defines the engineering decisions that must be agreed before
infiltration contributes to an hourly cooling result. The current calculator
only stores infiltration evidence in `hourly_load_model.json` as an explicit
`unapproved_components` record. Stored values are excluded from totals.

## Required decisions

### 1. Input basis and units

Select one or more approved bases and define precedence when more than one is
available:

- air changes per hour (ACH);
- volumetric flow (`L/s`, `m³/s`, or `m³/h`);
- envelope leakage or test-derived flow;
- an approved project-specific method.

Every value must identify its room, source, citation, review status, and
temperature/pressure basis. Unit conversion must be deterministic and visible
in the report.

### 2. Room volume and conversion

The approved method must state how room volume is obtained, including the
required area and ceiling-height evidence, treatment of voids, and whether
volume is constant for all hours. ACH conversion must be tested in both
directions against hand-calculated examples.

### 3. Sensible and latent treatment

Define the psychrometric state used for infiltration air, including:

- outdoor dry-bulb and humidity state;
- room dry-bulb and humidity state;
- pressure/elevation correction;
- sensible and latent equations;
- sign convention for cooling gain and heating loss;
- rounding point and precision.

No latent contribution may be inferred from an ACH value without an approved
humidity basis.

### 4. Schedule interaction

Specify whether infiltration is:

- constant while the room is in the selected schedule;
- present for all 24 hours;
- driven by a separate infiltration schedule; or
- controlled by an approved operating state.

Missing day types remain missing. Weekday values must not be copied into
Saturday or Sunday/holiday profiles.

### 5. Safety factors and interaction with other air paths

Define whether infiltration is subject to the existing room safety factor or a
separate allowance. State how it interacts with outside air, transfer air,
extract, spill, make-up air, leakage, and future AHU fan/duct terms. These
mechanisms must not be substituted for one another.

### 6. Evidence and review requirements

An infiltration input is eligible only when it has:

- a stable component ID and room owner;
- a valid unit and positive value, or an explicit confirmed-not-present status;
- source and citation(s);
- reviewer attribution and review date;
- the approved basis/method identifier;
- required room volume and humidity inputs.

AI or PDF extraction may propose these fields but may not approve them.

## Required verification cases

Before implementation, create reviewed cases for:

1. zero/not-present infiltration — no change to the cooling result;
2. positive ACH — expected sensible and latent gains in the correct direction;
3. equivalent volumetric-flow input — same result as the ACH case;
4. room-volume change — deterministic change in converted flow;
5. schedule-off hour — behaviour matches the approved schedule policy;
6. dry-air or humidity edge case — no negative or silently missing latent load;
7. invalid unit, missing source, missing citation, and unsupported method —
   blocked with an actionable readiness issue;
8. safety-factor interaction — documented and reconciled without double
   counting.

## Acceptance gate

The method is approved only when an engineering owner signs this record, the
equations and units are fixed, the verification cases have expected values,
and the report treatment of included, excluded, provisional, and blocked
inputs is agreed. Until then, `infiltration` remains
`stored_not_calculated` or `not_assessed` and cannot affect cooling totals.
