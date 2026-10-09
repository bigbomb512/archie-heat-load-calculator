# Archie runtime skills

The instructions composed into each skill worker's prompt. A worker for one sub-skill receives:

1. `shared_policy.md`, the rules every sub-skill follows;
2. `<parent skill>/playbook.md`, its parent skill's playbook;
3. `<parent skill>/<sub-skill>.md`, its own procedure;

followed by its validated prerequisite proposals and a bounded evidence packet. Each file's title line names it and
is not sent. The skills and sub-skills themselves (tasks, inputs, prerequisites, output fields) are defined in
`config/archie_skills_v1.json` and `config/archie_subskills_v1.json`.

A sub-skill's input fingerprint includes exactly the text it is sent, so editing one sub-skill's file re-runs that
sub-skill (and the skills that depend on its result) on the next review; editing a playbook re-runs that parent's
sub-skills; editing the shared policy re-runs every sub-skill.

On the case-file route some sub-skills are answered together in one call (`_SKILL_GROUPS` in
`backend/skill_workflow_service.py`): the shared policy, each playbook, the job details, page index and key pages
are then sent once per group instead of once per sub-skill. Each member's answer is still checked, stored and
fingerprinted on its own, so reuse and edits work per sub-skill. A member that can reuse its earlier result is
left out of the call; a member left out of a reply, or a group prompt over budget, falls back to its own call.
