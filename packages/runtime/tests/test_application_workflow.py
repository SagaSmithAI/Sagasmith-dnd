import subprocess
import sys


def test_campaign_lifecycle_runs_without_mcp(tmp_path) -> None:
    program = r"""
import asyncio
import sys
from pathlib import Path
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.config import McpConfig
from sagasmith_dnd_runtime.operations import RequestIdentity

async def main():
    root = Path(sys.argv[1])
    runtime = create_runtime(McpConfig(
        home=root, database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=root / "skills", modulegen_skills_dir=root / "modulegen",
        auto_seed_rules=False,
    ))
    try:
        # Hosted creation carries an empty campaign scope before a campaign exists.
        identity = RequestIdentity("system:local", "")
        args = {"name": "Application-only campaign", "edition": "2014",
                "idempotency_key": "create-campaign"}
        campaign = await runtime.execute("campaign_create", args, context=identity)
        replay = await runtime.execute("campaign_create", args, context=identity)
        assert campaign == replay
        assert campaign["revision"] >= 0
        result = await runtime.execute("campaign_query", {
            "view": "get", "payload": {"campaign_id": campaign["id"]},
        }, context=identity)
        assert campaign["id"] in str(result)
        await runtime.execute("character_query", {"view": "list", "payload": {}},
                              context=RequestIdentity("system:local", campaign["id"]))
        try:
            await runtime.execute("character_query", {
                "view": "list", "payload": {"campaign_id": "different-campaign"},
            }, context=RequestIdentity("system:local", campaign["id"]))
        except PermissionError:
            pass
        else:
            raise AssertionError("nested campaign must not override trusted scope")
        try:
            await runtime.execute("campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            }, context=RequestIdentity("player:untrusted"))
        except (PermissionError, ValueError):
            pass
        else:
            raise AssertionError("payload principal must not override request identity")
        assert not any(n == "mcp" or n.startswith("mcp.") for n in sys.modules)
        assert not any(n.startswith("sagasmith_dnd_mcp") for n in sys.modules)
    finally:
        runtime.close()

asyncio.run(main())
"""
    subprocess.run([sys.executable, "-c", program, str(tmp_path)], check=True, timeout=60)
