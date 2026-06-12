"""File-backed scaffolding for Pact's optional production-readiness pack."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from pact.project import ProjectManager
from pact.readiness import ReadinessProfile


PRODUCTION_DIR = "production"
REPORTS_DIR = "reports"

REQUIRED_FILES = {
    "manifest": "manifest.yaml",
    "prompt": "prompt.md",
    "constraints": "constraints.yaml",
    "component_map": "component_map.yaml",
    "trust_policy": "trust_policy.yaml",
    "control_matrix": "control_matrix.yaml",
    "threat_model": "threat_model.yaml",
    "architecture_laws": "architecture_laws.yaml",
    "preflight": "preflight.yaml",
    "live_validation": "live_validation.yaml",
    "done_gate": "done_gate.yaml",
    "na_register": "na_register.yaml",
    "build_charter": "build_charter.md",
}

DERIVED_SOURCES = {
    "project_config",
    "constrain_bundle",
    "contracts",
    "contract_tests",
    "goodhart_tests",
    "analysis",
    "checklist",
    "review",
    "certification",
    "stub_scan",
    "static_checks",
}

SOURCE_FINGERPRINT_PATHS = (
    "pact.yaml",
    "task.md",
    "sops.md",
    "design.md",
    "decomposition",
    "contracts",
    "tests",
    "src",
)


def production_dir(project_dir: str | Path, artifact_dir: str = PRODUCTION_DIR) -> Path:
    """Return the production artifact directory for a project."""

    project_root = Path(project_dir).resolve()
    configured_path = Path(artifact_dir)
    if configured_path.is_absolute():
        raise ValueError("production_artifact_dir must be relative to the project directory")
    root = (project_root / configured_path).resolve()
    if root == project_root or project_root not in root.parents:
        raise ValueError("production_artifact_dir must resolve inside the project directory")
    return root


def _hash_path(hasher: Any, root: Path, path: Path) -> None:
    relative_path = path.relative_to(root).as_posix()
    hasher.update(f"FILE\0{relative_path}\0".encode())
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)


def compute_source_fingerprint(project_dir: str | Path) -> str:
    """Compute a stable hash for the source and Pact artifacts an attestation covers."""

    root = Path(project_dir).resolve()
    hasher = hashlib.sha256()
    for relative_path in SOURCE_FINGERPRINT_PATHS:
        path = root / relative_path
        if not path.exists():
            hasher.update(f"MISSING\0{relative_path}\0".encode())
            continue
        if path.is_symlink():
            hasher.update(f"SYMLINK\0{relative_path}\0".encode())
            continue
        if path.is_file() and not path.is_symlink():
            _hash_path(hasher, root, path)
            continue
        if path.is_dir() and not path.is_symlink():
            for child in sorted(path.rglob("*")):
                if child.is_symlink():
                    hasher.update(f"SYMLINK\0{child.relative_to(root).as_posix()}\0".encode())
                elif child.is_file():
                    _hash_path(hasher, root, child)
    return hasher.hexdigest()


def _yaml_text(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def _default_manifest(readiness: ReadinessProfile | None = None) -> dict[str, Any]:
    if readiness is None:
        readiness = ReadinessProfile()
    return {
        "version": "1",
        "profile": "production-readiness",
        "artifact_dir": PRODUCTION_DIR,
        "source_fingerprint": "",
        "validated_at": "",
        "max_age_hours": 168,
        "readiness": readiness.model_dump(mode="json"),
        "required_files": list(REQUIRED_FILES.values()),
        "derived_sources": sorted(DERIVED_SOURCES),
    }


def _default_gate() -> dict[str, Any]:
    items = [
        {
            "id": "planned_components",
            "category": "code_completeness",
            "description": "Plan-only contracts and implementation scope exist.",
            "evidence_kind": "derived",
            "evidence_source": "contracts",
        },
        {
            "id": "contract_tests",
            "category": "testing",
            "description": "Visible contract tests exist for planned components.",
            "evidence_kind": "derived",
            "evidence_source": "contract_tests",
        },
        {
            "id": "goodhart_tests",
            "category": "testing",
            "description": "Goodhart or anti-gaming tests exist for planned components.",
            "evidence_kind": "derived",
            "evidence_source": "goodhart_tests",
        },
        {
            "id": "analysis",
            "category": "planning",
            "description": "Cross-artifact analysis report exists.",
            "evidence_kind": "derived",
            "evidence_source": "analysis",
        },
        {
            "id": "checklist",
            "category": "planning",
            "description": "Requirements checklist exists.",
            "evidence_kind": "derived",
            "evidence_source": "checklist",
        },
        {
            "id": "review",
            "category": "review",
            "description": "Advocate and Simulacrum review report exists after fixes.",
            "evidence_kind": "derived",
            "evidence_source": "review",
        },
        {
            "id": "certification",
            "category": "testing",
            "description": "Certification artifact exists after all tests pass.",
            "evidence_kind": "derived",
            "evidence_source": "certification",
        },
        {
            "id": "no_stubs",
            "category": "code_completeness",
            "description": "Shipped code contains no stubs, placeholders, or TODO markers.",
            "evidence_kind": "derived",
            "evidence_source": "stub_scan",
        },
        {
            "id": "static_checks",
            "category": "security",
            "description": "Static production checks have no blocking failures.",
            "evidence_kind": "derived",
            "evidence_source": "static_checks",
        },
        {
            "id": "fail_closed",
            "category": "security",
            "description": "Uncertain authorization, eligibility, and integrity paths fail closed.",
            "evidence_kind": "external",
        },
        {
            "id": "single_source_truth",
            "category": "consistency",
            "description": "Authoritative state and audit intent commit atomically.",
            "evidence_kind": "external",
        },
        {
            "id": "least_privilege",
            "category": "security",
            "description": "Privileged operations are boundary-scoped and least-privilege.",
            "evidence_kind": "external",
        },
        {
            "id": "auditability",
            "category": "compliance",
            "description": "Significant state changes and regulated-data access have durable audit evidence.",
            "evidence_kind": "external",
        },
        {
            "id": "no_silent_failure",
            "category": "error_handling",
            "description": "External calls and half-configured controls fail loudly rather than silently.",
            "evidence_kind": "external",
        },
        {
            "id": "docs_honesty",
            "category": "documentation",
            "description": "Docs identify partial capabilities and cite enforcing artifacts.",
            "evidence_kind": "external",
        },
        {
            "id": "live_verified",
            "category": "live_validation",
            "description": "Live end-to-end validation passed against a running instance.",
            "evidence_kind": "external",
        },
        {
            "id": "security_scan",
            "category": "security",
            "description": "Dependency scan and SBOM evidence exist.",
            "evidence_kind": "external",
        },
        {
            "id": "runbook",
            "category": "documentation",
            "description": "Operator runbook exists for defined alerts and live failures.",
            "evidence_kind": "external",
        },
    ]
    return {"version": "1", "items": items}


def _default_templates(readiness: ReadinessProfile | None = None) -> dict[str, str]:
    return {
        REQUIRED_FILES["manifest"]: _yaml_text(_default_manifest(readiness)),
        REQUIRED_FILES["prompt"]: (
            "# Production Readiness Brief\n\n"
            "Describe the build in precise technical language. Include actors, "
            "data classes, operations, boundaries, external systems, and what "
            "must not be built.\n"
        ),
        REQUIRED_FILES["constraints"]: _yaml_text(
            {
                "constraints": [
                    "Fail closed when authorization, eligibility, or integrity is uncertain.",
                    "Keep one authoritative source of truth for state and audit intent.",
                    "Use least privilege and explicit boundary scoping.",
                    "Do not swallow external-call failures.",
                    "Require live validation before done.",
                ],
            }
        ),
        REQUIRED_FILES["component_map"]: _yaml_text(
            {
                "components": [
                    {"id": "production_artifacts", "name": "Production artifact pack"},
                    {"id": "production_gate", "name": "Production gate validation"},
                    {"id": "production_cli", "name": "Production CLI integration"},
                ],
            }
        ),
        REQUIRED_FILES["trust_policy"]: _yaml_text(
            {
                "trust_assertions": [
                    {
                        "id": "authorization_boundary",
                        "actor": "service",
                        "capability": "perform protected operation",
                        "condition": "server-side authorization passes for the scoped boundary",
                        "status": "pending",
                        "evidence_kind": "external",
                        "evidence": [],
                    }
                ]
            }
        ),
        REQUIRED_FILES["control_matrix"]: _yaml_text(
            {
                "controls": [
                    {
                        "id": "fail_closed",
                        "control": "Uncertain decisions deny or halt.",
                        "enforcement": "Boundary validators and negative-path tests.",
                        "status": "pending",
                        "evidence_kind": "external",
                        "evidence": [],
                    }
                ]
            }
        ),
        REQUIRED_FILES["threat_model"]: _yaml_text(
            {
                "assets": ["<asset>"],
                "actors": ["<actor>"],
                "boundaries": ["<boundary>"],
                "threats": [
                    {
                        "id": "TM-001",
                        "threat": "<threat>",
                        "boundary": "<boundary>",
                        "mitigation": "<mitigation>",
                        "status": "open",
                        "evidence": [],
                    }
                ],
            }
        ),
        REQUIRED_FILES["architecture_laws"]: _yaml_text(
            {
                "laws": [
                    {
                        "id": "LAW-001",
                        "statement": "No privileged operation crosses an unscoped boundary.",
                        "rationale": "Least privilege is a hard production invariant.",
                        "evidence": [],
                    }
                ]
            }
        ),
        REQUIRED_FILES["preflight"]: _yaml_text(
            {
                "red_lines": [
                    {
                        "id": "no_stubs",
                        "rule": "Do not ship placeholders, stubs, or TODO-only implementations.",
                        "trigger": "Any generated code contains a placeholder marker.",
                        "action": "Stop and repair before review.",
                    }
                ],
                "contingencies": [
                    {
                        "id": "scope_fallback",
                        "rule": "Preserve hard controls if scope shrinks.",
                        "trigger": "A nonessential feature threatens the delivery window.",
                        "action": "Cut the feature, not the control.",
                    }
                ],
            }
        ),
        REQUIRED_FILES["live_validation"]: _yaml_text(
            {
                "status": "pending",
                "environment": "",
                "command": "",
                "evidence": [],
                "notes": "",
            }
        ),
        REQUIRED_FILES["done_gate"]: _yaml_text(_default_gate()),
        REQUIRED_FILES["na_register"]: _yaml_text({"items": []}),
        REQUIRED_FILES["build_charter"]: (
            "# Build Charter\n\n"
            "## Raw ask\n\n"
            "<paste the ask exactly>\n\n"
            "## Failure asymmetry\n\n"
            "<describe worst-case harm if this fails open, silently, or dishonestly>\n\n"
            "## Seven non-negotiables\n\n"
            "- Fail closed\n- Single source of truth\n- Least privilege\n"
            "- Full auditability\n- No silent failure\n- Docs honesty\n- Live verified\n"
        ),
    }


def _write_if_missing(path: Path, content: str, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if overwrite or not path.exists():
        path.write_text(content)


def initialize_production_pack(project_dir: str | Path, *, overwrite: bool = False) -> Path:
    """Scaffold a production artifact pack for an existing or empty project."""

    project = ProjectManager(project_dir)
    if not project.config_path.exists():
        if project.project_dir.exists() and any(project.project_dir.iterdir()):
            raise ValueError(
                f"{project.project_dir} is not an initialized Pact project; "
                "run 'pact init' first or use an empty directory"
            )
        project.init()

    root = production_dir(project.project_dir, project.load_config().production_artifact_dir)
    readiness = project.load_config().readiness
    for relative_path, content in _default_templates(readiness).items():
        _write_if_missing(root / relative_path, content, overwrite=overwrite)
    (root / REPORTS_DIR).mkdir(parents=True, exist_ok=True)

    raw = yaml.safe_load(project.config_path.read_text()) or {}
    artifact_dir = raw.setdefault("production_artifact_dir", PRODUCTION_DIR)
    raw.setdefault("production_profile", True)
    raw.setdefault("plan_only", True)
    raw.setdefault("constrain_dir", artifact_dir)
    project.config_path.write_text(_yaml_text(raw))
    return root
