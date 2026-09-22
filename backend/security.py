"""Security boundary for Archie HTTP requests.

Development remains deliberately local-only.  Production requests require a
verified Cognito access token; this module never accepts unsigned tokens or a
client-provided identity header.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import threading
import time
from typing import Mapping
import urllib.request


class SecurityError(PermissionError):
    """A request failed authentication, authorization, or origin checks."""


ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}
_JWKS_CACHE: dict[str, tuple[float, dict]] = {}
_JWKS_LOCK = threading.Lock()


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str = ""
    development: bool = False


@dataclass(frozen=True)
class SecurityConfig:
    environment: str
    public_origin: str
    cognito_region: str
    cognito_user_pool_id: str
    cognito_client_id: str
    database_url: str
    s3_bucket: str

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @classmethod
    def from_env(cls) -> "SecurityConfig":
        environment = os.environ.get("ARCHIE_ENV", "development").strip().lower()
        if environment not in {"development", "test", "production"}:
            raise RuntimeError("ARCHIE_ENV must be development, test, or production.")
        return cls(
            environment=environment,
            public_origin=os.environ.get("ARCHIE_PUBLIC_ORIGIN", "").rstrip("/"),
            cognito_region=os.environ.get("ARCHIE_COGNITO_REGION", "").strip(),
            cognito_user_pool_id=os.environ.get("ARCHIE_COGNITO_USER_POOL_ID", "").strip(),
            cognito_client_id=os.environ.get("ARCHIE_COGNITO_CLIENT_ID", "").strip(),
            database_url=os.environ.get("DATABASE_URL", "").strip(),
            s3_bucket=os.environ.get("ARCHIE_S3_BUCKET", "").strip(),
        )

    def validate_startup(self, host: str) -> None:
        if not self.production:
            if host not in {"127.0.0.1", "::1", "localhost"}:
                raise RuntimeError("Development Archie must bind to loopback only. Use the hosted deployment for public access.")
            return
        missing = [name for name, value in {
            "ARCHIE_PUBLIC_ORIGIN": self.public_origin,
            "ARCHIE_COGNITO_REGION": self.cognito_region,
            "ARCHIE_COGNITO_USER_POOL_ID": self.cognito_user_pool_id,
            "ARCHIE_COGNITO_CLIENT_ID": self.cognito_client_id,
            "DATABASE_URL": self.database_url,
            "ARCHIE_S3_BUCKET": self.s3_bucket,
        }.items() if not value]
        if missing:
            raise RuntimeError("Production security configuration is incomplete: " + ", ".join(missing))
        if not self.public_origin.startswith("https://"):
            raise RuntimeError("ARCHIE_PUBLIC_ORIGIN must be an HTTPS origin in production.")

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return self.issuer + "/.well-known/jwks.json"


def config() -> SecurityConfig:
    return SecurityConfig.from_env()


def require_allowed_origin(headers: Mapping[str, str], *, host: str, configuration: SecurityConfig | None = None) -> None:
    """Reject cross-origin state changes while allowing non-browser tooling."""
    configuration = configuration or config()
    origin = str(headers.get("Origin", "")).rstrip("/")
    if not origin:
        return
    if configuration.production:
        allowed = {configuration.public_origin}
    else:
        allowed = {f"http://{host}", f"http://127.0.0.1:{host.rsplit(':', 1)[-1]}", f"http://localhost:{host.rsplit(':', 1)[-1]}"}
    if origin not in allowed:
        raise SecurityError("Cross-origin request rejected.")


def _jwks(configuration: SecurityConfig) -> dict:
    with _JWKS_LOCK:
        cached = _JWKS_CACHE.get(configuration.jwks_url)
        if cached and cached[0] > time.time():
            return cached[1]
        request = urllib.request.Request(configuration.jwks_url, headers={"User-Agent": "ArchieAuth/1.0"})
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
        keys = {key.get("kid"): key for key in payload.get("keys", []) if key.get("kid")}
        if not keys:
            raise SecurityError("Cognito JWKS response contained no signing keys.")
        _JWKS_CACHE[configuration.jwks_url] = (time.time() + 3600, keys)
        return keys


def identity_from_headers(headers: Mapping[str, str], *, client_host: str, configuration: SecurityConfig | None = None) -> Identity:
    """Return a verified identity, or fail closed in production."""
    configuration = configuration or config()
    if not configuration.production:
        if client_host not in {"127.0.0.1", "::1", "localhost"}:
            raise SecurityError("Development Archie accepts requests from loopback only.")
        return Identity(subject="local-development-user", development=True)

    authorization = str(headers.get("Authorization", ""))
    if not authorization.startswith("Bearer "):
        raise SecurityError("Authentication required.")
    token = authorization[7:].strip()
    if not token:
        raise SecurityError("Authentication required.")
    try:
        import jwt
        from jwt.algorithms import RSAAlgorithm
    except ImportError as error:
        raise SecurityError("Production authentication dependency is unavailable.") from error
    try:
        header = jwt.get_unverified_header(token)
        jwk = _jwks(configuration).get(header.get("kid"))
        if not jwk:
            raise SecurityError("Unknown Cognito signing key.")
        claims = jwt.decode(token, RSAAlgorithm.from_jwk(json.dumps(jwk)), algorithms=["RS256"],
                            issuer=configuration.issuer, options={"require": ["exp", "iat", "sub"], "verify_aud": False})
    except SecurityError:
        raise
    except Exception as error:
        raise SecurityError("Invalid or expired authentication token.") from error
    if claims.get("token_use") != "access" or claims.get("client_id") != configuration.cognito_client_id:
        raise SecurityError("Token is not valid for this Archie application.")
    return Identity(subject=str(claims["sub"]), email=str(claims.get("username", "")))


def role_for_project(project: Mapping[str, object], identity: Identity) -> str:
    """Read local membership metadata; production persistence supplies the same shape."""
    if identity.development:
        return "owner"
    if project.get("owner_id") == identity.subject:
        return "owner"
    members = project.get("memberships", [])
    if isinstance(members, list):
        for member in members:
            if isinstance(member, Mapping) and member.get("user_id") == identity.subject:
                role = str(member.get("role", ""))
                if role in ROLE_RANK:
                    return role
    return ""


def require_project_role(project: Mapping[str, object], identity: Identity, required_role: str) -> str:
    if required_role not in ROLE_RANK:
        raise RuntimeError("Unsupported required project role.")
    role = role_for_project(project, identity)
    if ROLE_RANK.get(role, 0) < ROLE_RANK[required_role]:
        raise SecurityError("You do not have permission to access this project.")
    return role
