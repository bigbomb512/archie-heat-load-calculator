"""Read-only replay of schedule, plan-tag and traced-wall matching for saved dwellings.

No room writes, input assembly, or load calculation is performed. Run with an optional
output/web_review directory; the default is the local saved-job collection.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import house_windows
from ai.autonomous_tasks import usable_calibration
from backend import house_windows_service as service, reviewer_room_geometry_service
from backend.autonomous_tasks_service import _p0_main_geometry_pages
from backend.job_runner import read_json


DEFAULT_ROOT = Path('/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/output/web_review')


def evaluate(root):
    paths = reviewer_room_geometry_service._paths({'review_dir': str(root)})
    records = [row for row in reviewer_room_geometry_service.current_records(paths)
               if usable_calibration(row.get('calibration', {}).get('status'))
               and float(row.get('calibration', {}).get('mm_per_px') or 0) > 0]
    scopes = {row.get('room_id'): row.get('space_scope') for row in
              read_json(root / 'room_use_resolution.json').get('records', []) if isinstance(row, dict)}
    levels = {row['page']: row.get('level_name') for row in _p0_main_geometry_pages(root)}
    cooled = [row for row in records if scopes.get(row.get('room_id')) in service.COOLED
              and service._same_plan_level(row, levels)]
    systems = {page.get('page'): page.get('coordinate_systems', {}).get('image_px', {}) for page in
               read_json(root / 'vector_geometry.json').get('geometry_key_points', {}).get('pages', [])}
    source = read_json(root / 'ai_input.json').get('source_pdf')
    close, read_page = service._pdf_pages(source)
    try:
        windows = []
        for page in service._schedule_pages(root):
            windows.extend(house_windows.read_schedules(read_page(page)[2], page)['windows'])
        openings, unplaced, tags_found = [], [], []
        by_page = {}
        for trace in cooled:
            by_page.setdefault(trace.get('page'), []).append(trace)
        for page in sorted({*by_page, *[p for p in levels if isinstance(p, int)]}):
            width, height, words = read_page(page)
            tags = house_windows.find_window_tags(words + read_page.flow(page), page, levels.get(page))
            tags_found.extend(tags)
            system = systems.get(page) or {}
            if not system.get('image_width') or not width:
                unplaced.extend({'mark': tag['mark'], 'page': page, 'reason': 'no image frame'} for tag in tags)
                continue
            sx, sy = system['image_width'] / width, system['image_height'] / height
            scale = service._page_scale(root, page, records, sx)
            if scale is None:
                unplaced.extend({'mark': tag['mark'], 'page': page, 'reason': 'no plan scale'} for tag in tags)
                continue
            rooms = [{'trace_id': trace['trace_id'], 'points': trace['points_image_px'],
                      'outside_edges': service._outside_edges(trace, records, float(trace['calibration']['mm_per_px'])),
                      'mm_per_point': float(trace['calibration']['mm_per_px']),
                      'existing_openings': trace.get('openings', [])}
                     for trace in by_page.get(page, [])]
            result = house_windows.place_windows([{**tag, 'x': tag['x'] * sx, 'y': tag['y'] * sy}
                                                   for tag in tags], windows, rooms, scale / sx)
            openings.extend(result['openings'])
            unplaced.extend(result['unplaced'])
        placed_marks = {house_windows.normalized_mark(row['mark']) for row in openings}
        unplaced_marks = {house_windows.normalized_mark(row['mark']) for row in unplaced}
        for row in windows:
            if house_windows.normalized_mark(row['mark']) not in placed_marks | unplaced_marks:
                unplaced.append({'mark': row['mark'], 'page': row['page'],
                                 'reason': 'schedule window without a matched traced wall'})
                unplaced_marks.add(house_windows.normalized_mark(row['mark']))
        return {'schedule': len(windows), 'placed': len(openings), 'tags': len(tags_found),
                'reasons': dict(sorted(Counter(row['reason'] for row in unplaced).items())),
                'openings': openings, 'unplaced': unplaced, 'tags_found': tags_found}
    finally:
        close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('saved_jobs', type=Path, nargs='?', default=DEFAULT_ROOT)
    parser.add_argument('--json', action='store_true', help='include tag and opening details')
    args = parser.parse_args()
    for root in sorted(args.saved_jobs.iterdir()):
        if not root.is_dir() or not (root / 'house_windows.json').exists():
            continue
        use = read_json(root / 'room_use_resolution.json')
        if (use.get('dwelling_context') or {}).get('is_dwelling') is not True:
            continue
        try:
            result = evaluate(root)
            if not args.json:
                result = {key: result[key] for key in ('schedule', 'placed', 'tags', 'reasons')}
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            result = {'error': str(error)}
        print(json.dumps({'job': root.name, **result}, sort_keys=True))


if __name__ == '__main__':
    main()
