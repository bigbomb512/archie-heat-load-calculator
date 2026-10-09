"""Skill instructions live in one file per sub-skill, and a sub-skill's fingerprint covers only what it is sent."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from ai import skill_registry
from backend import skill_workflow_service as skills

ROOT = Path(__file__).resolve().parents[1]


class InstructionFileTests(unittest.TestCase):
    def setUp(self):
        # A copy of the instructions to edit; the loader is pointed at it.
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "ai" / "skills", self.root / "ai" / "skills")
        self.patch = patch.object(skill_registry, "_ROOT", self.root)
        self.patch.start()
        self.registry = {row["id"]: row for row in skill_registry.load_subskill_registry()["subskills"]}

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def fingerprints(self):
        return {skill_id: skill_registry.subskill_instructions_fingerprint(row) for skill_id, row in self.registry.items()}

    def edit(self, *parts):
        path = self.root / "ai" / "skills" / Path(*parts)
        path.write_text(path.read_text(encoding="utf-8") + "\n- One more rule.\n", encoding="utf-8")

    def test_every_sub_skill_has_its_own_file_and_gets_shared_policy_playbook_and_procedure(self):
        catalog = skill_registry.load_catalog()
        for skill_id, row in self.registry.items():
            own = skill_registry.instruction_path(catalog, row["parent"], skill_id)
            self.assertTrue(own.is_file(), skill_id)
            text = skill_registry.compose_subskill_instructions(row["parent"], row)
            self.assertTrue(text.startswith("### Role and authority"), skill_id)          # shared policy first
            self.assertNotIn("# Subskill:", text)                                          # title lines aren't sent
            self.assertTrue(text.endswith(skill_registry._read_instruction(own)), skill_id)

    def test_editing_one_sub_skill_changes_only_its_fingerprint(self):
        before = self.fingerprints()
        self.edit("rooms_geometry_gains", "equipment_evidence.md")
        after = self.fingerprints()
        self.assertEqual({skill_id for skill_id in before if before[skill_id] != after[skill_id]}, {"equipment_evidence"})

    def test_editing_a_playbook_changes_that_parents_sub_skills_and_the_shared_policy_changes_all(self):
        before = self.fingerprints()
        self.edit("rooms_geometry_gains", "playbook.md")
        after = self.fingerprints()
        changed = {skill_id for skill_id in before if before[skill_id] != after[skill_id]}
        self.assertEqual(changed, {skill_id for skill_id, row in self.registry.items() if row["parent"] == "rooms_geometry_gains"})
        self.edit("shared_policy.md")
        final = self.fingerprints()
        self.assertTrue(all(after[skill_id] != final[skill_id] for skill_id in after))

    def test_the_run_identity_doesnt_carry_instructions_so_an_edit_doesnt_re_run_every_skill(self):
        inputs = skills._source_inputs({"root": self.root, "ai_input": self.root / "a", "coverage": self.root / "b",
                                        "spatial": self.root / "c", "vector": self.root / "d", "vision": self.root / "e"},
                                       skill_registry.load_catalog())
        before = json.dumps(inputs, sort_keys=True, default=str)
        self.edit("rooms_geometry_gains", "equipment_evidence.md")
        after = json.dumps(skills._source_inputs({"root": self.root, "ai_input": self.root / "a", "coverage": self.root / "b",
                                                  "spatial": self.root / "c", "vector": self.root / "d", "vision": self.root / "e"},
                                                 skill_registry.load_catalog()), sort_keys=True, default=str)
        self.assertEqual(before, after)
        self.assertNotIn("subskill_registry", inputs)

    def test_a_missing_file_is_named_when_the_catalog_is_checked(self):
        (self.root / "ai" / "skills" / "rooms_geometry_gains" / "lighting_evidence.md").unlink()
        with self.assertRaisesRegex(ValueError, "rooms_geometry_gains/lighting_evidence.md"):
            skills.validate_catalog(skill_registry.load_catalog())
        with self.assertRaisesRegex(ValueError, "lighting_evidence.md"):
            skill_registry.compose_subskill_instructions("rooms_geometry_gains", self.registry["lighting_evidence"])


if __name__ == "__main__":
    unittest.main()
