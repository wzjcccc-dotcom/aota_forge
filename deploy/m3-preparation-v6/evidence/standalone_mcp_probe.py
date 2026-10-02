"""Extract and validate one candidate's isolated runtime archive and MCP surface."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


async def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: standalone_mcp_probe.py CANDIDATE OUTPUT_JSON")
    candidate = Path(sys.argv[1]).resolve(strict=True)
    output = Path(sys.argv[2]).resolve()
    manifest = json.loads((candidate / "artifact-manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((candidate / "deployment-metadata.json").read_text(encoding="utf-8"))
    source_manifest = json.loads((candidate / metadata["runtime_source_file_manifest"]).read_text(encoding="utf-8"))
    archive_path = candidate / metadata["runtime_source_archive"]
    archive_bytes = archive_path.read_bytes()
    assert sha(archive_bytes) == metadata["runtime_source_archive_sha256"]
    expected_members = source_manifest["runtime_source_file_sha256"]

    with tempfile.TemporaryDirectory(prefix="plan004-m3-v6-", dir="/tmp/opencode") as temporary:
        temporary_root = Path(temporary)
        runtime_root = temporary_root / "runtime"
        runtime_root.mkdir(mode=0o700)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            files = {member.name: member for member in members if member.isfile()}
            assert len(files) == metadata["runtime_source_file_count"] == len(expected_members)
            assert set(files) == set(expected_members)
            for member in members:
                member_path = PurePosixPath(member.name)
                assert not member_path.is_absolute() and ".." not in member_path.parts
                assert member.isdir() or member.isfile()
                destination = runtime_root.joinpath(*member_path.parts)
                if member.isdir():
                    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                    source_file = archive.extractfile(member)
                    assert source_file is not None
                    contents = source_file.read()
                    assert sha(contents) == expected_members[member.name]
                    destination.write_bytes(contents)
                    destination.chmod(0o644)

        assert (runtime_root / ".aota/project.yaml").is_file()
        assert (runtime_root / ".aota/contracts/operations.yaml").is_file()
        assert sha((runtime_root / ".aota/contracts/operations.yaml").read_bytes()) == source_manifest[
            "operations_yaml_sha256"
        ]

        sys.path.insert(0, str(runtime_root))
        from aota_forge.core.contracts.loader import load_operation_descriptor_map

        descriptors = load_operation_descriptor_map(runtime_root)
        assert len(descriptors) == 43
        module_paths = {
            name: str(Path(module.__file__).resolve())
            for name, module in sys.modules.items()
            if (name == "aota_forge" or name.startswith("aota_forge."))
            and getattr(module, "__file__", None)
        }
        assert module_paths and all(Path(path).is_relative_to(runtime_root) for path in module_paths.values())

        entry = temporary_root / "mcp_entry.py"
        entry.write_text(
            "import asyncio, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(sys.argv[1]).resolve()))\n"
            "from aota_forge.mcp_transport import UnboundHostContext, create_unbound_mcp_server\n"
            "root = sys.argv[1]\n"
            "server = create_unbound_mcp_server(UnboundHostContext(repo_root=root, bind_global_dispatcher=False))\n"
            "asyncio.run(server.run_stdio_async())\n",
            encoding="utf-8",
        )
        entry.chmod(0o600)
        environment = dict(os.environ)
        for key in (
            "AOTA_BRIDGE_SOCKET",
            "AOTA_FORGE_GOVERNANCE_BASE",
            "AOTA_FORGE_GOVERNANCE_STORE",
            "AOTA_FORGE_REGISTRY",
            "AOTA_FORGE_RUNTIME_CONFIG",
            "AOTA_GLOBAL_MCP",
            "AOTA_FORGE_REPO_ROOT",
            "AOTA_FORGE_RUNTIME_ROOT",
        ):
            environment.pop(key, None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        parameters = StdioServerParameters(
            command="/usr/bin/python3",
            args=["-P", str(entry), str(runtime_root)],
            env=environment,
            cwd=str(runtime_root),
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = sorted(tool.name for tool in (await session.list_tools()).tools)
                assert tools == ["aota.invoke"]

        write_json(
            output,
            {
                "status": "PASS",
                "candidate_version": manifest["preparation_version"],
                "source_frontier": metadata["source_frontier"],
                "source_tree": metadata["source_tree"],
                "runtime_archive_sha256": metadata["runtime_source_archive_sha256"],
                "runtime_source_member_count": len(files),
                "all_archive_members_match_manifest": True,
                "operations_yaml_sha256": source_manifest["operations_yaml_sha256"],
                "operation_descriptor_count": len(descriptors),
                "module_imports_from_independent_extraction": True,
                "mcp_initialize": "PASS",
                "mcp_protocol_version": initialized.protocolVersion,
                "tools_list": tools,
                "production_bridge_or_config_used": False,
                "production_runtime_or_store_touched": False,
                "extraction_location": "temporary directory under /tmp/opencode, removed after probe",
                "package_file_count_at_probe": len(manifest["candidate_files"]) + 1,
                "candidate_digest_added_after_this_validation": True,
            },
        )


asyncio.run(main())
