#!/usr/bin/env python3
"""Exercise every resolution GET endpoint through the HTTP handler."""

from http.server import ThreadingHTTPServer
from pathlib import Path
import http.client
import json
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import airflow_resolution, ahu_resolution, plant_resolution, safety_factor_resolution
from backend import web_app


ENDPOINTS = (
    "/api/site-location-resolution",
    "/api/site-design-weather-resolution",
    "/api/room-use-resolution",
    "/api/ceiling-volume-resolution",
    "/api/internal-gains-resolution",
    "/api/thermal-surface-resolution",
    "/api/value-resolution",
    "/api/model-input-resolution",
    "/api/airflow-resolution",
    "/api/ahu-resolution",
    "/api/plant-resolution",
    "/api/safety-factor-resolution",
    "/api/hourly-load-model",
)


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def get(server, path):
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        status, content_type, body = response.status, response.getheader("Content-Type", ""), response.read()
        return status, content_type, body
    finally:
        connection.close()


def post(server, path, payload):
    body = json.dumps(payload)
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    try:
        connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        status, content_type, response_body = response.status, response.getheader("Content-Type", ""), response.read()
        return status, content_type, response_body
    finally:
        connection.close()


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        fresh_dir = root / "fresh"
        saved_dir = root / "saved"
        artifact_dir = root / "artifacts"
        fresh_dir.mkdir()
        saved_dir.mkdir()
        artifact_dir.mkdir()
        saved_model = web_app.empty_hourly_load_model()
        (saved_dir / "hourly_load_model.json").write_text(json.dumps(saved_model), encoding="utf-8")
        (artifact_dir / "hourly_load_model.json").write_text(json.dumps(saved_model), encoding="utf-8")
        for name, value in (
            ("airflow_resolution.json", airflow_resolution.empty_airflow_resolution()),
            ("ahu_resolution.json", ahu_resolution.empty_ahu_resolution()),
            ("plant_resolution.json", plant_resolution.empty_plant_resolution()),
            ("safety_factor_resolution.json", safety_factor_resolution.empty_safety_factor_resolution()),
        ):
            (artifact_dir / name).write_text(json.dumps(value), encoding="utf-8")
        projects = {
            "fresh": {"id": "fresh", "review_dir": str(fresh_dir)},
            "saved": {"id": "saved", "review_dir": str(saved_dir), "hourly_load_model": str(saved_dir / "hourly_load_model.json")},
            "artifacts": {"id": "artifacts", "review_dir": str(artifact_dir), "hourly_load_model": str(artifact_dir / "hourly_load_model.json")},
        }
        project_file = root / "projects.json"
        project_file.write_text(json.dumps(projects), encoding="utf-8")
        with patch.object(web_app, "WEB_REVIEW", root), patch.object(web_app, "PROJECTS_FILE", project_file):
            server = ThreadingHTTPServer(("127.0.0.1", 0), web_app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for project_id in ("fresh", "saved", "artifacts"):
                    for endpoint in ENDPOINTS:
                        path = f"{endpoint}?project_id={project_id}"
                        try:
                            status, content_type, body = get(server, path)
                        except (OSError, http.client.HTTPException) as error:
                            raise AssertionError(f"{project_id} {endpoint} dropped the connection: {error}") from error
                        try:
                            payload = json.loads(body)
                        except (json.JSONDecodeError, UnicodeDecodeError) as error:
                            raise AssertionError(f"{project_id} {endpoint} did not return JSON: {body[:300]!r}") from error
                        check(f"{project_id} {endpoint} returns successful JSON without API error",
                              status == 200 and "application/json" in content_type and isinstance(payload, dict)
                              and "error" not in payload)
                for service in ("airflow", "ahu", "plant", "safety-factor"):
                    endpoint = f"/api/{service}-resolution"
                    try:
                        status, content_type, body = post(server, endpoint, {"project_id": "artifacts", "action": "resolve"})
                    except (OSError, http.client.HTTPException) as error:
                        raise AssertionError(f"POST {endpoint} dropped the connection: {error}") from error
                    try:
                        payload = json.loads(body)
                    except (json.JSONDecodeError, UnicodeDecodeError) as error:
                        raise AssertionError(f"POST {endpoint} did not return JSON: {body[:300]!r}") from error
                    check(f"POST {endpoint} resolves without Handler attribute error",
                          status == 200 and "application/json" in content_type and "error" not in payload
                          and "Handler" not in json.dumps(payload))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    main()
