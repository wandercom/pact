"""Tests for readiness profiles and AI-authored build specs."""

from __future__ import annotations

from pathlib import Path
import argparse

import pytest
import yaml

from pact.readiness import (
    BuildSpec,
    BuildSpecError,
    ReadinessLevel,
    ReadinessProfile,
    apply_build_spec,
    default_answer_for_question,
    dump_build_spec,
    load_build_spec,
    parse_readiness_level,
    readiness_questions,
    resolve_readiness_profile,
)


def test_readiness_profile_resolves_cumulative_controls() -> None:
    profile = ReadinessProfile(
        security={"level": "strict", "controls": ["Require signed dependency provenance."]},
    )

    controls = profile.resolved_controls()["security"]

    assert "Validate inputs and keep secrets out of source." in controls
    assert "Use least privilege and negative authorization tests." in controls
    assert "Require threat modeling, dependency scanning, and security review." in controls
    assert "Require signed dependency provenance." in controls


def test_readiness_questions_include_default_levels() -> None:
    profile = ReadinessProfile(compliance="regulated")

    questions = readiness_questions(profile)

    assert len(questions) == 7
    assert any("compliance" in question and "regulated" in question for question in questions)


def test_resolve_readiness_profile_applies_canonical_answers() -> None:
    profile = ReadinessProfile()
    question = next(question for question in readiness_questions(profile) if "security" in question)

    resolved = resolve_readiness_profile(profile, {question: "strict"})

    assert resolved.security.level == ReadinessLevel.STRICT


def test_default_answer_for_question_extracts_bracketed_default() -> None:
    assert default_answer_for_question("Readiness: What level of security is required? [default: standard]") == "standard"


def test_parse_readiness_level_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="Invalid readiness level"):
        parse_readiness_level("high")


def test_resolve_readiness_profile_rejects_invalid_answer() -> None:
    profile = ReadinessProfile()
    question = next(question for question in readiness_questions(profile) if "security" in question)

    with pytest.raises(ValueError, match="expected one of"):
        resolve_readiness_profile(profile, {question: "high"})


def test_build_spec_round_trip_and_apply(tmp_path: Path) -> None:
    spec = BuildSpec(
        task="# Build API\n\nBuild a small API.",
        sops="# SOPs\n\nUse typed Python.",
        readiness={"security": "strict", "compliance": "basic"},
        config={"budget": 25, "build_mode": "hierarchy"},
    )
    source = tmp_path / "spec.yaml"
    source.write_text(dump_build_spec(spec))

    loaded = load_build_spec(source)
    assert loaded.readiness.security.level == ReadinessLevel.STRICT

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pact.yaml").write_text("budget: 10\n")
    apply_build_spec(project_dir, loaded, source_path=source)

    config = yaml.safe_load((project_dir / "pact.yaml").read_text())
    assert config["budget"] == 25
    assert config["readiness"]["security"]["level"] == "strict"
    assert (project_dir / "task.md").read_text().startswith("# Build API")
    assert (project_dir / "build_spec.yaml").read_text() == source.read_text()


def test_apply_build_spec_rolls_back_partial_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pact.readiness as readiness

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pact.yaml").write_text("budget: 10\n")
    (project_dir / "build_spec.yaml").write_text("version: '1'\n")

    spec = BuildSpec(task="# New task", config={"budget": 25})
    original_atomic_write = readiness._atomic_write_text

    def failing_atomic_write(path: Path, content: str) -> None:
        if path.name == "build_spec.yaml":
            raise OSError("disk full")
        original_atomic_write(path, content)

    monkeypatch.setattr(readiness, "_atomic_write_text", failing_atomic_write)

    with pytest.raises(BuildSpecError, match="Unable to apply build spec"):
        apply_build_spec(project_dir, spec)

    assert (project_dir / "pact.yaml").read_text() == "budget: 10\n"
    assert (project_dir / "build_spec.yaml").read_text() == "version: '1'\n"
    assert not (project_dir / "task.md").exists()


def test_load_build_spec_reports_invalid_input(tmp_path: Path) -> None:
    source = tmp_path / "spec.yaml"
    source.write_text("readiness: [")

    with pytest.raises(BuildSpecError, match="Unable to parse YAML build spec"):
        load_build_spec(source)


def test_load_build_spec_rejects_unsupported_extension(tmp_path: Path) -> None:
    source = tmp_path / "spec.txt"
    source.write_text("task: Build API")

    with pytest.raises(BuildSpecError, match="Unsupported build spec format"):
        load_build_spec(source)


def test_load_build_spec_rejects_unknown_version(tmp_path: Path) -> None:
    source = tmp_path / "spec.yaml"
    source.write_text("version: '2'\ntask: Build API")

    with pytest.raises(BuildSpecError, match="Unsupported build spec version"):
        load_build_spec(source)


def test_load_build_spec_rejects_unknown_readiness_dimension(tmp_path: Path) -> None:
    source = tmp_path / "spec.yaml"
    source.write_text("readiness:\n  securty: strict")

    with pytest.raises(BuildSpecError, match="Extra inputs are not permitted"):
        load_build_spec(source)


def test_cli_spec_apply_and_show(tmp_path: Path, capsys) -> None:
    from pact.cli import cmd_spec
    from pact.project import ProjectManager

    project = ProjectManager(tmp_path / "project")
    project.init()
    spec = BuildSpec(
        task="# Build API",
        readiness={"security": "strict"},
    )
    source = tmp_path / "spec.yaml"
    source.write_text(dump_build_spec(spec))

    cmd_spec(
        argparse.Namespace(
            spec_command="apply",
            project_dir=str(project.project_dir),
            spec_file=str(source),
        )
    )
    assert "Applied build spec" in capsys.readouterr().out
    assert project.load_config().readiness.security.level == ReadinessLevel.STRICT

    cmd_spec(
        argparse.Namespace(
            spec_command="show",
            spec_file=str(source),
            json_output=False,
        )
    )
    assert "security" in capsys.readouterr().out
