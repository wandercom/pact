"""Deterministic static checks used by the production-readiness gate.

These checks borrow the useful parts of webprobe's static audit model:
typed result states, explicit not-detected semantics, and artifact-backed
evidence. They deliberately do not run browser, network, or LLM probes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from pact.schemas_production import StaticCheckResult


_EXCLUDED_DIRS = {
    ".git",
    ".pact",
    ".venv",
    "__pycache__",
    "dist",
    "docs",
    "node_modules",
    "tests",
}
_SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".env",
}
_SECRET_RE = re.compile(
    r"(?i)\b(?:secret|api[_-]?key|token|password|passwd|access[_-]?token)\b"
    r"\s*[:=]\s*['\"]([^'\"]{8,})['\"]"
)
_OPENAPI_NAMES = {
    "openapi.json",
    "openapi.yaml",
    "openapi.yml",
    "swagger.json",
    "swagger.yaml",
    "swagger.yml",
}
_MANIFEST_NAMES = {
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "requirements.txt",
    "requirements.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
}
_SBOM_NAMES = {
    "sbom.json",
    "bom.json",
    "cyclonedx.json",
    "sbom.cdx.json",
}


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def _relative(root: Path, paths: list[Path]) -> list[str]:
    return [str(path.relative_to(root)) for path in paths]


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized.startswith("${")
        or normalized in {"todo", "tbd", "fixme", "placeholder", "example", "dummy"}
        or "your-" in normalized
        or "<" in normalized
        or "os.environ" in normalized
        or "env.get" in normalized
    )


def _find_secret_hits(root: Path) -> list[str]:
    hits: list[str] = []
    for path in _iter_files(root):
        if path.suffix not in _SOURCE_SUFFIXES:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in _SECRET_RE.finditer(text):
            value = match.group(1)
            if not _is_placeholder(value):
                hits.append(f"{path.relative_to(root)}:{text[:match.start()].count(chr(10)) + 1}")
    return hits


def _find_named_files(root: Path, names: set[str]) -> list[Path]:
    return [path for path in _iter_files(root) if path.name in names]


def _load_openapi(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        if path.suffix == ".json":
            data = json.loads(path.read_text())
        else:
            data = yaml.safe_load(path.read_text())
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "OpenAPI document is not an object"
    if not any(key in data for key in ("openapi", "swagger")):
        return None, "OpenAPI document is missing openapi or swagger version field"
    return data, ""


def _operations(spec: dict[str, Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return operations
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            if isinstance(operation, dict):
                operations.append(operation)
    return operations


def _openapi_results(root: Path) -> list[StaticCheckResult]:
    files = _find_named_files(root, _OPENAPI_NAMES)
    if not files:
        return [
            StaticCheckResult(
                check_id="static.openapi_present",
                title="OpenAPI document present",
                status="not_detected",
                severity="suggestion",
                reason="artifact_unavailable:openapi:none_found",
            )
        ]

    path = files[0]
    spec, error = _load_openapi(path)
    evidence = [str(path.relative_to(root))]
    if spec is None:
        return [
            StaticCheckResult(
                check_id="static.openapi_valid",
                title="OpenAPI document parses cleanly",
                status="fail",
                severity="warning",
                evidence=evidence,
                reason=error,
            )
        ]

    results = [
        StaticCheckResult(
            check_id="static.openapi_valid",
            title="OpenAPI document parses cleanly",
            status="pass",
            evidence=evidence,
        )
    ]
    components = spec.get("components", {})
    security_schemes = components.get("securitySchemes", {}) if isinstance(components, dict) else {}
    top_level_security = spec.get("security", [])
    if security_schemes or top_level_security:
        results.append(
            StaticCheckResult(
                check_id="static.openapi_auth_schemes",
                title="OpenAPI auth scheme documented",
                status="pass",
                evidence=evidence,
            )
        )
    else:
        results.append(
            StaticCheckResult(
                check_id="static.openapi_auth_schemes",
                title="OpenAPI auth scheme documented",
                status="fail",
                severity="warning",
                evidence=evidence,
                reason="OpenAPI has no securitySchemes or top-level security declaration",
            )
        )

    operations = _operations(spec)
    if not operations:
        results.append(
            StaticCheckResult(
                check_id="static.openapi_error_responses",
                title="OpenAPI error responses defined",
                status="not_detected",
                severity="suggestion",
                evidence=evidence,
                reason="OpenAPI has no operations",
            )
        )
    else:
        missing_errors = 0
        has_429 = False
        missing_retry_after = False
        for operation in operations:
            responses = operation.get("responses", {})
            if not isinstance(responses, dict):
                missing_errors += 1
                continue
            status_codes = {str(code) for code in responses}
            if not any(code.startswith(("4", "5")) or code == "default" for code in status_codes):
                missing_errors += 1
            if "429" in status_codes:
                has_429 = True
                response = responses["429"]
                headers = response.get("headers", {}) if isinstance(response, dict) else {}
                if not isinstance(headers, dict) or "Retry-After" not in headers:
                    missing_retry_after = True
        if missing_errors:
            results.append(
                StaticCheckResult(
                    check_id="static.openapi_error_responses",
                    title="OpenAPI error responses defined",
                    status="fail",
                    severity="warning",
                    evidence=evidence,
                    reason=f"{missing_errors} operation(s) have no 4xx, 5xx, or default response",
                )
            )
        else:
            results.append(
                StaticCheckResult(
                    check_id="static.openapi_error_responses",
                    title="OpenAPI error responses defined",
                    status="pass",
                    evidence=evidence,
                )
            )
        if has_429 and missing_retry_after:
            results.append(
                StaticCheckResult(
                    check_id="static.openapi_rate_limit_shape",
                    title="OpenAPI 429 response includes Retry-After",
                    status="fail",
                    severity="warning",
                    evidence=evidence,
                    reason="At least one 429 response has no Retry-After header",
                )
            )
        elif has_429:
            results.append(
                StaticCheckResult(
                    check_id="static.openapi_rate_limit_shape",
                    title="OpenAPI 429 response includes Retry-After",
                    status="pass",
                    evidence=evidence,
                )
            )
        else:
            results.append(
                StaticCheckResult(
                    check_id="static.openapi_rate_limit_shape",
                    title="OpenAPI 429 response includes Retry-After",
                    status="not_detected",
                    severity="suggestion",
                    evidence=evidence,
                    reason="No 429 response declared",
                )
            )
    return results


def run_static_checks(project_dir: str | Path) -> list[StaticCheckResult]:
    """Run deterministic static checks without network, browser, or LLM access."""

    root = Path(project_dir).resolve()
    results: list[StaticCheckResult] = []

    secret_hits = _find_secret_hits(root)
    if secret_hits:
        results.append(
            StaticCheckResult(
                check_id="static.secret_scan",
                title="No likely hard-coded secrets",
                status="fail",
                severity="critical",
                evidence=secret_hits,
                reason="Likely hard-coded secret values found in source or config",
            )
        )
    else:
        results.append(
            StaticCheckResult(
                check_id="static.secret_scan",
                title="No likely hard-coded secrets",
                status="pass",
            )
        )

    manifests = _find_named_files(root, _MANIFEST_NAMES)
    if manifests:
        results.append(
            StaticCheckResult(
                check_id="static.supply_chain_manifest",
                title="Dependency manifest present",
                status="pass",
                evidence=_relative(root, manifests),
            )
        )
    else:
        results.append(
            StaticCheckResult(
                check_id="static.supply_chain_manifest",
                title="Dependency manifest present",
                status="not_detected",
                severity="suggestion",
                reason="artifact_unavailable:dependency_manifest:none_found",
            )
        )

    sboms = _find_named_files(root, _SBOM_NAMES)
    if sboms:
        results.append(
            StaticCheckResult(
                check_id="static.sbom",
                title="SBOM present",
                status="pass",
                evidence=_relative(root, sboms),
            )
        )
    else:
        results.append(
            StaticCheckResult(
                check_id="static.sbom",
                title="SBOM present",
                status="not_detected",
                severity="suggestion",
                reason="artifact_unavailable:sbom:none_found",
            )
        )

    results.extend(_openapi_results(root))
    return results
