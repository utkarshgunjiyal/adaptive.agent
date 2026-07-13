"""Deployment environment validation (Phase 42B).

Validates a deployment's environment BEFORE the stack is exposed. It reads a
plain mapping (``os.environ`` on the VM, or a dict in tests) — it never imports
application settings, never connects to anything, and NEVER prints secret values
(only variable *names* and safe verdicts).

Run on the VM:

    python -m app.deploy.env_check            # validate the process environment
    set -a; . ./.env; set +a; python -m app.deploy.env_check   # validate a .env

Exit code 0 == safe to deploy; non-zero == blocking errors were found.

Profiles are derived from ``ENVIRONMENT``:
- ``production`` / ``staging`` / ``demo`` → strict (public-ish exposure)
- anything else (``development``)          → lenient (warnings only)
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping

# Values that indicate an unset placeholder / well-known default that must not
# survive into a public deployment. Compared case-insensitively, exact match.
_PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "change-me",
    "change_me",
    "placeholder",
    "todo",
    "tbd",
    "xxx",
    "example",
    "secret",
    "password",
    "minioadmin",  # MinIO default credential
    "admin",
    "root",
    "your-key-here",
    "your_api_key",
}

# A permissive but real hostname check (labels + TLD). Rejects schemes, paths,
# spaces, and bare "localhost"/IPs for a public domain.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)

_STRICT_ENVIRONMENTS = {"production", "staging", "demo"}


class ValidationReport:
    """Collected verdicts. ``ok`` is False iff there are blocking errors."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key, default) or "").strip()


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in _PLACEHOLDER_VALUES


def validate_env(env: Mapping[str, str]) -> ValidationReport:
    """Validate a deployment environment mapping. Never reads secret *values*
    into the report — only names and safe verdicts."""
    report = ValidationReport()
    environment = _get(env, "ENVIRONMENT", "development").lower()
    strict = environment in _STRICT_ENVIRONMENTS
    public = environment in {"production", "staging"}  # demo is private-by-proxy

    def require(name: str, *, secret: bool = False) -> str:
        value = _get(env, name)
        if not value:
            report.error(f"{name} is required but not set.")
        elif secret and _is_placeholder(value):
            report.error(f"{name} is set to a placeholder/default value — set a real secret.")
        return value

    # -- Core connectivity -------------------------------------------------- #
    require("MONGO_URL")
    require("REDIS_URL")
    require("QDRANT_URL")
    require("MINIO_ENDPOINT")

    # -- Credentials must not be defaults in a strict profile --------------- #
    for name in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        value = _get(env, name)
        if not value:
            continue
        if strict and _is_placeholder(value):
            report.error(f"{name} is a default/placeholder value; rotate it before deploying.")

    # -- Domain + CORS agreement -------------------------------------------- #
    domain = _get(env, "DOMAIN")
    if strict:
        if not domain:
            report.error("DOMAIN is required for a public/demo deployment (used by the reverse proxy).")
        elif domain.lower() not in {"localhost"} and not _DOMAIN_RE.match(domain):
            report.error(f"DOMAIN does not look like a valid hostname: {domain!r}.")

    cors = _get(env, "CORS_ORIGINS")
    if public:
        if not cors:
            report.error("CORS_ORIGINS is required in production (never leave it unset).")
        elif cors == "*":
            report.error("CORS_ORIGINS='*' is unsafe in production; set explicit origins.")
        elif domain and domain.lower() != "localhost":
            expected = f"https://{domain}"
            origins = {o.strip().rstrip("/") for o in cors.split(",") if o.strip()}
            if expected not in origins:
                report.error(
                    f"CORS_ORIGINS does not include the deployment origin ({expected}). "
                    f"Set CORS_ORIGINS to your app origin(s)."
                )
    elif cors == "*":
        report.warn("CORS_ORIGINS='*' (credentials are disabled). Fine for local dev only.")

    # -- Secure-cookie posture ---------------------------------------------- #
    if public:
        if not _truthy(_get(env, "COOKIE_SECURE")):
            report.warn(
                "COOKIE_SECURE is not enabled. If/when real cookie auth is wired, "
                "set COOKIE_SECURE=true (TLS-only cookies)."
            )
        samesite = _get(env, "COOKIE_SAMESITE", "lax").lower()
        if samesite == "none" and not _truthy(_get(env, "COOKIE_SECURE")):
            report.error("COOKIE_SAMESITE=none requires COOKIE_SECURE=true.")

    # -- Auth gate ---------------------------------------------------------- #
    allow_dev_auth = _truthy(_get(env, "ALLOW_DEV_AUTH"))
    if environment == "production" and allow_dev_auth:
        report.warn(
            "ALLOW_DEV_AUTH=true in production: every request authenticates as "
            "'dev_user'. Only acceptable behind reverse-proxy authentication. "
            "Replace with real auth before public multi-user use."
        )
    if environment == "production" and not allow_dev_auth:
        report.note(
            "Dev auth stub is NOT permitted in production — ensure real auth is "
            "wired (a dependency override) or startup will refuse to boot."
        )

    # -- Demo mode ---------------------------------------------------------- #
    if _truthy(_get(env, "DEMO_MODE")):
        if environment == "production":
            report.error("DEMO_MODE must not be true in production (run the demo as ENVIRONMENT=demo).")
        else:
            report.note("DEMO_MODE is enabled — marked demo prompts will pause for HITL.")

    # -- LLM provider ------------------------------------------------------- #
    use_real_llm = _truthy(_get(env, "AGENT_USE_REAL_LLM"))
    if use_real_llm:
        if not (_get(env, "ANTHROPIC_API_KEY") or _get(env, "OPENROUTER_API_KEY")):
            report.error(
                "AGENT_USE_REAL_LLM=true but neither ANTHROPIC_API_KEY nor "
                "OPENROUTER_API_KEY is set."
            )
    elif strict:
        report.note("AGENT_USE_REAL_LLM is false — the deterministic stub provider will answer.")

    # -- Checkpoint backend ------------------------------------------------- #
    checkpoint = _get(env, "AGENT_CHECKPOINT_BACKEND", "memory").lower()
    if checkpoint not in {"memory", "mongo"}:
        report.error(f"AGENT_CHECKPOINT_BACKEND must be 'memory' or 'mongo', got {checkpoint!r}.")
    elif public and checkpoint != "mongo":
        report.warn("AGENT_CHECKPOINT_BACKEND is not 'mongo'; resume will not survive a restart.")

    # -- Rate limiting ------------------------------------------------------ #
    if public:
        if not _truthy(_get(env, "RATE_LIMIT_ENABLED")):
            report.warn("RATE_LIMIT_ENABLED is false in production; enable it before public exposure.")
        elif _get(env, "RATE_LIMIT_BACKEND", "memory").lower() != "redis":
            report.warn("RATE_LIMIT_BACKEND is not 'redis'; limits are per-process only (not shared).")

    # -- Metrics exposure policy (must be explicit) ------------------------- #
    metrics_enabled = _truthy(_get(env, "METRICS_ENABLED"))
    if metrics_enabled:
        report.note(
            "METRICS_ENABLED=true → GET /metrics is served. Ensure the reverse "
            "proxy does NOT expose /metrics publicly."
        )
    else:
        report.note("METRICS_ENABLED=false → no /metrics endpoint (default).")

    return report


def format_report(report: ValidationReport, *, environment: str) -> str:
    lines = [f"Environment validation — profile: {environment or 'development'}", ""]
    for msg in report.errors:
        lines.append(f"  ERROR   {msg}")
    for msg in report.warnings:
        lines.append(f"  WARN    {msg}")
    for msg in report.notes:
        lines.append(f"  note    {msg}")
    lines.append("")
    lines.append("RESULT: " + ("OK (safe to deploy)" if report.ok else "FAILED (fix ERRORs above)"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    env = os.environ
    report = validate_env(env)
    environment = _get(env, "ENVIRONMENT", "development")
    print(format_report(report, environment=environment))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
