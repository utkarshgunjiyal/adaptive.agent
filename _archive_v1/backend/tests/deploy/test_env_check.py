"""Phase 42B — deployment environment validation. Config-free dict-driven tests.

Also asserts the report never contains secret *values* (only names/verdicts).
"""

from app.deploy.env_check import format_report, validate_env


def _base_prod_env(**overrides):
    env = {
        "ENVIRONMENT": "production",
        "MONGO_URL": "mongodb://mongodb:27017",
        "REDIS_URL": "redis://redis:6379/0",
        "QDRANT_URL": "http://qdrant:6333",
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ROOT_USER": "runner_admin",
        "MINIO_ROOT_PASSWORD": "s3cr3t-rotated-value",
        "DOMAIN": "demo.runner.ai",
        "CORS_ORIGINS": "https://demo.runner.ai",
        "AGENT_CHECKPOINT_BACKEND": "mongo",
        "RATE_LIMIT_ENABLED": "true",
        "RATE_LIMIT_BACKEND": "redis",
        "AGENT_USE_REAL_LLM": "false",
        "METRICS_ENABLED": "false",
    }
    env.update(overrides)
    return env


def test_valid_production_env_is_ok():
    report = validate_env(_base_prod_env())
    assert report.ok, report.errors


def test_missing_required_var_errors():
    env = _base_prod_env()
    del env["MONGO_URL"]
    report = validate_env(env)
    assert not report.ok
    assert any("MONGO_URL" in e for e in report.errors)


def test_default_minio_credentials_rejected_in_production():
    report = validate_env(_base_prod_env(MINIO_ROOT_PASSWORD="minioadmin"))
    assert not report.ok
    assert any("MINIO_ROOT_PASSWORD" in e for e in report.errors)


def test_wildcard_cors_rejected_in_production():
    report = validate_env(_base_prod_env(CORS_ORIGINS="*"))
    assert not report.ok
    assert any("CORS_ORIGINS" in e for e in report.errors)


def test_cors_must_include_deployment_origin():
    report = validate_env(_base_prod_env(CORS_ORIGINS="https://other.example.com"))
    assert not report.ok
    assert any("deployment origin" in e for e in report.errors)


def test_invalid_domain_rejected():
    report = validate_env(_base_prod_env(DOMAIN="not a domain"))
    assert not report.ok
    assert any("DOMAIN" in e for e in report.errors)


def test_real_llm_requires_key():
    report = validate_env(_base_prod_env(AGENT_USE_REAL_LLM="true"))
    assert not report.ok
    assert any("ANTHROPIC_API_KEY" in e for e in report.errors)


def test_real_llm_with_key_is_ok():
    report = validate_env(_base_prod_env(AGENT_USE_REAL_LLM="true", ANTHROPIC_API_KEY="sk-real"))
    assert report.ok, report.errors


def test_demo_mode_in_production_is_error():
    report = validate_env(_base_prod_env(DEMO_MODE="true"))
    assert not report.ok
    assert any("DEMO_MODE" in e for e in report.errors)


def test_demo_environment_allows_demo_mode():
    env = _base_prod_env(ENVIRONMENT="demo", DEMO_MODE="true")
    # demo is strict for creds/domain but permits demo mode.
    report = validate_env(env)
    assert report.ok, report.errors


def test_samesite_none_requires_secure():
    report = validate_env(_base_prod_env(COOKIE_SAMESITE="none", COOKIE_SECURE="false"))
    assert not report.ok
    assert any("COOKIE_SAMESITE" in e for e in report.errors)


def test_rate_limit_warning_when_disabled_in_production():
    report = validate_env(_base_prod_env(RATE_LIMIT_ENABLED="false"))
    assert report.ok  # a warning, not an error
    assert any("RATE_LIMIT_ENABLED" in w for w in report.warnings)


def test_development_env_is_lenient():
    report = validate_env({
        "ENVIRONMENT": "development",
        "MONGO_URL": "mongodb://localhost:27017",
        "REDIS_URL": "redis://localhost:6379/0",
        "QDRANT_URL": "http://localhost:6333",
        "MINIO_ENDPOINT": "localhost:9000",
        "CORS_ORIGINS": "*",
    })
    assert report.ok, report.errors


def test_report_never_prints_secret_values():
    secret = "super-secret-password-value-123"
    report = validate_env(_base_prod_env(
        MINIO_ROOT_PASSWORD=secret,
        ANTHROPIC_API_KEY="sk-" + secret,
        AGENT_USE_REAL_LLM="true",
    ))
    rendered = format_report(report, environment="production")
    assert secret not in rendered
    for msg in report.errors + report.warnings + report.notes:
        assert secret not in msg
