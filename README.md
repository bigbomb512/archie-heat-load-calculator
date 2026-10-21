# Archie Heat Load Calculator

Dedicated evidence-first cooling and heating load-calculation project for Archie. Archie is an in-house tool used by two operators to calculate heat loads from client PDFs.

This repository owns load inputs, schedules, design-day scenarios, room/zone load models, calculation reports, and calculation readiness. It also retains the PDF intake and evidence-to-AI handoff pipeline needed to collect cited project facts.

It does **not** own HVAC layout, equipment placement, routing, CAD actions, or drawing-file generation. Those remain in the broader Archie design project and may consume reviewed calculator outputs through a later API/artifact contract.

## Current capability

- PDF page triage, rendering, review packets, spatial OCR, and cited building evidence.
- Manual AI packets for visual/evidence review.
- External-research handoff packets for a web-enabled AI or researcher; every external fact must have a direct citation and remains review-required.
- Evidence-aware hourly cooling reports with explicit blocked, draft, and review-ready states.
- Reviewed per-project envelope libraries and boundary models, including
  exterior, fixed-adjacent-temperature and gated ground-contact steady-state
  conduction, reviewed glazing with manual hourly solar, controlled geometric
  shading records, and gated first-order RC thermal-mass/cited surface-
  irradiance contracts.
- Site conditions, schedules, design-day scenarios, reviewed floor/zone/room overlays, readiness, and parity-report scaffolding.
- Room-owned evidence records for unsupported airflow and moisture/process inputs. They are captured as confirmed absent, stored-not-calculated, or unassessed; they never silently change cooling totals.
- Evidence-to-calculator draft bridge: cited drawing/thermal evidence becomes versioned proposals that an operator can accept, edit, reject, or mark as needing evidence before anything is applied to the hourly model.

The current cooling method is limited to its declared inputs. Infiltration can
contribute only when its project method gate, air-path declaration, schedule,
volume and cited room input are complete. Stored transfer/extract/make-up air,
vapour/steam, and process latent inputs remain explicit exclusions until their
methods exist. Glazing, geometric shading, partitions and ground contact are
similarly gated and remain excluded when evidence or approval is incomplete.
This is not CAMEL+/DA09 parity, equipment selection, heating design, AHU/plant
analysis, or annual analysis. The advanced RC and irradiance methods are not
enabled merely because their artifacts exist: each requires its own approved
method gate plus complete cited surface/source records.

## Run locally

```bash
git clone https://github.com/bigbomb512/archie-heat-load-calculator.git
cd archie-heat-load-calculator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
brew install poppler
./start_web --port 8000
```

Open `http://127.0.0.1:8000`. It opens the job workspace: Jobs (with New job, Import a job and Export per job),
then Drawings, Rooms, Operating loads and Results per job (Results creates the report). The old app, its engineer
tools and the manual AI task review were removed on 2026-10-19.

Before opening a pull request, run the dependency-free quality and repository
hygiene checks:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_python_quality.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 tests/test_repository_hygiene.py
```

For every meaningful code change, the mandatory developer smoke gate runs the
same checks plus the independent calculation sanity vectors, regression scripts,
frontend syntax/contract checks, and a Git whitespace check:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 tools/mandatory_smoke_gate.py
```

It intentionally omits the local-socket video test in restricted environments;
run `python3 tools/mandatory_smoke_gate.py --include-video` when socket binding
is permitted. A passing gate is development confidence only, not engineering or
CAMEL+ validation.

`start_web` uses `.venv/bin/python` when available, otherwise it uses `python3` (or
the optional `PYTHON_BIN` environment variable). It never relies on a developer's
personal machine path. Dependencies for PDF processing are listed in
`requirements.txt`; Poppler is required for rendered page previews.

## Evidence-to-research flow

```text
PDF drawings
→ reviewed PDF/evidence packet
→ manual AI visual review and/or web-enabled research handoff
→ cited proposed facts
→ operator review
→ calculator draft (`calculator_draft.json`)
→ save review → preview conflicts → apply accepted records
→ calculator input artifacts
→ provisional or confirmed load report
```

The bridge API is `GET`/`POST /api/calculator-draft`. Building and saving review
decisions never changes calculator artifacts. `preview_apply` reports creates,
empty fields, existing records, conflicts and missing dependencies; `apply`
performs additive, fingerprint-checked writes with recoverable staging. Accepted
evidence is not the same as a complete calculation input: unresolved occupancy,
schedules, airflow, envelope or other required fields remain visible in
readiness and keep the cooling report draft or blocked.

See [the reviewed envelope guide](docs/reviewed_envelope_slice.md), [the cooling workflow runbook](docs/cooling_workflow_runbook.md),
and [the cooling roadmap](docs/cool_heat_load_roadmap.md).

For a private drawing-based case, use `tools/create_reviewed_cooling_case.py`.
It requires a named reviewer manifest, keeps the source PDF outside GitHub, and
creates only proposal/evidence artifacts until the review is explicitly applied.

If the evidence packet already has its coverage, evidence-fusion, thermal, and
calculator-draft artifacts, run the additive bootstrap before opening the
calculator:

```bash
PYTHONPATH=. python3 tools/create_reviewed_cooling_case.py \
  --source-dir /private/path/evidence-packet \
  --output-dir output/web_review/private-reviewed-cooling-case \
  --bootstrap
```

Bootstrap creates only missing `project_context.json`,
`calculator_input_overrides.json`, and `hourly_load_model.json`; existing
authored files and private PDFs are not overwritten or copied.

## Design weather (AIRAH Technical Handbook)

Each computer that runs Archie imports the design-temperature table once from the operators' own copy of the AIRAH
Technical Handbook (4th edition, 2007). The table is AIRAH's copyright, so it is kept outside the repository:

```bash
python3 tools/import_airah_handbook_design_temps.py ~/Downloads/AIRAH_Handbook.pdf
```

It is written to `~/.archie/airah_handbook_2007_design_temperatures.json` (or `ARCHIE_DESIGN_WEATHER_TABLE`). On the
Project tab, **Weather location** picks the listed location for the job (suggested from the address's town or state,
or from a confirmed map position); that choice is used first. Otherwise, when a job's site position is confirmed, the
calculation uses the nearest listed location: the design dry bulb with its
coincident wet bulb sizes the load, and the design wet bulb with its coincident dry bulb is reported as a
dehumidification check. Results name the location and its distance from the site. Without a confirmed site or the
table, a generic Australian design day is used and labelled so. A released AIRAH DA09 pack, when imported, takes
precedence. People's heat by activity and the supply fan's heat also come from the handbook (`config/ai_preliminary_assumption_pack.json`).

The wall and roof U-values are imported the same way (to `~/.archie/airah_handbook_2007_u_values.json`, or
`ARCHIE_U_VALUE_TABLE`):

```bash
python3 tools/import_airah_handbook_u_values.py ~/Downloads/AIRAH_Handbook.pdf
```

On the What-we-need-to-find list, a "Wall or roof construction (U-value)" answer picks one of the handbook's wall or
roof constructions (roofs use the summer value) or takes a U-value typed from another source, for every room or one
room. Answered external walls and exposed roofs use it; the others keep the preliminary U-values (walls 0.6, roof
0.5 W/m²K), which the Results tab lists as typical. The handbook has no sun-on-surface (sol-air) data, so sun on walls
stays the preliminary pack's allowance and roof sun is not assessed until DA09 or ASHRAE data is available.

## Walls to unconditioned spaces

On the Walls tab a traced wall can face an "Unconditioned space (plant room, store, dock)". Neither the handbook nor
the app supplies a temperature for such a space, so it is never guessed: the wall is left out, and the Results tab
lists it as to find, until the operators answer "Temperature of an unconditioned space beyond a wall" (for every room
or one room). The wall is then conducted against that temperature (area × U × (temperature beyond − room), no sun),
using the answered wall construction or the preliminary wall U-value. Neighbouring tenancies, enclosed malls and
internal walls stay excluded, as conditioned on both sides; refrigerated rooms are their own scope.

## Kitchen appliance heat (data sheets)

The handbook has no table of appliance heat gains (that is AIRAH DA09 or ASHRAE Fundamentals ch. 18), so cooking,
dishwashing and hot-drink equipment are not given typical ratings. On the Drawings tab, each equipment item takes its
heat to the room from the manufacturer's data sheet (sensible W each, moisture W each, and the make and model it came
from); this replaces rated W × the generic heat-to-room factor, adds the moisture to the room's latent load, and an
accepted item can be changed later ("Change this item"). Results list kitchen appliances read from the drawings that
are not yet in the calculation.

## AI provider for the PDF review

Pass 1 (every page), pass 2 (values) and the skills use one AI provider, chosen when the server starts:

| `ARCHIE_AI_PROVIDER` | Needs | Model (`ARCHIE_AI_MODEL`) |
|---|---|---|
| `openrouter` (default) | `OPENROUTER_API_KEY` | the tested mix per step (below) unless `ARCHIE_AI_MODEL` or a step setting is given. Hosts that store or train on prompts are excluded; `ARCHIE_OPENROUTER_PROVIDERS` pins the host, `ARCHIE_OPENROUTER_ZDR=1` requires zero data retention. Each call's US$ cost is logged and totalled. Without the key the review stops and says so; it never falls back to another provider. |
| `codex_cli` | The Codex CLI installed and signed in with ChatGPT (`codex login`); used only when chosen | the CLI's default |
| `openai` | `OPENAI_API_KEY` | `gpt-5` unless set |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` unless set |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-flash` unless set (DeepSeek's model that reads page images; `deepseek-v4-pro` is text only and can't read pages) |

The team uses OpenRouter only (decided 2026-10-11): with just the key set, every step goes through OpenRouter on
the tested model mix (DeepSeek V4.1 Flash reads the pages and runs the skills; Gemini 3.8 Flash reads the equipment,
two pages per call; replies up to 48,000 tokens). Another provider is used only if `ARCHIE_AI_PROVIDER` names it,
e.g. `ARCHIE_AI_PROVIDER=anthropic ANTHROPIC_API_KEY=… ./start_web`. Tests blank every AI key, so they never make paid calls.
Each step can use its own model: `ARCHIE_AI_MODEL_PASS1` (what is on each page), `ARCHIE_AI_MODEL_PASS2` (equipment
values) and `ARCHIE_AI_MODEL_SKILLS` (the skills and the needs list); on OpenRouter, `ARCHIE_OPENROUTER_PROVIDERS_PASS1`
(and `_PASS2`, `_SKILLS`) pins a step's hosts. `ARCHIE_PASS2_BATCH` sets equipment pages per call (else
`ARCHIE_PAGE_BATCH`, default 4). In tests on Butcher Buffet (2026-10-09/10) DeepSeek V4.1 Flash read pages and wrote
the needs list well and very cheaply but missed equipment drawn on plans; Gemini 3.8 Flash read plan equipment well
two pages per call.
Gemini thinks at low effort for equipment and the drawing tasks (`ARCHIE_OPENROUTER_REASONING_PASS2` and
`_DRAWINGS`; `minimal`, `low`, `medium`, `high`, or `default` for the model's own setting). Compared on the four
answer-key sets (2026-10-18, `tools/compare_vision_models.py`), its default thinking was about 95% of what it wrote;
at low effort it scored the same or better on every keyed task and cost 4–30 times less (Butcher Buffet drawing
tasks US$0.28 to 0.06, equipment US$0.34 to 0.07). Qwen 3.8 Flash can't be used: its only host may store prompts.
Equipment is read from the schedule pages first; the other pages are then read with the schedule's codes, so a plan
label ("COMBI OVEN") comes back as the scheduled item (E06) instead of a second copy (Butcher Buffet on Gemini: 80
items without the codes, 48 with them). When the schedule is printed as text (most CAD sets), Archie reads its rows
itself (code, name, size, quantity): the plans are read with every printed code, a code the AI misreads ("E49" for
"E48 6 BURNER") is corrected to the row its name and size match, and a plan label that matches exactly one row by
name and width, or by width and depth ("UB FR 1500" is E21 UB FRIDGE 1500), is tied to that row and counted once, with
a note on its card. A scheduled item's size and quantity are the printed row's. A label that could be two rows
("UB FRIDGE" with no size, "OVEN" with a combi and a pizza oven) is not guessed: its card says which items it may be,
for the operator to reject. Sizes that agree in their
numbers ("4900X700" on a plan, "4900MM(W)X700MM(D)X850MM(H)" in the schedule) are not reported as a disagreement.
After the equipment, the AI answers the drawing tasks itself (backend/drawing_tasks_service.py): the scale from a
printed dimension, the room names, the room outlines, north, what each wall faces, shopfront openings and kitchen
equipment, in rounds (each step unlocks the next), each reply checked as a pasted one was. Coordinates are asked for
on a 0–1000 grid and converted to pixels (vision models answer on that grid whatever the image size). The skills run
when it finishes, so they see the traced rooms. On Butcher Buffet (2026-10-11) the AI's outlines came within 1–3 % of
the Kitchen, Coolroom and Freezer areas; the Bar island was missed (so the Shop outline included it), and a full run
cost about US$0.30. A task still refused after two tries waits for a person; the manual P0–P6 route remains.
A skill's citations may be written as sentences naming the page ("c1: p21: …"): they count by that page, which must
be in the job's case file; a number whose references name no page of the file is still refused.
The APIs cap each reply at 16,000 tokens; `ARCHIE_MAX_OUTPUT_TOKENS` changes it. A model that thinks at length counts
its thinking against the cap, so it may need more (DeepSeek V4.1 Flash used about 10,000 tokens of thinking per call).
Every AI call is logged per job in `ai_usage.jsonl` (pass, provider, model, seconds, tokens where reported); the
Drawings tab shows the totals under "AI use for this job". The Codex CLI reports one combined token count per call;
the APIs report input and output separately. OpenRouter also reports each call's US$ cost, which is shown and totalled;
for the other APIs no prices are stored, so multiply the tokens by your provider's current rates.

### Setting up a computer to use the team's OpenRouter account

The AI key belongs to the computer that runs the Archie server, not to the person using the app: the browser never
talks to the AI, the server does, with the key in its own environment. The server only listens on `127.0.0.1`, so
each person who runs Archie on their own laptop needs a key on that laptop. Everyone can bill the same OpenRouter
account; give each person their own key so their spending is visible and limited, and can be revoked on its own.

1. **Account owner: make a key for the person.** On openrouter.ai, Settings → API Keys → Create Key. Name it after
   the person (e.g. "Archie – Sam") and set a credit limit (e.g. US$5). It draws on the same account credit.
2. **Hand the key over safely.** Type it in on their Mac yourself, read it out, or use a password manager's secure
   share. Never send it by email, chat or text, and never put it in this repository or a project file.
3. **On their Mac: store the key in the Keychain.** In Terminal (it asks for the key without showing it):

   ```bash
   security add-generic-password -a "$USER" -s openrouter -w
   ```

4. **On their Mac: add this line to `~/.zshrc`** (open it with `open -e ~/.zshrc`). It reads the key from the
   Keychain. OpenRouter and the tested model mix are Archie's defaults, so nothing else is needed:

   ```bash
   export OPENROUTER_API_KEY="$(security find-generic-password -s openrouter -w 2>/dev/null)"
   ```

5. **Open a new Terminal window, then start Archie** with `./start_web` from the project folder. To check the key is
   there without showing it:

   ```bash
   [ -n "$OPENROUTER_API_KEY" ] && echo "key found" || echo "key missing"
   ```

6. **Check in the app.** On a job's Drawings tab, "AI use for this job" names OpenRouter and the model, and shows the
   cost of each job. A full review of Butcher Buffet (38 pages) cost about US$0.47 on this mix (2026-10-10).

**Replacing a key** (it expired or leaked): store the new one with `security add-generic-password -U -a "$USER" -s
openrouter -w` (`-U` replaces the old entry), then stop Archie (Ctrl-C) and start it again with `./start_web`.
`start_web` reads the key from the Keychain each time it starts, so an old Terminal window can't pass the old key on.

If a key leaks, delete it on OpenRouter; the other keys keep working. A single shared server with one
key for everyone needs a login and a hosted machine, which is later work.
