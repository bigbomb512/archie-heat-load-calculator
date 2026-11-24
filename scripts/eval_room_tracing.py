"""Read-only coverage report for saved room traces and printed plan labels.

Run with an optional web_review directory or one job directory. Printed labels
come from the page text layer, constrained by inferred and saved AI room names.
The report counts label occurrences, so repeated BEDROOM text remains visible.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import autonomous_tasks_service as tasks, house_windows_service, reviewer_room_geometry_service
from ai import room_inference


DEFAULT_ROOT = Path('/Users/jeremyzheng/Documents/GitHub/archie-heat-load-calculator/output/web_review')
HOUSE_LABELS = ('bedroom', 'masterbedroom', 'ensuite', 'bathroom', 'wir', 'walkinrobe',
                'studynook', 'study', 'sitting', 'kitchen', 'scullery', 'laundry',
                'dining', 'living', 'family', 'media', 'entry', 'garage', 'storage',
                'cellar', 'balcony', 'alfresco', 'linen', 'powderroom', 'toilet',
                'corridor', 'robe', 'pantry')


def _read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def _key(value):
    return re.sub(r'[^a-z0-9]', '', str(value).casefold())


def _base(value):
    return _key(re.sub(r'\s*\(\d+\)$', '', str(value)))


def evaluate(root):
    spatial = {row['page']: row for row in _read(root / 'spatial_ocr.json').get('pages', [])}
    vector = {row['page']: row for row in _read(root / 'vector_geometry.json').get('geometry_key_points', {}).get('pages', [])}
    saved_names = _read(root / 'ai_named_rooms.json').get('pages', {})
    records = reviewer_room_geometry_service.current_records(
        reviewer_room_geometry_service._paths({'review_dir': str(root)}))
    pages = []
    for plan in tasks._p0_main_geometry_pages(root):
        page = plan['page']
        source = spatial.get(page, {})
        system = vector.get(page, {}).get('coordinate_systems', {}).get('image_px', {})
        sx = (system.get('image_width') or 0) / (source.get('width') or 1)
        sy = (system.get('image_height') or 0) / (source.get('height') or 1)
        wanted = {_base(name): name for name in tasks._p0_plan_names(root, page)}
        for rows in saved_names.values():
            for row in rows:
                if row.get('page') == page and row.get('named_on_plan', True):
                    wanted.setdefault(_base(row.get('label', '')), row.get('label', ''))
        # A numbered room such as Bedroom 3 may be printed as BEDROOM. Keep
        # individual text occurrences instead of collapsing the repeated name.
        for name in list(wanted.values()):
            short = re.sub(r'\s*\d+$', '', str(name))
            if short != name:
                wanted.setdefault(_base(short), short)
        labels = []
        for raw, bbox in room_inference._text_item_lines(source.get('standalone_text_items', [])):
            key = _key(raw)
            name = wanted.get(key)
            if not name and any(key == term or (term == 'bedroom' and re.fullmatch(r'bedroom\d+', key))
                                for term in HOUSE_LABELS):
                name = raw
            if name and sx and sy and bbox[1] < source.get('height', 0) * .82:
                labels.append({'name': raw, 'x': (bbox[0] + bbox[2]) * sx / 2,
                               'y': (bbox[1] + bbox[3]) * sy / 2})
        traces = []
        for record in records:
            if record.get('page') != page or len(record.get('points_image_px') or []) < 3:
                continue
            polygon = Polygon(record['points_image_px'])
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            scale = float((record.get('calibration') or {}).get('mm_per_px') or 0)
            area = round(polygon.area * (scale / 1000) ** 2, 2) if scale else None
            inside = [index for index, item in enumerate(labels)
                      if polygon.covers(Point(item['x'], item['y']))]
            traces.append({'trace_id': record.get('trace_id'), 'room': record.get('room_label'),
                           'method': record.get('method'), 'area_m2': area,
                           'printed_names': [labels[index]['name'] for index in inside],
                           'merged': len({_base(labels[index]['name']) for index in inside}) > 1,
                           'name_mismatch': bool(inside) and all(
                               _base(labels[index]['name']) != _base(record.get('room_label'))
                               for index in inside),
                           'implausible_area': area is not None and (area < 2 or area > 120)})
        covered = {index for record in records if record.get('page') == page
                   and len(record.get('points_image_px') or []) >= 3
                   for index, item in enumerate(labels)
                   if Polygon(record['points_image_px']).buffer(0).covers(Point(item['x'], item['y']))}
        located = {_base(item['name']) for item in labels}
        unlocated = sorted({name for key, name in wanted.items() if key not in located})
        pages.append({'page': page, 'level': plan.get('level_name'), 'printed_names': labels,
                      'names_without_position': unlocated,
                      'untraced_names': [labels[index]['name'] for index in range(len(labels)) if index not in covered],
                      'traces': traces})
    return {'job': root.name, 'summary': {
        'printed_names': sum(len(page['printed_names']) for page in pages),
        'names_without_position': sum(len(page['names_without_position']) for page in pages),
        'untraced_names': sum(len(page['untraced_names']) for page in pages),
        'traces': sum(len(page['traces']) for page in pages),
        'merged_traces': sum(trace['merged'] for page in pages for trace in page['traces']),
        'name_mismatches': sum(trace['name_mismatch'] for page in pages for trace in page['traces']),
        'implausible_areas': sum(trace['implausible_area'] for page in pages for trace in page['traces'])},
        'pages': pages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('saved_jobs', nargs='?', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--all', action='store_true', help='include non-dwelling jobs')
    args = parser.parse_args()
    roots = ([args.saved_jobs] if (args.saved_jobs / 'reviewer_room_geometry.json').exists()
             else sorted(path for path in args.saved_jobs.iterdir()
                         if (path / 'reviewer_room_geometry.json').exists()))
    for root in roots:
        if not args.all and not house_windows_service.is_dwelling(root):
            continue
        try:
            result = evaluate(root)
            if args.summary:
                result = {'job': result['job'], **result['summary']}
        except (OSError, ValueError, KeyError, TypeError) as error:
            result = {'job': root.name, 'error': str(error)}
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
