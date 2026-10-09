# Parent playbook: document_mapping

Run first for every analyzed PDF. Build the canonical physical-page register
from page text, title blocks, drawing indexes, sheet labels, and existing
coverage; preserve source page identity separately from drawing number. Map
revision, issue status, drawing type, level, main-view scale, and explicit
cross-sheet references. Treat plans, dimensions, RCPs, sections, elevations,
details, schedules, site plans, and mechanical sheets distinctly. Do not let
dates become sheet numbers, detail scale leak into a main viewport, or
adjacent page order become proof of a relationship. Handoff is a cited page
map and ambiguity list for the existing drawing-coverage resolver.
