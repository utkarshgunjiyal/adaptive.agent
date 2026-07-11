"""Phase 42B — production startup guard. Config-free pure-function tests."""

from app.deploy.startup_guard import check_startup_safety


def test_development_never_blocks():
    problems = check_startup_safety(
        environment="development", dev_auth_active=True, allow_dev_auth=False, demo_mode=True
    )
    assert problems == []


def test_production_blocks_silent_dev_auth():
    problems = check_startup_safety(
        environment="production", dev_auth_active=True, allow_dev_auth=False, demo_mode=False
    )
    assert len(problems) == 1
    assert "dev_user" in problems[0]


def test_production_allows_acknowledged_dev_auth():
    problems = check_startup_safety(
        environment="production", dev_auth_active=True, allow_dev_auth=True, demo_mode=False
    )
    assert problems == []


def test_production_with_real_auth_is_safe():
    problems = check_startup_safety(
        environment="production", dev_auth_active=False, allow_dev_auth=False, demo_mode=False
    )
    assert problems == []


def test_production_blocks_demo_mode():
    problems = check_startup_safety(
        environment="production", dev_auth_active=False, allow_dev_auth=False, demo_mode=True
    )
    assert any("DEMO_MODE" in p for p in problems)


def test_demo_environment_is_not_production_gated():
    # A private demo runs as ENVIRONMENT=demo → no startup gate here.
    problems = check_startup_safety(
        environment="demo", dev_auth_active=True, allow_dev_auth=False, demo_mode=True
    )
    assert problems == []


def test_environment_matching_is_case_insensitive():
    problems = check_startup_safety(
        environment="  Production  ", dev_auth_active=True, allow_dev_auth=False, demo_mode=False
    )
    assert len(problems) == 1
