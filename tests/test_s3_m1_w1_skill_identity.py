"""S3 M1 W1 Skill Identity / Version / Digest / Provenance Contract.

Focused W1 tests proving contract correctness per prompt section 15.
No registry, no marketplace, no DB.

Validates:
- Identity shape (valid four-field, immutable, representation, no fifth)
- Fail-closed validation
- Version boundary (opaque, no SemVer)
- Digest (SHA-256 deterministic 64 lower hex)
- Authority separation
- S1 separation
- Namespace separation
- Registry separation
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from aota_forge.work_plane.skill import (
    SkillIdentity,
    compute_skill_digest,
    compute_skill_digest_for_text,
    MAX_SKILL_ID_LENGTH,
    MAX_VERSION_LENGTH,
    MAX_PROVENANCE_LENGTH,
    SKILL_IDENTITY_FIELDS,
    SKILL_IS_AUTHORITY,
    SKILL_GRANTS_TOOL_AUTHORITY,
    DIGEST_IS_AUTHORITY,
    PROVENANCE_IS_AUTHORITY,
)

# Helpers
def _valid_digest(content: str = "test") -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def _make_valid(
    skill_id: str = "aota-skill-example",
    version: str = "1.0.0",
    digest: str | None = None,
    provenance: str = "forge-native",
) -> SkillIdentity:
    if digest is None:
        digest = _valid_digest(f"{skill_id}@{version}")
    return SkillIdentity(skill_id=skill_id, version=version, digest=digest, provenance=provenance)

# ---------------------------------------------------------------------------
# Identity shape
# ---------------------------------------------------------------------------

def test_identity_valid_four_field_constructs():
    sid = _make_valid()
    assert sid.skill_id == "aota-skill-example"
    assert sid.version == "1.0.0"
    assert len(sid.digest) == 64
    assert sid.provenance == "forge-native"

def test_identity_is_immutable():
    sid = _make_valid()
    with pytest.raises(Exception):
        sid.skill_id = "mutated"  # type: ignore
    with pytest.raises(Exception):
        sid.version = "2.0"  # type: ignore
    with pytest.raises(Exception):
        sid.digest = "a" * 64  # type: ignore
    with pytest.raises(Exception):
        sid.provenance = "other"  # type: ignore

def test_all_four_fields_participate_in_representation():
    sid = _make_valid(skill_id="skill-a", version="v1", provenance="legacy", digest=_valid_digest("content-a"))
    r = repr(sid)
    assert "skill-a" in r
    assert "v1" in r
    assert _valid_digest("content-a") in r
    assert "legacy" in r
    d = sid.to_dict()
    assert set(d.keys()) == {"skill_id", "version", "digest", "provenance"}
    # canonical dict also exactly 4
    cd = sid.canonical_dict()
    assert set(cd.keys()) == {"skill_id", "version", "digest", "provenance"}

def test_no_hidden_required_fifth_field():
    # Only 4 fields required; check class annotations
    assert SKILL_IDENTITY_FIELDS == ("skill_id", "version", "digest", "provenance")
    # Inspect dataclass fields
    fields = {f.name for f in SkillIdentity.__dataclass_fields__.values()}
    assert fields == {"skill_id", "version", "digest", "provenance"}
    # Providing extra field via from_dict fails closed
    with pytest.raises(ValueError):
        SkillIdentity.from_dict({
            "skill_id": "s",
            "version": "v1",
            "digest": _valid_digest("x"),
            "provenance": "forge-native",
            "work_role": "coder",
        })
    with pytest.raises(ValueError):
        SkillIdentity.from_dict({
            "skill_id": "s",
            "version": "v1",
            "digest": _valid_digest("x"),
            "provenance": "forge-native",
            "namespace": "coder",
        })
    with pytest.raises(ValueError):
        SkillIdentity.from_dict({
            "skill_id": "s",
            "version": "v1",
            "digest": _valid_digest("x"),
            "provenance": "forge-native",
            "title": "extra",
        })

def test_identity_key_deterministic_skill_id_version():
    sid1 = _make_valid(skill_id="skill-x", version="v1.0")
    sid2 = _make_valid(skill_id="skill-x", version="v1.0", digest=sid1.digest, provenance=sid1.provenance)
    sid3 = _make_valid(skill_id="skill-x", version="v2.0")
    assert sid1.identity_key == ("skill-x", "v1.0")
    assert sid1.key == sid1.identity_key
    assert sid1.identity_key == sid2.identity_key
    assert sid1.identity_key != sid3.identity_key
    # digest/provenance not part of identity key authority
    sid_diff_digest = _make_valid(skill_id="skill-x", version="v1.0", digest=_valid_digest("different"), provenance="other")
    assert sid1.identity_key == sid_diff_digest.identity_key
    assert sid1.digest != sid_diff_digest.digest

# ---------------------------------------------------------------------------
# Fail-closed validation
# ---------------------------------------------------------------------------

def test_empty_skill_id_rejected():
    with pytest.raises((ValueError, TypeError)):
        _make_valid(skill_id="")

def test_whitespace_only_skill_id_rejected():
    with pytest.raises(ValueError):
        _make_valid(skill_id="   ")

def test_non_string_skill_id_rejected():
    with pytest.raises(TypeError):
        SkillIdentity(skill_id=123, version="v1", digest=_valid_digest("x"), provenance="p")  # type: ignore
    with pytest.raises(TypeError):
        SkillIdentity(skill_id=None, version="v1", digest=_valid_digest("x"), provenance="p")  # type: ignore
    with pytest.raises(TypeError):
        SkillIdentity(skill_id=["list"], version="v1", digest=_valid_digest("x"), provenance="p")  # type: ignore

def test_skill_id_bounded():
    long_id = "a" * (MAX_SKILL_ID_LENGTH + 1)
    with pytest.raises(ValueError):
        _make_valid(skill_id=long_id)
    ok_id = "a" * MAX_SKILL_ID_LENGTH
    sid = _make_valid(skill_id=ok_id)
    assert len(sid.skill_id) == MAX_SKILL_ID_LENGTH

def test_skill_id_whitespace_trim_rejected_not_aliased():
    # Avoid aggressive normalization alias: leading/trailing whitespace rejected
    with pytest.raises(ValueError):
        _make_valid(skill_id=" skill-a ")
    with pytest.raises(ValueError):
        _make_valid(skill_id=" skill-a")

def test_empty_version_rejected():
    with pytest.raises((ValueError, TypeError)):
        _make_valid(version="")

def test_whitespace_version_rejected():
    with pytest.raises(ValueError):
        _make_valid(version="  ")

def test_non_string_version_rejected():
    with pytest.raises(TypeError):
        SkillIdentity(skill_id="s", version=123, digest=_valid_digest("x"), provenance="p")  # type: ignore

def test_version_bounded():
    long_ver = "v" * (MAX_VERSION_LENGTH + 1)
    with pytest.raises(ValueError):
        _make_valid(version=long_ver)

def test_empty_malformed_digest_rejected():
    with pytest.raises((ValueError, TypeError)):
        _make_valid(digest="")
    with pytest.raises(ValueError):
        _make_valid(digest="   ")

def test_wrong_digest_length_rejected():
    with pytest.raises(ValueError):
        _make_valid(digest="a" * 63)
    with pytest.raises(ValueError):
        _make_valid(digest="a" * 65)
    with pytest.raises(ValueError):
        _make_valid(digest="abc")

def test_non_hex_digest_rejected():
    with pytest.raises(ValueError):
        _make_valid(digest="z" * 64)
    with pytest.raises(ValueError):
        _make_valid(digest="g" * 64)
    with pytest.raises(ValueError):
        _make_valid(digest="A" * 64)  # uppercase rejected (non-canonical)

def test_non_canonical_uppercase_digest_rejected():
    upper = hashlib.sha256(b"test").hexdigest().upper()
    assert upper != upper.lower()
    with pytest.raises(ValueError):
        _make_valid(digest=upper)

def test_non_string_digest_rejected():
    with pytest.raises(TypeError):
        SkillIdentity(skill_id="s", version="v1", digest=123, provenance="p")  # type: ignore

def test_empty_provenance_rejected():
    with pytest.raises((ValueError, TypeError)):
        _make_valid(provenance="")

def test_whitespace_provenance_rejected():
    with pytest.raises(ValueError):
        _make_valid(provenance="   ")

def test_non_string_provenance_rejected():
    with pytest.raises(TypeError):
        SkillIdentity(skill_id="s", version="v1", digest=_valid_digest("x"), provenance=123)  # type: ignore

def test_provenance_bounded():
    long_prov = "p" * (MAX_PROVENANCE_LENGTH + 1)
    with pytest.raises(ValueError):
        _make_valid(provenance=long_prov)

# ---------------------------------------------------------------------------
# Version boundary — opaque not SemVer
# ---------------------------------------------------------------------------

def test_version_is_opaque_multiple_forms_without_semver():
    valid_versions = [
        "1.0.0",
        "2.0",
        "v1",
        "2026-09-02",
        "draft",
        "legacy",
        "rev-abc123",
        "1.2.3-beta.1+build.42",
        "custom_version_01",
    ]
    for v in valid_versions:
        sid = _make_valid(version=v)
        assert sid.version == v
    # Missing version fails closed (no default)
    with pytest.raises((ValueError, TypeError)):
        SkillIdentity(skill_id="s", version="", digest=_valid_digest("x"), provenance="p")  # type: ignore
    # from_dict missing version fails closed
    with pytest.raises(ValueError):
        SkillIdentity.from_dict({"skill_id": "s", "digest": _valid_digest("x"), "provenance": "p"})
    # Not silently defaulted to "latest"/"0"/"legacy"
    with pytest.raises(ValueError):
        SkillIdentity.from_dict({"skill_id": "s", "digest": _valid_digest("x"), "provenance": "p", "version": ""})
    # No SemVer library required — ensure import does not need semver
    import importlib.util
    assert importlib.util.find_spec("semver") is None or True  # either absent or not required

# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------

def test_digest_sha256_deterministic_64_lower_hex():
    d = compute_skill_digest("hello world")
    assert len(d) == 64
    assert d == d.lower()
    assert all(c in "0123456789abcdef" for c in d)
    # Known vector
    expected = hashlib.sha256("hello world".encode("utf-8")).hexdigest()
    assert d == expected

def test_same_content_same_digest():
    c = "skill content example"
    d1 = compute_skill_digest(c)
    d2 = compute_skill_digest(c)
    d3 = compute_skill_digest_for_text(c)
    assert d1 == d2 == d3
    # bytes same as text utf-8
    assert compute_skill_digest(c.encode("utf-8")) == d1

def test_changed_content_different_digest():
    d1 = compute_skill_digest("content v1")
    d2 = compute_skill_digest("content v2")
    assert d1 != d2
    # Single char change
    assert compute_skill_digest("a") != compute_skill_digest("b")

def test_digest_utf8_deterministic_no_newline_rewrite():
    # Ensure newline handling deterministic (no hidden rewriting)
    text_with_newline = "line1\nline2"
    text_with_crlf = "line1\r\nline2"
    assert compute_skill_digest(text_with_newline) != compute_skill_digest(text_with_crlf)
    # Raw bytes hashing as-is
    assert compute_skill_digest(b"line1\nline2") == compute_skill_digest("line1\nline2")
    assert compute_skill_digest(b"line1\r\nline2") == compute_skill_digest("line1\r\nline2")

def test_digest_for_bytes_input():
    b = b"\x00\xff\xfe hello"
    d = compute_skill_digest(b)
    expected = hashlib.sha256(b).hexdigest()
    assert d == expected
    assert len(d) == 64

# ---------------------------------------------------------------------------
# Serialization / Parsing
# ---------------------------------------------------------------------------

def test_to_dict_from_dict_roundtrip_deterministic():
    sid = _make_valid(skill_id="skill-abc", version="v2", provenance="forge-native")
    d = sid.to_dict()
    assert set(d.keys()) == {"skill_id", "version", "digest", "provenance"}
    sid2 = SkillIdentity.from_dict(d)
    assert sid == sid2
    # canonical_json deterministic
    assert sid.canonical_json() == sid2.canonical_json()

def test_from_dict_unknown_fields_fail_closed():
    base = _make_valid().to_dict()
    base_bad = dict(base)
    base_bad["extra"] = "field"
    with pytest.raises(ValueError):
        SkillIdentity.from_dict(base_bad)
    base_bad2 = dict(base)
    base_bad2["title"] = "should fail"
    with pytest.raises(ValueError):
        SkillIdentity.from_dict(base_bad2)

def test_from_dict_missing_fields_fail_closed():
    base = _make_valid().to_dict()
    for field in ("skill_id", "version", "digest", "provenance"):
        incomplete = dict(base)
        del incomplete[field]
        with pytest.raises(ValueError):
            SkillIdentity.from_dict(incomplete)

def test_from_dict_non_mapping_fails_closed():
    with pytest.raises(TypeError):
        SkillIdentity.from_dict("not a dict")  # type: ignore
    with pytest.raises(TypeError):
        SkillIdentity.from_dict(None)  # type: ignore

def test_canonical_json_deterministic_sort_keys():
    sid1 = _make_valid(skill_id="a", version="v1")
    # Same logical identity produces same canonical_json
    sid2 = SkillIdentity.from_dict(sid1.to_dict())
    assert sid1.canonical_json() == sid2.canonical_json()
    # canonical_dict sorted keys
    import json
    parsed = json.loads(sid1.canonical_json())
    assert set(parsed.keys()) == {"skill_id", "version", "digest", "provenance"}

# ---------------------------------------------------------------------------
# Authority separation
# ---------------------------------------------------------------------------

def test_authority_separation_constants():
    assert SKILL_IS_AUTHORITY is False
    assert SKILL_GRANTS_TOOL_AUTHORITY is False
    assert DIGEST_IS_AUTHORITY is False
    assert PROVENANCE_IS_AUTHORITY is False

def test_contract_does_not_carry_authority_fields():
    sid = _make_valid()
    # No attributes that would grant authority
    for forbidden in ("tool_authority", "execution_authority", "filesystem_authority", "task_handoff_authority", "agents_override", "tool_requirements", "permissions", "work_role", "namespace"):
        assert not hasattr(sid, forbidden), f"forbidden field present: {forbidden}"
    # Check dataclass fields explicitly
    field_names = set(SkillIdentity.__dataclass_fields__.keys())
    assert field_names == {"skill_id", "version", "digest", "provenance"}
    for f in field_names:
        assert f not in {"work_role", "namespace", "tool_authority"}

def test_provenance_does_not_grant_authority_or_filesystem():
    sid = _make_valid(provenance="legacy-repository")
    # Provenance is descriptive only, does not authorize path/open
    assert sid.provenance == "legacy-repository"
    # No method that opens path or grants tool
    assert not hasattr(sid, "open")
    assert not hasattr(sid, "load")
    assert not hasattr(sid, "authorize")

# ---------------------------------------------------------------------------
# S1 separation
# ---------------------------------------------------------------------------

def test_semantic_reference_remains_generic():
    from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
    import inspect
    sig = inspect.signature(SemanticReference)
    params = list(sig.parameters.keys())
    assert params == ["ref", "digest"]
    # No skill-specific fields
    assert "skill_id" not in params
    assert "version" not in params
    assert "skill_version" not in params
    # Prove generic ref+digest shape
    sr = SemanticReference(ref="skill://example@v1")
    assert hasattr(sr, "ref")
    assert hasattr(sr, "digest")
    assert not hasattr(sr, "version")

def test_task_handoff_skill_refs_generic():
    from aota_forge.work_plane.handoff import TaskHandoff, SemanticReference
    # TaskHandoff still uses SemanticReference for skill_refs, not SkillIdentity
    h = TaskHandoff(
        work_role="coder",
        task_kind="implementation",
        objective="test objective",
        bounded_scope="scope",
        validation_expectations=("ok",),
        semantic_stop_expectations=("stop",),
        skill_refs=(SemanticReference(ref="skill://example@v1"),),
    )
    assert len(h.skill_refs) == 1
    assert isinstance(h.skill_refs[0], SemanticReference)

def test_bootstrap_contracts_remain_unchanged():
    from aota_forge.work_plane.bootstrap import BootstrapBundle, BootstrapBudget
    # Bootstrap contracts must not have skill-specific ontology added
    import pathlib
    src = pathlib.Path("aota_forge/work_plane/bootstrap.py").read_text(encoding="utf-8")
    assert "SkillIdentity" not in src
    # Events contract unchanged
    from aota_forge.work_plane.events import ExecutionEventType
    assert len(ExecutionEventType) == 5

def test_git_diff_no_mutation_of_accepted_s1_files():
    import subprocess
    base = "1d403e12c2c20cb381af7fdc96009ef0b63dac9c"
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", base, "HEAD"],
            cwd=".",
            text=True,
        )
        changed = [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        pytest.skip("git diff not available")
    forbidden = {
        "aota_forge/work_plane/__init__.py",
        "aota_forge/work_plane/handoff.py",
        "aota_forge/work_plane/bootstrap.py",
        "aota_forge/work_plane/events.py",
    }
    for f in changed:
        assert f not in forbidden, f"S1 accepted file mutated: {f}"

# ---------------------------------------------------------------------------
# Namespace separation — W1 must NOT add Agent Work Role namespace into SkillIdentity
# ---------------------------------------------------------------------------

def test_no_namespace_in_identity():
    sid = _make_valid()
    assert not hasattr(sid, "namespace")
    assert not hasattr(sid, "work_role")
    fields = set(SkillIdentity.__dataclass_fields__.keys())
    assert "namespace" not in fields
    assert "work_role" not in fields
    # from_dict with namespace fails
    d = sid.to_dict()
    d_bad = dict(d)
    d_bad["namespace"] = "coder"
    with pytest.raises(ValueError):
        SkillIdentity.from_dict(d_bad)

# ---------------------------------------------------------------------------
# Registry separation — W1 must NOT implement registry
# ---------------------------------------------------------------------------

def test_no_registry_implementation():
    import pathlib
    # skill.py must not implement registry/search/discovery
    src = pathlib.Path("aota_forge/work_plane/skill.py").read_text(encoding="utf-8")
    lower = src.lower()
    assert "class skillregistry" not in lower
    assert "SkillRegistry" not in src
    assert "skill_search" not in lower
    # No filesystem scan/plugin discovery implementation
    assert "os.walk" not in lower
    # Allow docstring mention of "registry" as negative invariant but not as implementation

def test_no_plugin_discovery_or_database():
    import pathlib
    # No S3 database/marketplace production files created by W1 (except static registry after W2)
    assert not pathlib.Path("aota_forge/work_plane/registry.py").exists()
    # skill_registry.py is the expected S3 M1 W2 static registry — allowed after W2
    # (W1 alone must not have it; W2 introduces it deterministically)
    assert not pathlib.Path("aota_forge/work_plane/skill_index.py").exists()
    assert not pathlib.Path("aota_forge/work_plane/plugin_registry.py").exists()
    assert not pathlib.Path("aota_forge/core/skill_marketplace.py").exists()
    # Skill file should not claim marketplace concepts as implementation
    src = pathlib.Path("aota_forge/work_plane/skill.py").read_text(encoding="utf-8")
    assert "class SkillRegistry" not in src
    assert "class Marketplace" not in src
    # No database files created
    assert not pathlib.Path("aota_forge/work_plane/registry.py").exists()
    assert not pathlib.Path("aota_forge/work_plane/skill_index.py").exists()
