# Project Plan

## Current State

The project can process architect PDFs, remove obviously irrelevant pages, keep possible HVAC context, render page screenshots, create review packets, extract spatial OCR evidence, and generate a ChatGPT-ready upload packet.

This branch does not call paid AI APIs. It uses manual ChatGPT review first so we can learn what the eventual automated AI workflow needs.

## Next Steps

### 1. Improve The ChatGPT Packet

Goal: make the manual packet clear enough that ChatGPT can act as both visual reviewer and reasoning assistant.

Build:

- stronger `prompt.md` examples for top-down plan detection
- clearer instructions for scale vs direct written dimensions
- stricter JSON output expectations
- a place to paste/save ChatGPT's returned JSON
- validation before any ChatGPT measurement output is trusted

### 2. Test Against Real Drawing Sets

Goal: find where page filtering or the prompt fails before adding paid automation.

Test:

- small HVAC drawing sets
- retail tenancy fit-outs
- architect PDFs with one page per floor
- PDFs with direct dimensions but no scale
- PDFs with top-down plans, sections, elevations, renders, notes, and schedules mixed together

Track:

- false kept pages
- wrongly discarded top-down plans
- missing scales or direct dimensions
- unclear floor labels
- ChatGPT wall/dimension matching accuracy

### 3. Add Manual ChatGPT Output Review

Goal: safely use ChatGPT results without pretending they are final truth.

Build:

- upload/paste `chatgpt_response.json`
- validate required fields
- flag low-confidence wall/dimension links
- show uncertainties and contractor questions in the website
- keep human approval before calculations or CAD actions

### 4. Prepare For CAD Planning

Goal: turn reviewed ChatGPT output into a structured CAD action plan.

Start with:

- confirmed floor/level list
- confirmed usable plan pages
- room/context summary
- confirmed or uncertain wall dimensions
- missing-info questions
- draft AutoCAD action plan, not actual DWG generation yet

### 5. Revisit API Automation Later

Only after manual ChatGPT testing proves the workflow should we reintroduce APIs.

Possible future path:

- use a vision model API for screenshot understanding
- use a reasoning model API for HVAC/CAD planning
- use AutoCAD/Revit APIs for drawing generation

The old OpenRouter/Kimi/DeepSeek work remains in the `openrouter-experiment` branch for reference.

## Long-Term Direction

The long-term goal is a fully autonomous HVAC design assistant, but autonomy should be earned through real drawing tests, confidence scoring, and contractor feedback.

The current human review step is a safety and learning layer. It should shrink only after the system proves it can handle real PDFs reliably.
