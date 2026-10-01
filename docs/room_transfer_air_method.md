# Room-to-room transfer-air method (draft)

This V1 method estimates the sensible and latent cooling contribution of a
cited transfer-air flow entering a receiving room from another modeled room.
It exists to make this input visible in hourly results; it is not an approved
design method or a balanced air-network solver.

For each hour, the model converts the transfer volume flow to mass flow using
the **sending-room** dry-bulb temperature, wet-bulb temperature and scenario
pressure. It compares sending-room and receiving-room moist-air enthalpy. The
sensible term uses the dry-bulb temperature difference and moist-air specific
heat; latent is the total enthalpy difference less sensible. Terms are signed,
so air that is cooler or drier than the receiving condition can appear as a
negative cooling contribution. A dedicated 24-hour transfer schedule scales
the flow.

Each calculated transfer component must identify its source room, cite the
flow evidence, use `room_air_transfer_psychrometric_v1`, declare the
`room_to_room` path and `sending_room_air_state` flow reference, and have a
dedicated schedule. Both rooms need reviewed cooling dry-bulb and wet-bulb
conditions. Results remain draft and cannot establish a review-ready project
peak while this method is pending project-specific HVAC engineer review.

Limitations: the calculation does not infer transfer flow, solve pressure
balance, model door/opening dynamics, or calculate replacement air and
exhaust-side effects. Do not enter the same flow as direct outside air or an
AHU contribution unless the system boundary explicitly prevents double
counting. Minimum supply, extract, spill and make-up paths remain excluded.

## Method references

- AIRAH, [DA09 Air Conditioning Load Estimation](https://www.airah.org.au/site/site/resources/da-manuals/da-09/Default.aspx), covering psychrometrics, ventilation/infiltration, sensible and latent loads, and system effects.
- ASHRAE, [Handbook—Fundamentals, Chapter 1](https://handbook.ashrae.org/Handbooks/F17/SI/f17_ch01/f17_ch01_si.aspx), psychrometric properties and moist-air enthalpy basis.

These references support the underlying psychrometric quantities, not this
project's transfer-flow assumptions or release status. Obtain HVAC engineer
approval and compare against an authorised reference case before design use.

`tests/fixtures/transfer_air_benchmarks_v2.json` stores one independent
analytical arithmetic case using version 2 ASHRAE saturation pressure, with a
declared sending-room volume reference and thermodynamic wet-bulb states. Its
test checks component loads, total-load reconciliation, flow conversion, and
exchange-sign reversal. Passing it verifies that the implementation follows
the stated draft equations; it does not approve the transfer-flow reference or
establish a balanced air-network method. The v1 fixture preserves the prior
approximation's reference outputs.
