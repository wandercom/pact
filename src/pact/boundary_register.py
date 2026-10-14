"""Boundary register: cross-boundary identifiers backed by verifiable records.

A boundary identifier is any literal two parties must agree on for a call
across a boundary to work: a service name, an audience, a database role, a
table, a metric resource type, a log field, a header, a grant. When one side
writes ``"level"`` and the other reads ``"severity"``, or a gateway queries a
table its role was never granted, nothing inside either component is wrong;
the defect lives on the boundary. Pact refuses to assume such values.

The register (format ``boundary-register/1``, shared with the factory and
Constrain) names every such identifier once, states which party defines,
grants, consumes, presents, or denies it, and pins each side to a record the
gate can re-verify mechanically: a file line or a captured observation, both
bound by SHA-256 of the file bytes. No LLM participates in verification.

Invariants upheld here:

- Every side cites at least one record; an unverified side is an error.
- A record is verified against the bytes on disk. A digest mismatch is a stale
  record, reported by file name: refresh the register from the source; never
  hand-edit it to fit.
- ``relation: equal`` requires one authority side (defines|grants) and one
  user side (consumes|presents), with byte-identical values.
- ``relation: absent`` proves isolation: the subject value must NOT occur as
  a token in any denies-side record.
- Placeholder values are refused; a value nobody can cite is an open question,
  not a register entry.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_ID = "boundary-register/1"
REGISTER_FILENAME = "boundary_register.yaml"

ENTRY_KINDS = frozenset({
    "name", "endpoint", "credential", "audience", "principal", "grant", "role",
    "table", "column", "field", "metric", "label", "env", "config-key", "topic",
    "queue", "bucket", "secret", "service", "header", "claim", "enum", "other",
})
RELATIONS = frozenset({"equal", "absent"})
SIDE_ROLES = frozenset({"defines", "grants", "consumes", "presents", "denies"})
AUTHORITY_ROLES = frozenset({"defines", "grants"})
USER_ROLES = frozenset({"consumes", "presents"})
RECORD_KINDS = frozenset({"file", "observation"})

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)
EXTERNAL_PREFIX = "external:"

# A value nobody can cite. "unknown"/"assumed"/"placeholder" are refused as the
# whole value; TODO/TBD/FIXME as a word anywhere; <...> and ??? anywhere.
_PLACEHOLDER_WHOLE_RE = re.compile(
    r"^\s*(?:unknown|assumed|placeholder|n/?a|tbd|todo|fixme|\?+|\.\.\.)\s*$",
    re.IGNORECASE,
)
_PLACEHOLDER_PART_RE = re.compile(r"(?:\b(?:TODO|TBD|FIXME)\b|<[^<>]*>|\?\?\?)", re.IGNORECASE)

_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_SEPARATORS_RE = re.compile(r"[-_.\s]")
MAX_RECORD_BYTES = 16 * 1024 * 1024


# ── Schema (lenient models; the validator owns semantics) ─────────────
#
# Contracts embed entries under ``boundary_identifiers``. The models accept
# loosely-typed data so a malformed LLM entry never makes the whole contract
# unloadable; ``verify_entries`` then reports every defect as an error.


class BoundaryRecord(BaseModel):
    """Evidence that a side's value is what the source says it is."""
    model_config = ConfigDict(extra="allow")
    kind: str = ""
    path: str = ""
    line: int | str | None = None
    sha256: str = ""
    command: str = ""
    observed_at: str = ""


class BoundarySide(BaseModel):
    """One party's view of the identifier, with its records."""
    model_config = ConfigDict(extra="allow")
    party: str = ""
    role: str = ""
    value: str = ""
    records: list[BoundaryRecord] = []


class BoundaryEntry(BaseModel):
    """One identifier that must agree (or must be absent) across a boundary."""
    model_config = ConfigDict(extra="allow")
    id: str = ""
    kind: str = ""
    relation: str = ""
    description: str = ""
    sides: list[BoundarySide] = []


class BoundaryRegister(BaseModel):
    """The project-level register file."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    schema_id: str = Field(default=SCHEMA_ID, alias="schema")
    entries: list[BoundaryEntry] = []


# ── Matching helpers ──────────────────────────────────────────────────


def contains_token(haystack: str, value: str) -> bool:
    """True if ``value`` occurs in ``haystack`` as a token.

    A token occurrence is a substring occurrence whose neighbours are not
    identifier characters ([A-Za-z0-9_-]) on whichever end of ``value`` is
    itself an identifier character. So ``mea-auth`` does not match inside
    ``mea-auth-runtime``, while ``level`` matches inside ``"level":``.
    """
    if not value:
        return False
    check_start = value[0] in _TOKEN_CHARS
    check_end = value[-1] in _TOKEN_CHARS
    start = haystack.find(value)
    while start != -1:
        end = start + len(value)
        ok_start = not check_start or start == 0 or haystack[start - 1] not in _TOKEN_CHARS
        ok_end = not check_end or end == len(haystack) or haystack[end] not in _TOKEN_CHARS
        if ok_start and ok_end:
            return True
        start = haystack.find(value, start + 1)
    return False


def is_placeholder(value: str) -> bool:
    """True if ``value`` is a stand-in rather than a cited literal."""
    return bool(_PLACEHOLDER_WHOLE_RE.match(value) or _PLACEHOLDER_PART_RE.search(value))


def near_miss(a: str, b: str) -> str:
    """Describe how two differing values nearly match, or return ``""``."""
    if a == b:
        return ""
    if a.lower() == b.lower():
        return "differ only by case"
    sa, sb = _SEPARATORS_RE.sub("", a), _SEPARATORS_RE.sub("", b)
    if sa == sb:
        return "differ only by separators (-, _, ., space)"
    if sa.lower() == sb.lower():
        return "differ only by case and separators (-, _, ., space)"
    return ""


def is_external_party(party: str) -> bool:
    return party.startswith(EXTERNAL_PREFIX) and len(party) > len(EXTERNAL_PREFIX)


# ── Loading ───────────────────────────────────────────────────────────


def register_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / REGISTER_FILENAME


def load_register(project_dir: str | Path) -> tuple[list[dict], list[str]]:
    """Load ``boundary_register.yaml`` from the project root.

    Returns ``(entries, errors)``. A missing file is not an error here (the
    gate decides whether a register is required); an unreadable or malformed
    file is.
    """
    path = register_path(project_dir)
    if not path.exists():
        return [], []
    label = REGISTER_FILENAME
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [], [f"{label}: could not be read as YAML: {exc}"]
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return [], [f"{label}: top level must be a mapping with 'schema' and 'entries'"]
    errors: list[str] = []
    schema = data.get("schema")
    if schema != SCHEMA_ID:
        errors.append(f"{label}: schema must be '{SCHEMA_ID}' (got {schema!r})")
    entries = data.get("entries", [])
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        return [], errors + [f"{label}: 'entries' must be a list"]
    return entries, errors


def load_register_entries(project_dir: str | Path | None) -> list[BoundaryEntry]:
    """Best-effort typed load for rendering. Never raises; the gate reports defects."""
    if project_dir is None:
        return []
    raw, _ = load_register(project_dir)
    out: list[BoundaryEntry] = []
    for item in raw:
        if isinstance(item, dict):
            try:
                out.append(BoundaryEntry.model_validate(item))
            except Exception:
                continue
    return out


def _as_dict(entry: Any) -> Any:
    if isinstance(entry, BaseModel):
        return entry.model_dump(by_alias=True, exclude_none=True)
    return entry


# ── Verification ──────────────────────────────────────────────────────


class _FileCache:
    """Reads each cited file once per verification run."""

    def __init__(self, root: Path | None) -> None:
        self.root = root.resolve() if root is not None else None
        self._cache: dict[str, tuple[bytes | None, str]] = {}

    def read(self, rel: str) -> tuple[bytes | None, str]:
        """Return ``(bytes, "")`` or ``(None, reason)``."""
        if rel in self._cache:
            return self._cache[rel]
        result = self._read(rel)
        self._cache[rel] = result
        return result

    def _read(self, rel: str) -> tuple[bytes | None, str]:
        if self.root is None:
            return None, "no project root is available to verify records against"
        if not isinstance(rel, str) or not rel.strip():
            return None, "record path is empty"
        posix, win = PurePosixPath(rel), PureWindowsPath(rel)
        if posix.is_absolute() or win.is_absolute() or win.drive or rel.startswith("~"):
            return None, f"record path '{rel}' must be relative to the project root"
        if ".." in posix.parts or ".." in win.parts:
            return None, f"record path '{rel}' must not contain '..'"
        candidate = self.root / rel
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None, f"record file '{rel}' does not exist"
        if resolved != self.root and self.root not in resolved.parents:
            return None, f"record path '{rel}' resolves outside the project root"
        if not resolved.is_file():
            return None, f"record path '{rel}' is not a regular file"
        try:
            with resolved.open("rb") as fh:
                data = fh.read(MAX_RECORD_BYTES + 1)
        except OSError as exc:
            return None, f"record file '{rel}' could not be read: {exc}"
        if len(data) > MAX_RECORD_BYTES:
            return None, f"record file '{rel}' exceeds {MAX_RECORD_BYTES // (1024 * 1024)} MiB"
        return data, ""


def _verify_record(
    rec: Any,
    *,
    where: str,
    value: str,
    files: _FileCache,
    mode: str,
) -> list[str]:
    """Verify one record.

    ``mode`` is ``"present"`` (the side's value must occur as a token at the
    cited place) or ``"absent"`` (``value`` is the subject and must NOT occur
    anywhere in the record's bytes).
    """
    if not isinstance(rec, dict):
        return [f"{where}: record must be a mapping"]
    errors: list[str] = []
    kind = rec.get("kind")
    if kind not in RECORD_KINDS:
        return [f"{where}: record kind must be one of {sorted(RECORD_KINDS)} (got {kind!r})"]
    path = rec.get("path")
    sha = rec.get("sha256")
    if not isinstance(sha, str) or not SHA256_RE.match(sha):
        errors.append(f"{where}: sha256 must be 64 hex characters (got {sha!r})")

    line_no: int | None = None
    if kind == "file":
        line = rec.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            errors.append(f"{where}: file record needs 'line' as a 1-based integer (got {line!r})")
        else:
            line_no = line
    else:
        command = rec.get("command")
        if not isinstance(command, str) or not command.strip():
            errors.append(f"{where}: observation record needs the exact 'command' that produced it")
        observed_at = rec.get("observed_at")
        if not _valid_rfc3339(observed_at):
            errors.append(
                f"{where}: observation record needs 'observed_at' as an RFC3339 timestamp "
                f"(got {observed_at!r})"
            )

    data, reason = files.read(path if isinstance(path, str) else "")
    if data is None:
        errors.append(f"{where}: {reason}")
        return errors

    if isinstance(sha, str) and SHA256_RE.match(sha):
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha.lower():
            errors.append(
                f"{where}: STALE record for file '{path}': sha256 on disk is {actual}, "
                f"register says {sha.lower()}. Refresh the register from the source; "
                f"never hand-edit it to fit"
            )
            return errors
    else:
        return errors

    text = data.decode("utf-8", errors="replace")
    if mode == "absent":
        if contains_token(text, value):
            errors.append(
                f"{where}: isolation violated: subject {value!r} occurs as a token in "
                f"'{path}', which this side cites as proof of absence"
            )
        if kind == "file" and line_no is not None:
            count = len(text.split("\n"))
            if line_no > count:
                errors.append(f"{where}: line {line_no} is beyond the end of '{path}' ({count} lines)")
        return errors

    if kind == "file":
        if line_no is None:
            return errors
        lines = text.split("\n")
        if line_no > len(lines):
            errors.append(f"{where}: line {line_no} is beyond the end of '{path}' ({len(lines)} lines)")
            return errors
        cited = lines[line_no - 1].rstrip("\r")
        if not contains_token(cited, value):
            errors.append(
                f"{where}: '{path}' line {line_no} does not contain {value!r} as a token "
                f"(line reads: {cited.strip()[:160]!r})"
            )
    else:
        if not contains_token(text, value):
            errors.append(f"{where}: observation '{path}' does not contain {value!r} as a token")
    return errors


def _valid_rfc3339(value: Any) -> bool:
    if not isinstance(value, str) or not RFC3339_RE.match(value):
        return False
    normalized = value.replace("z", "Z").replace("Z", "+00:00").replace(" ", "T").replace("t", "T")
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


def verify_entries(
    entries: list[Any],
    project_root: str | Path | None,
    *,
    known_components: set[str] | None = None,
    origin: str = "",
) -> list[str]:
    """Verify register entries. Returns errors (empty list = every entry holds).

    Each error names the entry id, the side (index, party, role), and the
    reason. ``known_components`` (when given) rejects non-external parties
    that are not components of the project: a misspelled party is itself a
    boundary-name mismatch.
    """
    files = _FileCache(Path(project_root) if project_root is not None else None)
    errors: list[str] = []
    seen: dict[str, str] = {}
    prefix = f"{origin}: " if origin else ""

    for index, raw_entry in enumerate(entries):
        entry = _as_dict(raw_entry)
        if not isinstance(entry, dict):
            errors.append(f"{prefix}boundary entry #{index}: must be a mapping")
            continue
        eid = entry.get("id")
        label = f"{prefix}boundary[{eid if isinstance(eid, str) and eid else f'#{index}'}]"
        if not isinstance(eid, str) or not ID_RE.match(eid):
            errors.append(f"{label}: id must match [A-Za-z0-9][A-Za-z0-9._-]* (got {eid!r})")
        elif eid in seen:
            errors.append(f"{label}: duplicate id (also defined in {seen[eid]})")
        else:
            seen[eid] = origin or "register"

        kind = entry.get("kind")
        if kind not in ENTRY_KINDS:
            errors.append(f"{label}: kind must be one of {sorted(ENTRY_KINDS)} (got {kind!r})")
        relation = entry.get("relation")
        if relation not in RELATIONS:
            errors.append(f"{label}: relation must be 'equal' or 'absent' (got {relation!r})")
        description = entry.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{label}: description must say why the sides must agree")

        sides = entry.get("sides")
        if not isinstance(sides, list) or not sides:
            errors.append(f"{label}: needs at least one side")
            continue

        well_formed: list[tuple[int, dict, str]] = []  # (index, side, side label)
        for s_index, side in enumerate(sides):
            if not isinstance(side, dict):
                errors.append(f"{label} side[{s_index}]: must be a mapping")
                continue
            party = side.get("party")
            role = side.get("role")
            value = side.get("value")
            s_label = f"{label} side[{s_index} {party}/{role}]"
            ok = True
            if not isinstance(party, str) or not party.strip():
                errors.append(f"{s_label}: party is required (component id or 'external:<system>')")
                ok = False
            elif party.startswith(EXTERNAL_PREFIX):
                if not is_external_party(party):
                    errors.append(f"{s_label}: external party must name the system ('external:<system>')")
                    ok = False
            elif known_components is not None and party not in known_components:
                errors.append(
                    f"{s_label}: party {party!r} is not a component of this project "
                    f"(known: {sorted(known_components)}); use 'external:<system>' for outside systems"
                )
            if role not in SIDE_ROLES:
                errors.append(f"{s_label}: role must be one of {sorted(SIDE_ROLES)} (got {role!r})")
                ok = False
            if not isinstance(value, str) or not value:
                errors.append(f"{s_label}: value must be the exact literal (non-empty string)")
                ok = False
            elif is_placeholder(value):
                errors.append(
                    f"{s_label}: value {value!r} is a placeholder. Never assume an identifier: "
                    f"cite the record, or raise an open question"
                )
                ok = False
            records = side.get("records")
            if not isinstance(records, list) or not records:
                errors.append(
                    f"{s_label}: UNVERIFIED: no records. Every side must cite at least one "
                    f"file or observation record"
                )
                ok = False
            if ok:
                well_formed.append((s_index, side, s_label))

        if relation == "equal":
            errors.extend(_check_equal(label, sides, well_formed, files))
        elif relation == "absent":
            errors.extend(_check_absent(label, sides, well_formed, files))

    return errors


def _roles(sides: list) -> list[str]:
    return [s.get("role") for s in sides if isinstance(s, dict)]


def _check_equal(label: str, sides: list, well_formed: list, files: _FileCache) -> list[str]:
    errors: list[str] = []
    roles = _roles(sides)
    if "denies" in roles:
        errors.append(f"{label}: role 'denies' is only valid with relation 'absent'")
    if not any(r in AUTHORITY_ROLES for r in roles):
        errors.append(f"{label}: relation 'equal' needs a side with role defines or grants")
    if not any(r in USER_ROLES for r in roles):
        errors.append(f"{label}: relation 'equal' needs a side with role consumes or presents")

    values = [
        (s.get("party"), s.get("role"), s.get("value"))
        for s in sides
        if isinstance(s, dict) and isinstance(s.get("value"), str) and s.get("value")
    ]
    distinct = sorted({v for _, _, v in values})
    if len(distinct) > 1:
        detail = "; ".join(f"{p}/{r}={v!r}" for p, r, v in values)
        msg = f"{label}: MISMATCH: sides disagree ({detail})"
        hints = {near_miss(distinct[0], other) for other in distinct[1:]}
        hints.discard("")
        if hints:
            msg += f". near-miss: values {', '.join(sorted(hints))}"
        errors.append(msg)

    for s_index, side, s_label in well_formed:
        for r_index, rec in enumerate(side["records"]):
            errors.extend(_verify_record(
                rec, where=f"{s_label} record[{r_index}]",
                value=side["value"], files=files, mode="present",
            ))
    return errors


def _check_absent(label: str, sides: list, well_formed: list, files: _FileCache) -> list[str]:
    errors: list[str] = []
    roles = _roles(sides)
    presents = [s for s in sides if isinstance(s, dict) and s.get("role") == "presents"]
    if len(presents) != 1:
        errors.append(
            f"{label}: relation 'absent' needs exactly one 'presents' side (the subject); "
            f"found {len(presents)}"
        )
    if "denies" not in roles:
        errors.append(f"{label}: relation 'absent' needs at least one 'denies' side")
    others = sorted({r for r in roles if r not in {"presents", "denies"} and isinstance(r, str)})
    if others:
        errors.append(f"{label}: relation 'absent' allows only presents and denies sides (got {others})")

    subject = presents[0].get("value") if len(presents) == 1 else None
    for s_index, side, s_label in well_formed:
        role = side.get("role")
        for r_index, rec in enumerate(side["records"]):
            where = f"{s_label} record[{r_index}]"
            if role == "presents":
                errors.extend(_verify_record(
                    rec, where=where, value=side["value"], files=files, mode="present",
                ))
            elif role == "denies" and isinstance(subject, str) and subject:
                errors.extend(_verify_record(
                    rec, where=where, value=subject, files=files, mode="absent",
                ))
    return errors


# ── Producing records (pact boundary cite) ───────────────────────────


class CiteError(ValueError):
    """The literal is not where the record would claim; no record is produced."""


def cite_record(
    project_root: str | Path,
    *,
    kind: str,
    path: str,
    value: str,
    line: int | None = None,
    command: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Produce a record from the source so nobody types a digest or line.

    Reads ``path`` under ``project_root`` with the verifier's path safety,
    hashes the whole file, and confirms ``value`` is where the record will
    claim: on the given line, or (``line`` omitted) on the first line that
    contains it as a token; for observations, anywhere in the file. The
    returned record round-trips through ``verify_entries``. Raises
    ``CiteError`` instead of producing a record that would not verify.
    """
    if kind not in RECORD_KINDS:
        raise CiteError(f"kind must be one of {sorted(RECORD_KINDS)} (got {kind!r})")
    if not isinstance(value, str) or not value:
        raise CiteError("value must be the exact non-empty literal")
    if is_placeholder(value):
        raise CiteError(f"value {value!r} is a placeholder; cite the real literal")
    if kind == "observation" and (not command or not command.strip()):
        raise CiteError("observation records need --command: the exact command or query that produced the file")

    data, reason = _FileCache(Path(project_root)).read(path)
    if data is None:
        raise CiteError(reason)
    digest = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8", errors="replace")

    if kind == "file":
        lines = text.split("\n")
        if line is None:
            for number, content in enumerate(lines, start=1):
                if contains_token(content.rstrip("\r"), value):
                    line = number
                    break
            else:
                raise CiteError(f"{value!r} does not occur as a token on any line of '{path}'")
        else:
            if line < 1 or line > len(lines):
                raise CiteError(f"line {line} is outside '{path}' ({len(lines)} lines)")
            if not contains_token(lines[line - 1].rstrip("\r"), value):
                raise CiteError(f"'{path}' line {line} does not contain {value!r} as a token")
        return {"kind": "file", "path": path, "line": line, "sha256": digest}

    if not contains_token(text, value):
        raise CiteError(f"observation '{path}' does not contain {value!r} as a token")
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "kind": "observation",
        "path": path,
        "sha256": digest,
        "command": command.strip(),
        "observed_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── Rendering for implementers ────────────────────────────────────────


def entries_for_party(entries: list[Any], party: str) -> list[BoundaryEntry]:
    out: list[BoundaryEntry] = []
    for raw in entries:
        try:
            entry = raw if isinstance(raw, BoundaryEntry) else BoundaryEntry.model_validate(raw)
        except Exception:
            continue
        if any(side.party == party for side in entry.sides):
            out.append(entry)
    return out


def render_boundary_identifiers(component_id: str, entries: list[Any]) -> str:
    """Render the registered literals a component must use, with their records."""
    mine = entries_for_party(entries, component_id)
    if not mine:
        return ""
    lines = [
        f"# Boundary identifiers for: {component_id}",
        "# Registered and verified against records. Use these exact literals;",
        "# never rename, re-case, or guess. If one looks wrong, stop and raise it.",
    ]
    for entry in mine:
        lines.append(f"- {entry.id} ({entry.kind}, {entry.relation}): {entry.description}")
        for side in entry.sides:
            cite = ""
            if side.records:
                rec = side.records[0]
                cite = f"{rec.path}:{rec.line}" if rec.kind == "file" else f"{rec.path} [observed]"
            marker = " <- you" if side.party == component_id else ""
            lines.append(f"    {side.party} {side.role}: {side.value!r}  ({cite}){marker}")
    return "\n".join(lines)


# ── Prompt rule (one authority for the wording every author sees) ─────

BOUNDARY_RULE_PROMPT = """Cross-boundary identifiers: any literal another party must agree on
(service name, URL/endpoint, audience, credential/secret name, principal, IAM
grant or role, table/column, log/metric field or label, metric resource type,
env var, config key, topic/queue/bucket, header, claim, enum value) is a
boundary identifier. Never invent or assume one. Record each in the boundary
register (boundary_register.yaml, format boundary-register/1, or the contract's
boundary_identifiers) with: id, kind, relation (equal, or absent for isolation),
description, and one side per party (party = component id or external:<system>;
role = defines|grants|consumes|presents|denies; value = the exact literal),
each side citing a record (kind file: path, line, sha256; or kind observation:
path, sha256, command, observed_at) that proves the value. Records are produced
by `pact boundary cite --kind file|observation --path <rel> --value <literal>
[--line N] [--command "<cmd>"]`, never typed: never write a digest or line
number by hand. If you cannot cite a
record for a value, do not guess and do not write a placeholder: raise it as an
open question for the owner of that system."""


def render_register_for_prompt(entries: list[Any]) -> str:
    """Render the full register (with records) so authors cite, never retype.

    The YAML here is the source of truth for values and digests: an author that
    needs an existing entry copies it verbatim rather than re-deriving it.
    """
    dumped = []
    for raw in entries:
        entry = raw if isinstance(raw, BoundaryEntry) else None
        if entry is None:
            try:
                entry = BoundaryEntry.model_validate(raw)
            except Exception:
                continue
        dumped.append(entry.model_dump(exclude_none=True, exclude_defaults=True))
    if not dumped:
        return ""
    body = yaml.safe_dump({"schema": SCHEMA_ID, "entries": dumped}, sort_keys=False)
    return (
        "Registered boundary identifiers (verified against records; reuse these "
        "exact values and records verbatim):\n```yaml\n" + body + "```"
    )
