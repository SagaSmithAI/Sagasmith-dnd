import asyncio

from sagasmith_core.database import Database
from sagasmith_core.rule_profiles import RuleProfileService
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.build_identity import implementation_identity
from sagasmith_dnd_runtime.config import McpConfig
from sagasmith_dnd_runtime.operations import RequestIdentity


def test_implementation_only_upgrade_requires_and_consumes_checkpoint(tmp_path):
    async def exercise():
        config = McpConfig(
            home=tmp_path, database_url=None, chroma_url=None,
            chroma_path_override=None, dnd_skills_dir=tmp_path / "none",
            modulegen_skills_dir=tmp_path / "none2", auto_seed_rules=False,
        )
        runtime = create_runtime(config)
        identity = RequestIdentity("system:local")

        async def call(operation, **arguments):
            result = await runtime.execute(operation, arguments, context=identity)
            return result.get("result", result) if isinstance(result, dict) else result

        try:
            campaign = await call("campaign_create", name="Build migration", idempotency_key="create")
            campaign_id = campaign["id"]
            profile = await call("campaign_rules", campaign_id=campaign_id,
                                 action="get_profile", payload={})
            options = dict(profile["profile"]["options"])
            fingerprint = options["_core_rule_pack_lock"]["fingerprint"]
            options["_implementation_identity"] = {
                "runtime_build_digest": "historical-build", "state_schema_version": 9,
            }
            database = Database("sqlite:///" + config.database_path.as_posix())
            try:
                RuleProfileService(database).set(
                    campaign_id, edition=profile["profile"]["edition"],
                    locale=profile["profile"]["locale"],
                    publications=profile["profile"]["publications"], options=options,
                )
            finally:
                database.dispose()
            current = await call("campaign_query", view="get", payload={"campaign_id": campaign_id})
            snapshot = await call(
                "snapshot_create", campaign_id=campaign_id, label="Before implementation upgrade",
                expected_revision=current["revision"], expected_head_snapshot_id="",
                idempotency_key="checkpoint",
            )
            branches = await call("branch_query", campaign_id=campaign_id, view="list", payload={})
            branch = next(item for item in branches if item["is_current"])
            args = dict(
                campaign_id=campaign_id, action="core_relock",
                payload={"expected_core_fingerprint": fingerprint,
                         "expected_head_snapshot_id": snapshot["id"],
                         "reason": "Explicit implementation-only migration"},
                branch_id=branch["id"], expected_revision=current["revision"],
                idempotency_key="migrate",
            )
            migrated = await call("campaign_rules", **args)
            assert migrated["mutation_applied"] is True
            assert (
                migrated["profile"]["options"]["_implementation_identity"]
                == implementation_identity()
            )
            assert migrated["core_pack"]["fingerprint"] == fingerprint
            assert migrated["checkpoint_snapshot_id"] == snapshot["id"]
            assert await call("campaign_rules", **args) == migrated
        finally:
            runtime.close()

    asyncio.run(exercise())
