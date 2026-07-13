"""Production startup guard (Phase 42B).

Pure, config-free safety checks evaluated at application startup. They exist to
make one class of mistake impossible: exposing a public production deployment
that silently authenticates everyone as ``dev_user`` (the development auth stub),
or running the demo evaluator in production.

``main.py`` gathers the real values (from settings + app state) and calls
``check_startup_safety``; a non-empty result aborts startup with a safe message.
Kept separate and dependency-free so it is unit-testable without importing
settings or building the app.
"""

from __future__ import annotations


def check_startup_safety(
    *,
    environment: str,
    dev_auth_active: bool,
    allow_dev_auth: bool,
    demo_mode: bool,
) -> list[str]:
    """Return a list of blocking problems for this startup. Empty == safe.

    - In production, the development auth stub must not be active unless it has
      been explicitly acknowledged (``allow_dev_auth=true``), which is only
      appropriate for a private demo fronted by reverse-proxy auth.
    - Demo mode must never run in production.

    Non-production environments (development, staging, demo) impose no gate here;
    a private demo runs as ``ENVIRONMENT=demo`` behind proxy basic auth.
    """
    problems: list[str] = []
    is_production = (environment or "").strip().lower() == "production"
    if not is_production:
        return problems

    if dev_auth_active and not allow_dev_auth:
        problems.append(
            "The development auth stub is active in production (every request "
            "would be authenticated as 'dev_user'). Wire real authentication, or "
            "set ALLOW_DEV_AUTH=true only for a private demo fronted by "
            "reverse-proxy authentication. See docs/SECURITY.md."
        )
    if demo_mode:
        problems.append(
            "DEMO_MODE must not be enabled in production. Run the demo as "
            "ENVIRONMENT=demo (behind reverse-proxy auth), not production."
        )
    return problems
