import asyncio
from pathlib import Path

import pytest

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server

HOST_TOKEN = "test-host-token-with-sufficient-entropy"


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
        npc_host_token=HOST_TOKEN,
    )


async def _call(server, name: str, arguments: dict):
    called = await server.call_tool(name, arguments)
    if isinstance(called, tuple):
        _, result = called
        return result.get("result", result) if isinstance(result, dict) else result
    return called


async def _campaign_with_party(server):
    campaign = await _call(
        server,
        "campaign_create",
        {"name": "Conversation playthrough", "idempotency_key": "campaign"},
    )

    async def create(name: str, *, character_type: str | None, key: str):
        payload = {"campaign_id": campaign["id"], "name": name}
        if character_type is not None:
            payload["character_type"] = character_type
        return await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": payload,
                "idempotency_key": key,
            },
        )

    mara = await create("Mara", character_type="npc", key="mara")
    tomas = await create("Tomas", character_type="npc", key="tomas")
    aria = await create("Aria", character_type=None, key="aria")
    current = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
    )
    await _call(
        server,
        "game_phase",
        {
            "campaign_id": campaign["id"],
            "action": "set",
            "tool_profile": "play",
            "expected_revision": current["revision"],
            "idempotency_key": "enter-play",
        },
    )
    return campaign, mara, tomas, aria


def _audience(
    decision_id: str,
    *,
    participants: list[str],
    response: list[str],
) -> dict:
    return {
        "decision_id": decision_id,
        "resolver": "agent",
        "perceived_actor_ids": participants,
        "understood_actor_ids": participants,
        "response_actor_ids": response,
        "partial_renditions": {},
        "basis_refs": ["scene:current"],
        "reason": "All participants are close enough and share Common.",
    }


def _proposal(
    capsule: dict,
    *,
    text: str,
    content_mode: str,
    basis_refs: list[str],
    targets: list[str],
    knowledge_delta: dict | None = None,
) -> dict:
    return {
        "schema_version": 5,
        "conversation_id": capsule["conversation_id"],
        "activation_id": capsule["activation_id"],
        "actor_runtime_id": capsule["actor_runtime_id"],
        "response_bid": {
            "should_respond": True,
            "urgency": 60,
            "reason": "The player addressed this NPC.",
        },
        "private_intent": "Answer according to the actor's own knowledge and motives.",
        "utterance_segments": [
            {
                "text": text,
                "content_mode": content_mode,
                "speech_act": "answer",
                "truth_posture": content_mode,
                "basis_refs": basis_refs,
                "targets": targets,
                "language": "Common",
                "delivery": "quietly",
            }
        ],
        "proposed_action": {
            "summary": "",
            "target_refs": [],
            "settlement": "narrative",
            "mechanic_hint": "",
        },
        "resolution_requests": [],
        "working_deltas": {
            "facts": [],
            "actor_knowledge": [knowledge_delta] if knowledge_delta else [],
            "commitments": [],
        },
        "visible_cues": [],
        "decision_summary": "Respond in character.",
    }


async def _open(server, campaign_id: str, actor_ids: list[str], *, key: str) -> dict:
    return await _call(
        server,
        "npc_conversation",
        {
            "campaign_id": campaign_id,
            "action": "open",
            "payload": {
                "participant_actor_ids": actor_ids,
                "query": "current scene, promises, secrets, and relationships",
                "idempotency_key": key,
            },
        },
    )


async def _ingest_speech(
    server,
    campaign_id: str,
    conversation_id: str,
    *,
    revision: int,
    key: str,
    speaker_id: str,
    content: str,
    targets: list[str],
    participants: list[str],
) -> dict:
    return await _call(
        server,
        "npc_conversation",
        {
            "campaign_id": campaign_id,
            "action": "ingest",
            "payload": {
                "conversation_id": conversation_id,
                "event": {
                    "type": "speech",
                    "speaker_actor_id": speaker_id,
                    "content": content,
                    "language": "Common",
                    "declared_target_actor_ids": targets,
                },
                "audience_facts": _audience(
                    f"audience-{key}", participants=participants, response=targets
                ),
                "expected_conversation_revision": revision,
                "idempotency_key": f"ingest-{key}",
            },
        },
    )


async def _claim(
    server,
    campaign_id: str,
    conversation_id: str,
    activation: dict,
    *,
    revision: int,
    key: str,
    cursor: int = 0,
    include_bootstrap: bool = True,
) -> dict:
    return await _call(
        server,
        "npc_conversation_transport",
        {
            "campaign_id": campaign_id,
            "conversation_id": conversation_id,
            "action": "claim_activation",
            "host_token": HOST_TOKEN,
            "payload": {
                "activation_ref": activation["activation_ref"],
                "expected_conversation_revision": revision,
                "idempotency_key": f"claim-{key}",
                "cursor": cursor,
                "include_bootstrap": include_bootstrap,
            },
        },
    )


async def _submit(
    server,
    campaign_id: str,
    conversation_id: str,
    activation: dict,
    capsule: dict,
    proposal: dict,
    *,
    revision: int,
    key: str,
) -> dict:
    return await _call(
        server,
        "npc_conversation_transport",
        {
            "campaign_id": campaign_id,
            "conversation_id": conversation_id,
            "action": "submit_proposal",
            "host_token": HOST_TOKEN,
            "payload": {
                "activation_ref": activation["activation_ref"],
                "lease_id": capsule["lease_id"],
                "proposal": proposal,
                "expected_conversation_revision": revision,
                "idempotency_key": f"submit-{key}",
            },
        },
    )


async def _publish(
    server,
    campaign_id: str,
    conversation_id: str,
    submitted: dict,
    *,
    revision: int,
    key: str,
    participants: list[str],
) -> dict:
    return await _call(
        server,
        "npc_conversation",
        {
            "campaign_id": campaign_id,
            "action": "publish",
            "payload": {
                "conversation_id": conversation_id,
                "publication_id": submitted["publication"]["publication_id"],
                "audience_facts": _audience(
                    f"publication-{key}", participants=participants, response=[]
                ),
                "expected_conversation_revision": revision,
                "idempotency_key": f"publish-{key}",
            },
        },
    )


def _assert_zero_tool_persistent_worker(capsule: dict) -> None:
    constraints = capsule["constraints"]
    assert {
        key: constraints[key]
        for key in (
            "may_call_tools",
            "may_roll_dice",
            "may_write_state",
            "utterance_content_modes",
            "factual_content_requires_actor_owned_basis_refs",
            "output_contract",
        )
    } == {
        "may_call_tools": False,
        "may_roll_dice": False,
        "may_write_state": False,
        "utterance_content_modes": [
            "nonfactual",
            "grounded",
            "deception",
            "uncertain",
        ],
        "factual_content_requires_actor_owned_basis_refs": True,
        "output_contract": "npc-conversation-proposal.v5",
    }
    delegation = capsule["bootstrap"]["delegation"]
    assert delegation["execution"] == "persistent_actor_worker"
    assert delegation["tools_exposed"] is False
    assert delegation["persist_worker_session"] is True
    assert delegation["authoritative_result"] is False


def test_two_npcs_keep_distinct_persistent_workers_across_four_content_modes(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign, mara, tomas, aria = await _campaign_with_party(server)
        participants = [aria["id"], mara["id"], tomas["id"]]
        opened = await _open(server, campaign["id"], participants, key="open-multi")
        conversation_id = opened["conversation_id"]

        first = await _ingest_speech(
            server,
            campaign["id"],
            conversation_id,
            revision=0,
            key="round-one",
            speaker_id=aria["id"],
            content="What happened at the north gate?",
            targets=[mara["id"], tomas["id"]],
            participants=participants,
        )
        activations = {item["actor_id"]: item for item in first["activations"]}
        assert set(activations) == {mara["id"], tomas["id"]}

        mara_capsule = await _claim(
            server,
            campaign["id"],
            conversation_id,
            activations[mara["id"]],
            revision=1,
            key="mara-one",
        )
        _assert_zero_tool_persistent_worker(mara_capsule)
        tomas_capsule = await _claim(
            server,
            campaign["id"],
            conversation_id,
            activations[tomas["id"]],
            revision=2,
            key="tomas-one",
        )
        _assert_zero_tool_persistent_worker(tomas_capsule)
        assert mara_capsule["actor_runtime_id"] != tomas_capsule["actor_runtime_id"]
        assert mara_capsule["bootstrap"]["actor"]["id"] == mara["id"]
        assert tomas_capsule["bootstrap"]["actor"]["id"] == tomas["id"]

        bad_grounding = _proposal(
            mara_capsule,
            text="The north gate was barred at midnight.",
            content_mode="grounded",
            basis_refs=["module:dm-only-secret"],
            targets=[aria["id"]],
        )
        rejected = await _submit(
            server,
            campaign["id"],
            conversation_id,
            activations[mara["id"]],
            mara_capsule,
            bad_grounding,
            revision=3,
            key="mara-bad-basis",
        )
        assert rejected["status"] == "validation_failed"
        assert rejected["lease_retained"] is True
        assert "outside its actor capsule" in rejected["validation_issues"][0]["message"]

        event_basis = mara_capsule["inbox"][0]["event_id"]
        mara_submitted = await _submit(
            server,
            campaign["id"],
            conversation_id,
            activations[mara["id"]],
            mara_capsule,
            _proposal(
                mara_capsule,
                text="The north gate was barred at midnight.",
                content_mode="grounded",
                basis_refs=[event_basis],
                targets=[aria["id"]],
            ),
            revision=3,
            key="mara-grounded",
        )
        assert mara_submitted["status"] == "publication_ready"
        tomas_submitted = await _submit(
            server,
            campaign["id"],
            conversation_id,
            activations[tomas["id"]],
            tomas_capsule,
            _proposal(
                tomas_capsule,
                text="I never went near the north gate.",
                content_mode="deception",
                basis_refs=[tomas_capsule["inbox"][0]["event_id"]],
                targets=[aria["id"]],
            ),
            revision=4,
            key="tomas-deception",
        )
        await _publish(
            server,
            campaign["id"],
            conversation_id,
            mara_submitted,
            revision=5,
            key="mara-grounded",
            participants=participants,
        )
        await _publish(
            server,
            campaign["id"],
            conversation_id,
            tomas_submitted,
            revision=6,
            key="tomas-deception",
            participants=participants,
        )

        second = await _ingest_speech(
            server,
            campaign["id"],
            conversation_id,
            revision=7,
            key="round-two",
            speaker_id=aria["id"],
            content="Could either of you guide me there?",
            targets=[mara["id"], tomas["id"]],
            participants=participants,
        )
        second_activations = {item["actor_id"]: item for item in second["activations"]}
        mara_second = await _claim(
            server,
            campaign["id"],
            conversation_id,
            second_activations[mara["id"]],
            revision=8,
            key="mara-two",
            cursor=1,
            include_bootstrap=True,
        )
        tomas_second = await _claim(
            server,
            campaign["id"],
            conversation_id,
            second_activations[tomas["id"]],
            revision=9,
            key="tomas-two",
            cursor=1,
            include_bootstrap=True,
        )
        assert mara_second["actor_runtime_id"] == mara_capsule["actor_runtime_id"]
        assert tomas_second["actor_runtime_id"] == tomas_capsule["actor_runtime_id"]
        assert mara_second["working_state"] == mara_capsule["working_state"]
        assert tomas_second["working_state"] == tomas_capsule["working_state"]

        mara_uncertain = await _submit(
            server,
            campaign["id"],
            conversation_id,
            second_activations[mara["id"]],
            mara_second,
            _proposal(
                mara_second,
                text="I think the canal path may still be open.",
                content_mode="uncertain",
                basis_refs=[mara_second["inbox"][-1]["event_id"]],
                targets=[aria["id"]],
            ),
            revision=10,
            key="mara-uncertain",
        )
        tomas_nonfactual = await _submit(
            server,
            campaign["id"],
            conversation_id,
            second_activations[tomas["id"]],
            tomas_second,
            _proposal(
                tomas_second,
                text="Perhaps another night, friend.",
                content_mode="nonfactual",
                basis_refs=[],
                targets=[aria["id"]],
            ),
            revision=11,
            key="tomas-nonfactual",
        )
        await _publish(
            server,
            campaign["id"],
            conversation_id,
            mara_uncertain,
            revision=12,
            key="mara-uncertain",
            participants=participants,
        )
        await _publish(
            server,
            campaign["id"],
            conversation_id,
            tomas_nonfactual,
            revision=13,
            key="tomas-nonfactual",
            participants=participants,
        )

        status = await _call(
            server,
            "npc_conversation",
            {
                "campaign_id": campaign["id"],
                "action": "get",
                "payload": {"conversation_id": conversation_id},
            },
        )
        assert status["activations"] == []
        assert status["conversation_revision"] == 14
        accepted = [
            item["candidate_id"]
            for item in status["memory_candidates"]
            if item["status"] == "available"
        ]
        closed = await _call(
            server,
            "npc_conversation",
            {
                "campaign_id": campaign["id"],
                "action": "close",
                "payload": {
                    "conversation_id": conversation_id,
                    "expected_conversation_revision": 14,
                    "accepted_candidate_ids": accepted,
                    "idempotency_key": "close-multi",
                },
            },
        )
        npc_segments = [
            segment
            for event in closed["event"]["payload"]["transcript"]
            for segment in event.get("utterance_segments", [])
        ]
        assert [item["text"] for item in npc_segments] == [
            "The north gate was barred at midnight.",
            "I never went near the north gate.",
            "I think the canal path may still be open.",
            "Perhaps another night, friend.",
        ]
        assert all("content_mode" not in item for item in npc_segments)
        assert all("basis_refs" not in item for item in npc_segments)
        assert "private_intent" not in str(closed["event"]["payload"])

    asyncio.run(exercise())


def test_actor_local_refresh_replaces_only_that_npcs_activation(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign, mara, tomas, aria = await _campaign_with_party(server)
        participants = [aria["id"], mara["id"], tomas["id"]]
        opened = await _open(server, campaign["id"], participants, key="open-refresh")
        conversation_id = opened["conversation_id"]
        ingested = await _ingest_speech(
            server,
            campaign["id"],
            conversation_id,
            revision=0,
            key="refresh",
            speaker_id=aria["id"],
            content="Which of you knows where the duke went?",
            targets=[mara["id"], tomas["id"]],
            participants=participants,
        )
        originals = {item["actor_id"]: item for item in ingested["activations"]}
        mara_capsule = await _claim(
            server,
            campaign["id"],
            conversation_id,
            originals[mara["id"]],
            revision=1,
            key="mara-before-refresh",
        )
        tomas_capsule = await _claim(
            server,
            campaign["id"],
            conversation_id,
            originals[tomas["id"]],
            revision=2,
            key="tomas-before-refresh",
        )
        await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "add",
                "payload": {
                    "campaign_id": campaign["id"],
                    "actor_id": mara["id"],
                    "knowledge_key": "duke-route",
                    "proposition": "The duke took the observatory road.",
                    "subject_ref": "actor:duke",
                    "epistemic_status": "known",
                },
                "idempotency_key": "refresh-mara-only",
            },
        )
        status = await _call(
            server,
            "npc_conversation",
            {
                "campaign_id": campaign["id"],
                "action": "get",
                "payload": {"conversation_id": conversation_id},
            },
        )
        assert status["conversation_revision"] == 4
        assert status["refreshed_actor_ids"] == [mara["id"]]
        active = {item["actor_id"]: item for item in status["activations"]}
        replacement = active[mara["id"]]
        assert replacement["replacement_for"] == originals[mara["id"]]["activation_ref"]
        assert replacement["reason"] == originals[mara["id"]]["reason"]
        assert replacement["from_cursor"] == originals[mara["id"]]["from_cursor"]
        assert replacement["to_cursor"] == originals[mara["id"]]["to_cursor"]
        assert active[tomas["id"]]["activation_ref"] == originals[tomas["id"]]["activation_ref"]
        assert "replacement_for" not in active[tomas["id"]]

        with pytest.raises(Exception, match="activation is invalidated"):
            await _claim(
                server,
                campaign["id"],
                conversation_id,
                originals[mara["id"]],
                revision=4,
                key="invalidated-mara",
            )
        refreshed_capsule = await _claim(
            server,
            campaign["id"],
            conversation_id,
            replacement,
            revision=4,
            key="replacement-mara",
        )
        assert refreshed_capsule["actor_runtime_id"] != mara_capsule["actor_runtime_id"]
        assert refreshed_capsule["inbox"][0]["content"] == (
            "Which of you knows where the duke went?"
        )
        assert "The duke took the observatory road." in str(
            refreshed_capsule["bootstrap"]["actor_knowledge"]
        )

        assert tomas_capsule["actor_runtime_id"] != mara_capsule["actor_runtime_id"]
        assert tomas_capsule["bootstrap"]["actor"]["id"] == tomas["id"]

        aborted = await _call(
            server,
            "npc_conversation",
            {
                "campaign_id": campaign["id"],
                "action": "abort",
                "payload": {
                    "conversation_id": conversation_id,
                    "expected_conversation_revision": 5,
                    "idempotency_key": "abort-refresh",
                },
            },
        )
        assert aborted["status"] == "aborted"

    asyncio.run(exercise())


def test_closed_conversation_memory_is_branch_local(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign, mara, _tomas, aria = await _campaign_with_party(server)
        participants = [aria["id"], mara["id"]]
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        before = await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign["id"],
                "label": "Before branch-local conversation",
                "expected_revision": current["revision"],
                "expected_head_snapshot_id": "",
                "idempotency_key": "snapshot-before-conversation",
            },
        )
        main_branch = next(
            item
            for item in await _call(
                server,
                "branch_query",
                {"campaign_id": campaign["id"], "view": "list", "payload": {}},
            )
            if item["is_current"]
        )
        opened = await _open(server, campaign["id"], participants, key="open-memory")
        conversation_id = opened["conversation_id"]
        ingested = await _ingest_speech(
            server,
            campaign["id"],
            conversation_id,
            revision=0,
            key="memory",
            speaker_id=aria["id"],
            content="Where is the moon-key hidden?",
            targets=[mara["id"]],
            participants=participants,
        )
        activation = ingested["activations"][0]
        capsule = await _claim(
            server,
            campaign["id"],
            conversation_id,
            activation,
            revision=1,
            key="memory",
        )
        submitted = await _submit(
            server,
            campaign["id"],
            conversation_id,
            activation,
            capsule,
            _proposal(
                capsule,
                text="The moon-key is beneath the dry fountain.",
                content_mode="grounded",
                basis_refs=[capsule["inbox"][0]["event_id"]],
                targets=[aria["id"]],
                knowledge_delta={
                    "action": "add",
                    "actor_id": mara["id"],
                    "knowledge_key": f"conversation:{conversation_id}:moon-key-question",
                    "proposition": "Aria asked where the moon-key was hidden.",
                    "subject_ref": f"actor:{aria['id']}",
                    "epistemic_status": "known",
                    "confidence": 4,
                    "cause": f"conversation:{conversation_id}",
                    "disclosure_scope": "dm",
                },
            ),
            revision=2,
            key="memory",
        )
        await _publish(
            server,
            campaign["id"],
            conversation_id,
            submitted,
            revision=3,
            key="memory",
            participants=participants,
        )
        status = await _call(
            server,
            "npc_conversation",
            {
                "campaign_id": campaign["id"],
                "action": "get",
                "payload": {"conversation_id": conversation_id},
            },
        )
        candidates = [item for item in status["memory_candidates"] if item["status"] == "available"]
        assert any(item["actor_id"] == aria["id"] for item in candidates)
        assert any(
            item["value"].get("knowledge_key")
            == f"conversation:{conversation_id}:moon-key-question"
            for item in candidates
        )
        closed = await _call(
            server,
            "npc_conversation",
            {
                "campaign_id": campaign["id"],
                "action": "close",
                "payload": {
                    "conversation_id": conversation_id,
                    "expected_conversation_revision": 4,
                    "accepted_candidate_ids": [item["candidate_id"] for item in candidates],
                    "idempotency_key": "close-memory",
                },
            },
        )
        assert closed["event"]["event_type"] == "npc_conversation"

        main_context = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "actor_id": mara["id"],
                "purpose": "npc_turn",
                "query": "moon-key fountain",
                "interlocutor_actor_ids": [aria["id"]],
                "branch_id": main_branch["id"],
            },
        )
        assert "The moon-key is beneath the dry fountain." in str(
            main_context["conversation"]["events"]
        )
        heard_on_main = await _call(
            server,
            "actor_knowledge_query",
            {
                "campaign_id": campaign["id"],
                "actor_id": aria["id"],
                "view": "list",
                "payload": {"branch_id": main_branch["id"]},
            },
        )
        assert any(
            "moon-key is beneath the dry fountain" in item["proposition"] for item in heard_on_main
        )

        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        after = await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign["id"],
                "label": "After branch-local conversation",
                "expected_revision": current["revision"],
                "expected_head_snapshot_id": before["id"],
                "idempotency_key": "snapshot-after-conversation",
            },
        )
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "branch_change",
            {
                "campaign_id": campaign["id"],
                "action": "create",
                "payload": {
                    "name": "before-the-moon-key-conversation",
                    "from_snapshot_id": before["id"],
                    "checkout": True,
                },
                "expected_revision": current["revision"],
                "expected_branch_id": main_branch["id"],
                "idempotency_key": "branch-before-conversation",
            },
        )
        assert after["id"] != before["id"]

        alternate_context = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "actor_id": mara["id"],
                "purpose": "npc_turn",
                "query": "moon-key fountain",
                "interlocutor_actor_ids": [aria["id"]],
            },
        )
        assert "The moon-key is beneath the dry fountain." not in str(
            alternate_context["conversation"]["events"]
        )
        heard_on_alternate = await _call(
            server,
            "actor_knowledge_query",
            {
                "campaign_id": campaign["id"],
                "actor_id": aria["id"],
                "view": "list",
                "payload": {},
            },
        )
        assert heard_on_alternate == []

        historical_main = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "actor_id": mara["id"],
                "purpose": "npc_turn",
                "query": "moon-key fountain",
                "interlocutor_actor_ids": [aria["id"]],
                "branch_id": main_branch["id"],
            },
        )
        assert "The moon-key is beneath the dry fountain." in str(
            historical_main["conversation"]["events"]
        )

    asyncio.run(exercise())
