#!/usr/bin/env python3
"""Regression checks for the public HTTP security boundary."""

from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import http.client
import os
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import security
from backend.web_app import Handler, MAX_JSON_BYTES, read_json_body, safe_link


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


class Request:
    def __init__(self, body, path="/api/example"):
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = BytesIO(body)


def request(server, path, *, method="GET", headers=None):
    connection = http.client.HTTPConnection(*server.server_address)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        frontend = root / "frontend"
        frontend.mkdir()
        (frontend / "index.html").write_text("safe homepage", encoding="utf-8")
        (frontend / "app.js").write_text("console.log('safe')", encoding="utf-8")
        (root / ".env").write_text("do-not-serve", encoding="utf-8")
        (root / "output").mkdir()
        (root / "output" / "private.pdf").write_bytes(b"%PDF-private")
        (root / ".git").mkdir()
        (root / ".git" / "HEAD").write_text("private", encoding="utf-8")
        with patch("backend.web_app.ROOT", root), patch("backend.web_app.FRONTEND", frontend):
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, headers, body = request(server, "/frontend/app.js")
                check("approved frontend asset is served", status == 200 and body.startswith(b"console"))
                check("frontend response has browser hardening headers", headers.get("X-Content-Type-Options") == "nosniff" and "frame-ancestors 'none'" in headers.get("Content-Security-Policy", ""))
                check("CORS wildcard is absent", "Access-Control-Allow-Origin" not in headers)
                for path in ("/.env", "/.git/HEAD", "/output/private.pdf", "/backend/web_app.py", "/../../../../../etc/hosts"):
                    status, _headers, body = request(server, path)
                    check(f"static exposure blocked for {path}", status == 404 and b"private" not in body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    oversized = Request(b"{" + b"x" * MAX_JSON_BYTES + b"}")
    try:
        read_json_body(oversized)
    except ValueError:
        check("oversized JSON is rejected before parsing", True)
    else:
        check("oversized JSON is rejected before parsing", False)

    check("safe links never leak an outside filesystem path", safe_link("/private/secret.pdf") == "")
    owner = {"owner_id": "owner", "memberships": [{"user_id": "editor", "role": "editor"}, {"user_id": "viewer", "role": "viewer"}]}
    check("owner role is resolved", security.require_project_role(owner, security.Identity("owner"), "owner") == "owner")
    check("editor cannot perform owner work", raises_security(lambda: security.require_project_role(owner, security.Identity("editor"), "owner")))
    check("viewer cannot edit", raises_security(lambda: security.require_project_role(owner, security.Identity("viewer"), "editor")))
    check("missing membership is denied", raises_security(lambda: security.require_project_role(owner, security.Identity("intruder"), "viewer")))
    production = security.SecurityConfig("production", "https://app.archie.example", "ap-southeast-2", "pool", "client", "postgresql://example", "archie-private")
    check("production rejects requests without a bearer token", raises_security(lambda: security.identity_from_headers({}, client_host="127.0.0.1", configuration=production)))
    check("production rejects a foreign mutation origin", raises_security(lambda: security.require_allowed_origin({"Origin": "https://attacker.example"}, host="app.archie.example", configuration=production)))


def raises_security(callback):
    try:
        callback()
    except security.SecurityError:
        return True
    return False


if __name__ == "__main__":
    main()
