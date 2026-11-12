"""A house's window glass: the NatHERS default windows without a certificate, and the certificate's own glass when the
P8 drawing task has read it (12 November 2026).

The defaults are the NatHERS default windows (fact sheet Table 1; NSW Planning Portal BASIX default window data):
ALM-001-01 A, aluminium single clear, hinged or projected, U 6.7 / SHGC 0.57; ALM-002-01 A, fixed or sliding, U 6.7 /
SHGC 0.70. Set 19's NatHERS certificate (45 Bayview St, Dwelling A) uses exactly these two.
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ai import (ai_preliminary, airflow_resolution, autonomous_tasks, ceiling_volume_resolution, certificate_glazing,
                house_household, house_windows, internal_gains_resolution, room_use_resolution)

SPACES = [("MASTER BEDROOM", 16), ("BED 2", 11), ("LIVING", 30)]


def opening(room, key, frame_group=None, **extra):
    return {"opening_key": key, "label": f"{room} window", "owner_room_label": room, "owner_level_name": "Ground",
            "host_surface_key": f"test:{room}:external", "width_m": 2.0, "height_m": 1.5, "quantity": 1,
            "external_exposure": "external", "orientation": "W", "azimuth_deg": 270.0, "shading_category": "unshaded",
            "page": 1, "evidence": [{"page": 1, "excerpt": "Window"}], "confidence": .65,
            **({"frame_group": frame_group} if frame_group else {}), **extra}


def wall(room):
    return {"surface_key": f"test:{room}:external", "label": f"{room} wall", "owner_room_label": room, "owner_level_name": "Ground",
            "physical_type": "wall", "thermal_role": "external", "external_exposure": "external", "orientation": "W",
            "azimuth_deg": 270.0, "gross_area_m2": 10.0, "confidence": .65, "page": 1,
            "evidence": [{"page": 1, "reference": "Room trace test", "excerpt": "Reviewer-declared room boundary."}],
            "opening_coverage": "complete"}


def assemble(openings, dwelling=True):
    building = {"spaces": [{"name": name, "level_name": "Ground", "area_m2": area, "ceiling_height_mm": 2700,
                            "evidence": [{"page": 1, "excerpt": name}]} for name, area in SPACES]}
    rooms = [{"kind": "room", "label": name, "level_name": "Ground", "area_m2": area, "preliminary_profile_id": "residential",
              "confidence": .8, "page": 1, "evidence": [{"page": 1, "excerpt": name}], "ceiling_height_mm": 2700}
             for name, area in SPACES]
    context = {"is_dwelling": dwelling, "evidence": [{"page": 1, "excerpt": "BASIX"}]}
    uses = room_use_resolution.resolve(building, proposal={"rooms": rooms}, dwelling_context=context)
    pack = ai_preliminary.load_pack()
    plan = house_household.plan([{"room_id": row["room_id"], "label": row["original_label"], "level": row["level_name"],
                                  "category": row["taxonomy_id"], "scope": row["space_scope"], "area_m2": 12} for row in uses["records"]])
    gains = internal_gains_resolution.resolve(building, {}, {"rooms": rooms}, uses, pack, {}, household=plan)
    ceiling = ceiling_volume_resolution.resolve(building, {}, {"rooms": rooms}, {}, pack, {})
    airflow = airflow_resolution.resolve(building, {}, {"rooms": rooms}, uses, ceiling, pack, {})
    proposal = {"rooms": rooms, "surfaces": [wall(name) for name, _ in SPACES], "openings": openings}
    model = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=uses,
                                    internal_gains_resolution=gains, airflow_resolution=airflow)
    return {surface["surface_id"]: surface for room in model["material"]["hourly_load_model"]["rooms"]
            for surface in room["cooling_load"]["glazing_surfaces"]}


CERTIFICATE = {"page": 20, "title": "NATHERS COMMITMENTS DWELLING A", "certificate": "NatHERS", "dwelling": "Dwelling A",
               "types": [{"window_id": "ALM-001-01 A", "description": "Aluminium A SG Clear", "u_value": 6.7, "shgc": 0.57},
                         {"window_id": "ALM-002-01 A", "description": "Aluminium B SG Clear", "u_value": 6.7, "shgc": 0.70}],
               "windows": [{"location": "Kitchen/Living", "window_id": "ALM-001-01 A", "height_mm": 1800, "width_mm": 970, "orientation": "SW"},
                           {"location": "Kitchen/Living", "window_id": "ALM-002-01 A", "height_mm": 3000, "width_mm": 3610, "orientation": "NW"},
                           {"location": "Master Bedroom", "window_id": "ALM-002-01 A", "height_mm": 3050, "width_mm": 3050, "orientation": "NW"},
                           {"location": "Bedroom 1", "window_id": "ALM-002-01 A", "height_mm": 900, "width_mm": 2710, "orientation": "SW"}]}


class DefaultGlassTests(unittest.TestCase):
    def test_frame_group_from_the_schedule_description(self):
        self.assertEqual(house_windows.frame_group("ALUMINIUM AWNING WINDOW, DG"), "A")
        self.assertEqual(house_windows.frame_group("Bi-fold door"), "A")
        self.assertEqual(house_windows.frame_group("Sliding door"), "B")
        self.assertEqual(house_windows.frame_group("FIXED GLAZING"), "B")
        self.assertEqual(house_windows.frame_group("W03"), "")

    def test_a_house_window_without_its_own_glass_takes_the_nathers_default(self):
        glass = assemble([opening("LIVING", "w1"), opening("MASTER BEDROOM", "w2", frame_group="A")])
        self.assertEqual(len(glass), 2)
        values = sorted((row["window"]["u_value_w_m2k"], row["window"]["shgc"]) for row in glass.values())
        self.assertEqual(values, [(6.7, 0.57), (6.7, 0.70)])          # the awning is group A, the unknown group B
        unknown = next(row for row in glass.values() if row["window"]["shgc"] == 0.70)
        self.assertIn("ALM-002-01 A", unknown["source"])
        self.assertIn("window type not shown", unknown["source"])

    def test_a_shop_keeps_the_pack_glass_and_an_answered_glass_wins(self):
        shop = assemble([opening("LIVING", "w1")], dwelling=False)
        self.assertEqual([(row["window"]["u_value_w_m2k"], row["window"]["shgc"]) for row in shop.values()], [(5.8, 0.45)])
        answered = assemble([opening("LIVING", "w1", u_value_w_m2k=2.9, shgc=0.48, glazing_source="Certificate test")])
        row = next(iter(answered.values()))
        self.assertEqual((row["window"]["u_value_w_m2k"], row["window"]["shgc"], row["source"]), (2.9, 0.48, "Certificate test"))


class CertificateReplyTests(unittest.TestCase):
    def reply(self, **change):
        body = {"certificate": "NatHERS", "dwelling": "Dwelling A", "types": CERTIFICATE["types"],
                "windows": CERTIFICATE["windows"], "not_shown": None}
        body.update(change)
        return json.dumps(body)

    def test_a_good_reply_and_the_checks(self):
        value = autonomous_tasks.validate_glazing_certificate_reply({}, self.reply())
        self.assertEqual(len(value["types"]), 2)
        self.assertEqual(value["windows"][1]["orientation"], "NW")
        with self.assertRaisesRegex(ValueError, "U-value"):
            autonomous_tasks.validate_glazing_certificate_reply({}, self.reply(types=[{**CERTIFICATE["types"][0], "u_value": 67}]))
        with self.assertRaisesRegex(ValueError, "must be one of the window types"):
            autonomous_tasks.validate_glazing_certificate_reply({}, self.reply(types=CERTIFICATE["types"][:1]))
        with self.assertRaisesRegex(ValueError, "not_shown"):
            autonomous_tasks.validate_glazing_certificate_reply({}, self.reply(types=[], windows=[]))
        empty = autonomous_tasks.validate_glazing_certificate_reply({}, self.reply(types=[], windows=[], not_shown="Only BASIX commitments, no window table."))
        self.assertEqual(empty["types"], [])


class RoomGlassTests(unittest.TestCase):
    def test_room_dwelling_and_types_bases(self):
        living = certificate_glazing.room_glass([CERTIFICATE], "LIVING")
        area_a, area_b = 1800 * 970, 3000 * 3610
        self.assertEqual(living["basis"], "room")
        self.assertAlmostEqual(living["shgc"], round((0.57 * area_a + 0.70 * area_b) / (area_a + area_b), 3), places=3)
        self.assertEqual(certificate_glazing.room_glass([CERTIFICATE], "MASTER BEDROOM")["shgc"], 0.70)
        self.assertEqual(certificate_glazing.room_glass([CERTIFICATE], "BED 1")["basis"], "room")
        self.assertEqual(certificate_glazing.room_glass([CERTIFICATE], "BED 3")["basis"], "dwelling")     # numbers differ
        self.assertEqual(certificate_glazing.room_glass([CERTIFICATE], "Study")["basis"], "dwelling")
        types_only = certificate_glazing.room_glass([{**CERTIFICATE, "windows": []}], "Study")
        self.assertEqual((types_only["basis"], types_only["shgc"]), ("types", 0.635))
        self.assertIsNone(certificate_glazing.room_glass([], "Study"))

    def test_two_dwellings_use_their_own_certificate(self):
        other = {**CERTIFICATE, "page": 21, "title": "NATHERS COMMITMENTS DWELLING B", "dwelling": "Dwelling B",
                 "types": [{"window_id": "X", "description": "Double", "u_value": 3.0, "shgc": 0.5}],
                 "windows": [{"location": "Living", "window_id": "X", "height_mm": 2000, "width_mm": 2000, "orientation": "N"}]}
        self.assertEqual(certificate_glazing.room_glass([CERTIFICATE, other], "Living", "Dwelling B")["shgc"], 0.5)
        self.assertNotEqual(certificate_glazing.room_glass([CERTIFICATE, other], "Living", "Dwelling A")["shgc"], 0.5)


class ServiceTests(unittest.TestCase):
    def test_openings_take_the_certificate_glass_unless_they_have_their_own(self):
        from backend import ai_preliminary_service, autonomous_tasks_service
        proposal = {"openings": [opening("LIVING", "w1"), opening("Study", "w2"), opening("BED 2", "w3", u_value_w_m2k=2.0, shgc=0.4)]}
        uses = {"dwelling_context": {"is_dwelling": True}}
        with mock.patch.object(autonomous_tasks_service, "drawing_glazing_certificates", return_value=[CERTIFICATE]):
            result = ai_preliminary_service._with_certificate_glazing({"root": Path(".")}, proposal, uses)
        living, study, own = result["openings"]
        self.assertIn("Kitchen/Living", living["glazing_source"])
        self.assertIn("certificate_glazing_dwelling", study["assumptions"])
        self.assertEqual((own["u_value_w_m2k"], own["shgc"]), (2.0, 0.4))
        with mock.patch.object(autonomous_tasks_service, "drawing_glazing_certificates", return_value=[CERTIFICATE]):
            shop = ai_preliminary_service._with_certificate_glazing({"root": Path(".")}, {"openings": [opening("LIVING", "w1")]}, {})
        self.assertNotIn("shgc", shop["openings"][0])

    def test_the_question_goes_to_certificate_pages_of_a_house_only(self):
        from PIL import Image
        from backend import autonomous_tasks_service
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "set.pdf"
            Image.new("RGB", (842, 595), "white").save(pdf, save_all=True,
                                                         append_images=[Image.new("RGB", (842, 595), "white")])
            (root / "ai_input.json").write_text(json.dumps({"source_pdf": str(pdf), "drawing_set": {"pages": [
                {"page": 1, "title": "Ground Floor Plan"}, {"page": 2, "title": "GSPublisherVersion 0.61.100.100"}]}}))
            # The page reading names the certificate sheet when the drawing set's own title is a title-block artefact.
            (root / "page_inventory.json").write_text(json.dumps({"pages": {"2": {"title": "NATHERS COMMITMENTS DWELLING A"}}}))
            (root / "room_use_resolution.json").write_text(json.dumps({"dwelling_context": {"is_dwelling": True}}))
            packets = autonomous_tasks_service._p8_packets(root)
            self.assertEqual([(task, target) for task, target, *_ in packets], [("P8_glazing_certificate", "page-2")])
            self.assertTrue(packets[0][4] and all(image[:4] == b"\x89PNG" for image in packets[0][4]))
            self.assertLessEqual(len(packets[0][3]), autonomous_tasks.TASKS["P8_glazing_certificate"]["budget_chars"])
            (root / "room_use_resolution.json").write_text(json.dumps({"dwelling_context": {"is_dwelling": False}}))
            self.assertEqual(autonomous_tasks_service._p8_packets(root), [])


if __name__ == "__main__":
    unittest.main()
