# Changelog

## v1.3.0 - 2026-10-14

### Added

- Boundary register (`boundary_register.yaml`, format `boundary-register/1`,
  shared with the factory and Constrain) plus `ComponentContract.boundary_identifiers`.
  Every cross-boundary identifier (service name, audience, credential, principal,
  grant/role, table/column, metric type, log field, env/config key, header,
  claim) must cite file or observation records bound by SHA-256. Verification
  is mechanical: exact `equal` values with near-miss hints, `absent` isolation
  proofs, token matching, stale-digest detection, placeholder refusal, and
  project-root path confinement.
- `pact boundary verify [project]` verifies the register and exits non-zero on
  any error.
- `pact boundary cite --kind file|observation --path <rel> --value <literal>`
  produces a verified record (sha256, line or observed_at) as JSON and refuses
  when the literal is not where the record would claim.
- Interview, shaper, and contract-author prompts require citing a record or
  raising an open question; implementer handoffs render registered literals.

### Changed

- `validate_all_contracts` now fails when a contract uses a cross-boundary
  dependency (`external:` names, `network_call`, `database_*`/`http_*`/
  `external_*` data-access side effects) that the register does not cover, or
  when any register entry fails verification. Projects with no cross-boundary
  usage are unaffected.
- `pact validate` now exits 1 when the contract gate fails.

## v1.2.0 - 2026-07-17

### Added

- Added constrained `pact agent spec-author` and `pact agent repair` commands.
  These commands use the OpenAI Responses API directly, require `PACT_AGENT_*`
  budget/context environment variables, collect a bounded file snapshot, and
  write only inside validated allowed roots when `--apply` is supplied. Without
  `--apply`, they produce a dry-run report with proposed paths.
- Added `IMPROVEMENTS.md` with field notes from using Pact against an existing
  service and marked which v1.2.0 items were addressed.
- Added the Pact engineer lean-review checklist and documented
  production-ready minimalism as a separate review lens.

### Changed

- `pact certify` now fails closed when a contracted component is missing its
  emission compliance test. Existing projects that use certification should
  regenerate or add emission tests before expecting a passing certification.
- `pact init --spec` now validates the build-spec file before project
  initialization, so unsupported formats such as `.md` do not leave a
  half-initialized project behind.
- CLI help and README wording now state that build specs are `.json`, `.yaml`,
  or `.yml`, and that `pact certify` / `pact production validate` operate on
  Pact-managed projects.
- `pact agent repair` now requires each `--source-root` to be scoped to the
  component name or `PACT_AGENT_COMPONENT_ID`; broad roots such as `services`
  are rejected.
- Agent help and README examples now show the exact constrained-agent
  environment variables, a valid `PACT_AGENT_ALLOWED_CONTEXT` object, when
  `PACT_AGENT_COMPONENT_ID` is required, and the v1.2 single-file apply ceiling.
- `PACT_AGENT_ALLOWED_CONTEXT` is now validated as a bounded reference-only
  schema. The Responses API input uses a separate user message for repository
  data, while authorization remains in back-end validation rather than prompt
  construction.
- Forbidden write classes are enforced from built-in constants. The legacy
  `PACT_AGENT_FORBIDDEN_WRITES` variable is only an optional deprecated
  compatibility check; extra entries must be normalized path prefixes and are
  enforced as denied roots while callers migrate them out of config.

### Failure Modes

- Constrained agent commands exit before model calls when required
  `PACT_AGENT_*` caps, `OPENAI_API_KEY`, component-scoped Pact project paths,
  allowed context, or source-root policies are invalid. JSON reports include
  `policy.violations` and no files are written on policy failure. `AGENT_SAFE_*`
  names are intentionally not aliases; wrappers must set the exact
  `PACT_AGENT_*` names.
- Agent reports include `agent.proposed_paths` for dry runs and
  `agent.applied_paths` only for validated `--apply` writes. Apply mode accepts
  one file change per run in v1.2.0; looping partial multi-file repairs is not
  supported because it can create inconsistent intermediate states. The model
  call has already happened by the time a multi-file proposal is rejected, so
  operators should dry-run first when repair scope is unknown. The rejected
  proposed paths remain in `agent.proposed_paths` for manual recovery.
- Agent usage reports include estimated post-call spend, estimated wasted spend
  for failed post-call gates, and `spend_over_cap_estimated_usd`. Both sides of
  the delta are rate-table estimates; positive means post-call usage exceeded
  the pre-call cap estimate.
- Constrained agent writes are rejected when the requested path is outside the
  allowed roots, resolves outside the workspace, crosses an existing symlink, is
  a directory, contains NUL bytes, or exceeds file-size limits.
- Certification artifacts now include emission-test hashes/results. A missing
  emission test records `missing_test: true`, produces a failing verdict, and
  is reported as missing during `--verify-only` artifact checks.

### Validation

- Full local suite: `python3 -m pytest tests/ -q` -> `2274 passed`.

## v1.1.0 - 2026-06-12

- Added typed readiness profiles, AI-authored build specs, optional production
  readiness artifact packs, and production validation/fingerprint commands.

## v1.0.0 - 2026-06-04

- Made Pact plan-first by default: `pact run` stops after contracts/tests unless
  `--implement` is supplied.
- Added the packaged Simulacrum runtime and the `pact review` workflow.
