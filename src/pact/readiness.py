"""Readiness profile and AI-authored build spec support.

The profile is intentionally concrete enough to drive downstream prompts:
each dimension has a level plus resolved baseline controls. The interview
confirms or overrides the levels before decomposition consumes them. Build
specs are tracked planning artifacts, so they carry task/config intent rather
than runtime secrets or credentials.

Typical flow: `pact init --spec` or `pact spec apply` loads the build request,
the interview confirms the default levels, and decomposition/contract
authoring consume the resolved profile.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class ReadinessLevel(StrEnum):
    """Degree of rigor required for a readiness dimension."""

    NONE = "none"
    BASIC = "basic"
    STANDARD = "standard"
    STRICT = "strict"
    REGULATED = "regulated"


class BuildSpecError(ValueError):
    """A build spec could not be read or validated."""


class ReadinessDimension(BaseModel):
    """One readiness dimension with explicit baseline and custom controls."""

    model_config = ConfigDict(extra="forbid")

    level: ReadinessLevel = ReadinessLevel.STANDARD
    controls: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_shorthand(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"level": value}
        return value


_LEVEL_ORDER = [
    ReadinessLevel.NONE,
    ReadinessLevel.BASIC,
    ReadinessLevel.STANDARD,
    ReadinessLevel.STRICT,
    ReadinessLevel.REGULATED,
]
_LEVEL_VALUES = ", ".join(level.value for level in _LEVEL_ORDER)


_BASELINE_CONTROLS: dict[str, dict[ReadinessLevel, list[str]]] = {
    "operational_maturity": {
        ReadinessLevel.BASIC: ["Document startup, shutdown, and expected failure behavior."],
        ReadinessLevel.STANDARD: ["Maintain an operator runbook and rollback path."],
        ReadinessLevel.STRICT: ["Define SLOs, capacity assumptions, and escalation behavior."],
        ReadinessLevel.REGULATED: ["Retain change approval and operational evidence."],
    },
    "security": {
        ReadinessLevel.BASIC: ["Validate inputs and keep secrets out of source."],
        ReadinessLevel.STANDARD: ["Use least privilege and negative authorization tests."],
        ReadinessLevel.STRICT: ["Require threat modeling, dependency scanning, and security review."],
        ReadinessLevel.REGULATED: ["Retain audit evidence and enforce separation of duties."],
    },
    "privacy": {
        ReadinessLevel.BASIC: ["Identify personal data and redact sensitive logs."],
        ReadinessLevel.STANDARD: ["Apply data minimization and retention/deletion rules."],
        ReadinessLevel.STRICT: ["Require privacy review and access logging."],
        ReadinessLevel.REGULATED: ["Document lawful basis, DPIA, and consent evidence where applicable."],
    },
    "compliance": {
        ReadinessLevel.BASIC: ["Document applicable obligations and exclusions."],
        ReadinessLevel.STANDARD: ["Map controls to evidence and release checks."],
        ReadinessLevel.STRICT: ["Require compliance review and approval evidence."],
        ReadinessLevel.REGULATED: ["Maintain formal audit pack and retention trail."],
    },
    "gating": {
        ReadinessLevel.BASIC: ["Require contract validation before implementation."],
        ReadinessLevel.STANDARD: ["Require tests and review before release."],
        ReadinessLevel.STRICT: ["Require production gate and live validation before release."],
        ReadinessLevel.REGULATED: ["Require separated approval and signed release evidence."],
    },
    "testing": {
        ReadinessLevel.BASIC: ["Require unit tests for core behavior."],
        ReadinessLevel.STANDARD: ["Require integration and negative-path tests."],
        ReadinessLevel.STRICT: ["Require load, failure-mode, and regression tests."],
        ReadinessLevel.REGULATED: ["Retain traceable test evidence and approvals."],
    },
    "monitoring": {
        ReadinessLevel.BASIC: ["Emit structured logs and error signals."],
        ReadinessLevel.STANDARD: ["Expose health checks and metrics."],
        ReadinessLevel.STRICT: ["Define alerts, dashboards, and SLO monitoring."],
        ReadinessLevel.REGULATED: ["Retain monitoring and audit evidence."],
    },
}


class ReadinessProfile(BaseModel):
    """Concrete readiness requirements for a Pact build."""

    model_config = ConfigDict(extra="forbid")

    operational_maturity: ReadinessDimension = Field(
        default_factory=lambda: ReadinessDimension(level=ReadinessLevel.STANDARD)
    )
    security: ReadinessDimension = Field(
        default_factory=lambda: ReadinessDimension(level=ReadinessLevel.STANDARD)
    )
    privacy: ReadinessDimension = Field(
        default_factory=lambda: ReadinessDimension(level=ReadinessLevel.BASIC)
    )
    compliance: ReadinessDimension = Field(
        default_factory=lambda: ReadinessDimension(level=ReadinessLevel.NONE)
    )
    gating: ReadinessDimension = Field(
        default_factory=lambda: ReadinessDimension(level=ReadinessLevel.STANDARD)
    )
    testing: ReadinessDimension = Field(
        default_factory=lambda: ReadinessDimension(level=ReadinessLevel.STANDARD)
    )
    monitoring: ReadinessDimension = Field(
        default_factory=lambda: ReadinessDimension(level=ReadinessLevel.BASIC)
    )
    notes: str = ""

    def resolved_controls(self) -> dict[str, list[str]]:
        """Return cumulative baseline plus custom controls by dimension."""

        controls: dict[str, list[str]] = {}
        for dimension_name in _BASELINE_CONTROLS:
            dimension = getattr(self, dimension_name)
            resolved: list[str] = []
            for level in _LEVEL_ORDER:
                if level == ReadinessLevel.NONE:
                    continue
                if _LEVEL_ORDER.index(level) > _LEVEL_ORDER.index(dimension.level):
                    break
                resolved.extend(_BASELINE_CONTROLS[dimension_name].get(level, []))
            resolved.extend(dimension.controls)
            controls[dimension_name] = resolved
        return controls

    def render_for_prompt(self) -> str:
        """Render levels and concrete controls for downstream agent prompts."""

        lines = ["Readiness profile:"]
        controls = self.resolved_controls()
        for dimension_name in _BASELINE_CONTROLS:
            dimension = getattr(self, dimension_name)
            label = dimension_name.replace("_", " ")
            lines.append(f"- {label}: {dimension.level}")
            for control in controls[dimension_name]:
                lines.append(f"  - {control}")
        if self.notes:
            lines.append(f"- notes: {self.notes}")
        return "\n".join(lines)


_READINESS_QUESTION_LABELS = {
    "operational_maturity": "operational maturity",
    "security": "security",
    "privacy": "privacy",
    "compliance": "compliance",
    "gating": "gating",
    "testing": "testing",
    "monitoring": "monitoring",
}


def readiness_questions(profile: ReadinessProfile) -> list[str]:
    """Return canonical up-front interview questions for readiness dimensions."""

    questions = []
    for field_name, label in _READINESS_QUESTION_LABELS.items():
        dimension = getattr(profile, field_name)
        questions.append(
            f"Readiness: What level of {label} is required? "
            f"[default: {dimension.level}] [options: {_LEVEL_VALUES}]"
        )
    return questions


def question_dimension(question: str) -> str | None:
    """Return the readiness dimension named by a canonical question."""

    normalized = question.lower()
    if not normalized.startswith("readiness:"):
        return None
    for field_name, label in _READINESS_QUESTION_LABELS.items():
        if label in normalized:
            return field_name
    return None


def default_answer_for_question(question: str) -> str | None:
    """Extract a bracketed default answer from a question."""

    match = re.search(r"\[default:\s*([^\]]+)\]", question, re.IGNORECASE)
    return match.group(1).strip() if match else None


def parse_readiness_level(answer: str) -> ReadinessLevel:
    """Parse a user-supplied readiness level with a useful error."""

    normalized = answer.strip().lower()
    try:
        return ReadinessLevel(normalized)
    except ValueError as exc:
        raise ValueError(
            f"Invalid readiness level {answer!r}; expected one of: {_LEVEL_VALUES}."
        ) from exc


def resolve_readiness_profile(
    profile: ReadinessProfile,
    answers: dict[str, str],
) -> ReadinessProfile:
    """Apply canonical interview answers to a readiness profile."""

    raw = profile.model_dump(mode="python")
    for question, answer in answers.items():
        dimension_name = question_dimension(question)
        if not dimension_name:
            continue
        raw[dimension_name]["level"] = parse_readiness_level(answer)
    return ReadinessProfile.model_validate(raw)


class BuildSpec(BaseModel):
    """AI-authored task/spec input accepted by Pact."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1"
    task: str = ""
    sops: str = ""
    readiness: ReadinessProfile = Field(default_factory=ReadinessProfile)
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        if value != "1":
            raise ValueError(f"Unsupported build spec version {value!r}; expected '1'.")
        return value


def default_build_spec() -> BuildSpec:
    """Return a blank AI-editable build spec with default readiness."""

    return BuildSpec()


def load_build_spec(path: str | Path) -> BuildSpec:
    """Load an AI-authored build spec from JSON or YAML."""

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BuildSpecError(f"Unable to read build spec {path}: {exc}") from exc

    suffix = path.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise BuildSpecError(
            f"Unsupported build spec format {suffix or '<none>'}; expected .json, .yaml, or .yml."
        )

    try:
        if suffix == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text) or {}
    except json.JSONDecodeError as exc:
        raise BuildSpecError(f"Unable to parse JSON build spec {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise BuildSpecError(f"Unable to parse YAML build spec {path}: {exc}") from exc

    try:
        return BuildSpec.model_validate(data)
    except ValidationError as exc:
        raise BuildSpecError(f"Invalid build spec {path}: {exc}") from exc


def dump_build_spec(spec: BuildSpec) -> str:
    """Render a build spec as YAML."""

    return yaml.safe_dump(spec.model_dump(mode="json"), sort_keys=False)


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text through a same-directory temp file, then atomically replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def apply_build_spec(
    project_dir: str | Path,
    spec: BuildSpec,
    *,
    source_path: str | Path | None = None,
) -> None:
    """Apply a build spec to task, SOPs, config, and tracked spec file."""

    project_dir = Path(project_dir)
    config_path = project_dir / "pact.yaml"
    destination = project_dir / "build_spec.yaml"
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw_config, dict):
            raise BuildSpecError(f"Unable to prepare build spec application for {project_dir}: pact.yaml must be a mapping.")
        raw_config.update(spec.config)
        raw_config["readiness"] = spec.readiness.model_dump(mode="json")
        destination_content = (
            Path(source_path).read_text(encoding="utf-8")
            if source_path
            else dump_build_spec(spec)
        )
    except (OSError, yaml.YAMLError) as exc:
        raise BuildSpecError(f"Unable to prepare build spec application for {project_dir}: {exc}") from exc

    updates: dict[Path, str] = {
        config_path: yaml.safe_dump(raw_config, sort_keys=False),
        destination: destination_content,
    }
    if spec.task:
        updates[project_dir / "task.md"] = spec.task
    if spec.sops:
        updates[project_dir / "sops.md"] = spec.sops

    previous_contents: dict[Path, str | None] = {}
    written: list[Path] = []
    try:
        for path in updates:
            previous_contents[path] = path.read_text(encoding="utf-8") if path.exists() else None
        for path, content in updates.items():
            _atomic_write_text(path, content)
            written.append(path)
    except OSError as exc:
        rollback_errors: list[str] = []
        for path in reversed(written):
            previous_content = previous_contents[path]
            try:
                if previous_content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write_text(path, previous_content)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path.name}: {rollback_exc}")
        rollback_detail = f" Rollback errors: {'; '.join(rollback_errors)}." if rollback_errors else ""
        raise BuildSpecError(
            f"Unable to apply build spec to {project_dir}: {exc}.{rollback_detail}"
        ) from exc
