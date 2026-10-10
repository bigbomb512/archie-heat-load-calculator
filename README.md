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

Open `http://127.0.0.1:8000`.

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

Run the research handoff after a reviewed `ai_input.json` exists:

```bash
PYTHONPATH=. python3 ai/research_packet.py output/review/<project>/ai_input.json
```

See [the research handoff guide](docs/ai_research_handoff.md), [the reviewed
envelope guide](docs/reviewed_envelope_slice.md), [the cooling workflow runbook](docs/cooling_workflow_runbook.md),
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

It is written to `~/.archie/airah_handbook_2007_design_temperatures.json` (or `ARCHIE_DESIGN_WEATHER_TABLE`). When a
job's site is confirmed (Project tab), the calculation uses the nearest listed location: the design dry bulb with its
coincident wet bulb sizes the load, and the design wet bulb with its coincident dry bulb is reported as a
dehumidification check. Results name the location and its distance from the site. Without a confirmed site or the
table, a generic Australian design day is used and labelled so. A released AIRAH DA09 pack, when imported, takes
precedence. People's heat by activity and the supply fan's heat also come from the handbook (`config/ai_preliminary_assumption_pack.json`).

## AI provider for the PDF review

Pass 1 (every page), pass 2 (values) and the skills use one AI provider, chosen when the server starts:

| `ARCHIE_AI_PROVIDER` | Needs | Model (`ARCHIE_AI_MODEL`) |
|---|---|---|
| `codex_cli` (default) | The Codex CLI installed and signed in with ChatGPT (`codex login`) | the CLI's default |
| `openai` | `OPENAI_API_KEY` | `gpt-5` unless set |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-5-5` unless set |
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-flash` unless set (DeepSeek's model that reads page images; `deepseek-v4-pro` is text only and can't read pages) |
| `openrouter` | `OPENROUTER_API_KEY` | `google/gemini-3.8-flash` unless set (any OpenRouter model ID that reads images). Hosts that store or train on prompts are excluded; `ARCHIE_OPENROUTER_PROVIDERS` pins the host, `ARCHIE_OPENROUTER_ZDR=1` requires zero data retention. Each call's US$ cost is logged and totalled. |

For example `ARCHIE_AI_PROVIDER=anthropic ANTHROPIC_API_KEY=… ./start_web`. Nothing else changes when switching.
Each step can use its own model: `ARCHIE_AI_MODEL_PASS1` (what is on each page), `ARCHIE_AI_MODEL_PASS2` (equipment
values) and `ARCHIE_AI_MODEL_SKILLS` (the skills and the needs list); on OpenRouter, `ARCHIE_OPENROUTER_PROVIDERS_PASS1`
(and `_PASS2`, `_SKILLS`) pins a step's hosts. `ARCHIE_PASS2_BATCH` sets equipment pages per call (else
`ARCHIE_PAGE_BATCH`, default 4). In tests on Butcher Buffet (2026-10-09/10) DeepSeek V4.1 Flash read pages and wrote
the needs list well and very cheaply but missed equipment drawn on plans; Gemini 3.8 Flash read plan equipment well
two pages per call.
The APIs cap each reply at 16,000 tokens; `ARCHIE_MAX_OUTPUT_TOKENS` changes it. A model that thinks at length counts
its thinking against the cap, so it may need more (DeepSeek V4.1 Flash used about 10,000 tokens of thinking per call).
Every AI call is logged per job in `ai_usage.jsonl` (pass, provider, model, seconds, tokens where reported); the
Drawings tab shows the totals under "AI use for this job". The Codex CLI reports one combined token count per call;
the APIs report input and output separately. No prices are stored: multiply the tokens by your provider's current rates.
