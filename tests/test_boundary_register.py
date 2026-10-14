"""Tests for the boundary register (boundary-register/1).

Every cross-boundary identifier must be registered with a record the gate can
re-verify; values are never assumed. These tests model the production defects
that motivated the feature: a log formatter writing "level" while the platform
reads "severity", a service name that does not exist, a project-wide grant
that breaks an isolation claim.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pytest
import yaml

from pact.boundary_register import (
    contains_token,
    is_placeholder,
    load_register_entries,
    render_register_for_prompt,
    verify_entries,
)
from pact.contracts import (
    find_boundary_usages,
    validate_all_contracts,
    validate_boundary_register,
)
from pact.interface_stub import render_dependency_map, render_handoff_brief
from pact.project import ProjectManager
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    DataAccessDeclaration,
    DataAccessSideEffect,
    DecompositionNode,
    DecompositionTree,
    FieldSpec,
    FunctionContract,
    SideEffect,
    SideEffectKind,
    TestCase,
)

OBSERVED_AT = "2026-10-14T12:00:00Z"


# ── helpers ───────────────────────────────────────────────────────────


def _write(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_record(root: Path, rel: str, line: int) -> dict:
    return {
        "kind": "file",
        "path": rel,
        "line": line,
        "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest(),
    }


def _obs_record(root: Path, rel: str, command: str = "gcloud monitoring uptime list-configs") -> dict:
    return {
        "kind": "observation",
        "path": rel,
        "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest(),
        "command": command,
        "observed_at": OBSERVED_AT,
    }


def _equal_entry(root: Path, *, producer_value: str = "severity", reader_value: str = "severity") -> dict:
    """Log field the app writes vs. the field the log platform reads."""
    _write(root, "app/logging.py", f'FORMAT = {{"{producer_value}": record.levelname}}\n')
    _write(root, "ops/log_platform.yaml", f"level_field: {reader_value}\n")
    return {
        "id": "log-level-field",
        "kind": "field",
        "relation": "equal",
        "description": "The log platform only derives severity from this JSON key",
        "sides": [
            {
                "party": "external:cloud-logging",
                "role": "defines",
                "value": reader_value,
                "records": [_file_record(root, "ops/log_platform.yaml", 1)],
            },
            {
                "party": "api",
                "role": "consumes",
                "value": producer_value,
                "records": [_file_record(root, "app/logging.py", 1)],
            },
        ],
    }


def _write_register(root: Path, entries: list[dict], schema: str = "boundary-register/1") -> None:
    (root / "boundary_register.yaml").write_text(
        yaml.safe_dump({"schema": schema, "entries": entries}, sort_keys=False)
    )


def _contract(cid: str = "api", **kwargs) -> ComponentContract:
    return ComponentContract(
        component_id=cid,
        name=cid.title(),
        description="Component",
        functions=kwargs.pop("functions", [
            FunctionContract(
                name="handle",
                description="Handles a request",
                inputs=[FieldSpec(name="x", type_ref="str")],
                output_type="str",
            ),
        ]),
        data_access=kwargs.pop("data_access", DataAccessDeclaration(
            reads=["PUBLIC"], rationale="Reads public request input only",
        )),
        **kwargs,
    )


def _suite(cid: str) -> ContractTestSuite:
    return ContractTestSuite(
        component_id=cid,
        contract_version=1,
        test_cases=[TestCase(id="t1", description="t", function="handle", category="happy_path")],
        generated_code="def test_ok(): pass\n",
    )


def _tree(*cids: str) -> DecompositionTree:
    return DecompositionTree(
        root_id=cids[0],
        nodes={c: DecompositionNode(component_id=c, name=c.title(), description=c) for c in cids},
    )


def _db_contract(cid: str = "api") -> ComponentContract:
    return _contract(cid, data_access=DataAccessDeclaration(
        reads=["PII"],
        rationale="Reads user rows from the accounts database for login",
        side_effects=[DataAccessSideEffect(type="database_read", classification="PII", fields=["users.email"])],
    ))


# ── token matching and placeholders ───────────────────────────────────


class TestTokenRule:
    def test_token_does_not_match_inside_longer_identifier(self):
        assert not contains_token("service: mea-auth-runtime", "mea-auth")
        assert not contains_token("xmea-auth", "mea-auth")
        assert not contains_token("mea_auth_x", "mea_auth")

    def test_token_matches_with_punctuation_neighbours(self):
        assert contains_token('{"level": "INFO"}', "level")
        assert contains_token("mea-auth", "mea-auth")
        assert contains_token("url: https://mea-auth.run.app", "mea-auth")

    def test_later_occurrence_can_match(self):
        assert contains_token("mea-auth-runtime and mea-auth", "mea-auth")

    @pytest.mark.parametrize(("haystack", "value", "expected"), [
        ("metric foo.bar.baz", "foo.bar", False),
        ("a.foo.bar", "foo.bar", False),
        ("type=foo.bar.", "foo.bar", True),
        ("GET /health/ready", "/health", False),
        ("GET /health HTTP/1.1", "/health", True),
        ("entry.severity = level", "severity", True),
        ('"level": "INFO"', "level", True),
        ("svc-auth-runtime", "svc-auth", False),
        ("serviceAccount:svc-auth@p.iam", "svc-auth", True),
    ])
    def test_shared_format_vectors(self, haystack, value, expected):
        """boundary-register/1 token vectors shared with the factory and Constrain."""
        assert contains_token(haystack, value) is expected

    @pytest.mark.parametrize("value", [
        "TODO", "tbd", "TBC", "assumed", "Placeholder", "changeme", "xxx", "XXXX",
        "?", "???", " todo ", "<service-name>", "svc-<env>",
    ])
    def test_placeholders_detected(self, value):
        assert is_placeholder(value)

    @pytest.mark.parametrize("value", [
        "severity", "cloud_run_revision", "roles/run.invoker",
        "unknown", "none", "null", "n/a", "UNKNOWN", "xx", "todo-list", "a < b",
    ])
    def test_real_values_are_not_placeholders(self, value):
        assert not is_placeholder(value)


# ── relation: equal ───────────────────────────────────────────────────


class TestEqualRelation:
    def test_matching_values_with_valid_records_pass(self, tmp_path):
        assert verify_entries([_equal_entry(tmp_path)], tmp_path) == []

    def test_value_mismatch_fails(self, tmp_path):
        errors = verify_entries([_equal_entry(tmp_path, producer_value="level")], tmp_path)
        joined = "\n".join(errors)
        assert "MISMATCH" in joined
        assert "log-level-field" in joined
        assert "'level'" in joined and "'severity'" in joined
        assert "near-miss" not in joined

    def test_near_miss_by_separators_is_named(self, tmp_path):
        errors = verify_entries(
            [_equal_entry(tmp_path, producer_value="uptime_url", reader_value="uptime-url")], tmp_path,
        )
        assert any("near-miss" in e and "separators" in e for e in errors), errors

    def test_near_miss_by_case_is_named(self, tmp_path):
        errors = verify_entries(
            [_equal_entry(tmp_path, producer_value="Severity", reader_value="severity")], tmp_path,
        )
        assert any("near-miss" in e and "case" in e for e in errors), errors

    def test_side_without_records_is_unverified(self, tmp_path):
        entry = _equal_entry(tmp_path)
        entry["sides"][1]["records"] = []
        errors = verify_entries([entry], tmp_path)
        assert any("UNVERIFIED" in e and "side[1 api/consumes]" in e for e in errors), errors

    def test_stale_digest_fails_naming_the_file(self, tmp_path):
        entry = _equal_entry(tmp_path)
        (tmp_path / "app/logging.py").write_text('FORMAT = {"severity": record.levelname}  # edited\n')
        errors = verify_entries([entry], tmp_path)
        assert any("STALE" in e and "app/logging.py" in e and "never hand-edit" in e for e in errors), errors

    def test_cited_line_must_contain_value(self, tmp_path):
        entry = _equal_entry(tmp_path)
        _write(tmp_path, "app/logging.py", "import json\nFORMAT = {\"severity\": 1}\n")
        entry["sides"][1]["records"] = [_file_record(tmp_path, "app/logging.py", 1)]
        errors = verify_entries([entry], tmp_path)
        assert any("line 1 does not contain 'severity'" in e for e in errors), errors

    def test_line_beyond_end_of_file_fails(self, tmp_path):
        entry = _equal_entry(tmp_path)
        entry["sides"][1]["records"][0]["line"] = 99
        errors = verify_entries([entry], tmp_path)
        assert any("beyond the end" in e for e in errors), errors

    def test_token_boundary_rejects_longer_service_name(self, tmp_path):
        _write(tmp_path, "infra/uptime.tf", 'service = "mea-auth-runtime"\n')
        _write(tmp_path, "infra/services.tf", 'name = "mea-auth"\n')
        entry = {
            "id": "uptime-target",
            "kind": "service",
            "relation": "equal",
            "description": "Uptime check must target a service that exists",
            "sides": [
                {"party": "external:cloud-run", "role": "defines", "value": "mea-auth",
                 "records": [_file_record(tmp_path, "infra/services.tf", 1)]},
                {"party": "monitoring", "role": "consumes", "value": "mea-auth",
                 "records": [_file_record(tmp_path, "infra/uptime.tf", 1)]},
            ],
        }
        errors = verify_entries([entry], tmp_path)
        assert len(errors) == 1 and "infra/uptime.tf" in errors[0] and "does not contain" in errors[0], errors

    def test_observation_record_passes(self, tmp_path):
        _write(tmp_path, "obs/uptime.json", '{"monitoredResource": {"type": "cloud_run_revision"}}\n')
        _write(tmp_path, "ops/alert.yaml", 'filter: resource.type = "cloud_run_revision"\n')
        entry = {
            "id": "uptime-resource-type",
            "kind": "metric",
            "relation": "equal",
            "description": "Alert filter must match the resource type uptime checks emit",
            "sides": [
                {"party": "external:cloud-monitoring", "role": "defines", "value": "cloud_run_revision",
                 "records": [_obs_record(tmp_path, "obs/uptime.json")]},
                {"party": "alerts", "role": "consumes", "value": "cloud_run_revision",
                 "records": [_file_record(tmp_path, "ops/alert.yaml", 1)]},
            ],
        }
        assert verify_entries([entry], tmp_path) == []

    def test_observation_needs_command_and_timestamp(self, tmp_path):
        _write(tmp_path, "obs/uptime.json", '{"type": "cloud_run_revision"}\n')
        rec = _obs_record(tmp_path, "obs/uptime.json")
        rec["command"] = ""
        rec["observed_at"] = "yesterday"
        entry = _equal_entry(tmp_path, producer_value="cloud_run_revision", reader_value="cloud_run_revision")
        entry["sides"][0]["records"] = [rec]
        errors = verify_entries([entry], tmp_path)
        assert any("command" in e for e in errors), errors
        assert any("observed_at" in e for e in errors), errors

    def test_observation_must_contain_value(self, tmp_path):
        _write(tmp_path, "obs/uptime.json", '{"type": "cloud_run_revision"}\n')
        entry = _equal_entry(tmp_path, producer_value="uptime_url", reader_value="uptime_url")
        entry["sides"][0]["records"] = [_obs_record(tmp_path, "obs/uptime.json")]
        errors = verify_entries([entry], tmp_path)
        assert any("observation 'obs/uptime.json' does not contain 'uptime_url'" in e for e in errors), errors

    def test_equal_needs_authority_and_user_sides(self, tmp_path):
        entry = _equal_entry(tmp_path)
        entry["sides"][0]["role"] = "consumes"
        errors = verify_entries([entry], tmp_path)
        assert any("defines or grants" in e for e in errors), errors

    @pytest.mark.parametrize("value", ["TODO", "<table-name>", "???", "changeme", "assumed"])
    def test_placeholder_value_fails(self, tmp_path, value):
        entry = _equal_entry(tmp_path)
        entry["sides"][1]["value"] = value
        errors = verify_entries([entry], tmp_path)
        assert any("placeholder" in e and "side[1 api/consumes]" in e for e in errors), errors


# ── structural rules ──────────────────────────────────────────────────


class TestStructure:
    def test_duplicate_ids_fail(self, tmp_path):
        entry = _equal_entry(tmp_path)
        errors = verify_entries([entry, dict(entry)], tmp_path)
        assert any("duplicate id" in e for e in errors), errors

    def test_bad_id_kind_relation_role_fail(self, tmp_path):
        entry = _equal_entry(tmp_path)
        entry.update({"id": "-bad id", "kind": "widget", "relation": "similar", "description": ""})
        entry["sides"][0]["role"] = "owns"
        joined = "\n".join(verify_entries([entry], tmp_path))
        for fragment in ("id must match", "kind must be one of", "relation must be", "description", "role must be one of"):
            assert fragment in joined, fragment

    def test_unknown_party_fails_when_components_known(self, tmp_path):
        errors = verify_entries([_equal_entry(tmp_path)], tmp_path, known_components={"gateway"})
        assert any("'api' is not a component" in e for e in errors), errors

    @pytest.mark.parametrize("bad_path", ["../outside.txt", "/etc/hosts", "app/../../outside.txt"])
    def test_path_traversal_refused(self, tmp_path, bad_path):
        project = tmp_path / "project"
        project.mkdir()
        (tmp_path / "outside.txt").write_text("severity\n")
        entry = _equal_entry(project)
        entry["sides"][1]["records"] = [{
            "kind": "file", "path": bad_path, "line": 1,
            "sha256": hashlib.sha256(b"severity\n").hexdigest(),
        }]
        errors = verify_entries([entry], project)
        assert any(("must be relative" in e or "must not contain '..'" in e) for e in errors), errors

    def test_symlink_escaping_root_refused(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (tmp_path / "outside.txt").write_text("severity\n")
        (project / "link.txt").symlink_to(tmp_path / "outside.txt")
        entry = _equal_entry(project)
        entry["sides"][1]["records"] = [{
            "kind": "file", "path": "link.txt", "line": 1,
            "sha256": hashlib.sha256(b"severity\n").hexdigest(),
        }]
        errors = verify_entries([entry], project)
        assert any("resolves outside the project root" in e for e in errors), errors

    def test_missing_record_file_fails(self, tmp_path):
        entry = _equal_entry(tmp_path)
        (tmp_path / "app/logging.py").unlink()
        errors = verify_entries([entry], tmp_path)
        assert any("'app/logging.py' does not exist" in e for e in errors), errors

    def test_wrong_schema_in_register_file_fails(self, tmp_path):
        _write_register(tmp_path, [_equal_entry(tmp_path)], schema="boundary-register/0")
        errors = validate_boundary_register({}, tmp_path)
        assert any("schema must be 'boundary-register/1'" in e for e in errors), errors


# ── relation: absent (isolation) ──────────────────────────────────────


def _absent_entry(root: Path, policy_text: str) -> dict:
    subject = "serviceAccount:gateway@proj.iam.gserviceaccount.com"
    _write(root, "infra/gateway_sa.tf", f'member = "{subject}"\n')
    _write(root, "obs/project_iam.json", policy_text)
    return {
        "id": "isolated-svc-no-gateway-invoker",
        "kind": "grant",
        "relation": "absent",
        "description": "The gateway must not be able to invoke the isolated service",
        "sides": [
            {"party": "gateway", "role": "presents", "value": subject,
             "records": [_file_record(root, "infra/gateway_sa.tf", 1)]},
            {"party": "external:gcp-iam", "role": "denies", "value": "roles/run.invoker",
             "records": [_obs_record(root, "obs/project_iam.json", "gcloud projects get-iam-policy proj")]},
        ],
    }


class TestAbsentRelation:
    def test_passes_when_subject_absent(self, tmp_path):
        entry = _absent_entry(tmp_path, '{"bindings": [{"role": "roles/run.invoker", "members": ["serviceAccount:ops@proj.iam.gserviceaccount.com"]}]}\n')
        assert verify_entries([entry], tmp_path) == []

    def test_fails_when_subject_present(self, tmp_path):
        entry = _absent_entry(tmp_path, '{"bindings": [{"role": "roles/run.invoker", "members": ["serviceAccount:gateway@proj.iam.gserviceaccount.com"]}]}\n')
        errors = verify_entries([entry], tmp_path)
        assert any("isolation violated" in e and "obs/project_iam.json" in e for e in errors), errors

    def test_denies_record_must_verify_by_digest(self, tmp_path):
        entry = _absent_entry(tmp_path, '{"bindings": []}\n')
        (tmp_path / "obs/project_iam.json").write_text('{"bindings": [{"members": []}]}\n')
        errors = verify_entries([entry], tmp_path)
        assert any("STALE" in e and "obs/project_iam.json" in e for e in errors), errors

    def test_needs_exactly_one_presents_and_a_denies(self, tmp_path):
        entry = _absent_entry(tmp_path, '{"bindings": []}\n')
        entry["sides"] = [entry["sides"][0], dict(entry["sides"][0])]
        errors = verify_entries([entry], tmp_path)
        assert any("exactly one 'presents'" in e for e in errors), errors
        assert any("at least one 'denies'" in e for e in errors), errors


# ── gate wiring: validate_all_contracts ───────────────────────────────


class TestGateWiring:
    def test_no_boundaries_no_register_passes(self, tmp_path):
        gate = validate_all_contracts(_tree("api"), {"api": _contract()}, {"api": _suite("api")}, tmp_path)
        assert gate.passed, gate.details

    def test_unregistered_database_usage_fails_and_names_it(self, tmp_path):
        gate = validate_all_contracts(_tree("api"), {"api": _db_contract()}, {"api": _suite("api")}, tmp_path)
        assert not gate.passed
        joined = "\n".join(gate.details)
        assert "UNREGISTERED boundary" in joined
        assert "data_access side effect 'database_read'" in joined
        assert "users.email" in joined

    def test_unregistered_external_dependency_fails(self, tmp_path):
        contract = _contract(requires=["external:stripe"])
        gate = validate_all_contracts(_tree("api"), {"api": contract}, {"api": _suite("api")}, tmp_path)
        assert not gate.passed
        assert any("requires 'external:stripe'" in d for d in gate.details), gate.details

    def test_network_call_side_effect_is_a_boundary(self):
        contract = _contract(functions=[FunctionContract(
            name="probe", description="Calls a peer", inputs=[], output_type="bool",
            structured_side_effects=[SideEffect(kind=SideEffectKind.NETWORK_CALL, target="billing /healthz")],
        )])
        usages = find_boundary_usages(contract)
        assert usages and usages[0][0] == "" and "network_call on 'billing /healthz'" in usages[0][1]

    def test_registered_usage_passes(self, tmp_path):
        _write_register(tmp_path, [_equal_entry(tmp_path)])
        contract = _contract(requires=["external:cloud-logging"])
        gate = validate_all_contracts(_tree("api"), {"api": contract}, {"api": _suite("api")}, tmp_path)
        assert gate.passed, gate.details

    def test_registered_entry_that_does_not_cover_named_system_fails(self, tmp_path):
        _write_register(tmp_path, [_equal_entry(tmp_path)])
        contract = _contract(requires=["external:cloud-logging", "external:postgres"])
        gate = validate_all_contracts(_tree("api"), {"api": contract}, {"api": _suite("api")}, tmp_path)
        assert not gate.passed
        assert any("external:postgres" in d and "UNREGISTERED" in d for d in gate.details), gate.details
        assert not any("external:cloud-logging'" in d and "UNREGISTERED" in d for d in gate.details)

    def test_register_errors_are_hard_errors(self, tmp_path):
        _write_register(tmp_path, [_equal_entry(tmp_path, producer_value="level")])
        gate = validate_all_contracts(_tree("api"), {"api": _contract()}, {"api": _suite("api")}, tmp_path)
        assert not gate.passed
        assert any("MISMATCH" in d for d in gate.details), gate.details
        assert not any(d.startswith("[warning]") and "MISMATCH" in d for d in gate.details)

    def test_contract_embedded_entries_are_verified(self, tmp_path):
        bad = _equal_entry(tmp_path)
        bad["sides"][1]["records"][0]["sha256"] = "0" * 64
        contract = _db_contract().model_copy(update={
            "boundary_identifiers": ComponentContract.model_validate(
                {"component_id": "api", "name": "Api", "description": "d", "boundary_identifiers": [bad]}
            ).boundary_identifiers,
        })
        gate = validate_all_contracts(_tree("api"), {"api": contract}, {"api": _suite("api")}, tmp_path)
        assert not gate.passed
        assert any("STALE" in d and "app/logging.py" in d for d in gate.details), gate.details
        assert not any("UNREGISTERED" in d for d in gate.details)

    def test_duplicate_id_across_register_and_contract_fails(self, tmp_path):
        entry = _equal_entry(tmp_path)
        _write_register(tmp_path, [entry])
        contract = ComponentContract.model_validate({
            **_contract().model_dump(), "boundary_identifiers": [entry],
        })
        errors = validate_boundary_register({"api": contract}, tmp_path, _tree("api"))
        assert any("duplicate id" in e for e in errors), errors

    def test_project_validate_cli_and_mcp_enforce(self, tmp_path, capsys):
        from pact.cli import cmd_boundary, cmd_validate
        from pact.mcp_server import PactMCPServer

        pm = ProjectManager(tmp_path)
        pm.save_tree(_tree("api"))
        pm.save_contract(_db_contract())
        pm.save_test_suite(_suite("api"))

        with pytest.raises(SystemExit) as exc:
            cmd_validate(argparse.Namespace(project_dir=str(tmp_path)))
        assert exc.value.code == 1
        assert "UNREGISTERED boundary" in capsys.readouterr().out

        with pytest.raises(SystemExit) as exc:
            cmd_boundary(argparse.Namespace(boundary_command="verify", project_dir=str(tmp_path)))
        assert exc.value.code == 1
        assert "UNREGISTERED boundary" in capsys.readouterr().out

        (tmp_path / ".pact").mkdir(exist_ok=True)
        result = PactMCPServer(tmp_path).tool_validate()
        assert result.get("passed") is False, result
        assert any("UNREGISTERED" in e for e in result["errors"])


class TestBoundaryCli:
    def test_verify_prints_entry_side_and_reason(self, tmp_path, capsys):
        from pact.cli import cmd_boundary

        _write_register(tmp_path, [_equal_entry(tmp_path, producer_value="level")])
        with pytest.raises(SystemExit) as exc:
            cmd_boundary(argparse.Namespace(boundary_command="verify", project_dir=str(tmp_path)))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "boundary[log-level-field]" in out
        assert "MISMATCH" in out

    def test_verify_passes_on_valid_register(self, tmp_path, capsys):
        from pact.cli import cmd_boundary

        _write_register(tmp_path, [_equal_entry(tmp_path)])
        cmd_boundary(argparse.Namespace(boundary_command="verify", project_dir=str(tmp_path)))
        assert "PASSED: 1 entry verified" in capsys.readouterr().out

    def test_main_dispatches_boundary_verify(self, tmp_path, monkeypatch):
        from pact.cli import main

        _write_register(tmp_path, [_equal_entry(tmp_path, producer_value="level")])
        monkeypatch.setattr("sys.argv", ["pact", "boundary", "verify", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


# ── rendering for implementers and authors ────────────────────────────


class TestRendering:
    def test_dependency_map_shows_registered_values(self, tmp_path):
        _write_register(tmp_path, [_equal_entry(tmp_path)])
        entries = load_register_entries(tmp_path)
        rendered = render_dependency_map("api", {"api": _contract()}, entries)
        assert "log-level-field" in rendered
        assert "'severity'" in rendered
        assert "app/logging.py:1" in rendered
        assert "<- you" in rendered

    def test_dependency_map_omits_entries_for_other_components(self, tmp_path):
        _write_register(tmp_path, [_equal_entry(tmp_path)])
        rendered = render_dependency_map("worker", {"worker": _contract("worker")}, load_register_entries(tmp_path))
        assert "log-level-field" not in rendered

    def test_handoff_brief_shows_registered_values_even_when_compressed(self, tmp_path):
        _write_register(tmp_path, [_equal_entry(tmp_path)])
        contract = _contract()
        brief = render_handoff_brief(
            "api", contract, {"api": contract},
            boundary_entries=load_register_entries(tmp_path),
            sops="x " * 5000, max_context_tokens=50,
        )
        assert "Registered boundary identifiers you must use verbatim" in brief
        assert "'severity'" in brief

    def test_contract_embedded_entries_render(self, tmp_path):
        contract = ComponentContract.model_validate({
            **_contract().model_dump(), "boundary_identifiers": [_equal_entry(tmp_path)],
        })
        rendered = render_dependency_map("api", {"api": contract})
        assert "log-level-field" in rendered and "'severity'" in rendered

    def test_register_rendered_for_contract_author(self, tmp_path):
        _write_register(tmp_path, [_equal_entry(tmp_path)])
        text = render_register_for_prompt(load_register_entries(tmp_path))
        assert "boundary-register/1" in text
        assert "severity" in text and "sha256" in text


class TestPrompts:
    def test_contract_author_interview_and_shaper_forbid_invented_identifiers(self):
        from pact.agents.contract_author import CONTRACT_SYSTEM
        from pact.agents.shaper import SYSTEM_PROMPT
        from pact.decomposer import INTERVIEW_SYSTEM

        for prompt in (CONTRACT_SYSTEM, INTERVIEW_SYSTEM, SYSTEM_PROMPT):
            assert "Never invent or assume one" in prompt
            assert "boundary-register/1" in prompt
            assert "open question" in prompt
            assert "pact boundary cite" in prompt
            assert "denies side's value names the" in prompt


# ── producing records: pact boundary cite ─────────────────────────────


class TestCite:
    def test_file_record_round_trips_through_verifier(self, tmp_path):
        from pact.boundary_register import cite_record

        _write(tmp_path, "ops/platform.yaml", "# header\nlevel_field: severity\n")
        _write(tmp_path, "app/logging.py", 'FORMAT = {"severity": record.levelname}\n')
        entry = {
            "id": "log-level-field", "kind": "field", "relation": "equal",
            "description": "The log platform reads severity from this key",
            "sides": [
                {"party": "external:cloud-logging", "role": "defines", "value": "severity",
                 "records": [cite_record(tmp_path, kind="file", path="ops/platform.yaml", value="severity")]},
                {"party": "api", "role": "consumes", "value": "severity",
                 "records": [cite_record(tmp_path, kind="file", path="app/logging.py", value="severity", line=1)]},
            ],
        }
        assert verify_entries([entry], tmp_path) == []

    def test_line_auto_detect_uses_first_token_match(self, tmp_path):
        from pact.boundary_register import cite_record

        _write(tmp_path, "infra/svc.tf", 'a = "mea-auth-runtime"\nb = "mea-auth"\nc = "mea-auth"\n')
        record = cite_record(tmp_path, kind="file", path="infra/svc.tf", value="mea-auth")
        assert record["line"] == 2
        assert record["sha256"] == hashlib.sha256((tmp_path / "infra/svc.tf").read_bytes()).hexdigest()

    def test_refuses_when_value_absent_or_not_on_line(self, tmp_path):
        from pact.boundary_register import CiteError, cite_record

        _write(tmp_path, "infra/svc.tf", 'a = "mea-auth-runtime"\nb = "other"\n')
        with pytest.raises(CiteError, match="does not occur as a token"):
            cite_record(tmp_path, kind="file", path="infra/svc.tf", value="mea-auth")
        with pytest.raises(CiteError, match="line 2 does not contain"):
            cite_record(tmp_path, kind="file", path="infra/svc.tf", value="other-x", line=2)
        with pytest.raises(CiteError, match="does not contain"):
            cite_record(tmp_path, kind="observation", path="infra/svc.tf", value="mea-auth", command="cat infra/svc.tf")

    def test_refuses_path_escape(self, tmp_path):
        from pact.boundary_register import CiteError, cite_record

        with pytest.raises(CiteError, match="must not contain '..'"):
            cite_record(tmp_path, kind="file", path="../x", value="v")

    def test_observation_needs_command_and_stamps_utc(self, tmp_path):
        from pact.boundary_register import CiteError, cite_record

        _write(tmp_path, "obs/uptime.json", '{"type": "cloud_run_revision"}\n')
        with pytest.raises(CiteError, match="--command"):
            cite_record(tmp_path, kind="observation", path="obs/uptime.json", value="cloud_run_revision")
        record = cite_record(
            tmp_path, kind="observation", path="obs/uptime.json", value="cloud_run_revision",
            command="gcloud monitoring uptime list-configs --format json",
        )
        assert record["observed_at"].endswith("Z")
        entry = _equal_entry(tmp_path, producer_value="cloud_run_revision", reader_value="cloud_run_revision")
        entry["sides"][0]["records"] = [record]
        assert verify_entries([entry], tmp_path) == []

    def test_cli_prints_json_and_exits_1_on_refusal(self, tmp_path, monkeypatch, capsys):
        import json

        from pact.cli import main

        _write(tmp_path, "ops/platform.yaml", "level_field: severity\n")
        monkeypatch.setattr("sys.argv", [
            "pact", "boundary", "cite", "--kind", "file", "--path", "ops/platform.yaml",
            "--value", "severity", str(tmp_path),
        ])
        main()
        record = json.loads(capsys.readouterr().out)
        assert record == {
            "kind": "file", "path": "ops/platform.yaml", "line": 1,
            "sha256": hashlib.sha256(b"level_field: severity\n").hexdigest(),
        }

        monkeypatch.setattr("sys.argv", [
            "pact", "boundary", "cite", "--kind", "file", "--path", "ops/platform.yaml",
            "--value", "level", str(tmp_path),
        ])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
        assert "Refusing to cite" in capsys.readouterr().err

    def test_cli_observation_with_command(self, tmp_path, monkeypatch, capsys):
        import json

        from pact.cli import main

        _write(tmp_path, "obs/uptime.json", '{"type": "cloud_run_revision"}\n')
        monkeypatch.setattr("sys.argv", [
            "pact", "boundary", "cite", "--kind", "observation", "--path", "obs/uptime.json",
            "--value", "cloud_run_revision", "--command", "gcloud monitoring uptime list-configs",
            str(tmp_path),
        ])
        main()
        record = json.loads(capsys.readouterr().out)
        assert record["command"] == "gcloud monitoring uptime list-configs"
        assert record["kind"] == "observation" and record["observed_at"].endswith("Z")
