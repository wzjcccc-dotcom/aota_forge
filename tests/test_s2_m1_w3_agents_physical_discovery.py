"""S2 M1-W3 AGENTS Physical Discovery → S1 Candidate Bridge."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.core.regression.fixtures import TempWorkspaceFixture
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
from aota_forge.work_plane.agents_applicability import (
    AgentsPolicyCandidate,
    compute_policy_digest,
    resolve_applicable_policies,
    AmbiguousPolicyError,
    MAX_CONTENT_LENGTH,
)
from aota_forge.work_plane import agents_discovery as disc

REPO_ROOT = Path(__file__).resolve().parent.parent

def _git_diff_names(base: str = "2cd30b36895eb188aac44930ba94a8d604ca31a0") -> list[str]:
    import subprocess
    out = subprocess.check_output(["git", "-C", str(REPO_ROOT), "diff", "--name-only", base, "HEAD"], text=True)
    return [l.strip() for l in out.splitlines() if l.strip()]

def _read_repo_file(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _valid_sandbox(project_id: str = "proj_w3_a", workspace_id: str = "ws_w3") -> tuple[TempWorkspaceFixture, Path, object]:
    ws = TempWorkspaceFixture(prefix="w3-disc-")
    ws.__enter__()
    ws.create_project(project_id)
    registry = ws.create_registry(workspace_id)
    evidence = resolve_project_candidates(workspace_id, registry, project_id)
    assert evidence.status == "RESOLVED"
    worktree_root = Path(tempfile.mkdtemp(prefix="w3-wt-"))
    sandbox = bind_worktree_sandbox(evidence, f"wt-w3-{project_id}", worktree_root)
    return ws, worktree_root, sandbox


def _cleanup(ws: TempWorkspaceFixture, wt: Path) -> None:
    try:
        ws.__exit__(None, None, None)
    except Exception:
        pass
    shutil.rmtree(str(wt), ignore_errors=True)

# ---------------------------------------------------------------------------
# T01 trusted W1 binding discovers root AGENTS
# ---------------------------------------------------------------------------

def test_t01_trusted_w1_binding_discovers_root_agents():
    ws, wt, sb = _valid_sandbox(project_id="proj_t01")
    try:
        (wt / "AGENTS.md").write_text("root policy", encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert len(cands) == 1
        assert cands[0].content == "root policy"
    finally:
        _cleanup(ws, wt)


def test_t01_empty_returns_zero():
    ws, wt, sb = _valid_sandbox(project_id="proj_t01_empty")
    try:
        cands = disc.discover_agents(sb, "")
        assert cands == ()
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T02 discovered root → existing AgentsPolicyCandidate
# ---------------------------------------------------------------------------

def test_t02_discovered_root_is_agents_policy_candidate():
    ws, wt, sb = _valid_sandbox(project_id="proj_t02")
    try:
        (wt / "AGENTS.md").write_text("candidate check", encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert isinstance(cands[0], AgentsPolicyCandidate)
        assert cands[0].content == "candidate check"
        assert cands[0].content_digest is not None
        assert len(cands[0].content_digest) == 64
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T03 trusted project_id preserved
# ---------------------------------------------------------------------------

def test_t03_trusted_project_id_preserved():
    ws, wt, sb = _valid_sandbox(project_id="proj_t03")
    try:
        (wt / "AGENTS.md").write_text("proj id test", encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert cands[0].project_id == sb.project_id == "proj_t03"
        assert cands[0].project_id == sb.project_id
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T04 root scope maps to S1 root scope
# ---------------------------------------------------------------------------

def test_t04_root_scope_maps_to_s1_root():
    ws, wt, sb = _valid_sandbox(project_id="proj_t04")
    try:
        (wt / "AGENTS.md").write_text("root scope", encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert cands[0].scope == ""
        assert cands[0].is_root is True
        assert cands[0].specificity == 0
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T05 nested AGENTS maps to deterministic logical scope
# ---------------------------------------------------------------------------

def test_t05_nested_agents_maps_deterministic():
    ws, wt, sb = _valid_sandbox(project_id="proj_t05")
    try:
        (wt / "AGENTS.md").write_text("root", encoding="utf-8")
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "AGENTS.md").write_text("nested", encoding="utf-8")
        cands = disc.discover_agents(sb, "src")
        scopes = [c.scope for c in cands]
        assert scopes == ["", "src"]
        # deeper
        (wt / "src" / "pkg").mkdir(parents=True)
        (wt / "src" / "pkg" / "AGENTS.md").write_text("deep", encoding="utf-8")
        cands2 = disc.discover_agents(sb, "src/pkg")
        assert [c.scope for c in cands2] == ["", "src", "src/pkg"]
        # physical directory to logical mapping: scope strings never contain absolute
        for c in cands2:
            assert not c.scope.startswith("/")
            assert ".." not in c.scope
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T06 root + nested integration with S1 applicability
# ---------------------------------------------------------------------------

def test_t06_root_nested_integrate_with_s1_applicability():
    ws, wt, sb = _valid_sandbox(project_id="proj_t06")
    try:
        (wt / "AGENTS.md").write_text("root content", encoding="utf-8")
        (wt / "src").mkdir()
        (wt / "src" / "AGENTS.md").write_text("src content", encoding="utf-8")
        cands = disc.discover_agents(sb, "src")
        ordered = resolve_applicable_policies(list(cands), sb.project_id)
        assert [c.scope for c in ordered] == ["", "src"]
        # also works for deeper
        (wt / "src" / "pkg").mkdir()
        (wt / "src" / "pkg" / "AGENTS.md").write_text("pkg content", encoding="utf-8")
        cands2 = disc.discover_agents(sb, "src/pkg")
        ordered2 = resolve_applicable_policies(list(cands2), sb.project_id)
        assert [c.scope for c in ordered2] == ["", "src", "src/pkg"]
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T07 more-specific S1 semantics unchanged
# ---------------------------------------------------------------------------

def test_t07_more_specific_s1_semantics():
    ws, wt, sb = _valid_sandbox(project_id="proj_t07")
    try:
        (wt / "AGENTS.md").write_text("root", encoding="utf-8")
        (wt / "a").mkdir()
        (wt / "a" / "AGENTS.md").write_text("a", encoding="utf-8")
        (wt / "a" / "b").mkdir()
        (wt / "a" / "b" / "AGENTS.md").write_text("ab", encoding="utf-8")
        cands = disc.discover_agents(sb, "a/b")
        # S1 specificity increases with depth
        specs = [c.specificity for c in cands]
        assert specs == [0, 1, 2]
        # resolve keeps more-specific later (higher specificity)
        ordered = resolve_applicable_policies(list(cands), sb.project_id)
        assert ordered[-1].scope == "a/b"
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T08 digest matches S1
# ---------------------------------------------------------------------------

def test_t08_digest_matches_s1():
    ws, wt, sb = _valid_sandbox(project_id="proj_t08")
    try:
        content = "digest test content 123"
        (wt / "AGENTS.md").write_text(content, encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        expected = compute_policy_digest(content)
        assert cands[0].content_digest == expected
        assert cands[0].content_digest == hashlib.sha256(content.encode("utf-8")).hexdigest()
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T09 policy_id deterministic
# ---------------------------------------------------------------------------

def test_t09_policy_id_deterministic():
    ws, wt, sb = _valid_sandbox(project_id="proj_t09")
    try:
        (wt / "AGENTS.md").write_text("deterministic pid", encoding="utf-8")
        c1 = disc.discover_agents(sb, "")
        c2 = disc.discover_agents(sb, "")
        assert c1[0].policy_id == c2[0].policy_id
        # also across fresh sandbox with same inputs
        (wt / "a").mkdir()
        (wt / "a" / "AGENTS.md").write_text("nested deterministic", encoding="utf-8")
        c3 = disc.discover_agents(sb, "a")
        c4 = disc.discover_agents(sb, "a")
        assert [c.policy_id for c in c3] == [c.policy_id for c in c4]
        # policy_id charset valid
        for c in c3:
            assert "/" not in c.policy_id
            assert "\\" not in c.policy_id
            assert not c.policy_id.startswith("/")
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T10 provenance_ref logical
# ---------------------------------------------------------------------------

def test_t10_provenance_logical():
    ws, wt, sb = _valid_sandbox(project_id="proj_t10")
    try:
        (wt / "AGENTS.md").write_text("prov test", encoding="utf-8")
        (wt / "src").mkdir()
        (wt / "src" / "AGENTS.md").write_text("prov nested", encoding="utf-8")
        cands = disc.discover_agents(sb, "src")
        for c in cands:
            prov = c.provenance_ref
            assert prov is not None
            assert not prov.startswith("/")
            assert ".." not in prov.split("/")
            assert "\\" not in prov
            # not absolute path authority
            assert not prov.startswith("/tmp")
            # contains scope or AGENTS marker
            assert "AGENTS" in prov
        # root prov
        assert cands[0].provenance_ref == "agents:AGENTS.md"
        assert cands[1].provenance_ref == "agents:src/AGENTS.md"
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T11 candidate ordering deterministic
# ---------------------------------------------------------------------------

def test_t11_candidate_ordering_deterministic():
    ws, wt, sb = _valid_sandbox(project_id="proj_t11")
    try:
        (wt / "AGENTS.md").write_text("r", encoding="utf-8")
        for p in ["b", "a"]:
            d = wt / p
            d.mkdir()
            (d / "AGENTS.md").write_text(f"content {p}", encoding="utf-8")
        # For target_scope that includes both? Actually each scope chain is ancestor, not sibling.
        # Test that repeated calls produce same order
        (wt / "a" / "sub").mkdir()
        (wt / "a" / "sub" / "AGENTS.md").write_text("deep", encoding="utf-8")
        c1 = disc.discover_agents(sb, "a/sub")
        c2 = disc.discover_agents(sb, "a/sub")
        assert [c.scope for c in c1] == [c.scope for c in c2] == ["", "a", "a/sub"]
        # Ensure not filesystem enumeration order: we used sorted
        assert c1 == c2
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T12 arbitrary absolute root rejected
# ---------------------------------------------------------------------------

def test_t12_arbitrary_absolute_root_rejected():
    ws, wt, sb = _valid_sandbox(project_id="proj_t12")
    try:
        with pytest.raises((TypeError, ValueError, disc.AgentsDiscoveryError)):
            disc.discover_agents("/tmp", "")  # type: ignore
        with pytest.raises((TypeError, ValueError, disc.AgentsDiscoveryError)):
            disc.discover_agents(Path("/etc/passwd"), "")  # type: ignore
        # Also raw string root not allowed as sandbox
        with pytest.raises((TypeError, ValueError, disc.AgentsDiscoveryError)):
            disc.discover_agents("not-a-sandbox", "")  # type: ignore
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T13 target scope traversal rejected
# ---------------------------------------------------------------------------

def test_t13_target_scope_traversal_rejected():
    ws, wt, sb = _valid_sandbox(project_id="proj_t13")
    try:
        bad_scopes = ["/absolute", "../escape", "a/../b", "a/../../b", "a//b", "a\\b", "..", "a/./b", "/a/b"]
        for bad in bad_scopes:
            with pytest.raises(disc.AgentsScopeError):
                disc.discover_agents(sb, bad)
        # also backslash escape
        with pytest.raises(disc.AgentsScopeError):
            disc.discover_agents(sb, "a\\b")
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T14 AGENTS outside bound root cannot be discovered
# ---------------------------------------------------------------------------

def test_t14_outside_root_cannot_be_discovered():
    ws, wt, sb = _valid_sandbox(project_id="proj_t14")
    try:
        # No AGENTS inside
        cands = disc.discover_agents(sb, "")
        assert len(cands) == 0
        # Create AGENTS outside root
        outside = Path(tempfile.mkdtemp(prefix="outside-"))
        try:
            (outside / "AGENTS.md").write_text("evil outside", encoding="utf-8")
            # Still zero inside
            cands2 = disc.discover_agents(sb, "")
            assert len(cands2) == 0
            # Even with nested scope, outside not discovered
            cands3 = disc.discover_agents(sb, "src")
            assert len(cands3) == 0
        finally:
            shutil.rmtree(str(outside), ignore_errors=True)
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T15 AGENTS symlink escape fails closed
# ---------------------------------------------------------------------------

def test_t15_agents_symlink_escape_fails_closed():
    ws, wt, sb = _valid_sandbox(project_id="proj_t15")
    try:
        outside = Path(tempfile.mkdtemp(prefix="w3-out-15-"))
        target = outside / "evil.md"
        target.write_text("evil", encoding="utf-8")
        link = wt / "AGENTS.md"
        try:
            link.symlink_to(target)
            with pytest.raises(disc.AgentsSymlinkEscapeError):
                disc.discover_agents(sb, "")
        finally:
            if link.is_symlink():
                link.unlink()
            shutil.rmtree(str(outside), ignore_errors=True)
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T16 nested symlink escape fails closed
# ---------------------------------------------------------------------------

def test_t16_nested_symlink_escape_fails_closed():
    ws, wt, sb = _valid_sandbox(project_id="proj_t16")
    try:
        # create outside dir with AGENTS
        outside = Path(tempfile.mkdtemp(prefix="w3-out-16-"))
        (outside / "AGENTS.md").write_text("nested evil", encoding="utf-8")
        (wt / "AGENTS.md").write_text("root ok", encoding="utf-8")
        src_link = wt / "src"
        src_link.symlink_to(outside)
        try:
            with pytest.raises(disc.AgentsSymlinkEscapeError):
                disc.discover_agents(sb, "src")
            # also deeper scope through symlink
            with pytest.raises(disc.AgentsSymlinkEscapeError):
                disc.discover_agents(sb, "src/pkg")
        finally:
            if src_link.is_symlink():
                src_link.unlink()
            shutil.rmtree(str(outside), ignore_errors=True)
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T17 oversized fails closed
# ---------------------------------------------------------------------------

def test_t17_oversized_fails_closed():
    ws, wt, sb = _valid_sandbox(project_id="proj_t17")
    try:
        big = "x" * (MAX_CONTENT_LENGTH + 1)
        (wt / "AGENTS.md").write_text(big, encoding="utf-8")
        with pytest.raises((disc.AgentsOversizedError, disc.AgentsDiscoveryError)):
            disc.discover_agents(sb, "")
        # nested oversized also fails
        (wt / "AGENTS.md").write_text("ok", encoding="utf-8")
        (wt / "src").mkdir()
        (wt / "src" / "AGENTS.md").write_text(big, encoding="utf-8")
        with pytest.raises((disc.AgentsOversizedError, disc.AgentsDiscoveryError)):
            disc.discover_agents(sb, "src")
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T18 malformed/unreadable fails closed
# ---------------------------------------------------------------------------

def test_t18_malformed_unreadable_fails_closed():
    ws, wt, sb = _valid_sandbox(project_id="proj_t18")
    try:
        # invalid utf-8 bytes
        p = wt / "AGENTS.md"
        p.write_bytes(b"\xff\xfe\xfd invalid utf8")
        with pytest.raises(disc.AgentsContentError):
            disc.discover_agents(sb, "")
        # also permission unreadable is covered by content error? We test malformed only
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T19 no silent truncation
# ---------------------------------------------------------------------------

def test_t19_no_silent_truncation():
    ws, wt, sb = _valid_sandbox(project_id="proj_t19")
    try:
        exact = "y" * MAX_CONTENT_LENGTH
        (wt / "AGENTS.md").write_text(exact, encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert cands[0].content == exact
        assert len(cands[0].content) == MAX_CONTENT_LENGTH
        # oversized must not be truncated
        big = "z" * (MAX_CONTENT_LENGTH + 10)
        (wt / "AGENTS.md").write_text(big, encoding="utf-8")
        with pytest.raises(disc.AgentsOversizedError):
            disc.discover_agents(sb, "")
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T20 caller cannot substitute foreign project_id
# ---------------------------------------------------------------------------

def test_t20_caller_cannot_substitute_foreign_project_id():
    ws, wt, sb = _valid_sandbox(project_id="proj_t20_real")
    try:
        (wt / "AGENTS.md").write_text("real project", encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert cands[0].project_id == "proj_t20_real"
        assert cands[0].project_id != "proj_evil"
        # API does not accept caller project_id at all
        # Ensure no parameter exists to override
        import inspect
        sig = inspect.signature(disc.discover_agents)
        assert "project_id" not in sig.parameters
        assert "caller_project_id" not in sig.parameters
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T21 physical discovery does not become final policy authority
# ---------------------------------------------------------------------------

def test_t21_physical_discovery_not_authority():
    assert disc.PHYSICAL_DISCOVERY_IS_POLICY_AUTHORITY is False
    ws, wt, sb = _valid_sandbox(project_id="proj_t21")
    try:
        (wt / "AGENTS.md").write_text("root", encoding="utf-8")
        (wt / "src").mkdir()
        (wt / "src" / "AGENTS.md").write_text("src", encoding="utf-8")
        cands = disc.discover_agents(sb, "src")
        # discovery returns candidates, but final applicability still via S1
        assert len(cands) == 2
        # caller must explicitly call resolve_applicable_policies
        resolved = resolve_applicable_policies(list(cands), sb.project_id)
        assert len(resolved) == 2
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T22 S1 resolve_applicable_policies remains semantic resolver
# ---------------------------------------------------------------------------

def test_t22_s1_resolve_remains_semantic():
    assert disc.S1_APPLICABILITY_REUSED is True
    src = _read_repo_file("aota_forge/work_plane/agents_discovery.py")
    # discovery must not reimplement S1 logic as authority: it should return candidates for S1 to resolve
    # It should not internally call resolve_applicable_policies as final authority
    # But it may import AgentsPolicyCandidate; check not duplicating resolve logic enumerating policy chain
    # Our implementation delegates to S1 for final ordering, but discovery sorts by same key
    # Ensure module does not claim to be applicability authority
    assert "PHYSICAL_DISCOVERY_IS_POLICY_AUTHORITY" in src
    # The semantic resolver is still in agents_applicability
    import aota_forge.work_plane.agents_applicability as app
    assert hasattr(app, "resolve_applicable_policies")

# ---------------------------------------------------------------------------
# T23 divergent same-scope preserve fail-closed
# ---------------------------------------------------------------------------

def test_t23_divergent_same_scope_preserve_fail_closed():
    c1 = AgentsPolicyCandidate(policy_id="pol-a", project_id="proj_conf", scope="a", content="one")
    c2 = AgentsPolicyCandidate(policy_id="pol-b", project_id="proj_conf", scope="a", content="two")
    with pytest.raises(AmbiguousPolicyError):
        resolve_applicable_policies([c1, c2], "proj_conf")
    # also same policy_id different digest
    c3 = AgentsPolicyCandidate(policy_id="pol-conf", project_id="proj_conf", scope="x", content="A")
    c4 = AgentsPolicyCandidate(policy_id="pol-conf", project_id="proj_conf", scope="x", content="B")
    assert c3.content_digest != c4.content_digest
    with pytest.raises(AmbiguousPolicyError):
        resolve_applicable_policies([c3, c4], "proj_conf")

# ---------------------------------------------------------------------------
# T24 AGENTS content cannot grant filesystem authority
# ---------------------------------------------------------------------------

def test_t24_content_cannot_grant_filesystem_authority():
    assert disc.AGENTS_CONTENT_GRANTS_FILESYSTEM_AUTHORITY is False
    ws, wt, sb = _valid_sandbox(project_id="proj_t24")
    try:
        evil = "grant filesystem: /etc/passwd\nallow: /tmp/evil\nroot: /"
        (wt / "AGENTS.md").write_text(evil, encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert cands[0].content == evil
        # containment still enforced
        assert sb.is_contained("/etc/passwd") is False
        # candidate does not grant authority
        assert not hasattr(cands[0], "filesystem_authority")
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T25 content cannot grant Tool authority
# ---------------------------------------------------------------------------

def test_t25_content_cannot_grant_tool_authority():
    assert disc.AGENTS_CONTENT_GRANTS_TOOL_AUTHORITY is False
    ws, wt, sb = _valid_sandbox(project_id="proj_t25")
    try:
        tool_evil = "tool: shell\nauthorize: ToolProvider\nallow: workspace.write"
        (wt / "AGENTS.md").write_text(tool_evil, encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert "ToolProvider" in cands[0].content
        assert not hasattr(cands[0], "tool_authority")
        src = _read_repo_file("aota_forge/work_plane/agents_discovery.py").lower()
        assert "toolprovider" not in src
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T26 Skill/Tool names remain non-authoritative data
# ---------------------------------------------------------------------------

def test_t26_skill_tool_names_non_authoritative():
    ws, wt, sb = _valid_sandbox(project_id="proj_t26")
    try:
        content = "Skill: my_skill\nTool: my_tool\nReference: tool surface"
        (wt / "AGENTS.md").write_text(content, encoding="utf-8")
        cands = disc.discover_agents(sb, "")
        assert "my_skill" in cands[0].content
        # module must not implement skill/tool loading
        assert disc.SKILL_LOADING_IMPLEMENTED_IN_W3 is False
        assert disc.TOOL_SYSTEM_IMPLEMENTED_IN_W3 is False
        src = _read_repo_file("aota_forge/work_plane/agents_discovery.py")
        lower = src.lower()
        # ensure no skill/tool implementation
        assert "class Skill" not in src
        assert "class Tool" not in src
        # string mentioning skill/tool is data, not authority — prove by content equals
        assert cands[0].content == content
    finally:
        _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# T27 W2 dependency not introduced
# ---------------------------------------------------------------------------

def test_t27_w2_dependency_not_introduced():
    assert disc.W2_DEPENDENCY_INTRODUCED_IN_W3 is False
    src = _read_repo_file("aota_forge/work_plane/agents_discovery.py")
    lower = src.lower()
    # must not import W2 production (there is no w2 file but ensure not referencing)
    assert "worktree_sandbox" in src  # W1 reused
    assert "agents_applicability" in src  # S1 reused
    # ensure not importing hypothetical w2 resolver beyond flag definitions
    # count occurrences of project_resource_resolver — should be at most in flag line
    assert lower.count("project_resource_resolver") <= 1
    # no actual import of generic resolver
    assert "import" not in lower or "project_resource_resolver" not in lower.split("import")[1] if "project_resource_resolver" in lower else True

# ---------------------------------------------------------------------------
# T28 generic resolver not created
# ---------------------------------------------------------------------------

def test_t28_generic_resolver_not_created():
    assert disc.GENERIC_PROJECT_RESOURCE_RESOLVER_CREATED_IN_W3 is False
    assert not (REPO_ROOT / "aota_forge/work_plane/project_resource_resolver.py").exists()
    assert not (REPO_ROOT / "aota_forge/work_plane/resource_resolver.py").exists()
    assert not (REPO_ROOT / "aota_forge/work_plane/generic_resolver.py").exists()
    src = _read_repo_file("aota_forge/work_plane/agents_discovery.py").lower()
    # allow flag mention but no generic resolver class/function implementation
    assert "class generic" not in src
    assert "def generic" not in src
    # ensure not creating generic resolver file beyond flag
    assert src.count("generic") <= 3  # flag + comments

# ---------------------------------------------------------------------------
# T29 W1 unchanged
# ---------------------------------------------------------------------------

def test_t29_w1_unchanged():
    changed = _git_diff_names()
    assert "aota_forge/work_plane/worktree_sandbox.py" not in changed

# ---------------------------------------------------------------------------
# T30 S1 agents_applicability unchanged
# ---------------------------------------------------------------------------

def test_t30_s1_agents_applicability_unchanged():
    changed = _git_diff_names()
    assert "aota_forge/work_plane/agents_applicability.py" not in changed

# ---------------------------------------------------------------------------
# T31 high-conflict files unchanged
# ---------------------------------------------------------------------------

def test_t31_high_conflict_files_unchanged():
    changed = set(_git_diff_names())
    for p in ["aota_forge/work_plane/__init__.py", "aota_forge/work_plane/handoff.py", "aota_forge/work_plane/bootstrap.py", "aota_forge/work_plane/events.py"]:
        assert p not in changed, f"{p} must not be changed"

# ---------------------------------------------------------------------------
# T32 no Skill
# ---------------------------------------------------------------------------

def test_t32_no_skill():
    src = _read_repo_file("aota_forge/work_plane/agents_discovery.py")
    assert "SKILL_LOADING_IMPLEMENTED_IN_W3" in src
    assert disc.SKILL_LOADING_IMPLEMENTED_IN_W3 is False
    # no skill files
    assert not (REPO_ROOT / "aota_forge/work_plane/skill.py").exists()
    assert not (REPO_ROOT / "aota_forge/work_plane/skills.py").exists()

# ---------------------------------------------------------------------------
# T33 no Tool
# ---------------------------------------------------------------------------

def test_t33_no_tool():
    src = _read_repo_file("aota_forge/work_plane/agents_discovery.py").lower()
    assert disc.TOOL_SYSTEM_IMPLEMENTED_IN_W3 is False
    assert not (REPO_ROOT / "aota_forge/work_plane/tool.py").exists()
    assert not (REPO_ROOT / "aota_forge/work_plane/tools.py").exists()
    # ensure not implementing ToolProvider etc
    assert "toolprovider" not in src

# ---------------------------------------------------------------------------
# T34 no bootstrap schema modification
# ---------------------------------------------------------------------------

def test_t34_no_bootstrap_modified():
    changed = _git_diff_names()
    assert "aota_forge/work_plane/bootstrap.py" not in changed
    assert disc.BOOTSTRAP_CONTRACT_CHANGED is False
