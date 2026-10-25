"""Seating precedence, geometry exclusion, allocation, and common occupant inputs; no providers."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai import seating, internal_gains_resolution as gains, room_use_resolution, ai_preliminary, airflow_resolution
from ai import page_inventory


def rooms():
    return [{"id":"shop","name":"Shop","level":"Ground","area_m2":100,"scope":"comfort_hvac","use":"dining"},
            {"id":"bar","name":"Bar","level":"Ground","area_m2":20,"scope":"comfort_hvac","use":"dining"}]


class SeatingTests(unittest.TestCase):
    def test_printed_forms_not_quantities(self):
        for text in ("SEATING QTY 35 SEATS 140", "140 SEATS", "SEATING CAPACITY 140", "PAX 140"):
            self.assertEqual(seating.printed_totals(text), [140])
        self.assertEqual(seating.printed_totals("35 TABLES, 1200 x 800 mm"), [])

    def test_detail_dimensions_and_table_capacity_are_not_totals(self):
        for text in ("BENCH SEAT 450 HIGH", "TABLE T1 SEATS 4", "4 SEATS PER TABLE", "SEAT 1",
                     "SEATS 450 mm", "SEATS 450 H", "SEATS 450 HEIGHT", "4 SEATER", "PAX 80 OUTDOOR"):
            with self.subTest(text=text):
                self.assertEqual(seating.printed_totals(text), [])

    def test_explicit_total_wins_over_schedule_rows(self):
        for total in ("TOTAL SEATS 140", "SEATING CAPACITY 140", "140 SEATS TOTAL", "PAX 140"):
            self.assertEqual(seating.printed_totals(total+"\nQTY 10 SEATS 4"),[140])

    def test_schedule_rows_resolve_sum_or_warn(self):
        self.assertEqual(seating.printed_totals("Seating schedule: table TB1 1200 x 800 mm, quantity 35, seats 140"),[140])
        self.assertEqual(seating.printed_totals("QTY 10 SEATS 4"),[40])
        self.assertEqual(seating.printed_totals("QTY 10 SEATS 4\nQTY 20 SEATS 4"),[120])
        for text in ("QTY 2 SEATS 4", "QTY 10 SEATS 4\nQTY 2 SEATS 4", "QTY 10 SEATS 4\nQTY 2"):
            reading=seating.printed_seating(text)
            self.assertEqual(reading["totals"],[])
            self.assertTrue(reading["warnings"])
        reading=seating.printed_seating("SEATING SCHEDULE A QTY 10 SEATS 4\nSEATING SCHEDULE B QTY 20 SEATS 4")
        self.assertEqual(reading["totals"],[])
        self.assertTrue(reading["warnings"])

    def test_service_ignores_detail_sheets_and_prefers_structured_total(self):
        from backend import seating_service
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'reviewer_room_geometry.json').write_text(json.dumps({"records":[{"room_id":"shop","page":1,
                "points_image_px":[[0,0],[100,0],[100,100],[0,100],[0,0]],"ai_measured_area_m2":100}]}))
            use={"records":[{"room_id":"shop","original_label":"Shop","taxonomy_id":"dining","space_scope":"comfort_hvac","level_name":"Ground"}]}
            inventory={"pages":{"1":{"page_type":"floor_plan"},"2":{"page_type":"detail"},"3":{"page_type":"schedule"}}}
            (root/'page_inventory.json').write_text(json.dumps(inventory))
            (root/'spatial_ocr.json').write_text(json.dumps({"pages":[
                {"page":2,"standalone_text_items":[{"text":"BENCH SEAT 450 HIGH","bbox":[0,0,100,10]}]},
                {"page":3,"standalone_text_items":[{"text":"TOTAL SEATS 140","bbox":[0,0,100,10]}]}]}))
            evidence=seating_service._gather(root,None,use)
            self.assertEqual(evidence['rooms']['shop']['seat_count'],140)
            self.assertEqual([r['page'] for r in evidence['printed']],[3])
            inventory['pages']['1']['information']=[{"kind":"people_occupancy","what":"TOTAL SEATS 160",
                "seat_total":140,"seat_scope":"indoor_tenancy"}]
            (root/'page_inventory.json').write_text(json.dumps(inventory))
            evidence=seating_service._gather(root,None,use)
            self.assertEqual(evidence['rooms']['shop']['seat_count'],140)
            self.assertEqual([r['source'] for r in evidence['printed']],["structured"])
            inventory['pages']['1']['information']=[{"kind":"people_occupancy","what":"QTY 35 SEATS 140",
                "evidence":"QTY 35 SEATS 140"}]
            inventory['pages'].pop('3')
            (root/'page_inventory.json').write_text(json.dumps(inventory))
            evidence=seating_service._gather(root,None,use)
            self.assertEqual(evidence['rooms']['shop']['seat_count'],140)

    def test_printed_total_shared_by_count_and_disagreement_recorded(self):
        drawn={"shop":{"count":60},"bar":{"count":10}}
        result=seating.allocate(rooms(),drawn,[{"total":140,"page":19,"level":"Ground"}])
        self.assertEqual([result[key]["seat_count"] for key in ("shop","bar")],[120,20])
        self.assertIn("more than 15%",result["shop"]["warning"])
        self.assertIn("counted seats",result["bar"]["rationale"])

    def test_area_allocation_conserves_whole_total(self):
        result=seating.allocate(rooms(),{},[{"total":140,"page":19,"level":"Ground"}])
        self.assertEqual(sum(row["seat_count"] for row in result.values()),140)
        self.assertIn("room area",result["shop"]["rationale"])

    def test_conflicting_printed_totals_are_not_arbitrarily_selected(self):
        result=seating.allocate(rooms(),{},[{"total":140,"page":19,"level":"Ground"},{"total":160,"page":20,"level":"Ground"}])
        self.assertEqual(result,{})

    def test_room_specific_total_is_not_added_again_to_tenancy_total(self):
        result=seating.allocate(rooms(),{"bar":{"count":20}},[{"total":140,"page":19,"level":"Ground"},
            {"total":20,"page":19,"level":"Ground","room":"bar"}])
        self.assertEqual(result["bar"]["seat_count"],20)
        self.assertEqual(result["shop"]["seat_count"],120)

    def test_repeated_chairs_inside_room_and_terrace_exclusion(self):
        objects=[{"bbox":[x,1,x+.45,1.45],"closed":True} for x in [1,2,3,11]]
        result=seating.count_symbols(objects,[{"id":"room","points":[[0,0],[10,0],[10,10],[0,10],[0,0]]}],1000)
        self.assertEqual(result["counts"],{"room":3})
        self.assertEqual(result["excluded"],1)

    def test_table_detail_circles_and_nested_paths_not_double_counted(self):
        objects=[{"bbox":[x,1,x+.45,1.45],"closed":True,"family":"circle"} for x in [1,2,3]]
        objects += [{"bbox":[x-.1,.9,x+.7,2.2],"closed":True,"family":"rect"} for x in [1,2,3]]
        result=seating.count_symbols(objects,[{"id":"room","points":[[0,0],[10,0],[10,10],[0,10],[0,0]]}],1000)
        self.assertEqual(result["counts"],{})

    def test_labelled_bench_uses_length_and_stays_inside_outline(self):
        result=seating.count_symbols([{"bbox":[1,1,3.4,1.6],"bench":True}],
            [{"id":"r","points":[[0,0],[5,0],[5,5],[0,5],[0,0]]}],1000)
        self.assertEqual(result["counts"]["r"],4)
        self.assertEqual(result["seats"][0]["kind"],"bench_estimate")

    def test_image_only_plan_is_reported_not_counted(self):
        from backend import seating_service
        class Page:
            width=100; height=100; lines=[]; curves=[]; rects=[]
        class Doc:
            pages=[Page()]
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'source.pdf').write_bytes(b'fake')
            (root/'reviewer_room_geometry.json').write_text(json.dumps({"records":[{"room_id":"r","page":1,
                "points_image_px":[[0,0],[100,0],[100,100],[0,100],[0,0]],"ai_measured_area_m2":100,"calibration":{"status":"agreed","mm_per_px":10}}]}))
            (root/'vector_geometry.json').write_text(json.dumps({"geometry_key_points":{"pages":[{"page":1,"coordinate_systems":{"image_px":{"image_width":100,"image_height":100}}}]}}))
            use={"records":[{"room_id":"r","original_label":"Dining","taxonomy_id":"dining","space_scope":"comfort_hvac","level_name":"Ground"}]}
            with patch('pdfplumber.open',return_value=Doc()):
                result=seating_service._gather(root,str(root/'source.pdf'),use)
            self.assertEqual(result["rooms"],{})
            self.assertIn("Image-only plan",result["warnings"][0]["reason"])

    def test_prompt_captures_structured_seating_without_an_extra_call(self):
        data={"page_type":"schedule","information":[{"kind":"people_occupancy","what":"SEATS 140","evidence":"Schedule","seat_total":140,"seat_scope":"indoor_tenancy"}]}
        self.assertEqual(page_inventory.validate_reply(data)["information"][0]["seat_total"],140)
        self.assertIn("seat_total",page_inventory.build_prompt())
        self.assertEqual(page_inventory.PROMPT_VERSION,"page-inventory-v2-seating")


class OccupancyIntegrationTests(unittest.TestCase):
    def fixture(self):
        building={"spaces":[{"name":"Dining","level_name":"Ground","area_m2":100},{"name":"Kitchen","level_name":"Ground","area_m2":20}]}
        uses=room_use_resolution.resolve(building)
        rid=uses['records'][0]['room_id']
        seats={"rooms":{rid:{"seat_count":140,"origin":"direct_project_evidence","rationale":"140 seats on page 19.","evidence":[{"page":19,"excerpt":"SEATS 140"}],"basis":seating.BASIS}}}
        return building,uses,rid,seats

    def test_seats_override_density_and_operator_override_wins(self):
        building,uses,rid,seats=self.fixture()
        result=gains.resolve(building,room_use=uses,pack=ai_preliminary.load_pack(),seating_evidence=seats)
        byid={row['room_id']:row for row in result['records']}
        self.assertEqual(byid[rid]['occupancy_count'],140)
        self.assertIn('Density fallback:',byid[uses['records'][1]['room_id']]['fields']['occupancy_count']['rationale'])
        changed=gains.apply_override(result,rid,'occupancy_count',80,'Operator','Eighty people')
        result=gains.resolve(building,room_use=uses,pack=ai_preliminary.load_pack(),existing=changed,seating_evidence=seats)
        self.assertEqual(next(row for row in result['records'] if row['room_id']==rid)['occupancy_count'],80)

    def test_own_cited_count_beats_allocated_and_vector_counts(self):
        building,uses,rid,seats=self.fixture()
        building['spaces'][0].update(seat_count=40,evidence=[{"page":1,"excerpt":"Dining seats 40"}])
        for origin in ('direct_project_evidence','project_evidence'):
            seats['rooms'][rid]['origin']=origin
            result=gains.resolve(building,room_use=uses,pack=ai_preliminary.load_pack(),seating_evidence=seats)
            row=next(r for r in result['records'] if r['room_id']==rid)
            self.assertEqual(row['occupancy_count'],40)
            self.assertFalse(row['seating']['applied'])

    def test_density_uses_existing_resolved_area_never_raw_trace_sum(self):
        building,uses,rid,seats=self.fixture()
        seats={"rooms":{},"room_inputs":[{"id":rid,"area_m2":999}]}
        areas={rid:{"value":80,"origin":"pdf_evidence","evidence":[{"page":2,"excerpt":"80m2"}]}}
        result=gains.resolve(building,room_use=uses,pack=ai_preliminary.load_pack(),seating_evidence=seats,room_areas=areas)
        row=next(r for r in result['records'] if r['room_id']==rid)
        self.assertEqual(row['fields']['occupancy_count']['operands']['area_m2'],80)
        result=gains.resolve(building,room_use=uses,pack=ai_preliminary.load_pack(),seating_evidence=seats)
        row=next(r for r in result['records'] if r['room_id']==rid)
        self.assertEqual(row['fields']['occupancy_count']['operands']['area_m2'],100)

    def test_missing_raw_area_reuses_draft_area_and_sum_with_fixed_minimum(self):
        building={"spaces":[{"name":"Dining","level_name":"Ground"}]}
        uses=room_use_resolution.resolve(building)
        rid=room_use_resolution.room_identity('Dining','Ground')
        flow_id=airflow_resolution.room_id('Dining','Ground')
        proposal={"rooms":[{"kind":"room","label":"Dining","level_name":"Ground","area_m2":203.52587909843004,
            "preliminary_profile_id":"hospitality","space_scope":"comfort_hvac","evidence":[{"page":20}]}]}
        areas=ai_preliminary.resolved_room_areas(building,{},proposal,uses)
        counts={flow_id:{"value":140,"origin":"direct_project_evidence"}}
        pack=ai_preliminary.load_pack()
        flow=airflow_resolution.resolve(building,{}, {},uses,{},pack,{},occupancy_counts=counts,
            room_areas={flow_id:areas[rid]})
        outside=next(r for r in flow['records'] if r['air_path_type']=='outside_air')
        self.assertAlmostEqual(outside['value'],1111.058,places=3)
        self.assertEqual(outside['operands']['area_m2'],areas[rid]['value'])
        self.assertIn(' + ',outside['formula'])
        data=ai_preliminary.assemble(building,preliminary_proposal=proposal,room_use_resolution=uses,
            internal_gains_resolution=gains.resolve(building,room_use=uses,pack=pack,
                seating_evidence={"rooms":{rid:{"seat_count":140,"origin":"direct_project_evidence","rationale":"Printed seats", "evidence":[{"page":19}],"basis":seating.BASIS}}},room_areas=areas),
            airflow_resolution=flow)
        room=data['material']['hourly_load_model']['rooms'][0]
        self.assertEqual(room['area_m2'],outside['operands']['area_m2'])
        self.assertEqual(room['occupancy'],outside['operands']['occupancy'])
        self.assertEqual(room['cooling_load']['outside_air_lps'],outside['value'])
        fixed=airflow_resolution.resolve(building,{}, {"rooms":[{"label":"Dining","level_name":"Ground","outside_air_fixed_lps":2000}]},
            uses,{},pack,{},occupancy_counts=counts,room_areas={flow_id:areas[rid]})
        self.assertEqual(next(r for r in fixed['records'] if r['air_path_type']=='outside_air')['value'],2000)

    def test_outside_air_uses_exact_same_occupancy_and_zero_override(self):
        building,uses,rid,seats=self.fixture()
        pack=ai_preliminary.load_pack()
        for count in (140,0):
            counts={airflow_resolution.room_id('Dining','Ground'):{'value':count,'origin':'contractor_override','rationale':'Test occupancy'}}
            flow=airflow_resolution.resolve(building,{}, {},uses,{},pack,{},occupancy_counts=counts)
            record=next(row for row in flow['records'] if row['air_path_type']=='outside_air' and row['owner_room_id']==airflow_resolution.room_id('Dining','Ground'))
            self.assertEqual(record['operands']['occupancy'],count)
            self.assertEqual(record['value'],count*pack['profiles']['hospitality']['outside_air_lps_person']+100*pack['profiles']['hospitality']['outside_air_lps_m2'])

    def test_materialized_people_and_outside_air_share_count_and_phase1_ignores_it(self):
        building,uses,rid,seats=self.fixture()
        pack=ai_preliminary.load_pack()
        internal=gains.resolve(building,room_use=uses,pack=pack,seating_evidence=seats)
        counts={airflow_resolution.room_id(row["original_label"],row["level"]):row["fields"]["occupancy_count"] for row in internal["records"]}
        flow=airflow_resolution.resolve(building,{}, {},uses,{},pack,{},occupancy_counts=counts)
        data=ai_preliminary.assemble(building,room_use_resolution=uses,internal_gains_resolution=internal,airflow_resolution=flow)
        room=next(row for row in data["material"]["hourly_load_model"]["rooms"] if row["name"]=="Dining")
        outside=next(row for row in flow["records"] if row["air_path_type"]=="outside_air" and row["owner_room_id"]==airflow_resolution.room_id("Dining","Ground"))
        self.assertEqual(room["occupancy"],140)
        self.assertEqual(room["cooling_load"]["outside_air_lps"],outside["value"])
        base=ai_preliminary._base_room_load_input_set(data)
        empty=next(row for row in base["material"]["hourly_load_model"]["rooms"] if row["name"]=="Dining")
        self.assertEqual(empty["occupancy"],0)
        self.assertEqual(empty["cooling_load"]["outside_air_lps"],0)
        self.assertEqual(room["occupancy"],140)


if __name__=='__main__':unittest.main()
