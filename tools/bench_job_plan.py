"""Time /api/job-plan's work for a job: the first call in a fresh process, then repeats with nothing changed.

    python3 tools/bench_job_plan.py <project id> [repeats]

Run it before and after a change that touches the job status, the draft model or the plan; the first call includes
building the draft geometry and reading the PDF's page sizes, the repeats show what a page refresh costs."""

import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from backend import job_plan_service, web_app  # noqa: E402


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    project = web_app.project_by_id(sys.argv[1])
    repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    for index in range(1 + repeats):
        started = time.perf_counter()
        plan = job_plan_service.get(web_app, project)
        print(f"{'first ' if index == 0 else 'repeat'} {time.perf_counter() - started:6.2f} s  "
              f"({len(plan.get('outlines', []))} outlines, {len(plan.get('rooms', []))} rooms)")


if __name__ == "__main__":
    main()
