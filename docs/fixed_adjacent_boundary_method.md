# Fixed adjacent-temperature partitions

The first adjacent-space method calculates a reviewed partition with:

```text
partition load kW = U-value × area × (adjacent temperature − indoor temperature) ÷ 1000
```

It requires one owning room, a stable adjacent-boundary ID, confirmed area and
construction/U-value, a fixed adjacent temperature, and a separate cited
source for that temperature. The result remains signed: a warmer adjacent
space adds cooling load and a cooler one removes it.

An adjacent boundary can be owned by only one partition surface. This prevents
the same wall from being added to each room in a zone. Room-to-room coupling,
adjacent temperature profiles, inferred boundary conditions, and dynamic heat
storage are deliberately excluded from V1.

Ground-contact floors are a separate gated method (`ground_contact_fixed_v1`).
They require a cited reviewed ground temperature (or complete 24-hour profile)
and an approved method gate; they never fall back to outdoor air or an inferred
soil temperature.
