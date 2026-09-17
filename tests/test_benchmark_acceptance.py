"""Synthetic acceptance tests: these do not authorize any engineering case."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai.benchmark_acceptance import MATERIALS, apply_acceptance, assess_acceptance, engine_fingerprint, record_acceptance
from ai.parity_harness import COMPONENTS, archie_results_from_hourly_load_report, compare_case
from backend import benchmark_service
from tests.test_parity_harness import ready_case


class BenchmarkAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = ready_case()
        self.case['comparison_policy'] = {'tolerance_status': 'approved', 'component_tolerance_percent': 1,
            'total_tolerance_percent': 1, 'absolute_tolerance_kw': 0.001,
            'rounding_policy': 'Synthetic test: compare supplied numbers without rounding', 'excluded_components': []}
        for material in MATERIALS:
            file = self.root / (material + '.txt')
            file.write_text('Synthetic test material only: ' + material)
            self.case['source_files'][material] = file.name
        peak = {'month': 'January', 'hour': 15, 'scenario_id': 'summer', 'sensible_kw': 5,
                'latent_kw': 1, 'total_kw': 6, 'design_total_kw': 6.6,
                'components': {name: 0 for name in COMPONENTS}}
        peak['components']['people'] = 6
        self.source = {'status': 'review_ready', 'project_peak': deepcopy(peak),
            'calculation_engine_fingerprint': engine_fingerprint(),
            'scenario_results': [{'scenario_id': 'summer',
                'rooms': [{'room_id': 'room_1', 'peak': deepcopy(peak)}],
                'zones': [{'zone_id': 'zone_1', 'peak': deepcopy(peak)}]}]}
        self.actual = archie_results_from_hourly_load_report(self.source)
        self.case['reference_results'] = deepcopy(self.actual)
        self.reviewer = {'engineer_name': 'Synthetic Reviewer', 'credential': 'TEST ONLY', 'rationale': 'Synthetic gate test; not a real acceptance'}

    def assess(self):
        return assess_acceptance(self.case, self.actual, compare_case(self.case, self.actual),
                                 case_root=self.root, source_report=self.source)

    def test_no_automatic_acceptance_even_when_numbers_match(self):
        assessment = self.assess()
        self.assertTrue(assessment['eligible'], assessment['issues'])
        result = apply_acceptance(compare_case(self.case, self.actual), assessment, None)
        self.assertEqual(result['status'], 'baseline_compared')
        self.assertFalse(result['final_parity_allowed'])

    def test_explicit_accepted_exact_case_can_be_validated(self):
        assessment = self.assess()
        acceptance = record_acceptance(assessment, self.reviewer)
        result = apply_acceptance(compare_case(self.case, self.actual), assessment, acceptance)
        self.assertEqual(result['status'], 'validated')
        self.assertTrue(result['final_parity_allowed'])
        self.assertEqual(result['validation']['scope'], 'room_zone_peak_comparison')
        self.assertEqual(self.source['status'], 'review_ready')

    def test_missing_reviewer_or_credential_rejected(self):
        for reviewer in (None, {}, {**self.reviewer, 'credential': ''}):
            with self.assertRaises(ValueError):
                record_acceptance(self.assess(), reviewer)

    def test_changed_case_results_policy_reference_or_code_invalidates_acceptance(self):
        acceptance = record_acceptance(self.assess(), self.reviewer)
        original_case, original_actual = deepcopy(self.case), deepcopy(self.actual)
        for mutation in ('case', 'results', 'policy', 'material', 'code'):
            with self.subTest(mutation=mutation):
                self.case, self.actual = deepcopy(original_case), deepcopy(original_actual)
                material = self.root / self.case['source_files']['camel_results']
                previous = material.read_text()
                if mutation == 'case': self.case['case_id'] = 'different'
                if mutation == 'results': self.actual['rooms'][0]['total_kw'] += 0.001
                if mutation == 'policy': self.case['comparison_policy']['rounding_policy'] = 'changed'
                if mutation == 'material': material.write_text('changed reference')
                with patch('ai.benchmark_acceptance.engine_fingerprint', return_value='changed') if mutation == 'code' else patch('ai.benchmark_acceptance.engine_fingerprint', return_value=engine_fingerprint()):
                    assessment = self.assess()
                result = apply_acceptance(compare_case(self.case, self.actual), assessment, acceptance)
                self.assertNotEqual(result['status'], 'validated')
                self.assertEqual(result['validation']['status'], 'stale')
                material.write_text(previous)

    def test_missing_or_escaped_material_blocks_acceptance(self):
        for path in ('absent.txt', '../outside.txt', '/etc/hosts'):
            self.case['source_files']['camel_results'] = path
            self.assertFalse(self.assess()['eligible'])

    def test_draft_partial_and_old_engine_reports_block_acceptance(self):
        for change in ({'status': 'draft'}, {'project_peak': {}}, {'calculator_input_coverage': {'complete_scope': False}},
                       {'calculation_engine_fingerprint': 'old'}, {'blocked_reasons': ['missing room']}):
            original = deepcopy(self.source)
            self.source.update(change)
            self.assertFalse(self.assess()['eligible'])
            self.source = original

    def test_missing_bad_and_unapproved_tolerance_policy_blocks(self):
        for change in ({'tolerance_status': 'proposed'}, {'rounding_policy': ''}, {'component_tolerance_percent': -1},
                       {'total_tolerance_percent': None}, {'absolute_tolerance_kw': True}):
            original = deepcopy(self.case['comparison_policy'])
            self.case['comparison_policy'].update(change)
            self.assertFalse(self.assess()['eligible'])
            self.case['comparison_policy'] = original

    def test_outside_tolerance_missing_metrics_and_unmatched_entities_block(self):
        original = deepcopy(self.actual)
        mutations = [lambda a: a['rooms'][0].update(total_kw=7),
                     lambda a: a['rooms'][0].pop('latent_kw'),
                     lambda a: a['zones'].clear(),
                     lambda a: a['rooms'].append(deepcopy(a['rooms'][0])),
                     lambda a: a['peak'].update(hour=16),
                     lambda a: a['rooms'][0]['components'].update(people=7)]
        for mutate in mutations:
            self.actual = deepcopy(original)
            mutate(self.actual)
            self.assertFalse(self.assess()['eligible'])

    def test_zero_reference_uses_explicit_absolute_tolerance(self):
        self.actual['rooms'][0]['components']['solar'] = 0.0005
        self.assertTrue(self.assess()['eligible'])
        self.actual['rooms'][0]['components']['solar'] = 0.002
        self.assertFalse(self.assess()['eligible'])

    def test_exclusions_cannot_hide_nonzero_loads(self):
        self.case['comparison_policy']['excluded_components'] = ['people']
        self.assertFalse(self.assess()['eligible'])

    def web(self):
        source = self.root / 'hourly.json'
        source.write_text(json.dumps(self.source))
        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        return SimpleNamespace(benchmark_case_dir=lambda p: self.root,
            current_hourly_load_report_path=lambda p: source,
            load_json=lambda p: json.loads(Path(p).read_text()), _atomic_write=write)

    def test_service_accept_read_revoke_and_history_do_not_change_hourly_report(self):
        web = self.web()
        before = (self.root / 'hourly.json').read_bytes()
        built = benchmark_service.prepare(web, {}, self.case, self.actual)
        data = {'expected_binding_fingerprint': built['validation']['binding_fingerprint'], 'reviewer': self.reviewer}
        accepted = benchmark_service.prepare(web, {}, self.case, self.actual, action='accept', data=data)
        self.assertEqual(accepted['status'], 'validated')
        self.assertEqual(benchmark_service.read(web, {}, self.case, accepted)['status'], 'validated')
        revoked = benchmark_service.prepare(web, {}, self.case, self.actual, action='revoke', data=data)
        self.assertFalse(revoked['final_parity_allowed'])
        self.assertEqual(revoked['validation']['status'], 'revoked')
        self.assertEqual(len(list((self.root / 'acceptance_history').glob('*.json'))), 2)
        self.assertEqual(before, (self.root / 'hourly.json').read_bytes())

    def test_service_rejects_stale_binding_and_user_supplied_results(self):
        web = self.web()
        with self.assertRaises(ValueError):
            benchmark_service.prepare(web, {}, self.case, self.actual, action='accept', data={'reviewer': self.reviewer})
        self.actual['rooms'][0]['total_kw'] += 0.001
        built = benchmark_service.prepare(web, {}, self.case, self.actual)
        self.assertFalse(built['validation']['eligible_for_acceptance'])

    def test_api_acceptance_and_stale_read_hide_outdated_downloads(self):
        from backend import web_app
        web = self.web()
        project = {'id': 'test', 'review_dir': str(self.root)}
        request = SimpleNamespace(path='/api/parity-report?project_id=test')
        body = {'project_id': 'test', 'benchmark_case': self.case}
        with patch.object(web_app, 'read_json_body', side_effect=lambda request: body), \
             patch.object(web_app, 'project_by_id', return_value=project), \
             patch.object(web_app, 'benchmark_case_dir', return_value=self.root), \
             patch.object(web_app, 'current_hourly_load_report_path', return_value=self.root / 'hourly.json'), \
             patch.object(web_app, 'current_heat_load_report_path', return_value=None), \
             patch.object(web_app, 'safe_link', side_effect=str), \
             patch.object(web_app, 'update_project'):
            built = web_app.api_save_parity_report(request)
            body.update(action='accept', reviewer=self.reviewer,
                        expected_binding_fingerprint=built['report']['validation']['binding_fingerprint'])
            accepted = web_app.api_save_parity_report(request)
            self.assertEqual(accepted['report']['status'], 'validated')
            self.assertIn('Accepted for this exact room/zone', Path(accepted['html_url']).read_text())
            current = web_app.api_parity_report(request)
            self.assertEqual(current['report']['status'], 'validated')
            (self.root / self.case['source_files']['camel_results']).write_text('changed reference')
            stale = web_app.api_parity_report(request)
            self.assertEqual(stale['status'], 'stale')
            self.assertFalse(stale['report']['final_parity_allowed'])
            for key in ('report_url', 'html_url', 'markdown_url', 'csv_url'):
                self.assertEqual(stale[key], '')

    def test_missing_case_or_removed_acceptance_does_not_keep_validated_status(self):
        web = self.web()
        built = benchmark_service.prepare(web, {}, self.case, self.actual)
        accepted = benchmark_service.prepare(web, {}, self.case, self.actual, action='accept', data={
            'expected_binding_fingerprint': built['validation']['binding_fingerprint'], 'reviewer': self.reviewer})
        missing_case = benchmark_service.read(web, {}, {}, accepted)
        self.assertEqual(missing_case['status'], 'blocked')
        self.assertFalse(missing_case['final_parity_allowed'])
        (self.root / 'benchmark_acceptance.json').unlink()
        removed = benchmark_service.read(web, {}, self.case, accepted)
        self.assertEqual(removed['status'], 'baseline_compared')
        self.assertFalse(removed['final_parity_allowed'])

    def test_reads_demote_acceptance_after_reference_changes(self):
        web = self.web()
        built = benchmark_service.prepare(web, {}, self.case, self.actual)
        accepted = benchmark_service.prepare(web, {}, self.case, self.actual, action='accept', data={
            'expected_binding_fingerprint': built['validation']['binding_fingerprint'], 'reviewer': self.reviewer})
        (self.root / self.case['source_files']['camel_results']).write_text('changed')
        reread = benchmark_service.read(web, {}, self.case, accepted)
        self.assertEqual(reread['validation']['status'], 'stale')
        self.assertFalse(reread['final_parity_allowed'])


if __name__ == '__main__':
    unittest.main()
