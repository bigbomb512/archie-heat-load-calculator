#!/usr/bin/env python3

import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "frontend" / "index.html"
SCRIPT = ROOT / "frontend" / "js" / "app.js"


def contract_errors(html, script):
    required_ids = set(re.findall(r'requiredElement\("([^"]+)"\)', script))
    html_ids = re.findall(r'\bid=["\']([^"\']+)["\']', html)
    counts = Counter(html_ids)
    return [
        f"Frontend template is missing #{element_id}"
        for element_id in sorted(required_ids - set(html_ids))
    ] + [
        f"Frontend template defines #{element_id} more than once"
        for element_id, count in sorted(counts.items()) if count > 1
    ]


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def main():
    html = HTML.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")

    check("current frontend contract", contract_errors(html, script) == [])
    broken_html = html.replace('id="btnAnalyse"', 'id="removedBtnAnalyse"', 1)
    check(
        "missing frontend id is named",
        contract_errors(broken_html, script) == ["Frontend template is missing #btnAnalyse"],
    )


if __name__ == "__main__":
    main()
