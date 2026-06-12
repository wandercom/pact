"""Tests for the optional production-readiness artifact pack."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import yaml
import pytest

from pact.cli import cmd_production
from pact.production import (
    REQUIRED_FILES,
    compute_source_fingerprint,
    initialize_production_pack,
    validate_production_pack,
)
from pact.project import ProjectManager


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _make_ready_pack(project_dir: Path, artifact_dir: str = "production") -> None:
    root = project_dir / artifact_dir
    root.joinpath("prompt.md").write_text("# Production brief\n\nA real build brief.")
    root.joinpath("build_charter.md").write_text("# Build Charter\n\nA real charter.")
    _write_yaml(
        root / "trust_policy.yaml",
        {
            "trust_assertions": [
                {
                    "id": "authz",
                    "actor": "service",
                    "capability": "protected operation",
                    "condition": "server-side authorization passes",
                    "status": "satisfied",
                    "evidence_kind": "external",
                    "evidence": ["tests/test_authz.py::test_denied_boundary"],
                }
            ]
        },
    )
    _write_yaml(
        root / "control_matrix.yaml",
        {
            "controls": [
                {
                    "id": "fail_closed",
                    "control": "Uncertain decisions deny.",
                    "enforcement": "Negative-path tests.",
                    "status": "satisfied",
                    "evidence_kind": "external",
                    "evidence": ["tests/test_authz.py::test_denied_boundary"],
                }
            ]
        },
    )
    _write_yaml(
        root / "threat_model.yaml",
        {
            "assets": ["subject record"],
            "actors": ["service"],
            "boundaries": ["tenant"],
            "threats": [
                {
                    "id": "TM-001",
                    "threat": "Cross-tenant read",
                    "boundary": "tenant",
                    "mitigation": "Query-scoped authorization",
                    "status": "mitigated",
                }
            ],
        },
    )
    _write_yaml(
        root / "architecture_laws.yaml",
        {
            "laws": [
                {
                    "id": "LAW-001",
                    "statement": "Protected operations stay boundary-scoped.",
                    "rationale": "Least privilege.",
                }
            ]
        },
    )
    _write_yaml(
        root / "preflight.yaml",
        {
            "red_lines": [{"id": "no_stubs", "rule": "No stubs."}],
            "contingencies": [{"id": "scope", "rule": "Preserve controls if scope shrinks."}],
        },
    )
    _write_yaml(
        root / "live_validation.yaml",
        {
            "status": "passed",
            "environment": "staging",
            "command": "pytest tests/test_live.py::test_smoke",
            "evidence": ["logs/live-smoke.txt"],
        },
    )
    _write_yaml(
        root / "done_gate.yaml",
        {
            "version": "1",
            "items": [
                {
                    "id": "config",
                    "category": "planning",
                    "description": "Project config exists.",
                    "evidence_kind": "derived",
                    "evidence_source": "project_config",
                },
                {
                    "id": "live",
                    "category": "live_validation",
                    "description": "Live validation passed.",
                    "evidence_kind": "external",
                    "status": "satisfied",
                    "evidence": ["logs/live-smoke.txt"],
                },
            ],
        },
    )
    _write_yaml(root / "na_register.yaml", {"items": []})
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["source_fingerprint"] = compute_source_fingerprint(project_dir)
    manifest["validated_at"] = datetime.now(timezone.utc).isoformat()
    _write_yaml(root / "manifest.yaml", manifest)


def test_initialize_production_pack_creates_templates_and_config(tmp_path: Path) -> None:
    root = initialize_production_pack(tmp_path)

    assert root == tmp_path / "production"
    for relative_path in REQUIRED_FILES.values():
        assert (root / relative_path).exists()

    config = yaml.safe_load((tmp_path / "pact.yaml").read_text())
    assert config["production_profile"] is True
    assert config["production_artifact_dir"] == "production"
    assert config["plan_only"] is True
    assert config["constrain_dir"] == "production"


def test_production_pack_honors_configured_artifact_dir(tmp_path: Path) -> None:
    project = ProjectManager(tmp_path)
    project.init()
    config = yaml.safe_load((tmp_path / "pact.yaml").read_text())
    config["production_artifact_dir"] = "release-evidence"
    (tmp_path / "pact.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    root = initialize_production_pack(tmp_path)

    assert root == tmp_path / "release-evidence"
    assert (root / "manifest.yaml").exists()
    assert not (tmp_path / "production").exists()
    updated_config = yaml.safe_load((tmp_path / "pact.yaml").read_text())
    assert updated_config["constrain_dir"] == "release-evidence"


def test_validation_honors_configured_artifact_dir(tmp_path: Path) -> None:
    project = ProjectManager(tmp_path)
    project.init()
    config = yaml.safe_load((tmp_path / "pact.yaml").read_text())
    config["production_artifact_dir"] = "release-evidence"
    (tmp_path / "pact.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path, "release-evidence")

    report = validate_production_pack(tmp_path)

    assert report.passed is True
    assert all("release-evidence" in path for path in report.checked_files)


def test_stale_source_fingerprint_blocks_validation(tmp_path: Path) -> None:
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "new_module.py").write_text("value = 1\n")

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert any("does not match the current project snapshot" in finding.message for finding in report.findings)


def test_stale_validation_timestamp_blocks_validation(tmp_path: Path) -> None:
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path)
    root = tmp_path / "production"
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["validated_at"] = "2000-01-01T00:00:00+00:00"
    _write_yaml(root / "manifest.yaml", manifest)

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert any("older than max_age_hours" in finding.message for finding in report.findings)


def test_oversized_production_artifact_blocks_validation(tmp_path: Path) -> None:
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path)
    manifest = tmp_path / "production" / "manifest.yaml"
    manifest.write_text("x" * (513 * 1024))

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert any("artifact limit" in finding.message for finding in report.findings)


def test_symlinked_production_artifact_blocks_validation(tmp_path: Path) -> None:
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("version: '1'\n")
    manifest = tmp_path / "production" / "manifest.yaml"
    manifest.unlink()
    manifest.symlink_to(outside)

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert any("must not be a symbolic link" in finding.message for finding in report.findings)


def test_production_artifact_dir_cannot_escape_project(tmp_path: Path) -> None:
    project = ProjectManager(tmp_path)
    project.init()
    config = yaml.safe_load((tmp_path / "pact.yaml").read_text())
    config["production_artifact_dir"] = "../outside"
    (tmp_path / "pact.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert any("must resolve inside the project directory" in finding.message for finding in report.findings)


def test_default_pack_is_blocked_until_templates_are_filled(tmp_path: Path) -> None:
    initialize_production_pack(tmp_path)

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert report.error_count > 0
    assert any("placeholder" in finding.message.lower() for finding in report.findings)


def test_ready_pack_passes_with_derived_and_external_evidence(tmp_path: Path) -> None:
    project = ProjectManager(tmp_path)
    project.init()
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path)

    report = validate_production_pack(tmp_path)

    assert report.passed is True
    assert report.derived_evidence["project_config"]


def test_derived_gate_item_fails_without_derived_evidence(tmp_path: Path) -> None:
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path)
    root = tmp_path / "production"
    _write_yaml(
        root / "done_gate.yaml",
        {
            "version": "1",
            "items": [
                {
                    "id": "missing",
                    "category": "testing",
                    "description": "Missing derived evidence.",
                    "evidence_kind": "derived",
                    "evidence_source": "certification",
                }
            ],
        },
    )

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert any("produced no evidence" in finding.message for finding in report.findings)


def test_not_applicable_gate_item_requires_register_entry(tmp_path: Path) -> None:
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path)
    root = tmp_path / "production"
    _write_yaml(
        root / "done_gate.yaml",
        {
            "version": "1",
            "items": [
                {
                    "id": "not_applicable",
                    "category": "security",
                    "description": "A non-applicable control.",
                    "evidence_kind": "not_applicable",
                    "status": "not_applicable",
                    "justification": "This build has no payment-card data.",
                    "review_ref": "simulacrum.md",
                }
            ],
        },
    )

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert any("missing a matching N/A register entry" in finding.message for finding in report.findings)


def test_not_applicable_trust_item_requires_register_entry(tmp_path: Path) -> None:
    initialize_production_pack(tmp_path)
    _make_ready_pack(tmp_path)
    root = tmp_path / "production"
    _write_yaml(
        root / "trust_policy.yaml",
        {
            "trust_assertions": [
                {
                    "id": "anonymous_access",
                    "actor": "anonymous",
                    "capability": "read protected data",
                    "condition": "This build has no anonymous access path.",
                    "status": "not_applicable",
                    "evidence_kind": "not_applicable",
                    "justification": "There is no anonymous access path.",
                    "review_ref": "simulacrum.md",
                }
            ]
        },
    )

    report = validate_production_pack(tmp_path)

    assert report.passed is False
    assert any(
        finding.item_id == "anonymous_access" and "missing a matching N/A register entry" in finding.message
        for finding in report.findings
    )


def test_production_cli_init_and_validate(tmp_path: Path, capsys) -> None:
    cmd_production(
        argparse.Namespace(
            production_command="init",
            project_dir=str(tmp_path),
            force=False,
        )
    )
    assert "Initialized production artifact pack" in capsys.readouterr().out

    with pytest.raises(SystemExit) as excinfo:
        cmd_production(
            argparse.Namespace(
                production_command="validate",
                project_dir=str(tmp_path),
                json_output=False,
            )
        )
    assert excinfo.value.code == 1
