import asyncio
import os
import sys

from mcp import Client, StdioServerParameters


def test_real_local_stdio_manages_revisions_and_restore_binding(tmp_path):
    async def run():
        parameters = StdioServerParameters(command=sys.executable,
            args=["-m", "sagasmith_dnd_mcp.server"], env={**os.environ,
                "SAGASMITH_DND_MCP_HOME": str(tmp_path),
                "SAGASMITH_DND_MCP_AUTO_SEED": "0",
                "SAGASMITH_DND_SKILLS_DIR": str(tmp_path / "skills"),
                "SAGASMITH_MODULEGEN_SKILLS_DIR": str(tmp_path / "modulegen"),
                "SAGASMITH_DND_LOCAL_AUTHORITY": "1",
                "SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID": "system:local",
                "SAGASMITH_AUTH_CONTEXT_SECRET": "",
                "SAGASMITH_DND_MCP_TRANSPORT": "stdio",
            })
        async with Client(parameters, mode="2026-07-28") as client:
            catalog = await client.list_tools()
            names = [tool.name for tool in catalog.tools]

            async def call(operation, **args):
                result = await client.call_tool(operation, args)
                assert not result.is_error, result.content
                return result.structured_content

            campaign = await call("campaign_create", name="Local stdio", idempotency_key="create")
            systems = await call("system_list")
            assert systems
            campaign_id = campaign["id"]
            before = campaign["host_context_binding"]["context_epoch"]
            snapshot = await call(
                "snapshot_create", campaign_id=campaign_id, idempotency_key="save",
            )
            restored = await call("snapshot_restore", campaign_id=campaign_id,
                                  slot=snapshot["slot"], idempotency_key="restore")
            assert restored["host_context_binding"]["context_epoch"] != before
            assert restored["local_context"]["campaign_id"] == campaign_id
            listed = await client.list_tools(cache_mode="reload")
            assert [tool.name for tool in listed.tools] == names
            assert restored["local_execution"]["database"]["queries"] > 0
    asyncio.run(run())


def test_local_adapter_preserves_runtime_images(tmp_path):
    from sagasmith_dnd_runtime.operations import Image, RenderResult

    from sagasmith_dnd_mcp.config import McpConfig
    from sagasmith_dnd_mcp.server import create_server

    server = create_server(McpConfig(
        home=tmp_path, database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=tmp_path / "skills", modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False, local_authority=True, bound_principal_id="system:local",
    ))

    async def run():
        picture = Image(b"test-image")
        for value in (picture, RenderResult({"rendered": True}, picture),
                      [{"rendered": True}, picture]):
            async def execute(*args, **kwargs):
                return value
            server.runtime.execute = execute
            content, _ = await server.call_tool("system_list", {})
            assert sum(item.type == "image" for item in content) == 1
    try:
        asyncio.run(run())
    finally:
        server.runtime.close()
