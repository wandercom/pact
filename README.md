# Pact

**Contracts before code. Tests as law. Agents that can't cheat.**

Website: [pact.tools](https://pact.tools) · Docs: [pact.tools/docs](https://pact.tools/docs/) · Repository: [github.com/wandercom/pact](https://github.com/wandercom/pact)

Pact is a multi-agent software engineering framework where the architecture is decided before a single line of implementation is written. Tasks are decomposed into components, each component gets a typed interface contract, and each contract gets executable tests. Only then do agents implement -- independently, in parallel, even competitively -- with no way to ship code that doesn't honor its contract. Generates Python, TypeScript, or JavaScript.

> **Breaking change in v1:** `pact run` now stops after decomposition,
> contracts, and tests by default. Existing automation that expects Pact to
> implement must add `--implement`.

The insight: LLMs are unreliable reviewers but tests are perfectly reliable judges. So make the tests first, make them mechanical, and let agents iterate until they pass. No advisory coordination. No "looks good to me." Pass or fail.

## When to Use Pact

Pact is for projects where **getting the boundaries right matters more than getting the code written fast.** If a single Claude or Codex session can build your feature in one pass, just do that -- Pact's decomposition, contracts, and multi-agent coordination would be pure overhead.

Use Pact when:
- The task has **multiple interacting components** with non-obvious boundaries
- You need **provable correctness at interfaces** -- not "it seems to work" but "it passes 200 contract tests"
- The system will be **maintained by agents** who need contracts to understand what each piece does
- You want **competitive or parallel implementation** where multiple agents race on the same component
- The codebase is large enough that **no single context window can hold it all**

Don't use Pact when:
- A single agent can build the whole thing in one shot
- The task is a bug fix, refactor, or small feature
- You'd spend more time on contracts than on the code itself

## Benchmark: ICPC World Finals

Tested on 5 ICPC World Finals competitive programming problems (212 test cases total) using Claude Opus 4.6.

| Condition | Pass Rate | Cost |
|-----------|-----------|------|
| Claude Code single-shot | 167/212 (79%) | $0.60 |
| Claude Code iterative (5 attempts) | 196/212 (92%) | $1.26 |
| Pact (solo, noshape) | **212/212 (100%)** | ~$13 |

Pact's contract-first pipeline solves problems that iterative prompting cannot. On **Trailing Digits** (2020 World Finals), Claude Code scores 31/47 even with 5 retry iterations and full test feedback -- the naive algorithm times out on large inputs. Pact's interview and decomposition phases force upfront mathematical analysis, producing the correct O(log n) approach on the first implementation attempt.

Full results: [icpc_official/RESULTS.md](https://github.com/wandercom/pact/blob/main/benchmarks/icpc_official/RESULTS.md) in the benchmark directory.

## Philosophy: Contracts Are the Product

Pact treats **contracts as source of truth and implementations as disposable artifacts.** The code is cattle, not pets.

When a module fails in production, the response isn't "debug the implementation." It's: add a test that reproduces the failure to the contract, flush the implementation, and let an agent rebuild it. The contract got stricter. The next implementation can't have that bug. Over time, contracts accumulate the scar tissue of every production incident -- they become the real engineering artifact.

This inverts the traditional relationship between code and tests. Code is cheap (agents generate it in minutes). Contracts are expensive (they encode hard-won understanding of what the system actually needs to do). Pact makes that inversion explicit: you spend your time on contracts, agents spend their time on code.

## Quick Start

```bash
git clone https://github.com/wandercom/pact.git
cd pact
make
source .venv/bin/activate
```

That's it. Now try:

```bash
pact init my-project
# Edit my-project/task.md with your task
# Edit my-project/sops.md with your standards
pact run my-project
```

Pact defaults to **plan-only**: it produces the decomposition, contracts, and
tests, then pauses for the active Claude or Codex agent to implement. Use
`pact run my-project --implement` when Pact should also own implementation and
integration.

**v1 migration:** `pact run` no longer implements by default. Existing
automation that expects a full build must add `--implement`.

For a complete plan-first agent workflow, load the cross-agent skill at
`skills/pact-engineer/SKILL.md`. The repository is also a Claude Code plugin:
`claude --plugin-dir ./pact`. The plugin includes the Pact engineering workflow
and a Simulacrum skill; Codex can invoke the same review path through Pact.
Pact also exposes constrained single-call agent lanes for trusted automation:
`pact agent spec-author` may write only under the component-scoped Pact project,
and `pact agent repair --source-root <dir>` may write only under explicit source
roots after budget and path-policy validation. Invalid caps, paths, context, or
model responses fail closed with a JSON report under `policy.violations`; files
are written only when `--apply` is supplied after the policy and write guard
both pass. Without `--apply`, the command produces a dry-run report with
`agent.proposed_paths`.

Required agent environment:

| Variable | Required for | Meaning |
|----------|--------------|---------|
| `PACT_AGENT_COMPONENT` | all agent commands | Component name or slug for the run |
| `PACT_AGENT_PROJECT` | all agent commands | Component-scoped Pact project directory |
| `PACT_AGENT_MAX_WALL_SECONDS` | all agent commands | Model-call socket timeout, 1-900 |
| `PACT_AGENT_MAX_MODEL_TOKENS` | all agent commands | Estimated input plus output token cap, 1-50000 |
| `PACT_AGENT_MAX_TOOL_CALLS` | all agent commands | Must allow at least one model call; v1.2 performs one model call |
| `PACT_AGENT_MAX_USD` | all agent commands | Spend cap, greater than 0 and at most 1.00 |
| `OPENAI_API_KEY` | all agent commands | OpenAI Responses API key |
| `PACT_AGENT_COMPONENT_ID` | when directory name differs | Pact verifies the final directory name of `PACT_AGENT_PROJECT`. If that name is `payments` but `PACT_AGENT_COMPONENT=payments-api`, set `PACT_AGENT_COMPONENT_ID=payments` |
| `PACT_AGENT_ALLOWED_CONTEXT` | `repair` | JSON object with only these keys: `issue`, `issue_context_ref`, `allowed_files` |
| `PACT_AGENT_FORBIDDEN_WRITES` | deprecated | Remove this from config. If present, it must include `contracts,visible-tests,control-plane,hidden-oracle`; extra entries must be normalized path prefixes and are enforced as denied roots |

`pact agent repair` rejects broad top-level source roots. Use a scoped root such
as `services/api`, `src/api`, or `app/handlers`, not `services` or `.`. If
`basename "$PACT_AGENT_PROJECT"` differs from `PACT_AGENT_COMPONENT`, set
`PACT_AGENT_COMPONENT_ID` to that basename.

Example constrained repair dry run:

```bash
PACT_AGENT_COMPONENT=payments-api \
PACT_AGENT_COMPONENT_ID=payments \
PACT_AGENT_PROJECT=pact/payments \
PACT_AGENT_MAX_WALL_SECONDS=60 \
PACT_AGENT_MAX_MODEL_TOKENS=12000 \
PACT_AGENT_MAX_TOOL_CALLS=1 \
PACT_AGENT_MAX_USD=0.25 \
PACT_AGENT_ALLOWED_CONTEXT='{"issue":"BUG-123","allowed_files":["services/payments/handler.py"]}' \
OPENAI_API_KEY=... \
pact agent repair --source-root services/payments
```

Security model: file contents can contain text that tries to manipulate the AI
model. Pact treats repair context as untrusted before it reaches the model and
accepts only reference fields: a short issue key, a normalized relative
`issue_context_ref`, and/or `allowed_files` as normalized relative paths.
`PACT_AGENT_FORBIDDEN_WRITES` is deprecated; remove it from config. If present,
extra entries are enforced as denied path prefixes.

The authorization boundary is the back-end schema, budget, path, symlink,
response-shape, and write-root validation plus the tests/certification that
judge the result. Prompt instructions are best-effort model guidance, not
authorization controls. `PACT_AGENT_MAX_USD` is a pre-call rate-table estimate,
not a hard billing stop; use provider/account-level budget alerts for
high-volume automation.

Warning: use the dry run first when repair scope is unknown. `--apply` in v1.2.0
accepts exactly one model-proposed file change. If a multi-file proposal is
rejected after the model call, the proposed paths are in the JSON report under
`agent.proposed_paths`; capture the report with `--output`, apply the changes
manually if appropriate, then rerun Pact tests/certification.

For a production-readiness build, scaffold the optional artifact pack first:

```bash
pact production init my-project
pact run my-project --constrain-dir my-project/production --plan-only
pact production fingerprint my-project
pact production status my-project
```

The pack is intentionally opt-in. It adds file-backed trust assertions, control
mapping, threat model, architecture laws, preflight, live-validation, N/A, and
done-gate artifacts under `production/`; it does not change ordinary Pact runs.
`pact production validate` blocks until derived evidence, external evidence,
and justified N/A records are all present and non-placeholder.

### Production-ready minimalism

Pact treats simplicity as an optional review lens, not as a replacement for the
production gate. Before adding code, dependencies, or layers, ask:

1. Does this need to exist at all?
2. Does the standard library, native platform, or an installed dependency
   already solve it?
3. Is the new abstraction justified by a real requirement, measured constraint,
   or recovery/observability need?
4. If this is an intentional shortcut, what is its ceiling, what signal says it
   is no longer enough, and what is the upgrade path?

Lean review may remove boilerplate, wrapper-only layers, speculative
configuration, hand-rolled stdlib behavior, and unnecessary dependencies. It
may not remove trust-boundary validation, security controls, accessibility,
observability, rollback, migrations, auditability, live validation, or
evidence-backed done gates. Production-ready minimalism means an operator can
answer "what broke, why, and how do I roll back?" without deploying new code.
For each layer, ask whether removing it makes a trust boundary unenforceable,
a failure mode unobservable, rollback/evidence gathering impossible, or a hard
invariant dependent on manual discipline. Yes to any means load-bearing; no to
all four means ceremony.

Pact now starts every new project with a typed readiness profile in
`pact.yaml` and an AI-editable `build_spec.yaml`. The interview phase confirms
operational maturity, security, privacy, compliance, gating, testing, and
monitoring before decomposition. An AI can provide the same inputs directly:

```bash
pact init my-project --spec ai-build-spec.yaml
# or apply one to an existing project
pact spec apply my-project ai-build-spec.yaml
```

## How It Works

```
Task
  |
  v
Interview --> Shape (opt) --> Decompose --> Contract --> Test
                                                          |
                                                          v
                                    Implement (parallel, competitive)
                                                          |
                                                          v
                                    Integrate (glue + parent tests)
                                                          |
                                                          v
                                    Arbiter Gate (access graph + trust)
                                                          |
                                                          v
                                    Polish (Goodhart tests + regression)
                                                          |
                                                          v
                                    Certify (tamper-evident proof)
```

**Nine phases** (plus diagnose as a recovery state):

1. **Interview** -- Establish processing register, then identify risks, ambiguities, ask clarifying questions
2. **Shape** -- (Optional) Produce a Shape Up pitch: appetite, breadboard, rabbit holes, no-gos
3. **Decompose** -- Task into 2-7 component tree, guided by shaping context if present. Contract generation, test authoring (including emission compliance tests), and Goodhart adversarial tests all happen here.
4. **Implement** -- Each component built independently by a code agent with structured event emission
5. **Integrate** -- Parent components composed via glue code
6. **Arbiter** -- Generate access_graph.json, register with Arbiter for blast radius analysis. HUMAN_GATE pauses pipeline.
7. **Polish** -- Cross-component regression check + Goodhart test evaluation with graduated-disclosure remediation
8. **Complete** -- Certification with tamper-evident proof (SHA-256 hashes)

**Diagnose** is not a numbered phase — it's a recovery state. On failure at any phase, the system enters diagnose for I/O tracing, root cause analysis, and recovery routing back to implement.

## Stack Integration

Pact is the contract-first build system in a larger stack:

| Tool | Role | Pact's Relationship |
|------|------|-------------------|
| **Constrain** | Upstream policy | `--constrain-dir` seeds decomposition with constraints, component maps, trust policies |
| **Arbiter** | Trust gate | Phase 8.5 POSTs `access_graph.json` for blast radius analysis. HUMAN_GATE pauses pipeline |
| **Ledger** | Field-level audit | `--ledger-dir` loads assertions into contract test suites as hard requirements |
| **Sentinel** | Production monitoring | Separate package. Pact embeds PACT keys for attribution. `pact sentinel push-contract` accepts tightened contracts |

All integrations are optional. Without them, Pact operates as a standalone build system.

## Contract Schema

Every contract includes:

```yaml
data_access:
  reads: [PUBLIC, PII]
  writes: [PUBLIC]
  rationale: "Reads user.email for personalization, writes public analytics events"
  side_effects:
    - type: database_read
      classification: PII
      fields: ["user.email", "user.created_at"]
      rationale: "Fetch user profile for display"

authority:
  domains: ["user_profile"]
  rationale: "Authoritative source for user profile data within this service"
```

Anti-cliche enforcement rejects vague rationale strings ("handles data", "manages stuff"). Rationale must describe the specific data accessed and why.

### Canonical Types with Validators

Contracts encourage defining canonical data structures with validators rather than passing raw primitives. A field like `email: str` becomes a validated type with a regex constraint; `amount: float` carries range and precision rules. The `ValidatorSpec` schema supports `range`, `regex`, `length`, and `custom` rules:

```yaml
types:
  - name: PaymentAmount
    kind: struct
    fields:
      - name: value
        type_ref: float
        validators:
          - kind: range
            expression: "0.01 <= value <= 999999.99"
            error_message: "Amount must be between $0.01 and $999,999.99"
      - name: currency
        type_ref: str
        validators:
          - kind: regex
            expression: "^[A-Z]{3}$"
            error_message: "Currency must be a 3-letter ISO 4217 code"
```

Tests automatically verify both acceptance and rejection: valid instances pass, invalid inputs fail with appropriate errors, and boundary values behave correctly. Implementations render these as Pydantic models (Python), Zod schemas (TypeScript), or class constructors with validation (JavaScript).

## Audit Repo Separation

Pact supports a two-repo separation-of-privilege model where the coding agent and auditing agent operate in different repositories:

```bash
pact audit-init ./my-project --audit-dir ./my-project-audit
pact sync ./my-project          # Sync visible tests (never Goodhart) to code repo
pact certify ./my-project       # Tamper-evident certification proof
```

The coding agent cannot modify the tests that judge its work. The certification artifact includes SHA-256 hashes of all contracts, tests, and implementations with a self-integrity hash.
Certification also requires an emission compliance test for every contracted
component. Missing emission tests are reported as `missing_test: true` and make
the certification verdict fail instead of silently skipping the invariant.
An emission is the structured PACT event/log record that a component writes at
its contract boundary so an auditor can verify what happened without trusting
the implementation. An emission compliance test is the deterministic test that
asserts those event names and required keys are emitted for the component's
contract scenarios. Pact writes these tests next to visible tests as
`tests/<component>/emission_test.py` for Python projects, or the corresponding
language extension for TypeScript/JavaScript projects. For existing projects
created before v1.2.0, rerun the Pact planning/test-generation flow, run the
component's test-authoring step again, or add the missing `emission_test` file
for each contracted component before expecting `pact certify` to pass.

## Structured Event Emission

All implementations accept optional `event_handler` and `log_handler`. Every public method emits structured events:

```python
self._emit({
    "pact_key": "PACT:auth_module:validate_token",
    "event": "completed",
    "output_classification": ["PII"],
    "side_effects": ["database_read"],
    "ts": time.time_ns()
})
```

PACT keys are string literals (not computed) so Sentinel can discover them via static analysis. Emission compliance tests are auto-generated from the contract interface. See [PACT_KEY_STANDARD.md](PACT_KEY_STANDARD.md) for the canonical format specification.

## Health Monitoring

Pact monitors its own coordination health -- detecting the specific failure modes of agentic pipelines before they consume the budget.

| Metric | What It Detects | Active Phases |
|--------|-----------------|---------------|
| **Output/planning ratio** | Spending $50 on planning and shipping nothing | decompose+ |
| **Rejection rate** | Agents optimizing for each other's approval, not outcomes | all |
| **Budget velocity** | Coordination cost exceeding execution value | decompose+ |
| **Phase balance** | Any single phase consuming disproportionate budget | all |
| **Cascade detection** | One component's failure propagating through the tree | all |
| **Register drift** | Agent departing from established processing mode mid-task | implement+ |

Health checks are **phase-aware**: artifact-production metrics (output/planning ratio, budget velocity) only activate after the decompose phase begins. Interview and shape phases are planning-only by design -- applying artifact-production checks there would trigger false positives and block the pipeline.

```bash
pact health my-project
```

## Two Execution Levers

| Lever | Config Key | Effect |
|-------|-----------|--------|
| **Parallel Components** | `parallel_components: true` | Independent components implement concurrently |
| **Competitive Implementations** | `competitive_implementations: true` | N agents implement the SAME component; best wins |

Either, neither, or both. Defaults: both off (sequential, single-attempt).

## CLI Commands

| Command | Purpose |
|---------|---------|
| `pact init <project>` | Scaffold a new project |
| `pact run <project>` | Run through contracts/tests, then pause (default) |
| `pact run <project> --implement` | Run the full Pact-managed pipeline |
| `pact daemon <project>` | Event-driven mode (recommended) |
| `pact status <project>` | Show project or component status |
| `pact components <project>` | List components with status |
| `pact build <project> <id>` | Build/rebuild a specific component |
| `pact validate <project>` | Re-run contract validation |
| `pact audit <project>` | Spec-compliance audit |
| `pact certify <project>` | Run certification for a Pact-managed project |
| `pact audit-init <project>` | Initialize audit repo separation |
| `pact sync <project>` | Sync visible tests from audit repo |
| `pact sentinel status` | Show Sentinel/Arbiter connection config |
| `pact sentinel push-contract <id> <file>` | Accept tightened contract from Sentinel |
| `pact sentinel list-keys` | List all PACT keys in project |
| `pact health <project>` | Show health metrics and proposed remedies |
| `pact tasks <project>` | List phase tasks with status |
| `pact handoff <project> <id>` | Render/validate handoff brief |
| `pact review <target> --claim <text>` | Run Advocate + Simulacrum review |
| `pact-sim <claim>` | Run Pact's packaged Simulacrum directly |
| `pact production init <project>` | Scaffold optional production-readiness artifacts |
| `pact production status <project>` | Show production-readiness status without blocking |
| `pact production fingerprint <project>` | Print the current source fingerprint for production evidence |
| `pact production validate <project>` | Validate a Pact-managed production gate and exit non-zero on blockers |
| `pact spec apply <project> <file>` | Apply an AI-authored JSON/YAML build spec |
| `pact spec show <file>` | Normalize and inspect an AI-authored build spec |
| `pact agent spec-author [--apply]` | Run one constrained spec-author agent |
| `pact agent repair --source-root <dir> [--apply]` | Run one constrained repair agent |
| `pact adopt <project>` | Adopt existing codebase under pact governance |
| `pact assess <directory>` | Architectural assessment — shallow modules, hub dependencies, coupling |
| `pact mcp-server` | Run MCP server (stdio transport) |

Run flags: `--constrain-dir`, `--ledger-dir`, `--skip-arbiter`, `--plan-only`,
`--implement`. Use `--plan-only` to override a project configured for
implementation on a single invocation.

## Monitoring the Daemon

`monitor-pact.sh` is a shell script included in this repo that watches a running daemon, auto-handles every pause type, and prints a live status line every N seconds.

**Usage:**

```bash
# from the pact repo directory
bash monitor-pact.sh <project-dir> [interval-seconds]

# examples
bash monitor-pact.sh .                          # project in current dir, 30 s poll
bash monitor-pact.sh ../my-project 15           # 15 s poll
bash monitor-pact.sh /abs/path/to/project 60    # 60 s poll
```

**What it does automatically:**

| Situation | Action |
|-----------|--------|
| Interview questions pending | Runs `pact approve` |
| Health gate / dysmemic pressure | Runs `pact resume` |
| Daemon process died | Restarts daemon, then resumes |
| Silent hang (active but no audit progress for N min) | Kills and restarts daemon |
| Phase changes or every 5th poll | Prints `pact log` tail |
| During implement/integrate | Also prints `pact components` |
| Build complete / certified | Prints summary and exits 0 |
| Build failed | Prints last 20 log lines and exits 1 |

Override the hang timeout (default 10 min):

```bash
STUCK_TIMEOUT=300 bash monitor-pact.sh . 15   # restart after 5 min of silence
```

**Path resolution** (no hardcoded paths):

- **Pact binary** — looks for `.venv/bin/pact` next to the script (pact repo checkout), falls back to `pact` on `PATH`
- **API key** — reads `ANTHROPIC_API_KEY` from environment, then walks up to 4 parent directories searching for `.env`, then checks `~/.env`

**Sample output:**

```
────────────────────────────────────────────────────────────────
  pact monitor
  project:  /path/to/my-project
  pact:     /path/to/pact/.venv/bin/pact
  interval: 30s   (Ctrl+C to stop)
────────────────────────────────────────────────────────────────
[13:29:00]  daemon=running   state=active    phase=decompose      cost=$0.07    HEALTHY
[13:29:30]  daemon=running   state=active    phase=decompose      cost=$0.41    HEALTHY
  ┌─ pact log (last 6 entries) ─────────────────────────
  │ 13:27:50 daemon_dispatch — Phase: interview
  │ 13:29:00 daemon_dispatch — Phase: decompose
  └──────────────────────────────────────────────────────
[13:30:00]  daemon=running   state=paused    phase=interview      cost=$0.08    HEALTHY
[13:30:00] → INTERVIEW PAUSE — running: pact approve
[13:30:30]  daemon=running   state=active    phase=implement      cost=$1.24    HEALTHY
[13:31:00] → HEALTH GATE — running: pact resume
[13:35:00]  ✅  BUILD COMPLETE
```

## Configuration

**Per-project** (`pact.yaml` in project directory):

```yaml
budget: 25.00
plan_only: true
parallel_components: true
competitive_implementations: true

# Stack integration (all optional)
constrain_dir: ./constrain-output/
ledger_dir: ./ledger-export/
arbiter_endpoint: http://localhost:8080
skip_arbiter: false

# Audit repo separation
audit_dir: ../my-project-audit
audit_mode: code    # "audit" | "code" | ""

# Shaping
shaping: true
shaping_depth: standard

# Health thresholds
health_thresholds:
  output_planning_ratio_warning: 0.3
  rejection_rate_critical: 0.9

# Optional production-readiness artifact pack
production_profile: true
production_artifact_dir: production

# Up-front readiness profile
readiness:
  operational_maturity:
    level: standard
    controls: []
  security:
    level: standard
    controls: []
  privacy:
    level: basic
    controls: []
  compliance:
    level: none
    controls: []
  gating:
    level: standard
    controls: []
  testing:
    level: standard
    controls: []
  monitoring:
    level: basic
    controls: []
  notes: ""
```

### AI Build Spec

`build_spec.yaml` is the handoff format for an AI-authored build request. It
can carry the task, SOPs, readiness profile, and project config in one file:

```yaml
version: "1"
task: |
  Build a tenant-scoped booking API.
sops: |
  Use Python 3.12, pytest, and strict typing.
readiness:
  security: strict
  privacy: standard
  compliance: basic
config:
  build_mode: hierarchy
  budget: 25
```

The readiness levels are `none`, `basic`, `standard`, `strict`, and
`regulated`. They resolve to concrete baseline controls rather than acting as
mere labels. Custom `controls` can be added per dimension.
`build_spec.yaml` is a tracked planning artifact, so do not put secrets,
tokens, credentials, or machine-local paths in it.
If the task scope materially changes after interview, update the spec or
`pact.yaml` and rerun interview before regenerating contracts.

### Production-Readiness Pack

The pack is a first-class observer over Pact's existing artifact model, not a
replacement pipeline. `production/` remains user-edited, git-trackable source
of truth. Pact validates it against existing contracts, tests, analysis,
checklist, review, certification, and source-tree state.
Set `production_artifact_dir` to use a different tracked artifact directory;
the generated `constrain_dir` follows that setting when it was not already set.
The production manifest also carries the project readiness profile, so the
production pack cannot silently drift away from `pact.yaml`.

The generated files are:

```text
production/
  manifest.yaml
  prompt.md
  constraints.yaml
  component_map.yaml
  trust_policy.yaml
  control_matrix.yaml
  threat_model.yaml
  architecture_laws.yaml
  preflight.yaml
  live_validation.yaml
  done_gate.yaml
  na_register.yaml
  build_charter.md
  reports/
```

Derived gate items are satisfied only from Pact-produced evidence such as
contracts, contract tests, Goodhart tests, analysis, checklist, review,
certification, stub scan results, and deterministic static checks. The static
layer borrows webprobe's useful non-live audit discipline: typed pass/fail/not
detected results, artifact-backed evidence, and no hidden fallback from missing
artifacts to false failures. It currently checks likely hard-coded secrets,
dependency manifest presence, SBOM presence, and OpenAPI validity/auth/error/
rate-limit shape. External items require explicit evidence. Not-applicable
items across the pack require a matching `na_register.yaml` record with a
justification and review reference. `manifest.yaml` also carries a source
fingerprint and bounded validation age, so stale evidence is rejected after the
project snapshot changes or the evidence ages out.
This is a deployment gate, not a runtime substitute: `live_validation.yaml`
still has to prove the running environment matches the evidence being claimed.

### Multi-Provider Configuration

Route different roles to different providers for cost optimization:

```yaml
role_models:
  decomposer: claude-opus-4-6
  contract_author: claude-opus-4-6
  test_author: claude-sonnet-4-5-20250929
  code_author: gpt-4o

role_backends:
  decomposer: anthropic
  code_author: codex_code
```

Available backends: `anthropic`, `openai`, `gemini`, `claude_code`,
`claude_code_team`, `codex_code`, `codex_code_team`. When a Codex backend is
selected without a Codex-specific model override, Pact inherits the model from
the installed Codex configuration.

For an entirely Codex-driven Pact planning run:

```yaml
role_backends:
  decomposer: codex_code
  contract_author: codex_code
  test_author: codex_code
  code_author: codex_code
  trace_analyst: codex_code
```

No model override is required; `codex_code` uses the installed Codex default
unless a Codex model is explicitly configured.

## Agent Review Workflow

`pact review` treats Advocate and Simulacrum as independent review tools and
persists their output under `.pact/reviews/`:

```bash
pact review . --claim "This change is done because all contract tests pass."
```

Advocate reviews the implementation. Simulacrum stress-tests the architecture
or done claim. Requested tool failures and Advocate critical/high findings make
the command exit non-zero. Simulacrum completion is not machine-readable
approval; the active agent must adjudicate its response, fix the work, and
rerun the gate.

Run a lean review as a separate pass from correctness and security review. The
lean pass asks what can be deleted, replaced with stdlib/native behavior, or
collapsed without weakening the recovery, audit, safety, or live-validation
surface. Use `skills/pact-engineer/references/lean-review.md` as the checklist;
do not treat a shorter diff as evidence that the system is safer.

Pact ships Simulacrum's MIT-licensed runtime and annotated corpus. It does not
search user home directories for another installation. Install review support
with `pip install 'pact-agents[review]'` and set `ANTHROPIC_API_KEY` or
`PACT_REVIEW_ANTHROPIC_API_KEY`. `PACT_SIMULACRUM_CMD` is an explicit override
for operators developing or replacing the packaged runtime. The packaged
runtime is also available directly as `pact-sim "<claim>"`. Provider calls use
the local operator's credentials directly; Pact does not proxy calls, share a
publisher key, or route them through Jeremy's infrastructure.

Review recovery is explicit:

- `unavailable` means install the named optional tool or rerun with
  `--advocate-only` / `--sim-only`.
- `failed` with credential/setup guidance means repair the local toolchain.
- `failed` with Advocate blockers means fix or explicitly adjudicate findings.
- Simulacrum `completed` means read and adjudicate `simulacrum.md`; it is not
  approval.

Advocate auto-selects a provider from standard `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, or Gemini credentials. Override it with `--provider` or
`PACT_ADVOCATE_PROVIDER`. Per-process key aliases are available as
`PACT_REVIEW_ANTHROPIC_API_KEY` and `PACT_REVIEW_OPENAI_API_KEY`; Pact does not
mutate caller exports.

## Project Structure

```
my-project/
  task.md              # What to build
  sops.md              # How to build it
  pact.yaml            # Budget and config
  access_graph.json    # Data access graph (consumed by Arbiter)
  decomposition/       # Decomposition tree, decisions, interview
  contracts/<cid>/     # Interface specs with data_access + authority
  src/<cid>/           # Implementation source + glue code
  tests/<cid>/         # Contract tests + Goodhart tests
  production/          # Optional production-readiness pack
  certification/       # Tamper-evident certification proof
  .pact/               # Ephemeral run state (gitignored)
```

## MCP Server

```bash
pip install pact-agents[mcp]
pact-mcp
```

MCP tools work with Claude Code and other stdio MCP-compatible clients:
status, contracts, budget, validate, and resume.

## Codebase Analysis (Tool Index)

When adopting or analyzing a codebase, Pact can leverage external tools for richer understanding. All tools are optional -- if not installed, they're silently skipped.

| Tool | What It Provides | Install |
|------|-----------------|---------|
| **universal-ctags** | Symbol index (functions, classes, members, scope, signatures) | `brew install universal-ctags` |
| **cscope** | Cross-references and call graph (best for C/C++) | `brew install cscope` |
| **tree-sitter** | Full CST, error-tolerant parsing, cross-language (preferred for Python/TS/JS) | `pip install pact-agents[analysis]` |
| **kindex** | Existing project knowledge from the knowledge graph | [kindex](https://github.com/wandercom/kindex) |

Tree-sitter is preferred over cscope for Python, TypeScript, and JavaScript codebases. Both `pact adopt` and green-field workflows benefit -- agents receive enriched context about symbol hierarchies, class structure, and existing project knowledge.

```yaml
# pact.yaml
tool_index_enabled: true  # true | false | null (auto-detect)
```

### Kindex Work Contract

Kindex is an optional knowledge graph for carrying agent decisions, tasks, and
project context across sessions. When Kindex is available, workers should start
or resume a tag, search before adding knowledge, use `task_add` /
`task_list` / `task_done` for durable work, and prefer `edit` or `supersede`
over duplicate nodes. Pact's own post-run publishing uses durable Kindex task
nodes rather than concept-only stand-ins. Kindex remains optional: Pact keeps
its own run state under `.pact/` and still runs when Kindex is unavailable.

This closes three concrete failure modes: task-shaped concepts were not durable
Kindex tasks, noninteractive `pact init` could block or EOF on the indexing
prompt, and an approved interview could remain paused until a daemon-specific
recovery path intervened. Existing automation should set `auto_index: true` or
`auto_index: false` in `.kin/config`; `PACT_INTERACTIVE=true|false` overrides
TTY detection when a shell's interactivity is ambiguous.

Tracked `.kin/` files are shipped project state:

- `.kin/config` contains project voice, domains, and work policy.
- `.kin/index.json` contains the tracked graph snapshot.
- `.kin/code-map.json` contains the repo-relative code map generated by
  `kin export code-map`.
- `.kin/.gitignore` ignores only local/private runtime state.

Do not commit machine-local paths, local-only report pointers, secrets, or
private scratch data into tracked `.kin` artifacts. Regenerate tracked
snapshots from source rather than hand-editing generated JSON. In
noninteractive shells, `pact init` skips the Kindex indexing prompt unless
`.kin/config` explicitly sets `auto_index: true`.

For an existing repository, refresh tracked Kindex artifacts with:

```bash
kin ingest code --directory . --project-path .
kin index --project-path . --output-dir .
kin export code-map --directory . --project-path . --output .kin/code-map.json
```

## Architectural Assessment

`pact assess` performs mechanical codebase analysis for structural friction -- no LLM, no project setup required. Point it at any Python source directory.

```bash
pact assess src/myproject/                    # Markdown report
pact assess src/myproject/ --json             # Machine-readable output
pact assess src/ --threshold hub_fan_in_warning=12  # Custom thresholds
```

Detects five categories of architectural friction:

| Category | What It Flags | Severity |
|----------|--------------|----------|
| **Hub dependency** | Modules with high fan-in (many dependents) | warning/error |
| **Shallow module** | Interface complexity rivals implementation complexity | warning |
| **Tight coupling** | Mutual imports, circular dependency clusters (SCCs) | warning/error |
| **Scattered logic** | Same intra-project import appearing in many files | info/warning |
| **Test gap** | Source modules with no corresponding test file | info |

Uses Python `ast` for parsing and Tarjan's algorithm for strongly connected component detection. Configurable thresholds via `--threshold KEY=VALUE`. Output includes per-module metrics (LOC, public interface size, depth ratio, fan-in/fan-out).

## Development

```bash
make dev          # Install with LLM backend support
make test         # Run full test suite (2073 tests)
make test-quick   # Stop on first failure
```

Requires Python 3.12+. Core dependencies: `pydantic` and `pyyaml`.

## Architecture

See [CLAUDE.md](CLAUDE.md) for the full technical reference. See [CAPABILITIES.md](CAPABILITIES.md) for the AI-friendly capability inventory and decision guide.

## Background

Pact is one of three systems (alongside Emergence and Apprentice) built to test
the ideas in [Beyond Code: Context, Constraints, and the New Craft of Software](https://www.amazon.com/dp/B0GNLTXVC7).

## Related

- [Baton](https://wandercom.github.io/baton/) -- Circuit orchestration for contract-first components
- [Sentinel](https://github.com/wandercom/sentinel) -- Production attribution and contract tightening

## License

MIT
