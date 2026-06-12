"""Tests for deterministic production static checks."""

from __future__ import annotations

import json
from pathlib import Path

from pact.production_static import run_static_checks


def _result(results, check_id: str):
    return next(result for result in results if result.check_id == check_id)


def test_static_checks_use_not_detected_for_missing_optional_artifacts(tmp_path: Path) -> None:
    results = run_static_checks(tmp_path)

    assert _result(results, "static.openapi_present").status == "not_detected"
    assert _result(results, "static.sbom").status == "not_detected"


def test_static_checks_fail_on_likely_hard_coded_secret(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "settings.py").write_text('API_KEY = "not-a-placeholder-secret"\n')

    result = _result(run_static_checks(tmp_path), "static.secret_scan")

    assert result.status == "fail"
    assert result.severity == "critical"


def test_static_checks_parse_openapi_and_check_security_shape(tmp_path: Path) -> None:
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "API", "version": "1"},
        "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
        "paths": {
            "/things": {
                "get": {
                    "responses": {
                        "200": {"description": "ok"},
                        "401": {"description": "unauthorized"},
                        "429": {
                            "description": "rate limited",
                            "headers": {"Retry-After": {"schema": {"type": "integer"}}},
                        },
                    }
                }
            }
        },
    }
    (tmp_path / "openapi.json").write_text(json.dumps(spec))

    results = run_static_checks(tmp_path)

    assert _result(results, "static.openapi_valid").status == "pass"
    assert _result(results, "static.openapi_auth_schemes").status == "pass"
    assert _result(results, "static.openapi_error_responses").status == "pass"
    assert _result(results, "static.openapi_rate_limit_shape").status == "pass"
