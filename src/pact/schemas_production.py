"""Schemas for Pact's optional production-readiness artifact pack."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from pact.readiness import ReadinessProfile


EvidenceKind = Literal["derived", "external", "not_applicable"]
GateStatus = Literal["pending", "satisfied", "not_applicable"]
FindingSeverity = Literal["error", "warning", "info"]
StaticCheckStatus = Literal["pass", "fail", "not_detected", "skipped"]
StaticCheckSeverity = Literal["critical", "warning", "suggestion", "info"]


class TrustAssertion(BaseModel):
    """A machine-checkable claim about a runtime capability or boundary."""

    id: str
    actor: str
    capability: str
    condition: str
    status: GateStatus = "pending"
    evidence_kind: EvidenceKind = "derived"
    evidence: list[str] = Field(default_factory=list)
    justification: str = ""
    review_ref: str = ""


class TrustPolicy(BaseModel):
    """Collection of trust assertions for the production artifact pack."""

    trust_assertions: list[TrustAssertion] = Field(default_factory=list)


class ControlEntry(BaseModel):
    """A control mapped to enforcement and evidence."""

    id: str
    control: str
    enforcement: str
    status: GateStatus = "pending"
    evidence_kind: EvidenceKind = "external"
    evidence: list[str] = Field(default_factory=list)
    justification: str = ""
    review_ref: str = ""


class ControlMatrix(BaseModel):
    """Collection of production controls and their evidence state."""

    controls: list[ControlEntry] = Field(default_factory=list)


class ThreatEntry(BaseModel):
    """A threat, its mitigation, and its residual status."""

    id: str
    threat: str
    boundary: str
    mitigation: str
    status: Literal["open", "mitigated", "accepted", "not_applicable"] = "open"
    evidence: list[str] = Field(default_factory=list)
    justification: str = ""


class ThreatModel(BaseModel):
    """Collection of identified threats and mitigations."""

    assets: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    threats: list[ThreatEntry] = Field(default_factory=list)


class ArchitectureLaw(BaseModel):
    """An invariant that the implementation must preserve."""

    id: str
    statement: str
    rationale: str
    evidence: list[str] = Field(default_factory=list)


class ArchitectureLaws(BaseModel):
    """Collection of implementation invariants."""

    laws: list[ArchitectureLaw] = Field(default_factory=list)


class PreflightRule(BaseModel):
    """A pre-committed implementation constraint or fallback."""

    id: str
    rule: str
    trigger: str = ""
    action: str = ""


class ProductionPreflight(BaseModel):
    """Production-specific self-constraints and fallback plan."""

    red_lines: list[PreflightRule] = Field(default_factory=list)
    contingencies: list[PreflightRule] = Field(default_factory=list)


class LiveValidationReport(BaseModel):
    """Result of a live end-to-end validation run."""

    status: Literal["pending", "passed", "failed"] = "pending"
    environment: str = ""
    command: str = ""
    evidence: list[str] = Field(default_factory=list)
    notes: str = ""


class GateItem(BaseModel):
    """One falsifiable production-readiness gate item."""

    id: str
    category: str
    description: str
    evidence_kind: EvidenceKind
    evidence_source: str = ""
    status: GateStatus = "pending"
    evidence: list[str] = Field(default_factory=list)
    justification: str = ""
    review_ref: str = ""


class ProductionGate(BaseModel):
    """The machine-checkable production-readiness gate."""

    version: str = "1"
    items: list[GateItem] = Field(default_factory=list)


class NaRecord(BaseModel):
    """Explicit justification for a non-applicable gate or control."""

    item_id: str
    justification: str
    review_ref: str


class NaRegister(BaseModel):
    """Collection of non-applicable records."""

    items: list[NaRecord] = Field(default_factory=list)


class ProductionManifest(BaseModel):
    """Metadata describing the production artifact pack."""

    version: str = "1"
    profile: str = "production-readiness"
    artifact_dir: str = "production"
    source_fingerprint: str = ""
    validated_at: str = ""
    max_age_hours: int = 168
    readiness: ReadinessProfile = Field(default_factory=ReadinessProfile)
    required_files: list[str] = Field(default_factory=list)
    derived_sources: list[str] = Field(default_factory=list)


class ProductionFinding(BaseModel):
    """A validation finding emitted by the production gate."""

    severity: FindingSeverity
    artifact_id: str = ""
    item_id: str = ""
    message: str


class StaticCheckResult(BaseModel):
    """Result of a deterministic static production check."""

    check_id: str
    title: str
    status: StaticCheckStatus
    severity: StaticCheckSeverity = "info"
    evidence: list[str] = Field(default_factory=list)
    reason: str = ""


class ProductionValidationReport(BaseModel):
    """Result of validating a production-readiness artifact pack."""

    passed: bool = False
    checked_files: list[str] = Field(default_factory=list)
    current_source_fingerprint: str = ""
    declared_source_fingerprint: str = ""
    derived_evidence: dict[str, list[str]] = Field(default_factory=dict)
    static_checks: list[StaticCheckResult] = Field(default_factory=list)
    findings: list[ProductionFinding] = Field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "warning")
