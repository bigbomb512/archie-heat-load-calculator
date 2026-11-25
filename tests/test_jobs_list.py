import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend import job_service, security, web_app


class JobsListTests(unittest.TestCase):
    def test_address_normalisation_and_newest_run_grouping(self):
        self.assertEqual(web_app.normalise_site_address('  139A Burns Road, Turramurra NSW 2074, Lot 2 DP 12345'),
                         '139a burns road turramurra')
        rows = [
            {'id': 'new', 'address': '139A Burns Road, Turramurra NSW 2074', 'archived': False},
            {'id': 'other', 'address': '', 'found_site': '', 'archived': False},
            {'id': 'old', 'found_site': '139a burns road turramurra - Lot 2 DP 12345', 'archived': False},
            {'id': 'unknown', 'address': '', 'archived': False},
            {'id': 'archived', 'address': '139A Burns Road, Turramurra', 'archived': True},
        ]
        groups = web_app.group_site_jobs(rows)
        self.assertEqual([row['id'] for row in groups], ['new', 'other', 'unknown', 'archived'])
        self.assertEqual([row['id'] for row in groups[0]['earlier_runs']], ['old'])

    def test_saved_loads_and_stale_flag_in_list(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            projects = {}
            for name in ('current', 'stale', 'building', 'none'):
                folder = root / name
                folder.mkdir()
                projects[name] = {'id': name, 'name': name + '.pdf', 'review_dir': str(folder),
                                  'updated_at': '2026-01-01'}
            for name in ('current', 'stale'):
                (root / name / 'hourly_ai_preliminary_load_report.json').write_text(json.dumps({
                    'included_scope_peak': {'final_design_total_kw': 37.52}}))
            (root / 'building' / 'phase1_base_room_load_report.json').write_text(json.dumps({
                'included_scope_peak': {'design_total_kw': 12.34}}))
            def summary(_web, project):
                return {'result_stale': project['id'] == 'stale', 'address': '', 'found_site': ''}
            with patch.object(web_app, 'load_projects', return_value=projects), \
                 patch.object(job_service, 'project_list_summary', side_effect=summary):
                rows = {row['id']: row for row in web_app.project_list()}
            self.assertEqual((rows['current']['cooling_load_kw'], rows['current']['cooling_load_kind'], rows['current']['result_stale']),
                             (37.52, 'final', False))
            self.assertEqual((rows['stale']['cooling_load_kw'], rows['stale']['cooling_load_kind'], rows['stale']['result_stale']),
                             (37.52, 'final', True))
            self.assertEqual((rows['building']['cooling_load_kw'], rows['building']['cooling_load_kind']),
                             (12.34, 'building_only'))
            self.assertEqual((rows['none']['cooling_load_kw'], rows['none']['cooling_load_kind']), (None, 'none'))

    def test_archive_save_read_and_editor_gate(self):
        with TemporaryDirectory() as directory:
            registry = Path(directory) / 'projects.json'
            registry.write_text(json.dumps({'one': {'id': 'one', 'name': 'one.pdf'}}))
            identity = security.Identity(subject='user', email='user@example.com', development=False)
            def request(value):
                return SimpleNamespace(_identity=lambda: identity, body=value)
            with patch.object(web_app, 'PROJECTS_FILE', registry), \
                 patch.object(web_app, 'read_json_body', side_effect=lambda req: req.body), \
                 patch.object(security, 'require_project_role', return_value='editor') as role:
                self.assertEqual(web_app.api_project_archive(request({'project_id': 'one', 'archived': True}))['archived'], True)
                self.assertTrue(web_app.load_projects()['one']['archived'])
                self.assertTrue(web_app.project_list()[0]['archived'])
                self.assertEqual(web_app.api_project_archive(request({'project_id': 'one', 'archived': False}))['archived'], False)
                self.assertFalse(web_app.load_projects()['one']['archived'])
                self.assertEqual(role.call_args.args[2], 'editor')
                with self.assertRaisesRegex(ValueError, 'true or false'):
                    web_app.api_project_archive(request({'project_id': 'one', 'archived': 'true'}))
            with patch.object(web_app, 'PROJECTS_FILE', registry), \
                 patch.object(web_app, 'read_json_body', return_value={'project_id': 'one', 'archived': True}), \
                 patch.object(security, 'require_project_role', side_effect=security.SecurityError('denied')):
                with self.assertRaises(security.SecurityError):
                    web_app.api_project_archive(request({}))
                self.assertFalse(web_app.load_projects()['one']['archived'])


if __name__ == '__main__':
    unittest.main()
