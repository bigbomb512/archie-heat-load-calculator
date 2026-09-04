# CAMEL+ Screenshot Feature Report

## Purpose and boundary

This report consolidates the CAMEL+ screenshots supplied during the cooling and heating load-calculation planning work. It is a product and engineering reference, not a claim that CAMEL+ calculations, defaults, libraries, or DA09 methods have been reproduced.

The screenshots describe a mature, spreadsheet-style engineering workflow. The current project keeps a stricter boundary: drawing-derived facts remain cited and reviewable; numerical design inputs are engineer-entered; no load rate, weather sequence, schedule, construction value, or solar/shading curve is inferred from the screenshots.

This report belongs to the **Cool / Heat Load Calculation** workstream. It is separate from the Archie geometry/CAD/platform workstream, although it may use Archie drawing evidence when an engineer approves it.

## What the supplied screens collectively show

CAMEL+ is organised around a project-wide building/load workflow:

1. Establish project and design conditions.
2. Define schedules, shading, windows and walls.
3. Select constructions and glass from standard or user libraries.
4. Define primary plant, preconditioning, AHUs, zones, and rooms.
5. Enter room loads, airflow, thermal mass, infiltration, moisture, envelope and shading data.
6. Produce coincident cooling/heating results and then size systems and plant.

The present backend implements only a deliberately limited part of that workflow: cited project/site conditions, preliminary static cooling/ventilation inputs, and engineer-entered hourly **cooling** design-day schedules and room/zone/project coincident peaks. It does not emulate the CAMEL+ UI or automatically apply its embedded tables.

## Screenshot catalogue and engineering interpretation

### 1. Shading geometry and rotations

Earlier Shading screens show tabular input for overhang depth, distance, left/right reveals, gaps, factors, drop depths, and rotations. The targeted help defines overhang, drop, and left/right-reveal angles, including their permitted angle ranges and sign conventions.

Engineering meaning:

- A façade opening has geometric shade features, not merely a single fixed shade coefficient.
- Overhang, side reveals/fins, distance and rotation affect sun obstruction by orientation and time.
- The model needs an unambiguous coordinate convention and a clearly defined surface normal.

Current project status: external surface evidence and a manually entered design solar basis are supported in the preliminary cooling inputs. Dynamic shading geometry, angle calculation, reveal/overhang obstruction, and any CAMEL+ rotation convention are **not implemented**. The hourly model accepts only an engineer-entered solar schedule assigned to each solar-bearing surface.

### 2. Windows, glass data and corrections

The Windows screens show a grid of window type, dimensions, U-value, shade factor, frame solar-factor correction, internal shading, and optional glass-number fields. Targeted help provides typical U-values and shade-factor guidance. The Glass Type chooser includes one/two-pane filtering, manufacturers, winter/summer coefficients, U-value for CAMEL, and shade coefficient for CAMEL. Other help screens show frame and glass-area correction tables for single and double glazing.

Engineering meaning:

- Window heat gain is normally a combination of glass properties, frame correction, area, orientation, shading and internal treatments.
- Manufacturer data and generic reference tables must not be conflated.
- Window identifiers should link a room surface to an auditable glazing record.

Current project status: an envelope surface can hold entered area, U-value, design solar basis, solar gain factor and shading factor, each with source/status. Detailed glass selection, manufacturer library ingestion, frame-area corrections, centre-of-glass conversion, internal-shade library logic, and dynamic glazing physics are **not implemented**.

### 3. Walls, roofs and standard construction libraries

The Walls screen lists custom wall types with U-value and surface density, alongside links to standard wall and roof libraries. The Wall & Roof Types selector classifies construction families such as clay brick, solid/hollow block, concrete, infill panels, metal siding, fibre cement, weatherboard and sandwich panels. A selected record displays U-value, resistance and density; the screen also permits manually maintained non-standard types.

Engineering meaning:

- Construction selection is a data-library problem plus a project override problem.
- U-value is needed for steady-state conduction; density and layer mass are relevant to dynamic thermal storage.
- A construction reference must preserve its source/version and whether it is standard, user-entered, or inferred.

Current project status: cited construction references can be retained; U-values and areas may be engineer-entered for preliminary envelope conduction. There is no bundled construction library, automatic U-value assignment, layer calculation, roof selection workflow, or dynamic-mass calculation.

### 4. Storage mass and the Calculate Storage Mass dialog

The room/zone screenshot exposes `Storage Mass (kg/m²)`, a calculated storage-mass field, infiltration air changes per hour, and vapour gain. Targeted help says storage mass is based on total external walls/roofs and relevant floor, ceiling and partition mass; it applies reduction factors for some boundaries and allows an entered value to override the calculated value. The calculation dialog groups contributing mass into external wall/roof surfaces, floors, ceilings/partitions, furniture/other, then divides total mass by floor area. It displays factors and material surface densities.

Engineering meaning:

- Dynamic load calculation needs room-level effective thermal mass rather than a single arbitrary global setting.
- The method requires boundary classification: external, conditioned adjacent, unconditioned adjacent, carpeted floor, furniture, etc.
- A manual override must supersede, but not erase, a calculated result and its inputs.

Current project status: storage mass is **not implemented**. It is an explicit exclusion from the current hourly cooling report, together with dynamic thermal mass. No mass value is inferred from wall type, density, area or the supplied screenshots.

### 5. Room air, infiltration, moisture, supply and transfer fields

The Room/Zone screens show:

- outside-air units such as air changes, fixed L/s, L/s per m² and L/s per person;
- an outside-air value;
- extracted-air flow and a `Spill to` target room;
- minimum supply-air quantity;
- infiltration air changes/hour;
- vapour gain;
- supply-duct heat gain, leakage loss and external gain.

Engineering meaning:

- Ventilation, infiltration, exhaust and transfer air are different mechanisms and must not be silently substituted for one another.
- Airflow needs clear units and ownership: room outdoor air, AHU supply, exhaust, spill/transfer and minimum supply.
- Moisture/latent gains require an explicit source and must be separated from sensible gains.
- Duct gains and leakage belong to the air-system path, not necessarily to the room envelope.

Current project status: the preliminary ventilation model supports engineer-entered outside-air basis, process exhaust, transfer air, make-up air and air balance. The hourly cooling model schedules explicitly entered outside-air flow and calculates psychrometric sensible/latent outdoor-air load. Infiltration, vapour gain, minimum supply air, extracted/spill-air network behaviour, supply/return duct gains, leakage and AHU effects are **not implemented** in the hourly engine.

### 6. AHU, zone and room hierarchy

The Project Summary shows AHUs, zones, rooms, and completion indicators for external, partitions and internal inputs. The AHU screen supports multiple system types, unit number-off, chiller/boiler connection, circuit type, room design conditions, operating hours, outside-air configuration, heat-exchanger effectiveness, humidity-control fields and preconditioner selection. The Tree Structure dialog visually supports AHU → zone → room creation, duplication and restructuring. The Zone and Rooms screens expose room lists, thermostat placement and air-distribution basis.

Engineering meaning:

- AHU, zone and room are separate aggregation levels with their own inputs and outputs.
- A room can be copied, split, regrouped or served by another AHU; stable IDs and source traceability matter.
- System type changes the calculation path and cannot be treated as cosmetic metadata.

Current project status: the hourly overlay supports stable rooms grouped by a stable `zone_id`, seeded from existing design zones. The seed mapping is intentionally marked inferred and requires engineer review; rooms can be renamed, split, merged and reassigned without mutating `design_requirements.json`. AHU/system hierarchy, air-distribution methods, thermostat behaviour, room-to-AHU system mapping and central-system aggregation remain **out of scope** for V1.

### 7. AHU coils, psychrometrics and fan/duct effects

AHU Coil screens show selectable psychrometric input method, bypass factor, apparatus dew point, indirect evaporative cooling, supply/return fan heat, return-duct gains/leakage, safety factors and print-load-chart timing. Targeted help defines bypass factor as the fraction of air that passes the cooling coil without psychrometric alteration, with reference ranges by coil depth, fin density, face velocity and spray condition.

Engineering meaning:

- Room loads and coil loads are not interchangeable.
- Coil leaving conditions, bypass factor, fan heat and duct gains need a selected system configuration and design air quantities.
- Any reference table must be explicitly cited and selected by an engineer; it must not become a hidden default.

Current project status: the project has a moist-air outside-air calculation using entered DB/WB, pressure and flow. It does not calculate coils, apparatus dew point, bypass factor, indirect evaporative cooling, fan heat, duct gains/leakage, AHU coincident peak, or load-chart outputs.

### 8. Chiller, boiler, circuits and preconditioners

The Chiller, Boiler & Circuits screen supports pump heat gain/losses, diversity factors, and refrigerant/water circuit accumulation. The Preconditioners screen supports temperature-control coils, chiller/boiler connection, CW/DX pre-cooling choice, desiccant humidity-control selection, moisture removal, fan kW and fresh-to-exhaust heat-exchanger sensible/latent/enthalpy efficiency. Help describes CW/DX and desiccant humidity-control modelling.

Engineering meaning:

- Plant loads are downstream of validated room and AHU loads.
- Preconditioning and heat recovery require an airflow/control topology, not a fixed multiplier.
- Pump and pipe provisions must be explicit and separated from terminal loads.

Current project status: **not implemented**. The hourly report explicitly excludes AHU coil effects, fan/duct effects, heat recovery and plant loads. Chiller/boiler/circuit/plant modelling remains a later milestone.

### 9. External surfaces, windows and skylights

The AHU External screens show an editable matrix for each room surface: exposure, height/width, shading schedule, wall/roof type, absorptivity, window/skylight type and count, direction, shade schedule, window spacing/offsets and a preview diagram. Buttons add surfaces or windows, and display options visualise overhang/reveal/outline geometry. The single view can open an exposure selector; the all view provides cross-room editing.

Engineering meaning:

- A surface should be a stable entity connected to construction, orientation, geometry, openings and shading.
- Openings are subordinate to or associated with a surface, rather than being a disconnected building-wide list.
- The detailed screen has both single-room review and all-room bulk-edit modes.

Current project status: the data boundary has stable `surface_id`s and individual solar schedule assignments. It does not yet support the full external-surface editor, construction/window library linkage, dimensional opening layout, graphical preview or automatic shadow geometry.

### 10. Exposure/orientation selector

The exposure dialog shows cardinal/inter-cardinal wall orientations with azimuths, an alternate southern-hemisphere azimuth entry, and roof options: sun, shade, sprayed, water-covered, or horizontal roof with shading. The screen demonstrates that a roof has different exposure classifications from a vertical wall.

Engineering meaning:

- Orientation must carry a declared azimuth convention and hemisphere context.
- Roof exposure needs a distinct taxonomy from wall orientation.
- A drawing north note and project/site orientation are prerequisites for reliable automated orientation assignment.

Current project status: surface orientation is retained as engineer-entered evidence; the site packet also has an optional north/orientation note. The backend does not calculate solar orientation, apply hemisphere-specific algorithms, or derive roof exposure from drawings.

### 11. Reported results and peak timing

The primary plant screenshot shows an AHU cooling summary beside primary plant results. It highlights a key distinction: a chiller grand total and boiler grand total are not simple sums of arbitrary individual room peaks; they are based on the selected calculation method, operating time and aggregation level.

Current project status: V1 calculates coincident room, zone and project **cooling** peaks at the same clock hour over engineer-entered 24-hour scenarios. It retains all tied peak hours and uses the earliest only as a deterministic display hour. It does not produce heating, AHU, coil, chiller or boiler results.

### 12. Window Types library and library-to-opening linkage

The additional Window Types dialog shows a reusable window definition table. Each type carries dimensions, U-value, shade factor, frame solar-factor correction, internal-shading selection and optional glass-reference/calculation rows. The surrounding External screen assigns a window type and number to a selected external surface.

Engineering meaning:

- A window type is reusable master data; an opening is an occurrence of that type on a particular surface.
- The occurrence needs quantity, dimensions/layout, exposure and shading association, while the type carries thermal/solar properties.
- The optional BEAVER/CAMEL fields demonstrate that calculated coefficients and raw manufacturer/library data should be kept distinguishable.

Current project status: the project supports a manually entered surface solar basis and stable surface IDs only. It does **not** yet have a separate window-type artifact, window instances, window quantities/dimensions, or a construction/glass-library relationship. Those should be introduced as an audited master-data layer rather than copied from CAMEL+ defaults.

### 13. Reusable shading schedules and visual selection

The Select Shading dialog lists named shade schedules (for example `Ohng`, `REV`, and `Ohng2`–`Ohng6`) and presents a visual preview of the selected profile. The External screen associates such a shading reference with a wall/opening and can visualise the combined façade geometry.

Engineering meaning:

- A shading reference is reusable master data distinct from the schedule used for people, lights or ventilation.
- The visual preview helps an engineer detect an incorrect overhang/reveal orientation before it becomes a calculated solar input.
- A surface should retain the identity and revision of the shading profile used, not only a derived multiplier.

Current project status: the hourly cooling model supports a reusable, explicitly cited 24-hour solar schedule assigned to an individual surface. It does not store graphical overhang/reveal profile definitions or calculate their solar obstruction. A future shading-library feature should preserve both geometry/profile evidence and the derived approved hourly solar fractions.

### 14. Adjacent-building shading geometry

The targeted help for `Adjacent shading` defines a separate obstruction object with **distance**, **height**, **width**, **left shift** and **depth**, and is explicit that these inputs are metres. Height is measured from the bottom of the shaded surface. A negative left shift places the obstruction within the left boundary; negative depth can represent a U-shaped adjacent building formed by side walls. The external-screen preview has plan and elevation modes for this geometry.

Engineering meaning:

- Adjacent shading is not equivalent to a generic shade factor: it is a three-dimensional obstruction relative to a target surface.
- The datum, units and sign convention need to be part of the persisted schema and validation—not merely UI guidance.
- The obstruction must be linked to a specific surface and its orientation before solar impact can be calculated.

Current project status: not implemented. The current report must continue to use an engineer-entered solar basis/schedule until a separately approved geometric shading method, coordinate system, solar-position method and verification cases are implemented.

### 15. Partitions, floors and ceilings as boundary-condition loads

The Partitions screen lists floor, ceiling and partition areas/U-values and assigns cooling/heating flags. Its supplied legend shows three boundary methods:

- `A`: add or subtract a stated temperature from the outdoor temperature;
- `C`: use a constant adjacent-space temperature;
- `P`: use a proportion of the temperature difference between the current space and ambient air.

The screen therefore treats floor/ceiling/partition loads as explicit thermal boundaries rather than as undifferentiated room mass.

Engineering meaning:

- Each partition needs its area, U-value, adjacent-condition method, method value, source and cooling/heating applicability.
- The `A`, `C` and `P` approaches are different modelling assumptions and must be displayed in reports.
- A future implementation must distinguish external surfaces, conditioned neighbours, unconditioned spaces and ground/floor boundaries.

Current project status: partitions and adjacent-condition heat transfer are explicitly excluded from V1. The future schema should avoid opaque one-letter flags; it should store a named method, units, cited basis, adjacent-zone reference where applicable, and separately reviewed cooling/heating values.

### 16. Select U-Value library for partitions

The additional `Select U-Value` dialog filters a material/construction library by categories such as all, concrete, timber and chipboard. Each entry displays a U-value, surface density and construction description; the visible examples distinguish concrete thicknesses and finishes such as vinyl tiles or carpet/underlay.

Engineering meaning:

- The same partition boundary needs both thermal transmittance and mass-related surface density when dynamic storage is modelled.
- Floor finish changes can materially alter both U-value and effective thermal response, so the construction description/version must be retained with the selected value.
- A selection dialog is master data; it is not proof that an arbitrary listed construction is present in the project.

Current project status: no U-value or density library is bundled. The engineer can enter and cite an approved U-value/surface basis in the existing preliminary envelope inputs. Future master data must be versioned, explicitly selected, and never silently substituted for drawing or manufacturer evidence.

### 17. Internal people, lighting, sensible, latent and steam gains

The AHU Internal screen separates three source groups:

- **People:** units, load, schedule number/title, return-air percentage, heating percentage, activity ID and activity description;
- **Light:** units, load, schedule, return-air/heating allocation and lighting type/description;
- **Sensible / Latent / Steam:** independently scheduled non-people internal gains, with return-air and heating allocations.

Engineering meaning:

- People, lights, equipment/process sensible heat, latent gain and steam should remain distinct components through calculation and reporting.
- Every time-varying gain needs a named schedule; every gain may also require an air-system allocation (for example, whether some heat reaches return air) that belongs to a later AHU model rather than a room-only model.
- The percentages for return air and heating are system/model assumptions, not generic room-load multipliers.

Current project status: the hourly cooling model schedules people sensible/latent gains, lighting, and individual equipment/refrigeration sources separately. It does not yet model steam as a separate source type, return-air allocation, heating allocation, AHU return-air effects, or a UI/internal-gain library.

### 18. Reusable operating-schedule catalogue

The `Select Schedule` dialog lists named profiles such as `8am-5pm`, `7am-10pm`, `8am-7pm` and `Restaurant`, and shows a visual hourly profile before applying it. The selected profile is then referenced from people, lighting and other internal-gain rows.

Engineering meaning:

- A schedule is reusable master data, while an internal-gain row is an assignment/instance.
- The preview is useful validation: a title alone is insufficient evidence of an hourly shape.
- A schedule definition should have a revision, source/citation, day-type coverage and explicit hourly values.

Current project status: implemented in an evidence-first form. `schedule_library.json` stores engineer-entered 24-hour values for weekday, Saturday and Sunday/holiday; there is no implied operating window or fallback day type. It supports assignment to people, lighting, outside air, equipment/refrigeration and solar surfaces. The current API does not recreate the CAMEL+ visual schedule picker.

### 19. Activity catalogue and people sensible/latent heat gains

The `Select Activity` dialog maps an activity ID to representative people activities and typical applications, including seated/resting, seated/standing, retail, airport terminal, bank, restaurant, factory/light work, dancing and heavy work. Its targeted help states that the selected activity determines people sensible and latent heat via metabolic rate, and the visible table presents sensible and latent heat gain at 22 °C.

Engineering meaning:

- Activity category, occupancy count and operating schedule are separate inputs.
- Sensible and latent gains must be retained separately; the apparent table values are condition-dependent reference values, not universal constants.
- A future activity library needs source/version, reference temperature/conditions, applicability, and a clear engineer override path.

Current project status: people sensible and latent W/person values are engineer-entered and cited in the design requirements/room overlay, then independently scheduled. No CAMEL+ activity table, activity ID default, metabolic-rate conversion, or automatic gain selection is embedded.

### 20. Lighting-type catalogue and heat-path context

The `Select Lights` dialog distinguishes fluorescent and incandescent luminaires, each offered as exposed or recessed variants. The recessed variants further distinguish whether the suspended ceiling has plenum return air. Visible examples include `FLE` (fluorescent exposed), `FLR` (fluorescent recessed), `FLP` (fluorescent recessed with plenum return air), `ILE` (incandescent exposed) and `ILP` (incandescent recessed with plenum return system).

Engineering meaning:

- Fixture technology, mounting condition and return-air path are separate characteristics; they should not be reduced to an unexplained lighting W/m² number when an AHU model is involved.
- A plenum-return classification may change how lighting heat is apportioned between the conditioned space and return-air stream. That is an air-system interaction, not a generic room-load assumption.
- The catalogue supports a reportable source description for a lighting input, but it is not evidence that a listed fixture exists in the project.

Current project status: lighting W/m²/diversity basis and its hourly schedule are supported as engineer-entered, cited room inputs. Lighting fixture type, exposed/recessed/plenum classification and return-air heat allocation are not yet modelled. They remain future AHU-layer data and must not be inferred from a chosen W/m² value.

### 21. Calculation-output selection and auditable project comments

The `Calculations - Project` screen provides a report/template selector and selectable output sections, including project details, configuration, input data, cooling/heating check figures, AHU cooling summary, chiller/boiler/circuits, outside-air temperatures and window shading effectiveness. The shown Comment output records a specific audit statement against several VAV AHUs: outside air was entered or changed in rooms and proportioned by room supply-air quantity.

Engineering meaning:

- A calculation package must disclose both selected outputs and material modelling overrides/allocations.
- A free-text comment is useful evidence, but a machine-readable calculation record should also expose the affected entities, allocation rule, source, status and timestamp so the condition can be validated and queried.
- Room-level outside-air changes and AHU-level reporting imply a dependency that must be explicit before system aggregation is calculated.

Current project status: V1 retains cited inputs, per-artifact timestamps, blocked/provisional status and component-level hourly reports. It does not yet provide a general report-template system, an AHU-specific audit-comment model, or an AHU allocation calculation. The observed CAMEL+ comment is recorded as product evidence, not implemented behaviour.

### 22. Project-details report: weather, design conditions and coverage

The Project Details output demonstrates a calculation report that prints its build/version, weather-data reference/date, location, latitude/hemisphere, daily range, building rotation, elevation, winter outdoor design DB/RH, total floor area and plant/pre-conditioner coverage. The screenshot contains example Melbourne weather/location values and a sample pre-conditioner area; those values belong to that displayed CAMEL+ example only.

Engineering meaning:

- A defensible result should identify the design-condition basis and enough site context to reproduce it: source/version/date, station/location, orientation convention, elevation/pressure basis and applicable design values.
- Area/coverage figures need a declared scope: total project area, area assigned to an AHU and area served by a pre-conditioner are not interchangeable.
- A report should distinguish data imported from a weather reference from data entered or confirmed by the engineer.

Current project status: the separate `site_design_conditions.json` packet stores site identity, weather/basis reference, orientation note, supplied elevation/pressure, summer/winter conditions and field-level source/status. It intentionally does not auto-select weather data or populate the cooling model. Total project/plant coverage reporting and automatic printout integration remain future work.

### 23. AHU cooling summary and cooling/heating check figures

The AHU Cooling Summary report presents AHU-level coincident cooling information at the time of peak grand total heat, including supply/outside-air quantities, total and sensible heat, coil entering/leaving conditions and pre-conditioner rows. A supplementary table compares adjusted sensible-load timing with total-heat timing and records revised air quantity or leaving-coil condition alternatives. The separate Cooling Check Figures & Heating report presents cooling checks, heating values, fan figures and a whole-building peak-time line.

Engineering meaning:

- System sizing requires a distinct aggregation layer above rooms/zones: AHU airflows, coil states, pre-conditioning, fan heat and adjustment rules cannot be represented faithfully as simple room-total arithmetic.
- A report must identify the governing time for each result; adjusted sensible peak, grand-total peak and whole-building peak can differ.
- Cooling and heating outputs must remain method-specific. A heading or blank calculation column is not evidence of a working heating engine.

Current project status: V1 provides room, zone and project **cooling** coincidence for the same hour and preserves tied peak hours. It does not calculate AHU supply/return/outside-air aggregation, coil conditions, pre-conditioners, fan heat, revised-air alternatives, heating checks or plant totals. These screenshots reinforce that the planned AHU and heating milestones need their own approved calculation method and validation cases.

### 24. Primary chiller and boiler plant roll-up

The `Chiller, Boiler & Circuits` result shows a primary-plant report that explicitly excludes unitary plant. The cooling section combines an air-handling load at its stated design time with explicit provisions for chiller pumps and pipe gains before showing a chiller grand total. The heating section separately combines air-handling load, outside-air-through-AHU treatment, a stated warm-up allowance, boiler-pump provision and boiler-pipe losses before showing a boiler grand total.

Engineering meaning:

- Plant sizing is a separate, traceable calculation stage. It must identify which AHUs and unitary equipment are included, the governing time, ancillary allowances and each contribution's source/status.
- Cooling and heating plant roll-ups differ in both inputs and method; one cannot be derived by relabelling the other.
- Pump, pipe-loss and warm-up provisions are engineering assumptions that require their own cited values and review status, rather than silent percentages.

Current project status: not implemented. The hourly model stops at coincident project cooling loads and has no plant ownership hierarchy, pump/pipe model, warm-up model, boiler result or chiller result. The screenshot's numerical totals and allowances are CAMEL+ example outputs only and are not used by this project.

### 25. Room cooling-load chart as a calculation trace

The Load Charts screen selects one room and displays its cooling calculation at a named time/month. Its printed trace exposes the solar position, AHU operating window, glass solar contribution, wall/roof solar and transmission contributions, other transmission terms (including glass, partition and infiltration), internal people/lights/appliance gains, safety factor, supply-duct heat/leakage allowance, and separate sensible and latent subtotals. The two supplied close-up images are continuations of the same room trace, including adjusted sensible and latent results.

Engineering meaning:

- A trustworthy peak result needs a component-level explanation at the exact governing hour, not just a final total.
- Inputs and intermediate terms need stable identifiers and units so an engineer can reconcile each line to the room, surface, schedule and scenario that produced it.
- The screen demonstrates why sensible, latent, safety, duct and adjusted values should be reported as distinct stages rather than merged prematurely.

Current project status: implemented in a deliberately narrower scope. `hourly_load_report.json` stores hour-by-hour room/zone/project component totals, base/scheduled sensible and latent values, subtotal, safety allowance and design total; APIs expose the report and governing/tied peak hours. V1 does **not** calculate solar geometry/glass coefficients, partitions, infiltration, supply/return duct gains, fan heat or CAMEL+ adjusted-room terms. Those omissions remain explicit exclusions in each report.

### 26. AHU cooling load chart and system-level reconciliation

The AHUs result screen selects an AHU and reports its system type, temperature-control location, plant/pre-conditioner connection, served area/volume, operating hours and load-chart peak time. It then reconciles accumulated adjusted room sensible/latent heat with supply-fan draw-through, outside-air sensible/latent effects, return-duct external gain, return-fan heat and exhaust-return-duct correction, ending with cooling grand-total, sensible and latent heat.

Engineering meaning:

- AHU aggregation requires a validated mapping from rooms/zones to the AHU, a declared system type and air-path topology.
- Coincident room loads become an AHU coil/air-system calculation only after outside-air location, fan placement, return/exhaust paths, duct gains and pre-conditioning are resolved.
- Cooling psychrometrics, outside-air summary and heating load charts are separate outputs because their methods and states differ.

Current project status: not implemented. The data model intentionally preserves `zone_id` aggregation but has no authoritative AHU hierarchy/system topology or coil/duct/fan/pre-conditioner calculation. The current project peak should therefore never be presented as an AHU coil or chiller duty.

### 27. Zone-and-room cooling results and air-allocation rules

The Zones & Rooms Cooling Results screen reports an AHU, its zones and individual rooms with adjusted sensible/latent loads, supply air, VAV turndown, outside air, heating and room-condition fields. Its notes make several assumptions visible: room supply air is shown as a proportion of zone air quantity at zone peak; room minimum/maximum values estimate room-temperature variation; a room sensible maximum may exceed AHU sensible heat; and VAV turndown is constrained by entered values and minimum room supply air.

Engineering meaning:

- Zone and room peaks cannot be treated as simple independent maxima once a VAV/air-distribution model is introduced; the reporting scope and governing time need to be explicit.
- Air allocation needs a declared rule, source, inputs and exceptions. A percentage is not self-explanatory without identifying the zone peak and design flow it refers to.
- Comfort-condition estimates, VAV limits and heating values require separate validated methods from the room cooling-load calculation.

Current project status: V1 produces truthful same-hour room, zone and project cooling totals, but does not allocate zone airflow to rooms, model VAV turndown, calculate room-condition variation or produce heating results. Any later zone-air allocation must remain a labelled, reviewed system-layer calculation rather than an implicit adjustment to room loads.

### 28. Psychrometric chart and state-point audit view

The Psychrometrics screen selects an AHU and draws labelled state points/process lines on an elevation-specific psychrometric chart. Display toggles include dry bulb, wet bulb, moisture, relative humidity, data and labels; the chart annotates an apparatus dew point. The screenshot's plotted values and elevation belong to the displayed CAMEL+ example.

Engineering meaning:

- Psychrometric visualisation is an audit aid for air-side calculations, not a replacement for a defined psychrometric calculation method.
- Pressure/elevation basis, state-point labels, units and the transformation between points must all be persisted for a chart to be reproducible.
- A chart should only display calculated, cited or engineer-confirmed states; it must not fabricate missing coil, mixed-air or supply-air conditions.

Current project status: V1 uses existing psychrometric functions for hourly outside-air sensible/latent load and requires scenario pressure. It does not calculate AHU state points, coil apparatus-dew-point conditions or a psychrometric chart. A future chart must be downstream of an approved AHU/coil method, not an independently drawn feature.

### 29. Hour-by-month tables and graph outputs

The Graph screen plots a selected metric against hour, with one series per month; visible selectors span chiller, circuit, AHU, zone, room and pre-conditioner equipment, and metrics including grand-total, adjusted sensible/latent, supply-air quantity and outside-air sensible/latent. The Tables screens present the same style of hour-by-month matrix for chiller grand-total heat and room adjusted sensible heat, explicitly printing the maximum value and its month/hour.

Engineering meaning:

- Hour-by-month matrices are annual/design-month result products, not interchangeable with one approved cooling design day.
- Each series must identify its scenario/weather source, equipment/room scope, metric definition, units and whether it represents coincident total, adjusted load or an individual component.
- The reported maximum must retain its exact hour/month and the full contributing input set, so it can be reconciled rather than treated as an opaque sizing number.

Current project status: the hourly report stores all 24 values for each selected, engineer-entered cooling scenario and reports governing/tied project peak hours. It does not build an annual or month-by-month weather simulation, a chart UI, or chiller/AHU results. Future visualisations can safely plot current report data only when labelled by scenario and calculation status; monthly curves require a separately approved annual-data milestone.

### 30. Interactive shadow visualisation

The Shadow screen selects a room and surface, shows a declared orientation, and visualises a façade/opening with overhangs, reveals and adjacent obstruction. It offers display toggles, outline/hatched/solid styles, zoom and an animated day/year playback driven by month, time, latitude, altitude and azimuth. Plan/elevation-style geometry is shown below the façade preview.

Engineering meaning:

- A shading visualiser must declare its coordinate system, north/azimuth convention, hemisphere, date/time, solar-position method and geometric input units.
- The view is a quality-assurance tool: it should reveal the exact surface/opening geometry and solar state used by the calculation, rather than produce an unexplained visual approximation.
- Day/year animation requires a calendar/solar model and must be distinguished from the V1 engineer-entered hourly solar fraction.

Current project status: not implemented. V1 permits a cited hourly solar schedule per surface, but has no geometry engine, solar-position calculation, animation, plan/elevation rendering or annual calendar. The current manual solar basis must remain authoritative until an approved geometric method and verification suite are available.

### 31. Printable calculation package and export review

The Printing view composes selected report sections into a formatted calculation package, includes product/version and an explicit independent-verification warning, and exposes print/document/export controls. The final screenshot shows a browser print preview with a six-page package and `Save as PDF` destination, page selection and portrait-layout controls.

Engineering meaning:

- Issued calculations need immutable or versioned inputs, result timestamps, calculation status, scope/exclusions and source references; PDF output alone does not establish traceability.
- The export must identify whether results are confirmed, provisional or blocked, and it must preserve the report's governing scenario/hour and component breakdown.
- A print template is presentation functionality. It should only consume already-validated artifacts and must never alter values or conceal warnings during export.

Current project status: API responses and JSON artifacts provide timestamps, source/status information and exclusions, but no printable template engine or PDF export is implemented. A later reporting milestone should build a versioned export packet from current artifacts, with the same readiness/staleness state visible in the rendered document.

## Current backend implementation snapshot

### Implemented

- Dedicated, cited site/design-condition packet.
- Engineer-entered, reusable 24-hour schedules for weekday, Saturday and Sunday/holiday.
- Cited hourly cooling design-day DB/WB and pressure scenarios with physical validation (`WB ≤ DB`).
- A reviewed room-within-zone overlay separate from `design_requirements.json`.
- Explicit schedules for people, lighting, outside air, individual equipment/refrigeration sources and each solar-bearing surface.
- Hourly people, lights, equipment/refrigeration, envelope conduction, manual solar and psychrometric outside-air loads.
- Per-hour sensible, latent, subtotal, safety allowance and post-safety total.
- Room, zone and coincident project aggregation; tied peaks and provisional/blocked states.
- Artifact staleness when source design requirements change.
- JSON API contracts and a parity adapter that exposes current hourly project peak/components without claiming CAMEL+/DA09 parity.

### Deliberately excluded from V1

- CAMEL+ proprietary/default data tables and automatic selection logic.
- Automatic weather lookup, climate data, schedule defaults or solar profiles.
- Dynamic thermal mass/storage, infiltration, vapour gain and detailed glazing physics.
- Geometric shading, glazing/frame corrections and manufacturer libraries.
- Partitions/adjacent-space heat transfer.
- AHU system types, thermostat/air distribution, coil calculations, fans, ducts and heat recovery.
- Chiller, boiler, piping, pumps, circuits and plant aggregation.
- Heating calculation, annual simulation, dated calendars and sub-hourly modelling.
- Any frontend recreation of the screenshots.

## Recommended implementation order from the visual evidence

1. **Room physical model completion:** introduce cited storage mass, infiltration, vapour gain, minimum supply and transfer/exhaust inputs, but keep them blocked until their calculation method is approved.
2. **Surface/opening model:** add separately reviewed external surfaces, openings, orientation/azimuth and construction/window references; do not infer thermal performance from a screenshot or library label.
3. **Dynamic envelope and shading:** only after an approved method, add storage mass, partitions, detailed glazing and geometric shading calculations with explicit validation cases.
4. **AHU system layer:** establish authoritative AHU → zone → room mapping and selected system type before adding coils, fans, ducts, heat recovery or preconditioning.
5. **Plant layer:** add circuits and plant only after AHU loads are modelled and an approved aggregation/timing method is selected.
6. **Heating and parity:** build a separate approved heating engine, then use authorised CAMEL+/DA09 reference cases to reconcile inputs, timing, components and tolerance. Do not claim numeric parity before that step.

## Implementation location

The current work is in `/Users/jeremyzheng/Documents/GitHub/mech-page-finder-vision-lab`.

- Hourly cooling engine: `ai/hourly_loads.py`
- API integration: `backend/web_app.py`
- API handoff document: `docs/hourly_schedules_api.md`
- Current scope checklist: `TODO.md`

Per-project calculation artifacts are saved beneath that project's review folder as `schedule_library.json`, `design_day_scenarios.json`, `hourly_load_model.json`, and `hourly_load_report.json`. They are intentionally separate from `design_requirements.json` and from the Archie workstream.
