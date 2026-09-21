"""Synthetic reporting tests; no licensed benchmark or private project data."""
from copy import deepcopy
import csv
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai.benchmark_reporting import render_csv, render_html, render_markdown, result_rows
from ai.parity_harness import compare_case, empty_benchmark_case
from tests.test_parity_harness import ready_case


class BenchmarkReportingTests(unittest.TestCase):
    def example(self):
        case = ready_case()
        actual = {'peak': {'month': 'January', 'hour': 16},
                  'rooms': [{'room_id': 'room_1', 'sensible_kw': 5.1, 'latent_kw': 1,
                             'total_kw': 6.1, 'components': {'solar': 2.2, 'lighting': 1}}],
                  'zones': []}
        return compare_case(case, actual)

    def test_reports_totals_components_missing_entities_and_peak_timing(self):
        report = self.example()
        rows = result_rows(report)
        total = next(row for row in rows if row[:3] == ('rooms', 'room_1', 'total_kw'))
        self.assertEqual(total[3:8], (6, 6.1, 0.1, 1.667, 'compared'))
        zone = next(row for row in rows if row[:3] == ('zones', 'zone_1', 'solar'))
        self.assertEqual(zone[3:8], (2, None, None, None, 'camel_only'))
        text = render_markdown(report)
        self.assertIn('Timing status: different', text)
        self.assertIn('hour 16', text)
        self.assertIn('Not provided', text)
        self.assertIn('not an engineering acceptance', text)

    def test_blocked_report_explains_missing_evidence_and_mapping(self):
        text = render_markdown(compare_case(empty_benchmark_case(), {}))
        self.assertIn('Status: blocked', text)
        self.assertIn('authorised DA09 reference', text)
        self.assertIn('Unmapped input family:', text)
        self.assertIn('No numerical comparison rows', text)

    def test_zero_reference_keeps_undefined_percent_and_numeric_zero(self):
        report = {'rooms': [{'entity_id': 'r', 'reference': {'total_kw': 0}, 'archie': {'total_kw': 2}, 'components': []}]}
        row = result_rows(report)[0]
        self.assertEqual(row[3:7], (0, 2, 2, None))
        data = list(csv.reader(StringIO(render_csv(report))))
        self.assertEqual(data[1][3:7], ['0', '2', '2', ''])

    def test_missing_is_not_converted_to_zero(self):
        report = {'rooms': [{'entity_id': 'r', 'status': 'archie_only', 'archie': {'total_kw': 0}}]}
        row = result_rows(report)[0]
        self.assertEqual(row[3:8], (None, 0, None, None, 'archie_only'))

    def test_html_and_markdown_escape_source_content(self):
        report = self.example()
        report['case_id'] = '<script>alert(1)</script>'
        report['source_files']['camel_results'] = '<img src=x onerror=alert(1)>|[x](bad)'
        html = render_html(report)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img', html)
        self.assertIn('&lt;script&gt;', html)
        markdown = render_markdown(report)
        self.assertIn('\\|', markdown)
        self.assertNotIn('<script>', markdown)

    def test_csv_preserves_quoted_sources_and_neutralizes_formula_labels(self):
        report = self.example()
        report['rooms'][0]['entity_id'] = '=SUM(A1:A2)'
        report['rooms'][0]['components'][0]['reference_source'] = 'drawing, "north"\npage 1'
        data = list(csv.reader(StringIO(render_csv(report))))
        self.assertEqual(data[1][1], "'=SUM(A1:A2)")
        self.assertTrue(any(row[8] == 'drawing, "north"\npage 1' for row in data[1:]))

    def test_export_does_not_mutate_status_policy_or_calculation(self):
        report = self.example()
        before = deepcopy(report)
        for render in (render_html, render_markdown, render_csv):
            self.assertEqual(render(report), render(report))
        self.assertEqual(report, before)
        self.assertFalse(report['final_parity_allowed'])
        self.assertEqual(report['status'], 'baseline_compared')

    def test_optional_floor_project_data_shown_without_inventing_aggregation(self):
        report = self.example()
        self.assertFalse(any(row[0] in {'floors', 'project'} for row in result_rows(report)))
        report['floors'] = [{'entity_id': 'f', 'reference': {'total_kw': 5}, 'archie': {'total_kw': 6}}]
        report['project'] = {'entity_id': 'p', 'reference': {'total_kw': 5}, 'archie': {'total_kw': 6}}
        self.assertEqual({row[0] for row in result_rows(report)}, {'rooms', 'zones', 'floors', 'project'})

    def test_comparison_retains_evidence_without_referencing_mutable_case(self):
        case = ready_case()
        report = compare_case(case, {})
        case['source_files']['camel_results'] = 'changed'
        self.assertEqual(report['source_files']['camel_results'], 'reference/camel_results.csv')

    def test_standalone_export_preserves_input_and_can_refresh_exports(self):
        from tools.export_benchmark_report import export_report
        with TemporaryDirectory() as folder:
            source = Path(folder) / 'comparison.json'
            source.write_text(json.dumps(self.example()))
            before = source.read_bytes()
            first = export_report(source, Path(folder) / 'exports')
            second = export_report(source, Path(folder) / 'exports')
            self.assertEqual(first, second)
            self.assertTrue(all(path.stat().st_size > 0 for path in first))
            self.assertEqual(source.read_bytes(), before)

    def test_api_build_and_read_offer_all_formats_without_modifying_load_inputs(self):
        from backend import web_app
        with TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / 'hourly_load_model.json'
            model.write_text('{"untouched": true}')
            project = {'id': 'synthetic', 'review_dir': folder}
            body = {'project_id': 'synthetic', 'benchmark_case': ready_case(),
                    'archie_results': {'peak': {}, 'rooms': [], 'zones': []}}
            with patch.object(web_app, 'read_json_body', return_value=body), \
                 patch.object(web_app, 'project_by_id', return_value=project), \
                 patch.object(web_app, 'benchmark_case_dir', return_value=root / 'benchmark'), \
                 patch.object(web_app, 'current_heat_load_report_path', return_value=None), \
                 patch.object(web_app, 'current_hourly_load_report_path', return_value=None), \
                 patch.object(web_app, 'safe_link', side_effect=str), \
                 patch.object(web_app, 'update_project'):
                saved = web_app.api_save_parity_report(SimpleNamespace())
                loaded = web_app.api_parity_report(SimpleNamespace(path='/api/parity-report?project_id=synthetic'))
            for key in ('report_url', 'markdown_url', 'html_url', 'csv_url'):
                self.assertEqual(saved[key], loaded[key])
                self.assertTrue(Path(saved[key]).is_file())
            self.assertEqual(model.read_text(), '{"untouched": true}')
            self.assertFalse(saved['report']['final_parity_allowed'])
            self.assertEqual(json.loads(Path(saved['report_url']).read_text()), saved['report'])


if __name__ == '__main__':
    unittest.main()
