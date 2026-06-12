"""Validation for Pact's file-backed production-readiness artifact pack."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from pact.production_static import run_static_checks
from pact.project import ProjectManager
from pact.schemas_production import (
    ArchitectureLaws,
    ControlMatrix,
    LiveValidationReport,
    NaRegister,
    ProductionFinding,
    ProductionGate,
    ProductionManifest,
    ProductionPreflight,
    ProductionValidationReport,
    ThreatModel,
    TrustPolicy,
)
from pact.production_templates import (
    DERIVED_SOURCES,
    REQUIRED_FILES,
    compute_source_fingerprint,
    production_dir,
)


PLACEHOLDER_RE = re.compile(
    r"(^\s*$|^\s*(?:todo|tbd|fixme|n/?a|placeholder)\s*$|<[^>]+>)",
    re.IGNORECASE,
)
MAX_ARTIFACT_BYTES = 512 * 1024


class ArtifactReadError(ValueError):
    """Raised when a production artifact cannot be read safely."""


def _read_artifact_text(path: Path, root: Path) -> str:
    if path.is_symlink():
        raise ArtifactReadError(f"{path.name} must not be a symbolic link")
    try:
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        raise ArtifactReadError(f"Could not read {path.name}: {exc}") from exc
    if resolved_path != root and root not in resolved_path.parents:
        raise ArtifactReadError(f"{path.name} resolves outside the production artifact directory")
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_ARTIFACT_BYTES + 1)
    except OSError as exc:
        raise ArtifactReadError(f"Could not read {path.name}: {exc}") from exc
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ArtifactReadError(
            f"{path.name} exceeds the {MAX_ARTIFACT_BYTES // 1024} KiB artifact limit"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactReadError(f"Could not read UTF-8 text from {path.name}: {exc}") from exc


def _load_yaml(path: Path, schema: type[BaseModel], root: Path) -> BaseModel:
    data = yaml.safe_load(_read_artifact_text(path, root)) or {}
    return schema.model_validate(data)


def _is_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER_RE.search(value))


def _has_placeholder(values: list[str]) -> bool:
    return any(_is_placeholder(value) for value in values)


def _add_finding(
    report: ProductionValidationReport,
    severity: str,
    message: str,
    *,
    artifact_id: str = "",
    item_id: str = "",
) -> None:
    report.findings.append(
        ProductionFinding(
            severity=severity,  # type: ignore[arg-type]
            artifact_id=artifact_id,
            item_id=item_id,
            message=message,
        )
    )


def _has_placeholder_text(path: Path, root: Path) -> bool:
    return _is_placeholder(_read_artifact_text(path, root))


def _validate_structured_text(
    report: ProductionValidationReport,
    *,
    artifact_id: str,
    values: list[str],
    message: str,
) -> None:
    if not values:
        _add_finding(report, "error", message, artifact_id=artifact_id)
    elif _has_placeholder(values):
        _add_finding(report, "error", f"{message} Contains a placeholder.", artifact_id=artifact_id)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp


def _latest_review_report(project: ProjectManager) -> list[str]:
    reviews_dir = project.project_dir / ".pact" / "reviews"
    if not reviews_dir.exists():
        return []
    candidates = sorted(reviews_dir.glob("*/review.json"))
    return [str(candidates[-1])] if candidates else []


def _derived_evidence(project: ProjectManager, root: Path, report: ProductionValidationReport) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {
        "project_config": [str(project.config_path)] if project.config_path.exists() else [],
        "constrain_bundle": [],
        "contracts": [],
        "contract_tests": [],
        "goodhart_tests": [],
        "analysis": [str(project.analysis_path)] if project.analysis_path.exists() else [],
        "checklist": [str(project.checklist_path)] if project.checklist_path.exists() else [],
        "review": _latest_review_report(project),
        "certification": [str(project.audit_root / "certification" / "certification.json")]
        if (project.audit_root / "certification" / "certification.json").exists()
        else [],
        "stub_scan": [],
        "static_checks": [],
    }

    constrain_files = [
        root / REQUIRED_FILES["prompt"],
        root / REQUIRED_FILES["constraints"],
        root / REQUIRED_FILES["component_map"],
        root / REQUIRED_FILES["trust_policy"],
    ]
    if all(path.exists() for path in constrain_files):
        evidence["constrain_bundle"] = [str(path) for path in constrain_files]

    tree = project.load_tree()
    if tree:
        for component_id in tree.nodes:
            contract = project.audit_root / "contracts" / component_id / "interface.json"
            if contract.exists():
                evidence["contracts"].append(str(contract))
            test = project.test_code_path(component_id)
            if test.exists():
                evidence["contract_tests"].append(str(test))
            goodhart = project.goodhart_test_code_path(component_id)
            if goodhart.exists():
                evidence["goodhart_tests"].append(str(goodhart))

    src_dir = project.project_dir / "src"
    if src_dir.exists():
        from pact.implementer import detect_stubs

        stubs = detect_stubs(src_dir, project.language)
        if stubs:
            for stub in stubs:
                _add_finding(report, "error", f"Stub marker found: {stub}", artifact_id="stub_scan")
        elif any(src_dir.rglob("*.py")):
            evidence["stub_scan"] = [f"{src_dir}: no stub markers found"]

    static_checks = run_static_checks(project.project_dir)
    report.static_checks = static_checks
    static_failures = [check for check in static_checks if check.status == "fail"]
    if static_failures:
        for check in static_failures:
            _add_finding(
                report,
                "error",
                f"{check.title}: {check.reason or 'static check failed'}",
                artifact_id="static_checks",
                item_id=check.check_id,
            )
    else:
        evidence["static_checks"] = [check.check_id for check in static_checks]
    return evidence


def _validate_status(
    report: ProductionValidationReport,
    *,
    artifact_id: str,
    item_id: str,
    status: str,
    evidence_kind: str,
    evidence: list[str],
    justification: str,
    review_ref: str,
    derived_source: str = "",
) -> None:
    if evidence_kind == "derived":
        if status == "not_applicable":
            _add_finding(
                report,
                "error",
                "Derived evidence cannot be marked not applicable.",
                artifact_id=artifact_id,
                item_id=item_id,
            )
        if derived_source not in DERIVED_SOURCES:
            _add_finding(
                report,
                "error",
                f"Unknown derived evidence source '{derived_source}'.",
                artifact_id=artifact_id,
                item_id=item_id,
            )
            return
        if not report.derived_evidence.get(derived_source, []):
            _add_finding(
                report,
                "error",
                f"Derived evidence source '{derived_source}' produced no evidence.",
                artifact_id=artifact_id,
                item_id=item_id,
            )
        return

    if evidence_kind == "external":
        if status != "satisfied":
            _add_finding(
                report,
                "error",
                "External item is not satisfied.",
                artifact_id=artifact_id,
                item_id=item_id,
            )
        if not evidence:
            _add_finding(
                report,
                "error",
                "External evidence is required.",
                artifact_id=artifact_id,
                item_id=item_id,
            )
        elif _has_placeholder(evidence):
            _add_finding(
                report,
                "error",
                "External evidence contains a placeholder.",
                artifact_id=artifact_id,
                item_id=item_id,
            )
        return

    if evidence_kind == "not_applicable":
        if status != "not_applicable":
            _add_finding(
                report,
                "error",
                "Not-applicable evidence must use not_applicable status.",
                artifact_id=artifact_id,
                item_id=item_id,
            )
        if _is_placeholder(justification):
            _add_finding(
                report,
                "error",
                "Not-applicable item needs a non-placeholder justification.",
                artifact_id=artifact_id,
                item_id=item_id,
            )
        if _is_placeholder(review_ref):
            _add_finding(
                report,
                "error",
                "Not-applicable item needs a review reference.",
                artifact_id=artifact_id,
                item_id=item_id,
            )


def _validate_gate(report: ProductionValidationReport, gate: ProductionGate, na_register: NaRegister) -> None:
    na_ids = {item.item_id for item in na_register.items}
    for item in gate.items:
        _validate_status(
            report,
            artifact_id="done_gate",
            item_id=item.id,
            status=item.status,
            evidence_kind=item.evidence_kind,
            evidence=item.evidence,
            justification=item.justification,
            review_ref=item.review_ref,
            derived_source=item.evidence_source,
        )
        if item.status == "not_applicable" and item.id not in na_ids:
            _add_finding(
                report,
                "error",
                "Not-applicable gate item is missing a matching N/A register entry.",
                artifact_id="na_register",
                item_id=item.id,
            )
    for record in na_register.items:
        if _is_placeholder(record.justification) or _is_placeholder(record.review_ref):
            _add_finding(
                report,
                "error",
                "N/A register entry needs justification and review reference.",
                artifact_id="na_register",
                item_id=record.item_id,
            )


def _validate_na_reference(
    report: ProductionValidationReport,
    *,
    artifact_id: str,
    item_id: str,
    status: str,
    na_ids: set[str],
) -> None:
    if status == "not_applicable" and item_id not in na_ids:
        _add_finding(
            report,
            "error",
            "Not-applicable item is missing a matching N/A register entry.",
            artifact_id="na_register",
            item_id=item_id,
        )


def _validate_assertion_like_items(
    report: ProductionValidationReport,
    *,
    artifact_id: str,
    items: list[Any],
    na_ids: set[str],
) -> None:
    for item in items:
        _validate_status(
            report,
            artifact_id=artifact_id,
            item_id=item.id,
            status=item.status,
            evidence_kind=item.evidence_kind,
            evidence=item.evidence,
            justification=item.justification,
            review_ref=item.review_ref,
        )
        _validate_na_reference(
            report,
            artifact_id=artifact_id,
            item_id=item.id,
            status=item.status,
            na_ids=na_ids,
        )


def _validate_parsed_artifacts(report: ProductionValidationReport, parsed: dict[str, BaseModel]) -> None:
    na_register = parsed.get("na_register")
    na_ids = {item.item_id for item in na_register.items} if isinstance(na_register, NaRegister) else set()

    if "trust_policy" in parsed:
        policy = parsed["trust_policy"]
        assert isinstance(policy, TrustPolicy)
        if not policy.trust_assertions:
            _add_finding(report, "error", "Trust policy has no assertions.", artifact_id="trust_policy")
        for assertion in policy.trust_assertions:
            _validate_structured_text(
                report,
                artifact_id="trust_policy",
                values=[assertion.actor, assertion.capability, assertion.condition],
                message="Trust assertion is incomplete.",
            )
        _validate_assertion_like_items(
            report,
            artifact_id="trust_policy",
            items=policy.trust_assertions,
            na_ids=na_ids,
        )

    if "control_matrix" in parsed:
        matrix = parsed["control_matrix"]
        assert isinstance(matrix, ControlMatrix)
        if not matrix.controls:
            _add_finding(report, "error", "Control matrix has no controls.", artifact_id="control_matrix")
        for control in matrix.controls:
            _validate_structured_text(
                report,
                artifact_id="control_matrix",
                values=[control.control, control.enforcement],
                message="Control entry is incomplete.",
            )
        _validate_assertion_like_items(
            report,
            artifact_id="control_matrix",
            items=matrix.controls,
            na_ids=na_ids,
        )

    if "threat_model" in parsed:
        threat_model = parsed["threat_model"]
        assert isinstance(threat_model, ThreatModel)
        _validate_structured_text(
            report,
            artifact_id="threat_model",
            values=[*threat_model.assets, *threat_model.actors, *threat_model.boundaries],
            message="Threat model is incomplete.",
        )
        if not threat_model.threats:
            _add_finding(report, "error", "Threat model has no threats.", artifact_id="threat_model")
        for threat in threat_model.threats:
            _validate_structured_text(
                report,
                artifact_id="threat_model",
                values=[threat.threat, threat.boundary, threat.mitigation],
                message="Threat entry is incomplete.",
            )
            _validate_na_reference(
                report,
                artifact_id="threat_model",
                item_id=threat.id,
                status=threat.status,
                na_ids=na_ids,
            )

    if "architecture_laws" in parsed:
        laws = parsed["architecture_laws"]
        assert isinstance(laws, ArchitectureLaws)
        if not laws.laws:
            _add_finding(report, "error", "Architecture laws are missing.", artifact_id="architecture_laws")
        for law in laws.laws:
            _validate_structured_text(
                report,
                artifact_id="architecture_laws",
                values=[law.statement, law.rationale],
                message="Architecture law is incomplete.",
            )

    if "preflight" in parsed:
        preflight = parsed["preflight"]
        assert isinstance(preflight, ProductionPreflight)
        if not preflight.red_lines:
            _add_finding(report, "error", "Preflight has no red lines.", artifact_id="preflight")
        if not preflight.contingencies:
            _add_finding(report, "error", "Preflight has no contingencies.", artifact_id="preflight")

    if "live_validation" in parsed:
        live = parsed["live_validation"]
        assert isinstance(live, LiveValidationReport)
        if live.status != "passed":
            _add_finding(report, "error", "Live validation has not passed.", artifact_id="live_validation")
        if _is_placeholder(live.environment) or _is_placeholder(live.command):
            _add_finding(
                report,
                "error",
                "Live validation requires environment and command.",
                artifact_id="live_validation",
            )
        if not live.evidence or _has_placeholder(live.evidence):
            _add_finding(
                report,
                "error",
                "Live validation requires non-placeholder evidence.",
                artifact_id="live_validation",
            )

    if "done_gate" in parsed and "na_register" in parsed:
        gate = parsed["done_gate"]
        na_register = parsed["na_register"]
        assert isinstance(gate, ProductionGate)
        assert isinstance(na_register, NaRegister)
        _validate_gate(report, gate, na_register)


def validate_production_pack(project_dir: str | Path) -> ProductionValidationReport:
    """Validate production artifacts and return a deterministic report."""

    project = ProjectManager(project_dir)
    report = ProductionValidationReport()
    try:
        root = production_dir(project.project_dir, project.load_config().production_artifact_dir)
    except ValueError as exc:
        _add_finding(report, "error", str(exc), artifact_id="manifest")
        return report
    report.derived_evidence = _derived_evidence(project, root, report)

    for artifact_id, relative_path in REQUIRED_FILES.items():
        path = root / relative_path
        if path.exists():
            report.checked_files.append(str(path))
        else:
            _add_finding(report, "error", f"Missing required file: {relative_path}", artifact_id=artifact_id)

    if not root.exists():
        report.passed = False
        return report

    for artifact_id in ("prompt", "build_charter"):
        path = root / REQUIRED_FILES[artifact_id]
        if path.exists():
            try:
                has_placeholder = _has_placeholder_text(path, root)
            except ArtifactReadError as exc:
                _add_finding(report, "error", str(exc), artifact_id=artifact_id)
                continue
            if has_placeholder:
                _add_finding(
                    report,
                    "error",
                    "Template still contains placeholder text.",
                    artifact_id=artifact_id,
                )

    try:
        manifest = _load_yaml(root / REQUIRED_FILES["manifest"], ProductionManifest, root)
        assert isinstance(manifest, ProductionManifest)
        report.current_source_fingerprint = compute_source_fingerprint(project.project_dir)
        report.declared_source_fingerprint = manifest.source_fingerprint
        if _is_placeholder(manifest.source_fingerprint):
            _add_finding(
                report,
                "error",
                "Manifest source_fingerprint is required.",
                artifact_id="manifest",
            )
        elif manifest.source_fingerprint != report.current_source_fingerprint:
            _add_finding(
                report,
                "error",
                "Manifest source_fingerprint does not match the current project snapshot.",
                artifact_id="manifest",
            )
        if manifest.readiness != project.load_config().readiness:
            _add_finding(
                report,
                "error",
                "Manifest readiness profile does not match pact.yaml.",
                artifact_id="manifest",
            )
        if _is_placeholder(manifest.validated_at):
            _add_finding(
                report,
                "error",
                "Manifest validated_at is required.",
                artifact_id="manifest",
            )
        else:
            validated_at = _parse_timestamp(manifest.validated_at)
            if validated_at is None:
                _add_finding(
                    report,
                    "error",
                    "Manifest validated_at must be an ISO 8601 timestamp with timezone.",
                    artifact_id="manifest",
                )
            elif manifest.max_age_hours <= 0:
                _add_finding(
                    report,
                    "error",
                    "Manifest max_age_hours must be greater than zero.",
                    artifact_id="manifest",
                )
            else:
                age = datetime.now(timezone.utc) - validated_at.astimezone(timezone.utc)
                if age.total_seconds() < 0:
                    _add_finding(
                        report,
                        "error",
                        "Manifest validated_at must not be in the future.",
                        artifact_id="manifest",
                    )
                elif age.total_seconds() > manifest.max_age_hours * 3600:
                    _add_finding(
                        report,
                        "error",
                        "Manifest validation evidence is older than max_age_hours.",
                        artifact_id="manifest",
                    )
        unknown_sources = set(manifest.derived_sources) - DERIVED_SOURCES
        for source in sorted(unknown_sources):
            _add_finding(report, "warning", f"Unknown derived source: {source}", artifact_id="manifest")
    except (ArtifactReadError, ValidationError, yaml.YAMLError, RecursionError) as exc:
        _add_finding(report, "error", f"Invalid manifest: {exc}", artifact_id="manifest")

    parsed: dict[str, BaseModel] = {}
    schema_map: dict[str, type[BaseModel]] = {
        "trust_policy": TrustPolicy,
        "control_matrix": ControlMatrix,
        "threat_model": ThreatModel,
        "architecture_laws": ArchitectureLaws,
        "preflight": ProductionPreflight,
        "live_validation": LiveValidationReport,
        "done_gate": ProductionGate,
        "na_register": NaRegister,
    }
    for artifact_id, schema in schema_map.items():
        path = root / REQUIRED_FILES[artifact_id]
        if not path.exists():
            continue
        try:
            parsed[artifact_id] = _load_yaml(path, schema, root)
        except (ArtifactReadError, ValidationError, yaml.YAMLError, RecursionError) as exc:
            _add_finding(report, "error", f"Invalid {artifact_id}: {exc}", artifact_id=artifact_id)

    _validate_parsed_artifacts(report, parsed)
    report.passed = report.error_count == 0
    return report


def save_production_report(report: ProductionValidationReport, project_dir: str | Path) -> Path:
    """Persist a machine-readable validation report under production/reports."""

    project = ProjectManager(project_dir)
    try:
        root = production_dir(project.project_dir, project.load_config().production_artifact_dir)
    except ValueError:
        root = project.project_dir / ".pact" / "production-validation"
    path = root / "reports" / "validation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2))
    return path


def render_production_report(report: ProductionValidationReport) -> str:
    """Render a concise human-readable production validation report."""

    lines = [
        f"Production readiness: {'passed' if report.passed else 'blocked'}",
        f"Checked files: {len(report.checked_files)}",
        f"Derived evidence sources: {sum(bool(v) for v in report.derived_evidence.values())}/{len(report.derived_evidence)}",
        f"Static checks: {len(report.static_checks)}",
        f"Findings: {report.error_count} error(s), {report.warning_count} warning(s)",
    ]
    if report.findings:
        lines.append("")
        for finding in report.findings:
            prefix = finding.item_id or finding.artifact_id or "production"
            lines.append(f"[{finding.severity.upper()}] {prefix}: {finding.message}")
    return "\n".join(lines)


def production_status(project_dir: str | Path) -> ProductionValidationReport:
    """Return the current production pack status without changing source files."""

    return validate_production_pack(project_dir)
