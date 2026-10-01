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
    room_use_ids = {"btnResolveRoomUses", "roomUseResolutionStatus", "roomUseResolutionResults"}
    check(
        "room-use review controls remain available",
        room_use_ids <= set(re.findall(r'\bid=["\']([^"\']+)["\']', html)),
    )
    ceiling_ids = {"btnResolveCeilingVolumes", "ceilingVolumeResolutionStatus", "ceilingVolumeResolutionResults"}
    check(
        "ceiling-volume review controls remain available",
        ceiling_ids <= set(re.findall(r'\bid=["\']([^"\']+)["\']', html)),
    )
    internal_gains_ids = {"btnResolveInternalGains", "internalGainsResolutionStatus", "internalGainsResolutionResults"}
    check(
        "internal-gains review controls remain available",
        internal_gains_ids <= set(re.findall(r'\bid=["\']([^"\']+)["\']', html)),
    )
    airflow_ids = {"btnResolveAirflow", "airflowResolutionStatus", "airflowResolutionResults"}
    check(
        "airflow review controls remain available",
        airflow_ids <= set(re.findall(r'\bid=["\']([^"\']+)["\']', html)) and "/api/airflow-resolution" in script,
    )
    ahu_ids = {"btnResolveAhuResolution", "btnMaterializeAhuPreliminary", "ahuResolutionStatus", "ahuResolutionResults"}
    check(
        "AHU preliminary review controls remain available",
        ahu_ids <= set(re.findall(r'\bid=["\']([^"\']+)["\']', html)) and "/api/ahu-resolution" in script,
    )
    plant_ids = {"btnResolvePlantResolution", "btnMaterializePlantPreliminary", "plantResolutionStatus", "plantResolutionResults"}
    check(
        "plant preliminary review controls remain available",
        plant_ids <= set(re.findall(r'\bid=["\']([^"\']+)["\']', html)) and "/api/plant-resolution" in script,
    )
    room_inference_ids = {"roomInferenceNotice", "roomInferenceStatus", "btnRetryRoomInference", "btnReviewRoomEvidence", "btnOpenRoomGeometry"}
    check(
        "automatic room-inference recovery controls remain available",
        room_inference_ids <= set(re.findall(r'\bid=["\']([^"\']+)["\']', html)) and "/api/room-inference" in script and "Finding rooms" in script,
    )
    test_workspace_ids = {"testWorkspacePanel", "testWorkspaceFixture", "btnTestWorkspaceRun", "btnTestWorkspaceReset", "testWorkspaceStatus", "testWorkspaceStages", "testWorkspaceSummary"}
    check(
        "permission-free local test workspace is feature-detected and draft-labelled",
        test_workspace_ids <= set(re.findall(r'\bid=["\']([^"\']+)["\']', html))
        and "/api/test-mode/status" in script and "/api/test-mode/run" in script
        and "report_label" in script and "provenance_components" in script,
    )


if __name__ == "__main__":
    main()
