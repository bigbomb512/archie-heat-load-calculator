# Casa Nova Gosford: independent peak-hour cooling check

**Scope and status (24 November 2026).** This is an independent, itemised **CLTD/SCL/CLF-style hand estimate**, not a run of Toki's hourly radiant time series (RTS) model and not an equipment selection or a compliance certificate. The reference hot, clear-sky check is **37.31 kW** (31.15 sensible + 6.16 latent) at **16:00 solar time in January**. A separate humid-weather screening case is **41.97 kW** if the same clear-sky solar load is imposed; those weather and sun conditions have not been shown to coincide. Toki's saved included-scope peak is **30.9657 kW** (31.2046 sensible − 0.2389 latent), March 16:00. Neither result establishes the actual design duty without the unresolved inputs below.

## Evidence, method and scope

The saved job files were read as evidence, without calling any application calculation code:

- [`ai_preliminary_input_set.json`](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/output/web_review/casa-nova-gosford-_cng_---mechanical-services-design-full-set_rev-2_1_-1795427419/ai_preliminary_input_set.json): owner overrides and the saved input provenance, especially `internal_gains_resolution`, `airflow_resolution`, `ceiling_volume_resolution`, `design_conditions_basis`, `phase1_assumptions`, and `exclusions`.
- [`hourly_ai_preliminary_load_report.json`](/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/output/web_review/casa-nova-gosford-_cng_---mechanical-services-design-full-set_rev-2_1_-1795427419/hourly_ai_preliminary_load_report.json): `included_scope_peak`, including its components. These are **comparison outputs only**, not inputs to the hand estimate.

The calculation form, 16:00 glass CLTD and summer SCL factors, people and lighting CLFs, and restaurant people/equipment heat gains come from [ASHRAE *Fundamentals* (1997), chapter 28, Tables 3, 8, 34–38 and equations 41, 43–46](https://www.tagengineering.ca/wp-content/uploads/2015/01/1997-Fundamentals_28.pdf). The dining ventilation reference is the [AIRAH-hosted extract of AS 1668.2:2002, Appendix A, Table A1](https://www.airah.org.au/Common/Uploaded%20files/Archive/Advocacy/Compliance/Appendix-2-Table-A1-AS1668.2-2002.pdf). The Sydney design pairs are recorded in the saved job as AIRAH Technical Handbook 4th edition (2007), p. 37, from DA9 (1998); [AIRAH's DA09 page](https://www.airah.org.au/site/site/resources/da-manuals/extracts/da-09/DA09_extracts.aspx) identifies its design-data extracts, but the older numerical page was **not independently checked**. Thus the weather numbers below are saved-job inputs, not newly verified DA9 values.

ASHRAE's published SCL table is for **July at 40°N**, not January at 33.4°S. I use its **south-facing row for local north-facing glass**, and its west row for local west, as a summer hemisphere analogue. The latitude/season translation and 16:00 solar-versus-clock-time alignment are approximate; this is a cross-check, not a local SCL table. The 16:00 hour is chosen because the west glass is near its afternoon maximum (Zone C SCL west: 153 at 16:00, 156 at 17:00 Btu/h·ft²), while the large north frontage retains a contribution. A local hour-by-hour check could move the peak to 17:00 or another month.

### Input register

| Input | Value used | Basis and limitation |
| --- | ---: | --- |
| Cooled floor area | Dining 186.0 + Toilets 10.6 = **196.6 m²** | Owner/saved traced job; Kitchen excluded because owner says it is not air-conditioned. No kitchen appliance or process exhaust load is charged to the cooled zone. |
| Ceiling height / volume | 2.77 m / 196.6 × 2.77 = **544.58 m³** | Owner override in saved job. Volume is recorded for context; no unsupported ACH infiltration is inferred from it. |
| People | Dining **60**, Toilets **1**; 61 simultaneous | Owner Dining count and saved Toilets count. No diversity reduction. Toilet occupant may already be one of the 60; the effect is about 0.23 kW. |
| Orientation | North = plan up; 24.0 m street frontage north, 3.1 + 5.5 = 8.6 m left return/curve west | Owner. Curve represented as west-facing equivalent; actual segment bearings could change solar load. |
| Glazing | Full-height single clear glazing; gross 24 × 2.77 = **66.48 m² north**, 8.6 × 2.77 = **23.822 m² west**, total **90.302 m²** | Owner geometry/glass description. The saved preliminary job has eight openings; this hand check uses the owner's combined lengths. |
| Glass properties | U = **6.7 W/m²·K**, SHGC = **0.70**, frame fraction = **0.15**, no external shade | Assumed NatHERS-style default given in the task, **not a measured product or independently confirmed NatHERS entry**. Gross assembly U applies to 90.302 m²; solar uses 0.85 × gross area. Treating 0.70 as centre-of-glass SHGC is an explicit convention; whole-window SHGC would change the result. |
| Other envelope | Shared walls; roof not exposed; opaque exterior wall and floor heat transfer = **0** | Owner tenancy description and saved slab assumption. This requires façade/adjacency and above/below-space verification. Toki carries small nonzero envelope and opaque-solar components. |
| Weather / setpoint | Hot: **31.1°C DB, 19.8°C coincident WB**; humid: **29.5°C DB, 22.7°C WB**; inside **24°C, 50% RH** | Sydney AIRAH/DA9 pairs supplied in task and saved job; inside supplied in task. Standard pressure **101.325 kPa** assumed for psychrometrics. |
| Daily dry-bulb range | **10°C** assumed | Needed only to correct ASHRAE's glass CLTD. No site design-day hourly range supplied. |
| Outdoor air | **7.5 L/s per dining person**, Grade 2 indicative = 60 × 7.5 = **450 L/s**; plus **7.5 L/s** for one toilet occupant = **457.5 L/s** | AS 1668.2:2002 Appendix A Table A1 lists dining rooms at 7.5 L/s/person for Grade 2; its toilet/rest-room row also lists 7.5. This is an **informative 2002 reference**, not verification of the governing 2024 edition or a complete toilet exhaust design. Whether the toilet air is transferred from Dining, rather than additional outdoor air, remains unresolved. Toki uses 7.5 L/s/person + 0.3 L/s/m²: Dining 505.797 + Toilets 10.676 = **516.473 L/s** in the saved airflow resolution. |
| Lighting | **14 W/m²** installed, all on; CLF **0.90** | Task lighting density. CLF 0.90 is a rounded Zone C, roughly seven-hours-on afternoon value from ASHRAE Table 38; schedule, fixture type and ceiling return are unverified. |
| Dining/bar equipment | Two self-contained refrigerated displays **0.8 kW sensible each**, one unhooded coffee brewer **1.1 kW sensible + 0.56 kW latent**, other bar electronics **0.4 kW sensible** | **Hand assumptions**, not a verified inventory. Coffee brewer values approximate ASHRAE Table 8 (3750 sensible and 1910 latent Btu/h); refrigeration and electronics are provisional heat-to-room allowances. Assume simultaneous operation and CLF = 1.00 (conservative for intermittent brewer use). Total **3.10 sensible + 0.56 latent kW**. Equipment installed in the uncooled Kitchen is excluded. |
| Infiltration / fan heat | **0 / 0 kW in this scope** | Infiltration is unknown and not quietly set to a measured zero; Toki's saved exclusions state its preliminary ACH path did not pass the infiltration method gate. Fan heat needs actual supply airflow, fan power and fan position. The hand sum is a space-plus-outdoor-air coil screening load before a known fan allowance. |

## Reproducible peak-hour arithmetic

All heat rates below are kW. This is a **single simultaneous hour**; there is no addition of separate component maxima. Conversion: 1 Btu/h·ft² = **3.15459 W/m²**, 1 Btu/h = **0.293071 W**. ASHRAE Table 35B's **Zone C** SCL is selected as the unshaded, hard-floor, light-partition, one/two-exposed-face analogue; actual floor and partition mass are unconfirmed. With no actual glass shading-coefficient test, use **SC = SHGC/0.87 = 0.70/0.87 = 0.8046** as an approximate conversion to the reference clear glass. Do not also multiply by SHGC: that would double count glass solar transmittance.

1. **Glass solar, sensible.** ASHRAE Table 36 Zone C at 16:00: local north ↔ table south **40**, west **153 Btu/h·ft²**. Net glass north = 66.48 × 0.85 = **56.508 m²**; west = 23.822 × 0.85 = **20.2487 m²**. Formula `q = A_net × SC × SCL × 3.15459 / 1000`: north `56.508 × 0.8046 × 40 × 3.15459 / 1000 = 5.737`; west `20.2487 × 0.8046 × 153 × 3.15459 / 1000 = 7.863`. **Solar = 13.600 sensible, 0 latent.** SCL already includes the table's storage/cooling-load effect; no extra CLF is applied.
2. **Glass conduction, sensible.** ASHRAE Table 34 at 16:00 gives **14°F = 7.7778°C** baseline CLTD, calibrated to 85°F (29.444°C) outdoor *daily mean* and 78°F (25.556°C) indoor. With assumed 10°C daily range, outdoor mean `31.1 − 10/2 = 26.1°C`; corrected CLTD `7.7778 + (26.1 − 29.4444) + (25.5556 − 24) = 5.9889°C`. Formula `q = U × A_gross × CLTD / 1000 = 6.7 × 90.302 × 5.9889 / 1000 = 3.623 sensible, 0 latent`. This correction is outside the table's stated reference mean, so its precision is limited. A simple 7.1°C steady-state difference would give 4.296 kW, a +0.673 kW sensitivity.
3. **People.** ASHRAE Table 3 restaurant row (including food) gives **275 sensible and 275 latent Btu/h/person**, each **80.595 W/person** at approximately this indoor temperature. Choose sensible **CLF = 0.90** as an afternoon occupancy approximation from Table 37 Zone C; latent is immediate. `q_s = 61 × 275 × 0.293071 × 0.90 / 1000 = 4.425`; `q_l = 61 × 275 × 0.293071 / 1000 = 4.916`. The actual mix of customers/staff and food heat may differ.
4. **Lighting.** `q_s = 196.6 × 14 × 1.00 use × 0.90 CLF / 1000 = 2.477`; latent **0**. If all installed watts are immediate coil load, this rises by 0.275 kW.
5. **Bar/dining equipment.** `q_s = 2 × 0.8 + 1.1 + 0.4 = 3.100`; `q_l = 0.560`. These are allowances for equipment *in the cooled area*, not a claim that these exact units appear on the drawing. An externally condensed display or hooded/vented brewer would change heat-to-space fractions.
6. **Outdoor air.** At 101.325 kPa, use `p_ws(T) = 0.61078 exp[17.2694 T/(T + 237.29)] kPa`, `W_sat(T) = 0.62198 p_ws/(P − p_ws)`, `W_i = 0.62198[0.50 p_ws(24)]/[P − 0.50 p_ws(24)] = 0.009295 kg/kg`, and the wet-bulb approximation `W_o = {[(2501 − 2.326 T_wb) W_sat(T_wb)] − 1.006(T_db − T_wb)}/[2501 + 1.86 T_db − 4.186 T_wb]`. The hot pair gives **W_o = 0.009793 kg/kg**, ΔW = **+0.000498 kg/kg**. Assume moist-air density **1.20 kg/m³**, dry-air specific heat **1.006 kJ/kg·K**, and water vapour latent heat **2501 kJ/kg**. `V̇ = 457.5/1000 = 0.4575 m³/s`; `q_s = 1.20 × 1.006 × 0.4575 × (31.1 − 24) = 3.921`; `q_l = 1.20 × 2501 × 0.4575 × (0.009793 − 0.009295) = 0.683`. **Outdoor-air total = 4.605 kW.** The sensible/latent split uses constant design coefficients and is a screening approximation; an exact moist-air enthalpy balance would differ slightly.
7. **Opaque envelope, infiltration, fan.** No exposed roof or confirmed opaque exterior area: **0** assigned. No defensible uncontrolled leakage rate or fan power: **0** assigned as *unquantified exclusions*, not physical absence. Kitchen process makeup, toilet exhaust imbalance, and door use could add loads.

| Hand component, January 16:00 hot pair | Sensible | Latent | Total |
| --- | ---: | ---: | ---: |
| People | 4.425 | 4.916 | 9.341 |
| Lighting | 2.477 | 0 | 2.477 |
| Bar/dining equipment | 3.100 | 0.560 | 3.660 |
| Glazing conduction | 3.623 | 0 | 3.623 |
| Glazing solar | 13.600 | 0 | 13.600 |
| Outdoor air | 3.921 | 0.683 | 4.605 |
| Opaque envelope, infiltration, fan allowance | 0 | 0 | 0 |
| **Total** | **31.147** | **6.160** | **37.307** |

**Humid pair, same-hour screening only.** The wet-bulb equation gives `W_o = 0.014539`, ΔW = 0.005244 kg/kg. Holding the same people, lights, equipment and **clear-sky** SCLs, correcting glass CLTD by the −1.6°C DB change gives glass conduction `6.7 × 90.302 × (5.9889 − 1.6)/1000 = 2.655` kW. Outdoor air sensible `1.20 × 1.006 × 0.4575 × (29.5 − 24) = 3.038`; latent `1.20 × 2501 × 0.4575 × (0.014539 − 0.009295) = 7.201`. Sum **29.295 sensible + 12.677 latent = 41.972 kW**. Actual humid-day cloud and hour could lower solar markedly; this is a moisture sensitivity, **not** a demonstrated 42 kW coincident peak.

## Side-by-side with Toki's saved included-scope peak

The Toki column is the saved **March 16:00** result. The hand column is the **January 16:00 hot** check; weather and seasonal sun are therefore not identical. Values are kW, sensible + latent = total. Toki's two additional `envelope` and `solar` categories are retained rather than silently merged into glass.

| Component | Hand sensible | Hand latent | Hand total | Toki sensible | Toki latent | Toki total | Main cause of difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| People | 4.425 | 4.916 | 9.341 | 3.329 | 2.748 | 6.077 | ASHRAE restaurant row includes food; no 0.9 occupancy diversity; different sensible CLF and Toki fallback W/person. |
| Lighting | 2.477 | 0 | 2.477 | 2.187 | 0 | 2.187 | Hand 14 W/m² with rounded CLF; saved schedules/diversity and RTS differ. |
| Equipment/refrigeration | 3.100 | 0.560 | 3.660 | 3.027 | 0 | 3.027 | Independently stated bar allowances, including unhooded brewer moisture; actual inventory and condenser locations unresolved. |
| Opaque envelope | 0 | 0 | 0 | 0.089 | 0 | 0.089 | Hand treats non-glass boundaries as shared/not exposed. |
| Opaque solar | 0 | 0 | 0 | 0.118 | 0 | 0.118 | Same exposure assumption; Toki retains small component. |
| Glazing conduction | 3.623 | 0 | 3.623 | 2.839 | 0 | 2.839 | Hand uses corrected published CLTD and 90.302 m² gross; weather/month/model differences. |
| Glazing solar | 13.600 | 0 | 13.600 | 15.176 | 0 | 15.176 | July-40°N SCL translated to January-33.4°S versus Toki's local March hourly sun/RTS; frame, curve and optical conventions matter. |
| Outside air | 3.921 | 0.683 | 4.605 | 3.585 | −2.987 | 0.598 | Hand Grade 2 457.5 L/s versus saved 516.473 L/s; **sign of moisture load reverses** at the two model weather states. Negative latent is possible when outside air is drier than inside; verify Toki's March state before treating it as a benefit. |
| Fan heat | 0 | 0 | 0 | 0.856 | 0 | 0.856 | Hand lacks fan duty/location; Toki includes a fan allowance. |
| **Total** | **31.147** | **6.160** | **37.307** | **31.205** | **−0.239** | **30.966** | **Hand is +6.341 kW total, almost entirely in the latent split; sensible totals differ by −0.058 kW. Coincidence is not independent validation.** |

The near equality of the two **sensible** totals is a cancellation of different component estimates, not proof that the façade, people or equipment inputs are correct. The hand hot pair's outside-air latent term is +0.68 kW, while Toki's saved March peak reports −2.99 kW. Toki's recorded design weather uses monthly offsets, including March 1.44°C below its January reference; this and its humidity treatment must be checked against the saved hourly state. Neither the old AS 1668.2:2002 indicative Grade 2 rate nor the hand toilet airflow establishes current-code compliance or actual makeup air.

## What would be required for the earlier 70–85 kW figure?

Holding **this hand hot-hour basis** and all other inputs fixed, the gap is **32.69–47.69 kW**. These are *isolated sensitivities*, not a proposed combination and not evidence that the earlier figure is wrong:

| Single changed input | Arithmetic to add 32.69–47.69 kW | Implied total / evidence needed |
| --- | --- | --- |
| More simultaneous diners plus their 7.5 L/s each | Per extra person: `0.0806 × 0.90 sensible + 0.0806 latent + 1.20 × 1.006 × 0.0075 × 7.1 sensible OA + 1.20 × 2501 × 0.0075 × 0.000498 latent OA = 0.2286 kW/person`; gap / 0.2286 = **143–209 extra people** | **204–270 people total** including the toilet-count convention. Requires seating/fire occupancy and ventilation design; far above the owner's 60 Dining count. |
| More unshaded west glazing of same construction at 16:00 | Per gross m²: `0.85 × (0.70/0.87) × 153 × 3.15459/1000 + 6.7 × 5.9889/1000 = 0.3702 kW/m²`; gap / 0.3702 = **88–129 m² extra** | **32–47 m additional full-height west-facing glass** at 2.77 m, beyond the recorded 8.6 m. Requires surveyed façade and verified orientation/glass; unlikely to be hidden in a single small return. |
| More in-zone equipment or process heat | At 100% heat-to-cooled-zone and coincident operation, simply **33–48 kW additional** | Equipment nameplates, duty cycles, condenser location, hood capture and heat-to-space factors; the uncooled Kitchen cannot be included without revising scope. Latent appliances need separate moisture accounting. |
| Higher actual outdoor/process makeup air | At the hot pair, outdoor-air total is `4.605/0.4575 = 10.07 kW per m³/s`; gap implies **3.25–4.74 m³/s extra** at this dry pair, before fan effects | Hood/exhaust schedule, toilet exhaust and transfer path, door leakage, supply outdoor-air fraction, and makeup-air conditioning. On the humid pair the outdoor-air effect is much larger, but its coincidence with clear sun is unknown. |

Combinations of moderate changes could also bridge the gap. The highest-leverage unknowns are actual façade bearings and shading/glazing product data, restaurant occupancy and schedules, bar/refrigeration heat rejection, kitchen exhaust/makeup-air interaction, measured infiltration/door traffic, correct AS 1668.2 edition and exhaust treatment, and site-specific coincident weather. The 70–85 kW figure may also refer to a broader scope (for example kitchen conditioning or plant capacity rather than the Dining+Toilets space/ventilation load); its original breakdown is needed before comparing like with like.

## Review limits and next evidence

This check is **implemented** as a reproducible independent hand calculation and comparison. Engineering validation remains **open**: the SCL table is a hemisphere/latitude analogue; the glazing optical conversion is approximate; the weather numbers come from the saved job; the humid weather/clear sun combination is only a screen; no measured leakage, fan or makeup-air data exist; and the equipment list is provisional. The next concrete step is to obtain the as-built façade and glass schedule, current ventilation/exhaust design, bar equipment and condenser schedule, fan selection, door-use basis, and a local coincident weather/solar basis, then have a qualified HVAC designer reconcile both methods on the **same hour and scope**. No sizing recommendation follows from this note alone.

## Update, 24 November 2026: Toki after the moisture rule

Toki now counts outside air drier than the room as adding no moisture load (it had subtracted it). Recalculated with
the same inputs, Toki's Casa Nova result is **33.95 kW** (March 16:00): people 6.08, glass sun 15.18, outside air
3.58 (was 0.60), glass conduction 2.84, equipment 3.03, lights 2.19, fan heat 0.86, walls/roof 0.21; latent
+2.75 kW (was −0.24). The gap to this hand check's 37.31 kW hot-day figure is now 3.36 kW, against 6.34 kW before,
and is within what the different methods (peak-hour CLTD/CLF here, hourly radiant time series in Toki) and the
different month (January here, March in Toki) would explain.

Two inputs still decide most of what's left, and neither is on the drawings:
- **Fresh-air rate.** Toki uses 7.5 L/s per person plus 0.3 L/s per m² (506 L/s for 60 people). At 10 L/s per person
  plus the same area rate, 656 L/s, the outside-air load rises roughly in proportion: about +1.1 kW at Toki's peak hour.
- **Bar and dining equipment.** Toki uses generic values (3.0 kW). Espresso machines, under-bench fridges, display
  lighting and a pizza oven in the cooled space would each add to this; the owner has said the pizza oven was not
  counted in the earlier figure.

The earlier 70–85 kW figure stays unexplained by these inputs: reaching it needs roughly 140–210 more people,
90–130 m² more west glass, or 33–48 kW more equipment heat in the dining room (see above). Its source and scope are
needed to compare it.
